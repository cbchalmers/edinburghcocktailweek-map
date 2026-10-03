#!/usr/bin/env python3
"""Scrape the Edinburgh Cocktail Week signature cocktail list, geocode each bar
and write public/data/venues.json.

Usage: python3 scripts/update_venues.py

Geocoding uses postcodes.io (postcode centroids) and OpenStreetMap Nominatim
(street addresses, rate-limited to 1 request/second). Nominatim's result is used
when it lands within 250 m of the postcode centroid; otherwise the postcode
centroid wins. OVERRIDES fixes known bad or missing entries.
"""
import html
import json
import math
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

SOURCE = "https://www.edinburghcocktailweek.co.uk/signature-cocktails"
OUT = Path(__file__).resolve().parent.parent / "public" / "data" / "venues.json"
UA = {"User-Agent": "edinburghcocktailweek-map/1.0 (+https://github.com/cbchalmers/edinburghcocktailweek-map)"}

AREAS = {
    "CITY CENTRE (EAST)": "City Centre (East)",
    "CITY CENTRE (WEST)": "City Centre (West)",
    "GRASSMARKET": "Grassmarket & Old Town",
    "STOCKBRIDGE & CANONMILLS": "Stockbridge & Canonmills",
    "LEITH": "Leith",
    "BRUNTSFIELD & TOLLCROSS": "Bruntsfield & Tollcross",
    "BROUGHTON": "Broughton",
}
LEGEND = {"VE", "AC", "DF", "NA"}
TAG_LINE = re.compile(r"^[A-Z]{2}([,.]\s*[A-Z]{2})*,?$")
POSTCODE = re.compile(r"(EH\d+\s*\d[A-Z]{2})$")

# Typos in the source listing: address text fixes, and which geocoder to trust.
ADDRESS_FIXES = {"EH21EE": "EH2 1EE", "Lothain": "Lothian", "Henderson Street EH22 2HE": "Henderson Street, Leith"}
PREFER_NOMINATIM = {"265 Leith Walk EH6 6LE", "3 Royal Terrace Gardens EH7 5DX", "25 Henderson Street EH22 2HE"}


def fetch(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def page_lines():
    s = fetch(SOURCE).decode("utf-8")
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    s = html.unescape(re.sub(r"<[^>]+>", "\n", s))
    lines = [l.replace("\xa0", " ").replace("​", "").strip() for l in s.split("\n")]
    lines = [l for l in lines if l]
    return lines[lines.index("Bar List"):lines.index("Newsletter Block")]


def parse(lines):
    out, area, i = [], None, 0
    while i < len(lines) and lines[i].upper() not in AREAS:
        i += 1
    while i < len(lines):
        l = lines[i]
        if l.upper() in AREAS:
            area = AREAS[l.upper()]; i += 1; continue
        if l in LEGEND or l.startswith("- "):
            i += 1; continue
        name, addr, cocktail, ingredients = lines[i:i + 4]
        i += 4
        tags = []
        if i < len(lines) and TAG_LINE.match(lines[i]):
            tags = [t.strip() for t in re.split(r"[,.]", lines[i]) if t.strip()]
            tags = ["AC" if t == "AD" else t for t in tags]  # "AD" is a typo for AC in the source
            i += 1
        m = POSTCODE.search(addr)
        pc = m.group(1).replace(" ", "") if m else ""
        out.append(dict(name=name, address=addr, postcode=pc and pc[:-3] + " " + pc[-3:], area=area,
                        cocktail=cocktail, ingredients=ingredients, tags=tags))
    return out


def dist_m(a, b):
    return math.hypot((a[0] - b[0]) * 111_000, (a[1] - b[1]) * 111_000 * math.cos(math.radians(55.95)))


def geocode(rows):
    pcs = sorted({r["postcode"] for r in rows if r["postcode"]})
    res = json.loads(fetch("https://api.postcodes.io/postcodes", json.dumps({"postcodes": pcs}).encode(),
                           {"Content-Type": "application/json"}))["result"]
    by_pc = {q["query"]: (q["result"]["latitude"], q["result"]["longitude"]) for q in res if q["result"]}

    by_addr = {}
    for addr in sorted({r["address"] for r in rows}):
        street = POSTCODE.sub("", addr).strip().replace("Lothain", "Lothian")
        street = re.sub(r"^(Unit [\d/]+,\s*|Arch [\d-]+\s*|[\d-]+\s*\(Level 3\)\s*)", "", street)
        street = re.sub(r"^(\d+)\(1F\)", r"\1", street)
        q = urllib.parse.urlencode({"q": street + ", Edinburgh, UK", "format": "json", "limit": 1})
        try:
            hit = json.loads(fetch("https://nominatim.openstreetmap.org/search?" + q))
            by_addr[addr] = (float(hit[0]["lat"]), float(hit[0]["lon"])) if hit else None
        except Exception as e:  # keep going; postcode fallback covers it
            print("nominatim failed:", addr, e)
            by_addr[addr] = None
        time.sleep(1.1)

    for r in rows:
        n, p = by_addr.get(r["address"]), by_pc.get(r["postcode"])
        if r["address"] in PREFER_NOMINATIM and n or (n and not p) or (n and p and dist_m(n, p) <= 250):
            r["ll"] = n
        elif p:
            r["ll"] = p
        else:
            raise SystemExit(f"Could not geocode {r['name']} — {r['address']}")


def main():
    rows = parse(page_lines())
    print(f"Parsed {len(rows)} cocktails")
    geocode(rows)
    venues = {}
    for r in rows:
        key = (r["name"].upper(), r["address"])
        if key not in venues:
            addr = r["address"]
            for bad, good in ADDRESS_FIXES.items():
                addr = addr.replace(bad, good)
            venues[key] = dict(name=r["name"], address=addr, area=r["area"],
                               lat=round(r["ll"][0], 6), lng=round(r["ll"][1], 6), cocktails=[])
        venues[key]["cocktails"].append(dict(name=r["cocktail"], ingredients=r["ingredients"], tags=r["tags"]))
    OUT.write_text(json.dumps(list(venues.values()), ensure_ascii=False, separators=(",", ":")))
    print(f"Wrote {len(venues)} venues to {OUT}")


if __name__ == "__main__":
    main()
