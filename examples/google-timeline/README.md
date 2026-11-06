# Google Timeline

Turns your Google Maps Timeline into a GeoGuessr map of places you've been.

## Getting your data

Follow Google's "Export your Timeline data" steps for your phone, then copy the file to your computer:

- [Android](https://support.google.com/maps/answer/6258979?hl=en&co=GENIE.Platform%3DAndroid)
- [iPhone](https://support.google.com/maps/answer/6258979?hl=en&co=GENIE.Platform%3DiOS)

If auto-delete is on in your Timeline settings, older history won't be in the export.

## Making the map

```sh
# Make a GeoJSON file of your timeline locations.
uv run python examples/google-timeline/timeline_to_geojson.py Timeline.json -o places.geojson

# Upload it to GeoGuessr.
uv run ggmm upload places.geojson --new "Places I've Been" --mode regions --check-coverage
```

## Parameters

`timeline_to_geojson.py`:

| Parameter | Default | What it does |
|---|---|---|
| `Timeline.json` | required | Your export, or a folder of exports. |
| `-o`, `--output` | `places.geojson` | Where to write the GeoJSON. |
| `--since` | - | Only use visits on or after this date (`YYYY-MM-DD`). |
| `--until` | - | Only use visits before this date. With `--since`, this makes a map of just one trip. |
| `--min-minutes` | 5 | Ignore visits shorter than this. Routes you drove are always ignored. |
| `--merge-m` | 250 | Merge visits within this many meters of each other into one place. |
| `--max-places` | no limit | Cap the number of places. |
| `--pick` | `spread` | How `--max-places` chooses: `spread` picks places that are spread out, `top` keeps the ones you spent the most time at, and `random` takes a random sample. |
| `--exclude LAT,LNG,RADIUS_M` | - | Leave out everything within `RADIUS_M` meters of a point. Repeat it for more than one. |
| `--keep-home` | off | Keep your home. By default, anything Google tagged as HOME is removed, plus anywhere you've slept `--residence-nights` or more nights. |
| `--residence-nights` | 20 | How many nights make a place count as a home. |
| `--home-radius-m` | 1000 | Also remove everything within this many meters of a home. |

`ggmm upload`:

| Parameter | Default | What it does |
|---|---|---|
| `places.geojson` | required | The file to upload. |
| `--new` | - | Name of the new map. It's saved as an unpublished draft. |
| `--mode` | `coordinates` for points | `regions` turns each place into a circle, and each round lands somewhere random inside one. `coordinates` uses the exact spots. |
| `--radius-m` | 400 | Circle radius in meters. |
| `--check-coverage` | off | Drop circles with no Street View. |
| `--five-k-m` | automatic | Only guesses within this many meters score 5,000, so being in the right city isn't enough. Minimum 25. |

Run `uv run ggmm upload --help` for the rest.
