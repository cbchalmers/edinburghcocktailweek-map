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

Photos: each cocktail block on the ECW pages starts with a photo, which is
stored as its Squarespace CDN URL (the page links to ECW's copy, it isn't
re-hosted). ECW occasionally shows two bars' photos the wrong way round; where
a photo's file name is exactly another bar's name, the photos are swapped back.

Opening hours: scripts/hours_manual.json (researched by hand from each bar's
own website, keyed by venue id) wins; otherwise the opening_hours tag of the
matching OpenStreetMap venue is used. Hours are stored as the original
OSM-syntax text plus a parsed weekly schedule the page uses for "Open now".
"""
import datetime
import difflib
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
HOURS_MANUAL = ROOT / "scripts" / "hours_manual.json"
OSM_HOURS_CACHE = ROOT / "scripts" / "hours_osm_cache.json"  # last good OSM lookup, used when Overpass is down
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
    """The page as plain text lines, with each photo as a "[[IMG]]<url>" line."""
    s = fetch(SITE + path).decode("utf-8")
    s = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", "", s, flags=re.S)

    def img(m):
        u = re.search(r'data-src="([^"]+)"', m.group(0)) or re.search(r'\ssrc="([^"]+)"', m.group(0))
        return f"\n[[IMG]]{u.group(1)}\n" if u and "squarespace-cdn.com" in u.group(1) else "\n"
    s = re.sub(r"<img[^>]*>", img, s)
    s = html.unescape(re.sub(r"<[^>]+>", "\n", s))
    lines = [l.replace("\xa0", " ").replace("​", "").strip() for l in s.split("\n")]
    lines = [l for l in lines if l]
    end = lines.index("Newsletter Block") if "Newsletter Block" in lines else len(lines)
    return lines[:end]


def parse_list(lines):
    """Parse repeating blocks of: name, address, cocktail, ingredients, [tags]."""
    # Bars start after the first area heading or the VE/AC/DF/NA legend, whichever comes first.
    i = next(i for i, l in enumerate(lines) if i > lines.index("Bar List") and (l.upper() in AREAS or l == "VE"))
    out, area, photo = [], None, None
    while i < len(lines):
        l = lines[i]
        if l.startswith("[[IMG]]"):
            photo = l[7:]; i += 1; continue
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
                        cocktail=cocktail, ingredients=ingredients, tags=tags, photo=photo))
        photo = None
    fix_photo_swaps(out)
    return out


def _norm(s):
    s = re.sub(r"\b(the|bar|and)\b", "", s.lower().replace("&", "and"))
    return re.sub(r"[^a-z0-9]", "", s)


def photo_name(url):
    return _norm(urllib.parse.unquote_plus(url.rsplit("/", 1)[-1]).rsplit(".", 1)[0])


def fix_photo_swaps(rows):
    """Swap back photos ECW shows against the wrong bar (e.g. Bar 1819 ↔ Metro)."""
    for r in rows:
        if not r["photo"] or photo_name(r["photo"]) == _norm(r["name"]):
            continue
        other = next((o for o in rows if o is not r and o["photo"] and photo_name(o["photo"]) == _norm(r["name"])), None)
        if other and photo_name(r["photo"]) != _norm(other["name"]) and _norm(other["name"]).startswith(photo_name(r["photo"]) or "-"):
            r["photo"], other["photo"] = other["photo"], r["photo"]
            print(f"  swapped photos: {r['name']} ↔ {other['name']}")


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


# ---- opening hours

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
OSM_POI_QUERY = """[out:json][timeout:90];(
nwr["name"]["amenity"~"^(bar|pub|restaurant|nightclub|cafe|biergarten|fast_food|food_court)$"](55.92,-3.25,55.99,-3.15);
nwr["name"]["tourism"="hotel"](55.92,-3.25,55.99,-3.15);
nwr["name"]["leisure"~"^(amusement_arcade|bowling_alley)$"](55.92,-3.25,55.99,-3.15););out center tags;"""


def parse_opening_hours(text, unlisted_closed=True):
    """Parse the common subset of OSM opening_hours into 7 lists (Mon..Sun) of
    [open, close] minutes after midnight; close can pass 1440 for after-midnight.
    Returns None for anything outside the subset (month/date ranges, "+", etc.),
    in which case the page shows the raw text instead of an open/closed status.
    Days the text never mentions are closed in OSM's convention; with
    unlisted_closed=False (hand-researched hours) they're None, meaning "not listed"."""
    text = text.strip()
    if text == "24/7":
        return [[[0, 1440]] for _ in DAYS]
    week = [[] if unlisted_closed else None for _ in DAYS]
    text = re.sub(r"(?<=\d),\s*(?=[A-Z][a-z])", "; ", text)  # "Mo 12:00-23:00, Tu …" is a common typo for ";"
    for rule in [r.strip() for r in text.split(";") if r.strip()]:
        m = re.fullmatch(r"(?:([A-Za-z]{2}(?:[-,][A-Za-z]{2})*(?:,\s*[A-Za-z]{2}(?:-[A-Za-z]{2})?)*)\s+)?(.+)", rule)
        if not m:
            return None
        days_part, times = m.group(1), m.group(2).strip()
        if days_part is None and re.fullmatch(r"[A-Za-z]{2}(?:[-,][A-Za-z]{2})*", times):
            return None  # days with no times
        days = set(range(7)) if days_part is None else set()
        for sel in re.split(r",\s*", days_part or ""):
            if not sel or sel == "PH":
                continue  # public-holiday rules don't apply to the festival week
            a, _, b = sel.partition("-")
            if a not in DAYS or (b and b not in DAYS):
                return None
            i, j = DAYS.index(a), DAYS.index(b or a)
            days |= set(range(i, j + 1)) if i <= j else set(range(i, 7)) | set(range(0, j + 1))
        if not days:
            continue
        if times in ("off", "closed"):
            spans = []
        else:
            spans = []
            for span in times.split(","):
                tm = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", span.strip())
                if not tm:
                    return None
                o = int(tm.group(1)) * 60 + int(tm.group(2))
                c = int(tm.group(3)) * 60 + int(tm.group(4))
                if c <= o:
                    c += 1440  # closes after midnight (00:00 means midnight)
                spans.append([o, c])
        for d in days:
            week[d] = spans  # later rules override earlier ones, as in OSM
    return week


