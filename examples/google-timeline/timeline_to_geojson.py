"""Google Maps Timeline -> GeoJSON points of the places you've been, ready for `ggmm upload`.

    uv run python examples/google-timeline/timeline_to_geojson.py Timeline.json -o places.geojson
    uv run ggmm upload places.geojson --new "Places I've Been" --mode regions --check-coverage

Inputs (a directory is scanned recursively for *.json):
    Android Timeline export     Timeline.json             {"semanticSegments": [...]}
    iOS Timeline export         location-history.json     [{"visit": ...}, ...]
    Legacy Takeout              Semantic Location History/*.json
    Google Photos Takeout       *.json photo sidecars with geoData

Only places you stopped at are used. Travel paths are ignored, because points you drove past make
forgettable rounds.

Privacy: homes are removed by default. That covers anything tagged HOME, plus anywhere slept at
--residence-nights times or more, because Google's HOME tag is often stale or missing. Check the
printed list of top places before you publish, because published maps are public.
"""

import json
import math
import random
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from geoguessr_mapmaker.geo import distance_m

HOME_KINDS = {"HOME", "INFERRED_HOME"}
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Visit:
    lat: float
    lng: float
    start: datetime | None = None
    end: datetime | None = None
    kind: str = ""  # HOME, WORK, SEARCHED_ADDRESS, PHOTO, ...

    @property
    def minutes(self) -> float:
        if self.start and self.end:
            return max((self.end - self.start).total_seconds() / 60, 0.0)
        return 0.0


def _e7(value: int, max_degrees: int) -> float:
    """Degrees from Takeout's degrees * 10^7.

    Old exports overflowed int32, so negatives arrive as huge positives.
    """
    return (value - 2**32 if value > max_degrees * 10**7 else value) / 10**7


def parse_latlng(value: Any) -> tuple[float, float] | None:
    """'40.71°, -74.00°', 'geo:40.71,-74.00', {'latLng': ...} or {'latitudeE7': ...}."""
    if isinstance(value, dict):
        if "latitudeE7" in value and "longitudeE7" in value:
            return _e7(value["latitudeE7"], 90), _e7(value["longitudeE7"], 180)
        for key in ("latLng", "LatLng", "point"):
            if key in value:
                return parse_latlng(value[key])
        return None
    if isinstance(value, str):
        nums = _NUM.findall(value)
        if len(nums) >= 2:
            lat, lng = float(nums[0]), float(nums[1])
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return lat, lng
    return None


def parse_time(value: Any, unit_ms: bool = True) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float) or (isinstance(value, str) and value.isdigit()):
        return datetime.fromtimestamp(int(value) / (1000 if unit_ms else 1), tz=UTC)
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _kind(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "_").removeprefix("TYPE_")


def _segment_visit(seg: dict[str, Any]) -> Visit | None:
    visit = seg.get("visit")
    if not isinstance(visit, dict):
        return None
    cand = visit.get("topCandidate") or {}
    latlng = parse_latlng(cand.get("placeLocation"))
    if latlng is None:
        return None
    return Visit(
        *latlng,
        parse_time(seg.get("startTime")),
        parse_time(seg.get("endTime")),
        _kind(cand.get("semanticType")),
    )


def _place_visit(pv: dict[str, Any]) -> Visit | None:
    loc = pv.get("location") or {}
    latlng = parse_latlng(loc)
    if latlng is None:
        return None
    dur = pv.get("duration") or {}
    return Visit(
        *latlng,
        parse_time(dur.get("startTimestamp") or dur.get("startTimestampMs")),
        parse_time(dur.get("endTimestamp") or dur.get("endTimestampMs")),
        _kind(loc.get("semanticType")),
    )


def _photo(meta: dict[str, Any]) -> Visit | None:
    """No end time, so --min-minutes never drops photos."""
    for key in ("geoData", "geoDataExif"):
        geo_data = meta.get(key) or {}
        lat, lng = geo_data.get("latitude"), geo_data.get("longitude")
        if isinstance(lat, int | float) and isinstance(lng, int | float) and (lat, lng) != (0, 0):
            taken = parse_time((meta.get("photoTakenTime") or {}).get("timestamp"), unit_ms=False)
            return Visit(float(lat), float(lng), taken, None, "PHOTO")
    return None


