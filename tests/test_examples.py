"""Tests for the example scripts, run offline."""

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from geoguessr_mapmaker import geojson
from geoguessr_mapmaker.geo import WGS84

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def load_example(relpath: str):
    name = "example_" + relpath.replace("/", "_").replace("-", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # @dataclass needs the module registered before it runs
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tl():
    return load_example("google-timeline/timeline_to_geojson.py")


@pytest.fixture(scope="module")
def osm():
    return load_example("osm-overpass/overpass_to_geojson.py")


ANDROID = {
    "semanticSegments": [
        {
            "startTime": "2024-03-01T08:00:00.000-05:00",
            "endTime": "2024-03-01T09:30:00.000-05:00",
            "visit": {
                "topCandidate": {
                    "semanticType": "HOME",
                    "placeLocation": {"latLng": "40.7128°, -74.0060°"},
                }
            },
        },
        {"startTime": "2024-03-01T09:30:00Z", "timelinePath": [{"point": "40.72°, -74.01°"}]},
        {
            "startTime": "2024-03-01T10:00:00Z",
            "endTime": "2024-03-01T11:00:00Z",
            "visit": {
                "topCandidate": {
                    "semanticType": "UNKNOWN",
                    "placeLocation": {"latLng": "40.7580°, -73.9855°"},
                }
            },
        },
    ]
}
IOS = [
    {
        "startTime": "2024-05-01T12:00:00.000-07:00",
        "endTime": "2024-05-01T13:00:00.000-07:00",
        "visit": {
            "topCandidate": {"semanticType": "Work", "placeLocation": "geo:37.774900,-122.419400"}
        },
    },
    {"startTime": "2024-05-01T13:00:00Z", "activity": {"start": "geo:37.7749,-122.4194"}},
]
LEGACY = {
    "timelineObjects": [
        {
            "placeVisit": {
                "location": {
                    "latitudeE7": 515007000,
                    "longitudeE7": -1246000,
                    "semanticType": "TYPE_SEARCHED_ADDRESS",
                },
                "duration": {
                    "startTimestamp": "2019-07-01T10:00:00Z",
                    "endTimestamp": "2019-07-01T10:45:00Z",
                },
            }
        }
    ]
}
PHOTO = {
    "title": "IMG_1.jpg",
    "photoTakenTime": {"timestamp": "1600000000"},
    "geoData": {"latitude": 0.0, "longitude": 0.0},
    "geoDataExif": {"latitude": 64.1466, "longitude": -21.9426},
}


@pytest.mark.parametrize(
    ("export", "expected"),
    [
        (ANDROID, [(40.7128, -74.006, "HOME", 90), (40.758, -73.9855, "UNKNOWN", 60)]),
        (IOS, [(37.7749, -122.4194, "WORK", 60)]),
        (LEGACY, [(51.5007, -0.1246, "SEARCHED_ADDRESS", 45)]),
        (PHOTO, [(64.1466, -21.9426, "PHOTO", 0)]),
    ],
    ids=["android", "ios", "legacy", "photo"],
)
def test_timeline_formats(tl, export, expected):
    assert [
        (visit.lat, visit.lng, visit.kind, visit.minutes) for visit in tl.visits_from(export)
    ] == expected


def test_photos_have_a_time_taken_but_no_end(tl):
    (photo,) = tl.visits_from(PHOTO)
    assert photo.start.year == 2020
    assert photo.end is None


def test_legacy_coordinates_that_overflowed_int32_become_negative(tl):
    overflowed = {"latitudeE7": 2**32 - 515007000, "longitudeE7": 2**32 - 1246000}
    assert tl.parse_latlng(overflowed) == (-51.5007, -0.1246)


def test_nights(tl):
    t = datetime.fromisoformat
    assert (
        tl.nights(tl.Visit(0, 0, t("2025-01-01T22:00:00-05:00"), t("2025-01-02T07:00:00-05:00")))
        == 1
    )
    assert (
        tl.nights(tl.Visit(0, 0, t("2025-01-01T09:00:00-05:00"), t("2025-01-01T17:00:00-05:00")))
        == 0
    )
    assert (
        tl.nights(tl.Visit(0, 0, t("2025-01-01T20:00:00+09:00"), t("2025-01-04T10:00:00+09:00")))
        == 3
    )


def test_residences_removed_even_when_home_tag_is_wrong(tl):
    start = datetime.fromisoformat("2025-01-01T22:00:00+10:30")

    def overnights(lat, lng, n):
        return [
            tl.Visit(lat, lng, start + timedelta(days=d), start + timedelta(days=d, hours=9))
            for d in range(n)
        ]

    residence = overnights(-34.93, 138.60, 25)
    hotel = overnights(-31.95, 115.86, 8)
    stale_home = [tl.Visit(-34.85, 138.48, kind="HOME")]
    # About 550 m and 23 km from the residence.
    others = [tl.Visit(-34.925, 138.60), tl.Visit(-34.72, 138.60)]
    places = tl.cluster(residence + hotel + stale_home + others, merge_m=250)
    kept = tl.drop_near(places, tl.private_zones(places, 1000, residence_nights=20))
    assert sorted((round(p.lat, 2), p.nights) for p in kept) == [(-34.72, 0), (-31.95, 8)]


def test_cluster_merges_close_visits_anywhere_on_earth(tl):
    separate = []
    for lat, lng, bearing in product(range(-60, 61, 5), (0, 139.7, 175, -179.999), (0, 45, 90)):
        lng2, lat2, _ = WGS84.fwd(lng, lat, bearing, 200)
        if len(tl.cluster([tl.Visit(lat, lng), tl.Visit(lat2, lng2)], merge_m=250)) != 1:
            separate.append((lat, lng, bearing))
    assert separate == []


def test_cluster_keeps_distant_visits_apart(tl):
    lng2, lat2, _ = WGS84.fwd(175, 40, 0, 300)
    assert len(tl.cluster([tl.Visit(40, 175), tl.Visit(lat2, lng2)], merge_m=250)) == 2


def test_unreadable_timeline_file_is_reported(tl, tmp_path, capsys):
    (tmp_path / "good.json").write_text(json.dumps(ANDROID), encoding="utf-8")
    (tmp_path / "broken.json").write_text('{"semanticSegments": [', encoding="utf-8")
    assert len(tl.load_visits(tmp_path)) == 2
    assert "broken.json" in capsys.readouterr().err


def test_timeline_cli_end_to_end(tl, tmp_path):
    export = tmp_path / "Timeline.json"
    export.write_text(json.dumps(ANDROID), encoding="utf-8")
    out = tmp_path / "places.geojson"
    result = CliRunner().invoke(tl.app, [str(export), "-o", str(out)])
    assert result.exit_code == 0, result.output
    shapes = geojson.load(out)
    assert [(p.lat, p.lng) for p in shapes.locations] == [(40.758, -73.9855)]  # home removed


def test_overpass_query_by_country(osm):
    assert osm.build_query("man_made", "lighthouse", country="is", limit=5) == (
        '[out:json][timeout:180];area["ISO3166-1"="IS"][admin_level=2]->.a;'
        'nwr["man_made"="lighthouse"](area.a);out center 5;'
    )


def test_overpass_query_by_bbox(osm):
    query = osm.build_query("railway", "*", bbox=(1, 2, 3, 4))
    assert query == '[out:json][timeout:180];nwr["railway"](1,2,3,4);out center;'


def test_overpass_refuses_worldwide_queries(osm):
    with pytest.raises(ValueError, match="worldwide"):
        osm.build_query("railway", "*")


def test_overpass_elements_to_geojson(osm):
    fc = osm.elements_to_geojson(
        [
            {"type": "node", "id": 1, "lat": 64.1, "lon": -21.9, "tags": {"name": "Reykjavik"}},
            {"type": "way", "id": 2, "center": {"lat": 63.4, "lon": -20.3}},
            {"type": "relation", "id": 3},  # no position: skipped
        ]
    )
    assert [f["geometry"]["coordinates"] for f in fc["features"]] == [[-21.9, 64.1], [-20.3, 63.4]]
    assert [f["properties"]["osm"] for f in fc["features"]] == ["node/1", "way/2"]


@pytest.mark.parametrize(
    ("statuses", "final_status", "expected_sleeps"),
    [
        ([429, 504, 200], 200, [10, 20]),
        ([503, 503, 503, 503], 503, [10, 20, 40]),
    ],
)
def test_overpass_backs_off_while_busy(osm, monkeypatch, statuses, final_status, expected_sleeps):
    monkeypatch.setattr(osm.httpx, "post", lambda *args, **kwargs: httpx.Response(statuses.pop(0)))
    sleeps = []
    monkeypatch.setattr(osm.time, "sleep", sleeps.append)
    assert osm.fetch("https://overpass.test", "q").status_code == final_status
    assert sleeps == expected_sleeps
