"""Data models for maps, drafts, regions and locations.

GeoGuessr maps come in two kinds, fixed when the map is created:

* ``regions`` maps hold polygons; each round GeoGuessr picks random Street View inside one.
* ``coordinates`` maps hold exact locations (optionally a specific panorama and camera angle).

The models are pydantic models: ``Draft.model_validate(api_json)`` reads the API's JSON and
``draft.model_dump()`` writes it. Field names are snake_case in Python and camelCase in the API.

All coordinates in this library are ``(lat, lng)``, the API's order. GeoJSON's ``[lng, lat]`` is
converted only in :mod:`geoguessr_mapmaker.geojson`.
"""

from pathlib import PurePosixPath
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from .geo import (
    CIRCLE_VERTICES,
    DEFAULT_FIVE_K_M,
    MIN_FIVE_K_M,
    circle_ring,
    five_k_to_max_error,
    max_error_to_five_k,
)

Mode = Literal["regions", "coordinates"]
MIN_LOCATIONS_TO_PUBLISH = 5
# Only used if the server returns no avatar. Values observed in a real map-maker save.
DEFAULT_AVATAR = {
    "background": "sunrise",
    "decoration": "tractor",
    "ground": "beige",
    "landscape": "houses",
}

BASE_URL = "https://www.geoguessr.com"


def map_id(value: str) -> str:
    """Accept a bare id or any geoguessr.com map / map-maker URL."""
    id_ = PurePosixPath(urlsplit(value.strip()).path).name
    if not id_:
        raise ValueError(f"no map id in {value!r}")
    return id_


def map_maker_url(id_: str) -> str:
    return f"{BASE_URL}/map-maker/{id_}"


def map_url(id_: str) -> str:
    return f"{BASE_URL}/maps/{id_}"


class _ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, serialize_by_alias=True
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_empty(cls, data: Any) -> Any:
        """The API sends null or "" for unset fields, which should mean "use the default"."""
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None and v != ""}
        return data


class Profile(_ApiModel):
    """The logged-in user, from ``Client.me``."""

    id: str
    nick: str


class Location(_ApiModel):
    """An exact spot. Without ``pano_id`` the game uses the nearest panorama."""

    lat: float
    lng: float
    heading: float = 0.0
    pitch: float = 0.0
    zoom: float = 0.0
    pano_id: str | None = None
    country_code: str | None = Field(default=None, exclude_if=lambda v: v is None)
    state_code: str | None = Field(default=None, exclude_if=lambda v: v is None)


class Region(_ApiModel):
    """A polygon as an open ring of ``(lat, lng)`` points. Holes are not supported by GeoGuessr."""

    points: list[tuple[float, float]] = Field(
        validation_alias="coordinates", serialization_alias="coordinates"
    )
    id: float | None = Field(default=None, exclude_if=lambda v: v is None)

    @field_validator("points", mode="before")
    @classmethod
    def _from_api_points(cls, points: Any) -> Any:
        if not isinstance(points, list):
            return points  # let pydantic report it
        return [(p.get("lat"), p.get("lng")) if isinstance(p, dict) else p for p in points]

    @field_validator("points")
    @classmethod
    def _open_ring(cls, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(points) > 1 and points[0] == points[-1]:
            points = points[:-1]
        if len(points) < 3:
            raise ValueError("a region needs at least 3 distinct points")
        return points

    @field_serializer("points")
    def _to_api_points(self, points: list[tuple[float, float]]) -> list[dict[str, float]]:
        return [{"lat": lat, "lng": lng} for lat, lng in points]

    @classmethod
    def circle(
        cls, lat: float, lng: float, radius_m: float, vertices: int = CIRCLE_VERTICES
    ) -> Self:
        """A circle-shaped region: 'somewhere within radius_m of here'."""
        return cls(points=circle_ring(lat, lng, radius_m, vertices))


class Draft(_ApiModel):
    """The editable copy of a map, as returned by ``Client.get_draft``.

    Edit ``regions`` / ``locations`` (whichever matches ``mode``) and pass it to
    ``Client.save_draft``.
    ``model_dump()`` gives the save body, so the read-only fields are left out of it.
    """

    id: str = Field(default="", validation_alias=AliasChoices("slug", "id"), exclude=True)
    name: str = ""
    mode: Mode = Field(exclude=True)
    description: str = ""
    regions: list[Region] = []
    # read back as "coordinates", written as "customCoordinates"
    locations: list[Location] = Field(
        default=[],
        validation_alias=AliasChoices("coordinates", "customCoordinates"),
        serialization_alias="customCoordinates",
    )
    tags: list[str] = []
    avatar: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_AVATAR))
    max_error_distance: int = 0
    has_custom_error_distance: bool = False
    version: int = 0
    created: str | None = Field(default=None, exclude=True)
    updated: str | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _from_api_quirks(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        regions: list[Region] = []
        for region in data.get("regions") or []:
            try:
                regions.append(Region.model_validate(region))
            except ValidationError:
                # The API can return regions that aren't polygons. Skip them, not the whole draft.
                continue
        return {**data, "regions": regions}

    @field_serializer("max_error_distance")
    def _default_scoring(self, max_error: int) -> int:
        if not max_error and not self.has_custom_error_distance:
            return five_k_to_max_error(DEFAULT_FIVE_K_M)  # match what the web client sends
        return max_error

    @property
    def size(self) -> int:
        """Number of regions or locations, depending on mode."""
        return len(self.regions) if self.mode == "regions" else len(self.locations)

    @property
    def edit_url(self) -> str:
        return map_maker_url(self.id)

    @property
    def five_k_distance_m(self) -> float | None:
        """Custom scoring distance, or None if GeoGuessr scales scoring to the map automatically."""
        if not self.has_custom_error_distance or not self.max_error_distance:
            return None
        return max_error_to_five_k(self.max_error_distance)

    def set_five_k_distance(self, meters: float | None) -> None:
        """Custom scoring: guesses within ``meters`` score 5,000. ``None`` restores automatic."""
        if meters is None:
            self.has_custom_error_distance = False
            self.max_error_distance = 0
            return
        if meters < MIN_FIVE_K_M:
            raise ValueError(f"the map maker's minimum 5K distance is {MIN_FIVE_K_M} m")
        self.max_error_distance = five_k_to_max_error(meters)
        self.has_custom_error_distance = True


class MapInfo(_ApiModel):
    """One entry from ``Client.list_maps``."""

    model_config = ConfigDict(coerce_numbers_to_str=True)

    id: str = Field(default="", validation_alias=AliasChoices("id", "slug"))
    name: str = ""
    published: bool = False
    coordinate_count: str = "0"  # a display string: "113", "1000+", "10K+"
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def url(self) -> str:
        return map_url(self.id)
