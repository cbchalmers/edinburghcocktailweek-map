#!/usr/bin/env python3
"""Scrape the Edinburgh Cocktail Week bar lists and Cocktail Village details,
geocode each bar and write public/data/venues.json.

Usage: python3 scripts/update_venues.py

Sources:
  /signature-cocktails  £6 cocktails, grouped by area
  /prestige-cocktails   £9 cocktails, no areas (each bar takes the area of the
                        nearest signature bar)
  /the-cocktail-village opening times, pop-up bars and street food

Geocoding: scripts/geocache.json maps each address to [lat, lng] and is checked
first, so only new addresses are looked up. A new address is looked up with
postcodes.io (postcode centroid) and OpenStreetMap Nominatim (street address,
1 request/second); Nominatim wins when it lands within 250 m of the centroid.
To correct a pin by hand, edit its entry in geocache.json and re-run.

Every venue gets a fixed `id` (slug of name + postcode). The page stores saved
bars by id, so ids must not change for an existing bar.
"""
import datetime
import html
import json
import math
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

SITE = "https://www.edinburghcocktailweek.co.uk"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "public" / "data" / "venues.json"
CACHE = ROOT / "scripts" / "geocache.json"
UA = {"User-Agent": "edinburghcocktailweek-map/1.0 (+https://github.com/cbchalmers/edinburghcocktailweek-map)"}

LISTS = [
    # (tier, path, price)
    ("signature", "/signature-cocktails", 6),
    ("prestige", "/prestige-cocktails", 9),
]
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

# Typos in the source listings: address text fixes, and which geocoder to trust.
ADDRESS_FIXES = {"EH21EE": "EH2 1EE", "Lothain": "Lothian", "Henderson Street EH22 2HE": "Henderson Street, Leith",
                 "Princes Street EH12AB": "Princes Street EH1 2AB", "Queen street": "Queen Street", "LEITH EH6": "Leith EH6"}
PREFER_NOMINATIM = {"265 Leith Walk EH6 6LE", "3 Royal Terrace Gardens EH7 5DX", "25 Henderson Street EH22 2HE"}
VILLAGE_FALLBACK_LL = (55.946066, -3.205560)  # Festival Square


