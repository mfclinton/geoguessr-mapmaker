# Examples

- [python-api](python-api/): a simple Python example
- [google-timeline](google-timeline/): a map of places you've been, from your Google Maps Timeline
- [osm-overpass](osm-overpass/): lighthouses, train stations, castles, or anything else that's tagged in OpenStreetMap

The last two are for specific use cases and just write a GeoJSON file. Uploading it is always the same command:

```sh
uv run ggmm upload your.geojson --new "Map name" --mode regions --check-coverage
```
