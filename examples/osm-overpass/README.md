# OpenStreetMap

This uses the Overpass API to find everything in a country (or a bounding box) with a given OpenStreetMap tag, and writes the results out as GeoJSON points.

```sh
uv run python examples/osm-overpass/overpass_to_geojson.py --tag man_made=lighthouse --country IS -o lighthouses.geojson
uv run ggmm upload lighthouses.geojson --new "Lighthouses of Iceland" --mode regions --radius-m 300 --check-coverage
```

Street View cars don't usually drive right up to a lighthouse, so this uses a 300 m circle around each one instead of the exact point. `--check-coverage` then drops the ones with nothing nearby.

Some other tags to try: `railway=station`, `amenity=university`, `tourism=viewpoint`, `leisure=stadium`, `historic=castle`, `amenity=ferry_terminal`. Use `key=*` to match any value.

For a city, use `--bbox S,W,N,E` instead of `--country`.