def visits_from(data: Any) -> Iterator[Visit]:
    if isinstance(data, dict) and "semanticSegments" in data:  # Android
        segments = data["semanticSegments"]
    elif isinstance(data, list):  # iOS
        segments = data
    elif isinstance(data, dict) and "timelineObjects" in data:  # legacy semantic history
        for obj in data["timelineObjects"]:
            if (
                isinstance(obj, dict)
                and obj.get("placeVisit")
                and (visit := _place_visit(obj["placeVisit"]))
            ):
                yield visit
        return
    elif isinstance(data, dict) and ("geoData" in data or "geoDataExif" in data):  # Google Photos
        if visit := _photo(data):
            yield visit
        return
    else:
        return
    for seg in segments:
        if isinstance(seg, dict) and (visit := _segment_visit(seg)):
            yield visit


def load_visits(path: Path) -> list[Visit]:
    files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    visits: list[Visit] = []
    for f in files:
        try:
            visits.extend(visits_from(json.loads(f.read_text(encoding="utf-8-sig"))))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            print(
                f"warning: couldn't read {f}, so any home in it won't be removed: {e}",
                file=sys.stderr,
            )
    return visits


def nights(visit: Visit) -> int:
    """How many 3 a.m.s (in the visit's own UTC offset) the visit spans."""
    if not (visit.start and visit.end) or visit.end <= visit.start:
        return 0
    t = visit.start.replace(hour=3, minute=0, second=0, microsecond=0)
    if t < visit.start:
        t += timedelta(days=1)
    return 0 if t > visit.end else 1 + int((visit.end - t) / timedelta(days=1))


@dataclass
class Place:
    lat: float
    lng: float
    visits: int = 0
    minutes: float = 0.0
    nights: int = 0
    kinds: set[str] = field(default_factory=set)
    first: datetime | None = None
    last: datetime | None = None

    def add(self, visit: Visit) -> None:
        self.visits += 1
        self.minutes += visit.minutes
        self.nights += nights(visit)
        if visit.kind and visit.kind != "UNKNOWN":
            self.kinds.add(visit.kind)
        if visit.start:
            self.first = min(self.first or visit.start, visit.start)
            self.last = max(self.last or visit.start, visit.start)


def cluster(visits: list[Visit], merge_m: float) -> list[Place]:
    """Each visit joins the nearest place within merge_m, else founds one.

    Longest visits anchor places.
    """
    # Places are bucketed into latitude bands merge_m tall, so a visit only compares against its
    # band and the two beside it. 110,574 m is the shortest degree of latitude, so bands are never
    # too short.
    band_degrees = merge_m / 110_574
    bands: dict[int, list[Place]] = {}
    places: list[Place] = []

    for visit in sorted(visits, key=lambda visit: -visit.minutes):
        band = math.floor(visit.lat / band_degrees)
        best, best_d = None, merge_m
        for nearby_band in (band - 1, band, band + 1):
            for p in bands.get(nearby_band, ()):
                d = distance_m(visit.lat, visit.lng, p.lat, p.lng)
                if d <= best_d:
                    best, best_d = p, d
        if best is None:
            best = Place(visit.lat, visit.lng)
            places.append(best)
            bands.setdefault(band, []).append(best)
        best.add(visit)
    return places


def private_zones(
    places: list[Place], radius_m: float, residence_nights: int
) -> list[tuple[float, float, float]]:
    return [
        (p.lat, p.lng, radius_m)
        for p in places
        if p.kinds & HOME_KINDS or (residence_nights and p.nights >= residence_nights)
    ]


def drop_near(places: list[Place], zones: list[tuple[float, float, float]]) -> list[Place]:
    return [
        p
        for p in places
        if all(
            distance_m(p.lat, p.lng, zone_lat, zone_lng) > radius
            for zone_lat, zone_lng, radius in zones
        )
    ]


def pick(places: list[Place], k: int | None, strategy: str) -> list[Place]:
    """spread = farthest-point sampling (geographic variety), top = most time, random."""
    if not k or k >= len(places):
        return places
    if strategy == "top":
        return sorted(places, key=lambda p: -p.minutes)[:k]
    if strategy == "random":
        return random.Random(0).sample(places, k)
    chosen = [max(places, key=lambda p: p.minutes)]
    dist = [distance_m(p.lat, p.lng, chosen[0].lat, chosen[0].lng) for p in places]
    while len(chosen) < k:
        farthest = places[max(range(len(places)), key=dist.__getitem__)]
        chosen.append(farthest)
        dist = [
            min(d, distance_m(p.lat, p.lng, farthest.lat, farthest.lng))
            for d, p in zip(dist, places, strict=True)
        ]
    return chosen


