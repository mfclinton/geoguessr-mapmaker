# GeoGuessr Map Maker

Make GeoGuessr maps from data instead of clicking them together by hand.

## Install

```sh
uv tool install git+https://github.com/mfclinton/geoguessr-mapmaker   # CLI
uv add git+https://github.com/mfclinton/geoguessr-mapmaker            # library
```

## Logging in

GeoGuessr doesn't hand out API keys, so this logs in with your browser cookie.

Log in on geoguessr.com, open DevTools (F12), go to Application → Cookies (Storage → Cookies in Firefox) and copy the value of `_ncfa`. Put it in a `.env` file in the folder you'll run commands from:

```
GEOGUESSR_NCFA=your-cookie-here
```

If `ggmm whoami` prints your username, you're set.

The cookie works like your password, so don't commit it or paste it anywhere. Logging out of GeoGuessr invalidates it.

## Usage

Create a map from a GeoJSON file.

```sh
ggmm upload places.geojson --new "My map"
```

`upload` prints the map's URL, which you can use as `<id>` in the commands below.

Publish the map once it has at least 5 locations:

```sh
ggmm publish <id>
```

Publishing hasn't been tried on the live site yet (see [docs/api.md](docs/api.md)), so check the map on geoguessr.com afterwards.

Add more locations to the map. Without `--append`, the file replaces everything already in it.

```sh
ggmm upload more.geojson --map <id> --append
```

### Options for `upload`

| Option | What it does |
|---|---|
| `--five-k-m 50` | Only guesses within 50 m score 5,000 |
| `--mode regions --radius-m 300` | Start each round at a random spot within 300 m of a point, not exactly on it |
| `--check-coverage` | Remove areas with no Street View (has no effect on exact points) |
| `--publish` | Publish right after saving |
| `--dry-run` | Show what would happen without saving |

## Examples

- [Python](examples/python-api/): a simple Python example.
- [Google Timeline](examples/google-timeline/): a map of places you've been. It leaves out your home and anywhere you've slept 20+ nights.
- [OpenStreetMap](examples/osm-overpass/): every lighthouse, train station or castle in a country.

## API

GeoGuessr's API is private and undocumented, so this can break without warning. My notes on it are in [docs/api.md](docs/api.md).