def osm_hours(venues):
    """venue id → (opening_hours text, OSM url) for venues matched to an OSM place."""
    pois, last = None, None
    for attempt, endpoint in enumerate(OVERPASS * 2):  # public servers are often busy; retry across mirrors
        try:
            req = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"data": OSM_POI_QUERY}).encode(), headers=UA)
            with urllib.request.urlopen(req, timeout=180) as r:
                pois = json.load(r)["elements"]
            break
        except Exception as e:
            last = e
            print(f"  Overpass {endpoint} failed ({e}), retrying…")
            time.sleep(5 * (attempt + 1))
    if pois is None:
        raise last
    out = {}
    for v in venues:
        best = None
        for e in pois:
            lat, lng = (e["lat"], e["lon"]) if "lat" in e else (e["center"]["lat"], e["center"]["lon"])
            dist = dist_m((v["lat"], v["lng"]), (lat, lng))
            if dist > 150:
                continue
            a, b = _norm(v["name"]), _norm(e["tags"]["name"])
            score = difflib.SequenceMatcher(None, a, b).ratio()
            if b and (a in b or b in a):
                score = max(score, 0.9)
            if score >= 0.75 and (best is None or (score, -dist) > (best[0], -best[1])):
                best = (score, dist, e)
        if best and best[2]["tags"].get("opening_hours"):
            e = best[2]
            out[v["id"]] = (e["tags"]["opening_hours"], f"https://www.openstreetmap.org/{e['type']}/{e['id']}")
    return out


def attach_hours(venues):
    manual = json.loads(HOURS_MANUAL.read_text()) if HOURS_MANUAL.exists() else {}
    try:
        osm = osm_hours(venues)
        OSM_HOURS_CACHE.write_text(json.dumps(osm, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as e:  # Overpass is flaky; fall back to the last successful lookup
        osm = {k: tuple(v) for k, v in json.loads(OSM_HOURS_CACHE.read_text()).items()} if OSM_HOURS_CACHE.exists() else {}
        print(f"  OpenStreetMap hours lookup failed ({e}); using {len(osm)} cached entries")
    counts = {"manual": 0, "osm": 0, "none": 0}
    for v in venues:
        if manual.get(v["id"], {}).get("opening_hours"):
            m = manual[v["id"]]
            text, source, url = m["opening_hours"], "website", m.get("source")
        elif v["id"] in osm:
            (text, url), source = osm[v["id"]], "osm"
        else:
            counts["none"] += 1
            continue
        week = parse_opening_hours(text, unlisted_closed=source != "website")
        if week is None:
            print(f"  hours not in the simple format, shown as text: {v['name']}: {text}")
        m = manual.get(v["id"], {}) if source == "website" else {}
        v["hours"] = dict(text=text, week=week, source=source, url=url, checked=m.get("checked"),
                          official=m.get("source_type", "official") == "official" if m else None, note=m.get("note"))
        counts["manual" if source == "website" else "osm"] += 1
    print(f"Hours: {counts['manual']} from bar websites, {counts['osm']} from OpenStreetMap, {counts['none']} not listed")


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
        venues[key]["cocktails"].append(dict(name=r["cocktail"], ingredients=r["ingredients"], tags=r["tags"], photo=r["photo"]))
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

    attach_hours(venues)

    village = parse_village(page_lines("/the-cocktail-village"))
    if village["address"] not in cache:
        cache[village["address"]] = list(nominatim("Festival Square, Edinburgh, UK") or VILLAGE_FALLBACK_LL)
    village["lat"], village["lng"] = cache[village["address"]]
    # The village runs on festival dates only, so give the page a dated schedule for "open now".
    expected = "Open Friday 2nd - Sunday 11th October, 12pm - 11pm (8pm close on Sundays)."
    if village["hours"] != expected:
        print(f"  ⚠ Cocktail Village hours text changed; update the schedule in {Path(__file__).name}:\n    {village['hours']}")
    village["schedule"] = dict(start="2026-10-02", end="2026-10-11",
                               week=parse_opening_hours("Mo-Sa 12:00-23:00; Su 12:00-20:00"))

    CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    OUT.write_text(json.dumps(dict(updated=datetime.date.today().isoformat(), village=village, venues=venues),
                              ensure_ascii=False, separators=(",", ":")))
    print(f"Wrote {len(venues)} venues ({sum(v['tier'] == 'prestige' for v in venues)} prestige) to {OUT}")


if __name__ == "__main__":
    main()