def to_geojson(places: list[Place]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(p.lng, 6), round(p.lat, 6)]},
                "properties": {
                    "visits": p.visits,
                    "hours": round(p.minutes / 60, 1),
                    "nights": p.nights,
                    "kinds": sorted(p.kinds),
                    "first": p.first.date().isoformat() if p.first else None,
                    "last": p.last.date().isoformat() if p.last else None,
                },
            }
            for p in places
        ],
    }


def _zone(s: str) -> tuple[float, float, float]:
    try:
        lat, lng, radius = (float(x) for x in s.split(","))
    except ValueError:
        raise typer.BadParameter(
            f"expected LAT,LNG,RADIUS_M, got {s!r}", param_hint="'--exclude'"
        ) from None
    return lat, lng, radius


app = typer.Typer(add_completion=False)


@app.command(help=__doc__)
def main(
    export: Annotated[
        Path,
        typer.Argument(metavar="EXPORT", help="Timeline / Takeout JSON file, or a folder of them"),
    ],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("places.geojson"),
    since: Annotated[
        datetime | None,
        typer.Option(
            formats=["%Y-%m-%d"], metavar="YYYY-MM-DD", help="only visits on or after this date"
        ),
    ] = None,
    until: Annotated[
        datetime | None,
        typer.Option(
            formats=["%Y-%m-%d"], metavar="YYYY-MM-DD", help="only visits before this date"
        ),
    ] = None,
    min_minutes: Annotated[float, typer.Option(help="ignore shorter visits")] = 5,
    merge_m: Annotated[
        float, typer.Option(min=1, help="visits closer than this are one place")
    ] = 250,
    max_places: Annotated[int | None, typer.Option(min=1, help="cap the number of places")] = None,
    pick_by: Annotated[
        Literal["spread", "top", "random"], typer.Option("--pick", help="how to cap")
    ] = "spread",
    exclude: Annotated[
        list[str] | None, typer.Option(metavar="LAT,LNG,RADIUS_M", help="also remove this area")
    ] = None,
    keep_home: Annotated[
        bool, typer.Option("--keep-home", help="don't remove homes and residences")
    ] = False,
    home_radius_m: Annotated[float, typer.Option(help="radius removed around homes")] = 1000,
    residence_nights: Annotated[int, typer.Option(help="slept here this often = a residence")] = 20,
) -> None:
    zones = [_zone(z) for z in exclude or []]
    if since:
        since = since.replace(tzinfo=UTC)
    if until:
        until = until.replace(tzinfo=UTC)
    visits = [
        visit
        for visit in load_visits(export)
        if not (since and visit.start and visit.start < since)
        and not (until and visit.start and visit.start >= until)
        and not (visit.start and visit.end and visit.minutes < min_minutes)
    ]
    if not visits:
        print(f"No visits found in {export}.", file=sys.stderr)
        raise typer.Exit(1)
    places = cluster(visits, merge_m)
    if not keep_home:
        homes = private_zones(places, home_radius_m, residence_nights)
        if not homes:
            print(
                "warning: found no home or residence to remove (photo-only data?); use --exclude",
                file=sys.stderr,
            )
        zones += homes
    kept = pick(drop_near(places, zones), max_places, pick_by)

    output.write_text(json.dumps(to_geojson(kept)), encoding="utf-8")
    print(
        f"{len(visits)} visits -> {len(places)} places; "
        f"removed {len(places) - len(kept)}; wrote {len(kept)}"
    )
    print("Top remaining places. Make sure none of these are private:")
    for p in sorted(kept, key=lambda p: (-p.minutes, -p.visits))[:5]:
        print(
            f"  {p.lat:.3f},{p.lng:.3f}  {p.visits:>4} visits "
            f"{p.minutes / 60:7.1f} h {p.nights:>3} nights"
        )
    print(f'Next: ggmm upload {output} --new "Places I\'ve Been" --mode regions --check-coverage')


if __name__ == "__main__":
    app()
