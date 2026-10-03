# Edinburgh Cocktail Week Map

An interactive map of every bar serving a £6 signature cocktail during
[Edinburgh Cocktail Week](https://www.edinburghcocktailweek.co.uk/signature-cocktails).
The official site lists the bars but has no map, so this site plots them.

- Pins are coloured by area. Click one to see its cocktail, ingredients and a directions link.
- You can search by bar, cocktail or ingredient.
- You can filter by area, base spirit (gin, rum, tequila…) and features: vegan alternative, accessible, dogs welcome, alcohol-free alternative.
- You can save bars to plan a route. Saved bars are stored in your browser.

It's a static site with no build step. Leaflet loads from cdnjs, and the basemap
is drawn from embedded OpenStreetMap vector data, so there's no tile server or API key.

## Structure

```
public/
  index.html          the app
  data/venues.json    bars, cocktails, coordinates (generated)
  data/basemap.json   simplified streets/parks/water/coastline (generated)
scripts/
  update_venues.py    scrape the ECW page + geocode → venues.json
  build_basemap.py    fetch OSM data via Overpass → basemap.json
vercel.json           serves public/ as-is
```

## Run locally

```bash
python3 -m http.server 4173 -d public
```

## Refresh the data

If the bar list changes:

```bash
python3 scripts/update_venues.py
```

This uses only the Python standard library. Geocoding uses postcodes.io and
OpenStreetMap Nominatim, and takes about 2 minutes because of Nominatim's rate
limit. Known typos in the source listing are fixed in `ADDRESS_FIXES` and
`PREFER_NOMINATIM` at the top of the script. Check any new bars on the map
after a refresh.

The basemap rarely needs rebuilding:

```bash
python3 scripts/build_basemap.py
```

## Deploy

Import the repo in Vercel. `vercel.json` sets the output directory to `public`
with no framework and no build command.

## Credits

The bar and cocktail data comes from edinburghcocktailweek.co.uk. This is an
unofficial fan project and isn't affiliated with Edinburgh Cocktail Week.
Map data © OpenStreetMap contributors, ODbL.
