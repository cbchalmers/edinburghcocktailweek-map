# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An unofficial interactive map of the bars serving £6 signature cocktails at Edinburgh Cocktail Week 2026. It's a static site with no build step and no package manager. It's deployed on Vercel at https://edinburghcocktailweek-map.vercel.app, and each push to `main` deploys to production.

## Commands

```bash
python3 -m http.server 4173 -d public   # run locally; fetch() of data/*.json needs HTTP, not file://
python3 scripts/update_venues.py        # re-scrape ECW listing + geocode → public/data/venues.json (~2 min, Nominatim rate limit)
python3 scripts/build_basemap.py        # re-fetch OSM via Overpass → public/data/basemap.json (rarely needed)
```

The scripts use only the Python standard library. There are no tests, linter or build. Check changes in a browser at both desktop and phone widths (~375px).

## Architecture

The whole app is `public/index.html`: inline CSS, then one inline script wrapped in an async IIFE. The IIFE fetches `data/venues.json` and `data/basemap.json` before doing anything else. Leaflet 1.9.4 loads from cdnjs. `vercel.json` serves `public/` as-is.

**Venue data** (`venues.json`): an array of venues, each `{name, address, area, lat, lng, cocktails:[{name, ingredients, tags}]}`. One venue can have several cocktails; for example, The Raging Bull has three. Tags are `VE`/`AC`/`DF`/`NA`; DF means "dogs welcome", not dairy-free. At load time the page derives more fields for each venue: `color` from area, a merged `tags` list, `spirits` from regexes over the ingredients (`SPIRITS`), and a lowercase search string `hay`. The `AREAS` constant in `index.html` fixes area order and colours, and its names must match the area strings that `update_venues.py` writes.

**Scraper** (`scripts/update_venues.py`): turns the ECW page into plain text lines and parses them as a fixed pattern of name, address, cocktail, ingredients, then an optional tag line. Area headings and legend lines are skipped. The source page has typos, which are handled by `ADDRESS_FIXES`, `PREFER_NOMINATIM` and the `AD`→`AC` tag fix. Geocoding uses the Nominatim result when it's within 250 m of the postcodes.io postcode centroid, and the centroid otherwise. After a refresh, check new or moved pins on the map.

**Basemap** (`basemap.json`): there are no map tiles. The streets, parks, water, rail and sea are OSM geometry simplified with Douglas-Peucker. Coordinates are delta-encoded integers (lat/lng × 1e5) and decoded by `decode()` in the page. The sea polygon is made by joining coastline ways, starting from the easternmost one, and closing the shape to the north. Each layer is drawn as a single Leaflet multi-polyline or multi-polygon on one shared canvas renderer. `labels` holds `[name, lat, lng, rank, angle]`; street labels appear by zoom level according to rank. They are only built for the visible area, on each `moveend`, and sit in a custom `labels` pane below the pins.

**Theming**: all colours are CSS custom properties on `:root`, redefined for `prefers-color-scheme: dark` and for `[data-theme]`. Canvas layers can't use CSS variables, so `styleBase()` reads them with `getComputedStyle` and is called again on zoom and theme changes. Any new map colour needs to be added there as well as in the CSS.

**Interaction state**: `state` holds the filters. Within a filter group the logic is OR for areas and spirits, and AND for features. `render()` rebuilds both the list and the marker layer. `select(id)` keeps the list and map in sync: it highlights the active pin, scrolls the list, and opens the popup after `flyTo`. Saved bars are stored in `localStorage` under the key `ecw-saved`, as venue indices into `venues.json`. A data refresh that reorders venues will therefore change which bars users see as saved.

## iOS constraints (learned the hard way)

- Pins are `L.marker` with a 32px `divIcon` tap target. Don't switch them back to `circleMarker`, and don't give each pin its own `L.svg()` renderer. One renderer per pin caused memory-related tab reloads on iOS Safari, and the small SVG circles were hard to tap.
- `html, body` have `overflow: hidden; overscroll-behavior: none`, and only `.list` scrolls. This stops the page itself from scrolling, which triggered pull-to-refresh while panning the map. `#map` turns off user-select, the touch callout and the tap highlight, because a long press used to select the whole map.
- Hover tooltips are bound only when `(hover: hover)` matches.
- On phones (≤760px), `.rail` is `display: contents`, so the header, map and list become rows of the `.app` grid.

## Content conventions

- Keep the "unofficial" wording.
- Keep the link back to edinburghcocktailweek.co.uk.
- Keep the OpenStreetMap attribution (ODbL).
