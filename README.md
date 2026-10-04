# Edinburgh Cocktail Week Map

An interactive map of every bar in [Edinburgh Cocktail Week](https://www.edinburghcocktailweek.co.uk):
the £6 Signature and £9 Prestige cocktail lists, plus the Cocktail Village. The official site
lists the bars but has no map, so this site plots them.

- Pins are coloured by area, and Prestige bars are gold-edged diamonds. Click one to see its cocktail photo, ingredients, opening hours and a walking directions link.
- Each bar shows whether it's open now (in Edinburgh time) and when it closes or next opens.
- Zoom in to see bar names on the map. Rotate the map with a two-finger twist, or Shift + drag on a computer; the compass resets north.
- The map can be maximised, and the bar list can be hidden. Areas in the list start folded.
- The Cocktail Village at Festival Square has its own pin with opening times, pop-up bars and street food.
- "Nearest to me" uses your location to sort bars by distance and show walking times.
- You can search by bar, cocktail or ingredient.
- You can filter by list (Signature/Prestige), area, base spirit, style and flavour, and features (vegan alternative, accessible, dogs welcome, alcohol-free alternative). "Leave out" hides cocktails with egg white or foams, or with dairy, based on the listed ingredients.
- You can save bars to plan a night. Saved bars are stored in your browser.

It's a static site with no build step. Leaflet loads from cdnjs and the
[leaflet-rotate](https://github.com/Raruto/leaflet-rotate) plugin (GPL-3.0) from
jsDelivr. The basemap is drawn from embedded OpenStreetMap vector data, so
there's no tile server or API key. Cocktail photos are loaded from Edinburgh
Cocktail Week's own image CDN, not copied into this repo.

## Structure

```
public/
  index.html          the app
  data/venues.json    bars, cocktails, coordinates, Cocktail Village (generated)
  data/basemap.json   simplified streets/parks/water/coastline (generated)
scripts/
  update_venues.py    scrape the ECW pages + geocode → venues.json
  geocache.json       address → [lat, lng]; edit to correct a pin by hand
  hours_manual.json   opening hours researched from bars' own websites (wins over OpenStreetMap)
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

This uses only the Python standard library. Addresses already in
`scripts/geocache.json` aren't looked up again. New ones are geocoded with
postcodes.io and OpenStreetMap Nominatim, at about one per second. Known typos
in the source listings are fixed in `ADDRESS_FIXES` and `PREFER_NOMINATIM` at
the top of the script. Check any new bars on the map after a refresh.

The basemap rarely needs rebuilding:

```bash
python3 scripts/build_basemap.py
```

## Deploy

Import the repo in Vercel. `vercel.json` sets the output directory to `public`
with no framework and no build command.

## Support

If the map helped your night out, you can [buy me a coffee](https://buymeacoffee.com/cbchalmers).

## Credits

The bar and cocktail data comes from edinburghcocktailweek.co.uk. This is an
unofficial fan project and isn't affiliated with Edinburgh Cocktail Week.
Map data © OpenStreetMap contributors, ODbL.

## Licence

The code is released under the [MIT licence](LICENSE). The licence doesn't
cover the data files or photos: the bar and cocktail listings and the cocktail
photos belong to Edinburgh Cocktail Week, and `public/data/basemap.json` is derived from OpenStreetMap
under the [ODbL](https://www.openstreetmap.org/copyright).
