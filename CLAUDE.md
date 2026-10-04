# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An unofficial interactive map of Edinburgh Cocktail Week 2026: the bars on the £6 Signature and £9 Prestige cocktail lists, plus the Cocktail Village. It's a static site with no build step and no package manager. It's deployed on Vercel at https://edinburghcocktailweek-map.vercel.app, and each push to `main` deploys to production.

The repo is public at https://github.com/cbchalmers/edinburghcocktailweek-map. Its GitHub description, website link and topics are set with `gh repo edit`, not in files. Everything committed is public, so keep secrets, personal paths and local tooling config out of it; the local `.claude/` folder is excluded via `.git/info/exclude`.

## Commands

```bash
python3 -m http.server 4173 -d public   # run locally; fetch() of data/*.json needs HTTP, not file://
python3 scripts/update_venues.py        # re-scrape ECW pages + geocode new addresses → public/data/venues.json
python3 scripts/build_basemap.py        # re-fetch OSM via Overpass → public/data/basemap.json (rarely needed)
```

The scripts use only the Python standard library. There are no tests, linter or build. Check changes in a browser at both desktop and phone widths (~375px).

## Architecture

The whole app is `public/index.html`: inline CSS, then one inline script wrapped in an async IIFE. The IIFE fetches `data/venues.json` and `data/basemap.json` before doing anything else. The map is MapLibre GL JS 5.24.0, the UMD build from jsDelivr; v6 is ESM-only with a separate worker file, so stay on 5.x unless the page moves to modules. `vercel.json` serves `public/` as-is.

MapLibre replaced Leaflet + leaflet-rotate because that plugin made the canvas basemap drift away from the pins while zooming and rotating. MapLibre zoom levels are one lower than Leaflet's for the same scale.

**Venue data** (`venues.json`): `{updated, village, venues}`. `venues` is an array of `{id, tier, price, name, address, area, lat, lng, hours?, cocktails:[{name, ingredients, tags, photo}]}`, where `tier` is `signature` (£6) or `prestige` (£9). One venue can have several cocktails; for example, The Raging Bull has three. Tags are `VE`/`AC`/`DF`/`NA`; DF means "dogs welcome", not dairy-free. `village` holds the Cocktail Village text, its pop-up bars, its street food, its position and a dated `schedule` ({start, end, week}) for "open now"; the script warns if ECW's village hours text changes, because that schedule is hard-coded.

**Photos**: `photo` is ECW's Squarespace CDN URL for that cocktail's picture, scraped from the image that precedes each block on the ECW page. The page requests sizes with `?format=300w` (list) and `?format=500w` (popup). Images are hotlinked, not committed, because they're ECW's. `fix_photo_swaps()` swaps photos back when ECW shows them against the wrong bar (it did for Bar 1819 ↔ Metro).

**Opening hours**: `hours` is `{text, week, source, url, checked, official, note}`. `text` is OpenStreetMap `opening_hours` syntax. `week` is 7 lists (Mon..Sun) of `[open, close]` minutes, with close > 1440 for after midnight, or null if `parse_opening_hours()` can't handle the text (the page then shows the text instead of a status). Sources in priority order: `scripts/hours_manual.json` (keyed by venue id, researched from bars' own websites), then the `opening_hours` tag of the matching OSM place (fetched via Overpass, matched by name within 150 m). The page computes open/closed in Europe/London time (`hoursStatus()`), so it's right wherever the viewer is. The `AREAS` constant in `index.html` fixes area order and colours, and its names must match the area strings that `update_venues.py` writes.

**Venue ids** are a slug of name + postcode, with `-prestige` added for Prestige bars. They're the keys for saved bars in `localStorage`, so they must stay stable across data refreshes; the script refuses to write duplicate ids.

**Filtering is per cocktail.** At load time each cocktail gets `spirits`, `styles` and `has.{egg,dairy}` from the regexes in `SPIRITS`, `STYLES` and `AVOID`, which run over the cocktail's name plus ingredients. `cocktailMatches(c)` applies the spirit, style, "leave out" and feature filters. A venue is shown if any of its cocktails matches, and in the list and popup the cocktails that don't match are dimmed. Tier, area, saved and search filters apply to the venue as a whole.

**Scraper** (`scripts/update_venues.py`): turns each ECW page into plain text lines and parses them as a fixed pattern of name, address, cocktail, ingredients, then an optional tag line. Area headings and legend lines are skipped. The Prestige page has no areas, so each Prestige bar takes the area of the nearest Signature bar. The source pages have typos, which are handled by `ADDRESS_FIXES`, `PREFER_NOMINATIM` and the `AD`→`AC` tag fix. `scripts/geocache.json` (address → `[lat, lng]`) is checked first; only new addresses are geocoded. New addresses use the Nominatim result when it's within 250 m of the postcodes.io postcode centroid, and the centroid otherwise. To move a pin by hand, edit the cache and re-run. After a refresh, check new or moved pins on the map.

