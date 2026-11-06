"""Convert between GeoJSON (or GeoGuessr / map-making.app JSON) and map regions and locations.

GeoJSON is the interchange format: draw shapes on geojson.io, export them from QGIS, query
OpenStreetMap, or generate them from your own data, then upload.

Reading:
    Polygon, MultiPolygon       -> Region per outer ring (holes are dropped, with a warning)
    Point, MultiPoint           -> Location; properties heading, pitch, zoom, panoId and radius_m
                                   are honored (radius_m is used when points become circles)
    LineString etc.             -> skipped, with a warning
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .geo import CIRCLE_VERTICES
from .models import Draft, Location, Region


@dataclass
class Shapes:
    regions: list[Region] = field(default_factory=list)
    # Each location with its own radius_m property, if it has one.
    points: list[tuple[Location, float | None]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def locations(self) -> list[Location]:
        return [location for location, _ in self.points]

    def to_regions(self, radius_m: float, vertices: int = CIRCLE_VERTICES) -> list[Region]:
        """Polygons as-is, and points as circles of radius_m unless a point sets its own."""
        circles = [
            Region.circle(location.lat, location.lng, own_radius or radius_m, vertices)
            for location, own_radius in self.points
        ]
        return self.regions + circles


def load(path: str | Path) -> Shapes:
    """Read a GeoJSON file, or a GeoGuessr / map-making.app location JSON export."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and isinstance(data.get("type"), str):
        return from_geojson(data)
    return from_geoguessr_json(data)


def from_geojson(obj: dict[str, Any]) -> Shapes:
    shapes = Shapes()
    _walk(obj, {}, shapes)
    return shapes


def from_geoguessr_json(obj: Any) -> Shapes:
    """``[{lat, lng, ...}]``, ``{"customCoordinates": [...]}`` or a draft/map dict."""
    shapes = Shapes()
    if isinstance(obj, dict):
        for region in _members(obj, "regions"):
            try:
                shapes.regions.append(Region.model_validate(region))
            except ValidationError as e:
                shapes.warnings.append(f"skipped region: {_reason(e)}")
        obj = obj.get("customCoordinates") or obj.get("coordinates") or []
    if not isinstance(obj, list):
        raise ValueError("not GeoJSON and not a GeoGuessr location list")
    for item in obj:
        try:
            shapes.points.append((Location.model_validate(item), None))
        except ValidationError as e:
            shapes.warnings.append(f"skipped location: {_reason(e)}")
    return shapes


def _members(obj: dict[str, Any], key: str) -> list[Any]:
    members = obj.get(key) or []
    if not isinstance(members, list):
        raise ValueError(f"{key!r} must be a list")
    return members


def _walk(obj: Any, props: dict[str, Any], shapes: Shapes) -> None:
    if not isinstance(obj, dict):
        shapes.warnings.append(f"skipped malformed GeoJSON: {str(obj)[:80]}")
        return
    kind = obj.get("type")
    if kind == "FeatureCollection":
        for feature in _members(obj, "features"):
            _walk(feature, {}, shapes)
    elif kind == "Feature":
        if obj.get("geometry"):
            _walk(obj["geometry"], obj.get("properties") or {}, shapes)
    elif kind == "GeometryCollection":
        for geometry in _members(obj, "geometries"):
            _walk(geometry, props, shapes)
    elif kind in ("Polygon", "MultiPolygon", "Point", "MultiPoint"):
        _add_geometry(kind, obj.get("coordinates"), props, shapes)
    else:
        shapes.warnings.append(f"skipped unsupported geometry: {kind}")


def _add_geometry(kind: str, coordinates: Any, props: dict[str, Any], shapes: Shapes) -> None:
    try:
        if kind == "Polygon":
            _add_polygon(coordinates, shapes)
        elif kind == "MultiPolygon":
            for rings in coordinates:
                _add_polygon(rings, shapes)
        elif kind == "Point":
            _add_point(coordinates, props, shapes)
        else:
            for position in coordinates:
                _add_point(position, props, shapes)
    except (IndexError, KeyError, TypeError):
        shapes.warnings.append(f"skipped {kind}: malformed coordinates")


def _add_polygon(rings: list[list[list[float]]], shapes: Shapes) -> None:
    if len(rings) > 1:
        shapes.warnings.append("polygon holes ignored (GeoGuessr regions can't have holes)")
    try:
        shapes.regions.append(Region(points=[(p[1], p[0]) for p in rings[0]]))
    except ValidationError as e:
        shapes.warnings.append(f"skipped polygon: {_reason(e)}")


def _add_point(position: list[float], props: dict[str, Any], shapes: Shapes) -> None:
    # properties like heading and panoId (or pano_id) carry over; anything else is ignored
    try:
        location = Location.model_validate({**props, "lat": position[1], "lng": position[0]})
    except ValidationError as e:
        shapes.warnings.append(f"skipped point: {_reason(e)}")
        return
    radius = props.get("radius_m")
    shapes.points.append(
        (location, float(radius) if isinstance(radius, int | float) and radius > 0 else None)
    )


def _reason(e: ValidationError) -> str:
    """The first problem, e.g. "heading: Input should be a valid number"."""
    err = e.errors()[0]
    msg = str(err["msg"]).removeprefix("Value error, ")
    return f"{'.'.join(map(str, err['loc']))}: {msg}" if err["loc"] else msg


def to_geojson(
    regions: Sequence[Region] = (), locations: Sequence[Location] = ()
) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for region in regions:
        ring = [[lng, lat] for lat, lng in region.points]
        features.append(
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [ring + ring[:1]]},
            }
        )
    for loc in locations:
        props = {k: v for k, v in loc.model_dump(exclude={"lat", "lng"}).items() if v}
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {"type": "Point", "coordinates": [loc.lng, loc.lat]},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def draft_to_geojson(draft: Draft) -> dict[str, Any]:
    collection = to_geojson(draft.regions, draft.locations)
    # A foreign member, which GeoJSON viewers ignore.
    collection["properties"] = {"name": draft.name, "mode": draft.mode, "id": draft.id}
    return collection


def draft_to_geoguessr_json(draft: Draft) -> dict[str, Any]:
    """The location-list JSON the map maker's Import button and map-making.app understand."""
    out: dict[str, Any] = {
        "name": draft.name,
        "customCoordinates": [loc.model_dump() for loc in draft.locations],
    }
    if draft.regions:
        out["regions"] = [region.model_dump(exclude={"id"}) for region in draft.regions]
    return out
