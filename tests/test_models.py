import math

import pytest

from geoguessr_mapmaker import Draft, Location, Region
from geoguessr_mapmaker.geo import five_k_to_max_error, max_error_to_five_k
from geoguessr_mapmaker.models import map_id

# Shapes as returned by the live API
LIVE_REGIONS_DRAFT = {
    "slug": "0a1b2c3d4e5f60718293a4b5",
    "name": "aa",
    "description": "",
    "highlighted": False,
    "avatar": {
        "background": "sunrise",
        "decoration": "tractor",
        "ground": "beige",
        "landscape": "houses",
    },
    "mode": "regions",
    "regions": [
        {
            "coordinates": [
                {"lat": 17.82, "lng": 22.78},
                {"lat": 8.07, "lng": 15.05},
                {"lat": 6.32, "lng": 30.34},
            ]
        }
    ],
    "coordinates": None,
    "created": "2026-11-04T17:11:32.8360000Z",
    "updated": "2026-11-04T17:12:18.3010000Z",
    "tags": [],
    "maxErrorDistance": 17499125,
    "hasCustomErrorDistance": False,
    "image": None,
    "collaborators": None,
    "version": 4,
}
LIVE_LOCATION = {
    "heading": 90,
    "pitch": 0,
    "zoom": 0,
    "panoId": None,
    "countryCode": None,
    "stateCode": None,
    "cityCode": None,
    "lat": 40.758,
    "lng": -73.9855,
}


def test_region_drops_its_closing_point():
    region = Region(points=[(0, 0), (0, 1), (1, 1), (0, 0)])
    assert region.points == [(0, 0), (0, 1), (1, 1)]


def test_region_needs_three_distinct_points():
    with pytest.raises(ValueError, match="at least 3"):
        Region(points=[(0, 0), (1, 1), (0, 0)])


def test_circles_near_the_poles_keep_their_latitude():
    assert all(lat < -85.9 for lat, _ in Region.circle(-86, 10, 400).points)


def test_location_round_trip_from_live_shape():
    loc = Location.model_validate(LIVE_LOCATION)
    assert (loc.lat, loc.lng, loc.heading, loc.pano_id) == (40.758, -73.9855, 90, None)
    assert loc.model_dump() == {
        "lat": 40.758,
        "lng": -73.9855,
        "heading": 90,
        "pitch": 0,
        "zoom": 0,
        "panoId": None,
    }


def test_draft_from_live_shape_and_save_body():
    draft = Draft.model_validate(LIVE_REGIONS_DRAFT)
    assert (draft.id, draft.mode, draft.size, draft.version, draft.locations) == (
        "0a1b2c3d4e5f60718293a4b5",
        "regions",
        1,
        4,
        [],
    )
    body = draft.model_dump()
    assert body["version"] == 4
    assert body["regions"][0]["coordinates"][0] == {"lat": 17.82, "lng": 22.78}
    assert set(body) == {
        "name",
        "description",
        "avatar",
        "regions",
        "customCoordinates",
        "tags",
        "maxErrorDistance",
        "hasCustomErrorDistance",
        "version",
    }


def test_draft_skips_regions_that_are_not_polygons():
    a, b = {"lat": 0, "lng": 0}, {"lat": 1, "lng": 1}
    regions = [*LIVE_REGIONS_DRAFT["regions"], {"coordinates": [a, b, a]}, {"coordinates": [a]}]
    assert Draft.model_validate({**LIVE_REGIONS_DRAFT, "regions": regions}).size == 1


def test_new_draft_sends_web_client_default_scoring():
    draft = Draft.model_validate({**LIVE_REGIONS_DRAFT, "maxErrorDistance": 0, "version": 0})
    assert draft.model_dump()["maxErrorDistance"] == 17_499_125


def test_five_k_distance_can_be_set_and_cleared():
    draft = Draft(id="x", name="x", mode="regions")
    assert draft.five_k_distance_m is None
    draft.set_five_k_distance(25)
    assert round(draft.five_k_distance_m) == 25
    draft.set_five_k_distance(None)
    assert draft.five_k_distance_m is None


def test_five_k_distance_has_a_minimum():
    with pytest.raises(ValueError, match="minimum"):
        Draft(id="x", name="x", mode="regions").set_five_k_distance(10)


def score(distance_m: float, max_error_distance: float) -> int:
    """GeoGuessr's points for a guess distance_m away."""
    return round(5000 * math.exp(-10 * distance_m / max_error_distance))


def test_scoring_curve_matches_five_k_formula():
    max_error = five_k_to_max_error(100)
    assert score(0, max_error) == 5000
    assert score(100, max_error) == 5000
    assert score(105, max_error) == 4999
    assert round(max_error_to_five_k(max_error)) == 100


@pytest.mark.parametrize(
    "value",
    [
        "5f4e3d2c1b0a99887766aa55",
        "https://www.geoguessr.com/map-maker/5f4e3d2c1b0a99887766aa55",
        "https://www.geoguessr.com/maps/5f4e3d2c1b0a99887766aa55/",
        "https://www.geoguessr.com/map-maker/5f4e3d2c1b0a99887766aa55?tab=settings",
        "https://www.geoguessr.com/map-maker/5f4e3d2c1b0a99887766aa55/?tab=settings",
        "https://www.geoguessr.com/map-maker/5f4e3d2c1b0a99887766aa55?next=/other",
        "https://www.geoguessr.com/map-maker/5f4e3d2c1b0a99887766aa55#a/b",
    ],
)
def test_map_id_accepts_urls(value):
    assert map_id(value) == "5f4e3d2c1b0a99887766aa55"


@pytest.mark.parametrize("value", ["", "  ", "https://www.geoguessr.com/"])
def test_map_id_rejects_values_without_an_id(value):
    with pytest.raises(ValueError, match="no map id"):
        map_id(value)
