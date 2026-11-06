"""OpenStreetMap features -> GeoJSON points, via the public Overpass API.

    uv run python examples/osm-overpass/overpass_to_geojson.py --tag man_made=lighthouse \
        --country IS -o lighthouses.geojson
    uv run ggmm upload lighthouses.geojson --new "Lighthouses of Iceland" --mode regions \
        --radius-m 300 --check-coverage

Any OSM tag works: railway=station, amenity=university, tourism=viewpoint, leisure=stadium,
shop=bakery, ...
Use --tag key=* for any value. Limit the area with --country (ISO 3166-1 code) or --bbox S,W,N,E.
"""

import json
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
BUSY_STATUSES = {429, 502, 503, 504}
BACKOFF_S = (10, 20, 40)


def build_query(
    key: str,
    value: str,
    *,
    country: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    limit: int | None = None,
) -> str:
    selector = f'["{key}"]' if value == "*" else f'["{key}"="{value}"]'
    if country:
        header = f'area["ISO3166-1"="{country.upper()}"][admin_level=2]->.a;'
        scope = "(area.a)"
    elif bbox:
        header = ""
        scope = f"({','.join(str(x) for x in bbox)})"
    else:
        raise ValueError("give --country or --bbox; worldwide queries are too heavy for Overpass")
    max_results = f" {limit}" if limit else ""
    return f"[out:json][timeout:180];{header}nwr{selector}{scope};out center{max_results};"


def elements_to_geojson(elements: list[dict[str, Any]]) -> dict[str, Any]:
    """Nodes use their position; ways and relations use the center Overpass computes."""
    features = []
    for el in elements:
        pos = el if "lat" in el else el.get("center")
        if not pos:
            continue
        tags = el.get("tags") or {}
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [pos["lon"], pos["lat"]]},
                "properties": {"name": tags.get("name"), "osm": f"{el['type']}/{el['id']}"},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def fetch(endpoint: str, query: str) -> httpx.Response:
    """POST the query, backing off when the shared server is busy (it asks clients to do this)."""

    def post() -> httpx.Response:
        headers = {"User-Agent": "geoguessr-mapmaker-example"}
        return httpx.post(endpoint, data={"data": query}, headers=headers, timeout=200)

    for wait in BACKOFF_S:
        response = post()
        if response.status_code not in BUSY_STATUSES:
            return response
        print(f"Overpass busy ({response.status_code}), retrying in {wait}s", file=sys.stderr)
        time.sleep(wait)
    return post()


def _bbox(s: str) -> tuple[float, float, float, float]:
    try:
        south, west, north, east = (float(x) for x in s.split(","))
    except ValueError:
        raise typer.BadParameter(f"expected S,W,N,E, got {s!r}", param_hint="'--bbox'") from None
    return south, west, north, east


app = typer.Typer(add_completion=False)


@app.command(help=__doc__)
def main(
    tag: Annotated[str, typer.Option(help="OSM tag as key=value (or key=*)")],
    country: Annotated[str | None, typer.Option(help="ISO 3166-1 alpha-2 code, e.g. NO")] = None,
    bbox: Annotated[
        str | None, typer.Option(metavar="S,W,N,E", help="south,west,north,east")
    ] = None,
    limit: Annotated[int | None, typer.Option(min=1, help="max features")] = None,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("osm.geojson"),
    endpoint: Annotated[
        str, typer.Option(help="Overpass server (any public instance works)")
    ] = OVERPASS_URL,
) -> None:
    if (country is None) == (bbox is None):
        raise typer.BadParameter("give one of them", param_hint="'--country' / '--bbox'")
    key, _, value = tag.partition("=")
    query = build_query(
        key, value or "*", country=country, bbox=_bbox(bbox) if bbox else None, limit=limit
    )
    response = fetch(endpoint, query)
    if response.status_code != 200:
        print(f"Overpass error {response.status_code}: {response.text[:300]}", file=sys.stderr)
        raise typer.Exit(1)
    collection = elements_to_geojson(response.json().get("elements", []))
    output.write_text(json.dumps(collection), encoding="utf-8")
    print(f"Wrote {len(collection['features'])} features to {output}")


if __name__ == "__main__":
    app()