def fetch(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def page_lines(path):
    s = fetch(SITE + path).decode("utf-8")
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    s = html.unescape(re.sub(r"<[^>]+>", "\n", s))
    lines = [l.replace("\xa0", " ").replace("​", "").strip() for l in s.split("\n")]
    lines = [l for l in lines if l]
    end = lines.index("Newsletter Block") if "Newsletter Block" in lines else len(lines)
    return lines[:end]


def parse_list(lines):
    """Parse repeating blocks of: name, address, cocktail, ingredients, [tags]."""
    # Bars start after the first area heading or the VE/AC/DF/NA legend, whichever comes first.
    i = next(i for i, l in enumerate(lines) if i > lines.index("Bar List") and (l.upper() in AREAS or l == "VE"))
    out, area = [], None
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
        out.append(dict(name=name, raw_address=addr, postcode=pc and pc[:-3] + " " + pc[-3:], area=area,
                        cocktail=cocktail, ingredients=ingredients, tags=tags))
    return out


def parse_village(lines):
    def after(prefix):
        return next(l for l in lines if l.startswith(prefix) and l != "Open Menu")

    def bullet_list(heading):
        i = lines.index(heading)
        return [s.strip() for s in lines[i + 1].split("•") if s.strip()]

    intro = after("The Cocktail Village at")
    return dict(
        name="The Cocktail Village",
        address="Festival Square, Lothian Road EH3 9SR",
        intro=intro,
        hours=after("Open "),
        details=after("Inside there are"),
        popups=bullet_list("POP-UP BARS INCLUDE:"),
        food=bullet_list("STREET FOOD MARKET INCLUDES:"),
        url=SITE + "/the-cocktail-village",
    )


def slug(s):
    s = s.lower().replace("&", " and ").replace("’", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def fix_address(addr):
    for bad, good in ADDRESS_FIXES.items():
        addr = addr.replace(bad, good)
    return addr


def dist_m(a, b):
    return math.hypot((a[0] - b[0]) * 111_000, (a[1] - b[1]) * 111_000 * math.cos(math.radians(55.95)))


def nominatim(query):
    q = urllib.parse.urlencode({"q": query, "format": "json", "limit": 1})
    try:
        hit = json.loads(fetch("https://nominatim.openstreetmap.org/search?" + q))
        return (float(hit[0]["lat"]), float(hit[0]["lon"])) if hit else None
    except Exception as e:  # keep going; the postcode fallback covers it
        print("  nominatim failed:", query, e)
        return None
    finally:
        time.sleep(1.1)


def geocode(rows, cache):
    todo = [r for r in rows if r["address"] not in cache]
    if not todo:
        return
    print(f"Geocoding {len(todo)} new addresses…")
    pcs = sorted({r["postcode"] for r in todo if r["postcode"]})
    res = json.loads(fetch("https://api.postcodes.io/postcodes", json.dumps({"postcodes": pcs}).encode(),
                           {"Content-Type": "application/json"}))["result"]
    by_pc = {q["query"]: (q["result"]["latitude"], q["result"]["longitude"]) for q in res if q["result"]}
    for r in todo:
        if r["address"] in cache:  # same address seen earlier in this loop
            continue
        street = POSTCODE.sub("", r["raw_address"]).strip().replace("Lothain", "Lothian")
        street = re.sub(r"^(Unit [\d/]+,\s*|Arch [\d-]+\s*|[\d-]+\s*\(Level 3\)\s*|\d+\w+ Floor,\s*)", "", street)
        street = re.sub(r"^(\d+)\(1F\)", r"\1", street)
        n, p = nominatim(street + ", Edinburgh, UK"), by_pc.get(r["postcode"])
        if r["raw_address"] in PREFER_NOMINATIM and n or (n and not p) or (n and p and dist_m(n, p) <= 250):
            ll = n
        elif p:
            ll = p
        else:
            raise SystemExit(f"Could not geocode {r['name']} — {r['raw_address']}. Add it to {CACHE.name} by hand.")
        cache[r["address"]] = [round(ll[0], 6), round(ll[1], 6)]
        print(f"  {r['name']}: {r['address']} → {cache[r['address']]}")


def main():
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    rows = []
    for tier, path, price in LISTS:
        parsed = parse_list(page_lines(path))
        print(f"{path}: {len(parsed)} cocktails")
        for r in parsed:
            r.update(tier=tier, price=price, address=fix_address(r["raw_address"]))
        rows += parsed
    geocode(rows, cache)

    venues = {}
    for r in rows:
        key = (r["tier"], r["name"].upper(), r["address"])
        if key not in venues:
            vid = slug(r["name"]) + "-" + slug(r["postcode"] or r["address"])
            if r["tier"] != "signature":
                vid += "-" + r["tier"]
            lat, lng = cache[r["address"]]
            venues[key] = dict(id=vid, tier=r["tier"], price=r["price"], name=r["name"], address=r["address"],
                               area=r["area"], lat=lat, lng=lng, cocktails=[])
        venues[key]["cocktails"].append(dict(name=r["cocktail"], ingredients=r["ingredients"], tags=r["tags"]))
    venues = list(venues.values())

    ids = [v["id"] for v in venues]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"Duplicate venue ids: {dupes}")

    # Prestige bars have no area in the source; borrow the nearest signature bar's area.
    sig = [v for v in venues if v["area"]]
    for v in venues:
        if not v["area"]:
            v["area"] = min(sig, key=lambda s: dist_m((v["lat"], v["lng"]), (s["lat"], s["lng"])))["area"]

    village = parse_village(page_lines("/the-cocktail-village"))
    if village["address"] not in cache:
        cache[village["address"]] = list(nominatim("Festival Square, Edinburgh, UK") or VILLAGE_FALLBACK_LL)
    village["lat"], village["lng"] = cache[village["address"]]

    CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    OUT.write_text(json.dumps(dict(updated=datetime.date.today().isoformat(), village=village, venues=venues),
                              ensure_ascii=False, separators=(",", ":")))
    print(f"Wrote {len(venues)} venues ({sum(v['tier'] == 'prestige' for v in venues)} prestige) to {OUT}")


if __name__ == "__main__":
    main()