**Basemap** (`basemap.json`): there are no map tiles. The streets, parks, water, rail and sea are OSM geometry simplified with Douglas-Peucker. Coordinates are delta-encoded integers (lat/lng × 1e5); `decode()` in the page turns them into GeoJSON `[lng, lat]`. The sea polygon is made by joining coastline ways, starting from the easternmost one, and closing the shape to the north. `setupBase()` adds each layer as a GeoJSON source plus fill or line layer once the style has loaded. `labels` holds `[name, lat, lng, rank, angle]`; there's one symbol layer per rank, each with its own `minzoom`, with `text-rotate` set to the angle and `text-rotation-alignment: map`.

**Map text** needs glyph PBFs. They're self-hosted in `public/fonts/<fontstack>/<range>.pbf`, taken from OpenFreeMap's Noto Sans. Only ranges 0-255 and 8192-8447 are present. If a name ever needs other characters, add that range or MapLibre will log 404s and skip those glyphs.

**Pins** are `maplibregl.Marker`s with our own HTML element (`makeMarker()`), so the CSS pins and 32px tap targets are unchanged. MapLibre moves them in its render loop, so they stay locked to the map. There's one shared `popup`, filled by `openPopupFor(id)`. **Bar names** are the `venue-names` symbol layer at zoom ≥ `NAME_ZOOM` (15), with `text-variable-anchor` left/right and `symbol-sort-key` priority (active, then saved, then Prestige). An invisible `pin-blockers` icon layer sits above it, so MapLibre's collision detection keeps names off the pins.

**Theming**: all colours are CSS custom properties on `:root`, redefined for `prefers-color-scheme: dark` and for `[data-theme]`. Map layers can't use CSS variables, so `styleBase()` reads them with `getComputedStyle` and calls `setPaintProperty`; it runs again when the theme changes. Any new map colour needs to be added there as well as in the CSS. `--ctrl-filter` inverts MapLibre's built-in control icons in dark mode.

**Load order**: the list and pins render immediately. `setupBase()` runs on the map's `load` event, which MapLibre holds back while the tab is hidden. Anything that touches sources or layers (`styleBase`, `updateNames`, the accuracy circle) checks `baseReady` first.

**Map extras**: rotation is native: two-finger twist, right-drag or Ctrl+drag. The compass is part of `NavigationControl`. The locate and maximise buttons are small custom controls built by `buttonControl()`. Maximise is CSS-only (`.app.map-max`) followed by `map.resize()`, because iPhones don't support the Fullscreen API for elements. Areas in the list start folded each visit (`openAreas`) and open automatically when any filter or search is on, or when a pin in that area is selected.

**Interaction state**: `state` holds the filters and the sort order (`area` or `near`). Within a filter group the logic is OR for tiers, areas, spirits and styles, and AND for "leave out" and features. `render()` rebuilds both the list and the marker layer, and refits the map unless it's called with `{refit: false}`. `markers` is a `Map` keyed by venue id, plus `"village"` for the Cocktail Village pin, which ignores filters. `select(id)` keeps the list and map in sync: it highlights the active pin, scrolls the list, and opens the popup after `flyTo`. Saved bars are stored in `localStorage` under `ecw-saved` as venue ids. The page converts older saves, stored as positions in the Signature list, to ids on load.

**Location**: "Nearest to me" and the locate control call `navigator.geolocation.watchPosition`. This needs HTTPS or localhost. On the first fix the list switches to distance order. After that it re-sorts each time you move more than 30 m, without moving the map. Walking time is the straight-line distance × 1.3 at 80 m per minute.

## iOS constraints (learned the hard way)

- Pins are HTML markers with a 32px tap target. In the Leaflet days, small SVG circles (and one SVG renderer per pin) caused hard-to-tap pins and memory-related tab reloads on iOS Safari, so don't turn them into tiny vector shapes.
- `html, body` have `overflow: hidden; overscroll-behavior: none`, and only `.list` scrolls. This stops the page itself from scrolling, which triggered pull-to-refresh while panning the map. `#map` turns off user-select, the touch callout and the tap highlight, because a long press used to select the whole map.
- Hover tooltips are bound only when `(hover: hover)` matches.
- The Saved toggle lives in the always-visible sort bar, not in the collapsible filters. Before this, people on phones couldn't find where saved bars went.
- On phones (≤760px), `.rail` is `display: contents`, so the header, map and list become rows of the `.app` grid.

## Content conventions

- Keep the "unofficial" wording.
- Keep the link back to edinburghcocktailweek.co.uk.
- Keep the OpenStreetMap attribution (ODbL).

## Licensing

The code is MIT (`LICENSE`, © Chris Chalmers). The MIT licence doesn't cover the data, and the README's Licence section says so; keep that distinction if the data or licence changes:
- The bar and cocktail listings in `public/data/venues.json` and `scripts/geocache.json` come from Edinburgh Cocktail Week.
- `public/data/basemap.json` is derived from OpenStreetMap and is under the ODbL.
