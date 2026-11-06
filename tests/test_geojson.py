import json

import pytest

from geoguessr_mapmaker import Draft, Location, Region, geojson


def fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def feature(geometry, **props):
    return {"type": "Feature", "properties": props, "geometry": geometry}


SQUARE = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]  # GeoJSON order: [lng, lat]


def test_polygons_become_regions_in_lat_lng_order():
    shapes = geojson.from_geojson(feature({"type": "Polygon", "coordinates": [SQUARE]}))
    assert shapes.regions[0].points == [(0, 0), (0, 1), (1, 1), (1, 0)]


def test_multipolygons_become_one_region_each_and_holes_are_dropped_with_a_warning():
    shapes = geojson.from_geojson(
        feature({"type": "MultiPolygon", "coordinates": [[SQUARE], [SQUARE, SQUARE]]})
    )
    assert len(shapes.regions) == 2
    assert any("holes" in warning for warning in shapes.warnings)


def test_points_keep_their_properties():
    point = {"type": "Point", "coordinates": [-73.98, 40.75]}
    ((location, own_radius),) = geojson.from_geojson(
        feature(point, heading=120, panoId="abc", radius_m=50)
    ).points
    assert (location.lat, location.lng, location.heading, location.pano_id) == (
        40.75,
        -73.98,
        120,
        "abc",
    )
    assert own_radius == 50


def test_point_with_a_bad_property_is_skipped_with_a_warning():
    shapes = geojson.from_geojson(
        feature({"type": "Point", "coordinates": [5, 5]}, heading="north")
    )
    assert shapes.locations == []
    assert shapes.warnings[0].startswith("skipped point: heading:")


def test_unsupported_geometry_is_skipped_with_a_warning():
    shapes = geojson.from_geojson(feature({"type": "LineString", "coordinates": [[0, 0], [1, 1]]}))
    assert shapes.warnings == ["skipped unsupported geometry: LineString"]


def test_feature_without_geometry_is_ignored():
    shapes = geojson.from_geojson({"type": "Feature", "properties": {}, "geometry": None})
    assert shapes == geojson.Shapes()


def test_geometry_collections_are_read():
    shapes = geojson.from_geojson(
        {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "MultiPoint", "coordinates": [[1, 2], [3, 4]]},
                {"type": "Polygon", "coordinates": [SQUARE]},
            ],
        }
    )
    assert [(location.lat, location.lng) for location in shapes.locations] == [(2, 1), (4, 3)]
    assert len(shapes.regions) == 1


@pytest.mark.parametrize(
    "malformed",
    [
        {"type": "Polygon"},
        {"type": "Polygon", "coordinates": []},
        {"type": "Polygon", "coordinates": [[[1], [2], [3]]]},
        {"type": "MultiPolygon"},
        {"type": "Point"},
        {"type": "Point", "coordinates": [1]},
        {"type": "Point", "coordinates": {"x": 1}},
        {"type": "MultiPoint", "coordinates": 5},
        {"type": "FeatureCollection", "features": [1]},
    ],
)
def test_malformed_geojson_is_skipped_with_a_warning(malformed):
    good = feature({"type": "Point", "coordinates": [1, 2]})
    shapes = geojson.from_geojson(fc(malformed, good))
    assert len(shapes.locations) == 1
    assert shapes.warnings


@pytest.mark.parametrize(
    "broken",
    [
        {"type": "FeatureCollection", "features": 5},
        {"type": "GeometryCollection", "geometries": "x"},
    ],
)
def test_collections_that_are_not_lists_are_rejected(broken):
    with pytest.raises(ValueError, match="must be a list"):
        geojson.from_geojson(broken)


@pytest.mark.parametrize("coordinates", [5, [{"lat": 0, "lng": 0}]])
def test_bad_regions_in_geoguessr_json_are_skipped_with_a_warning(coordinates):
    shapes = geojson.from_geoguessr_json({"regions": [{"coordinates": coordinates}]})
    assert shapes.regions == []
    assert len(shapes.warnings) == 1


def test_geoguessr_json_regions_must_be_a_list():
    with pytest.raises(ValueError, match="must be a list"):
        geojson.from_geoguessr_json({"regions": 5})


def test_points_to_circles_respect_per_point_radius():
    shapes = geojson.from_geojson(
        fc(
            feature({"type": "Point", "coordinates": [0, 0]}),
            feature({"type": "Point", "coordinates": [10, 10]}, radius_m=1000),
            feature({"type": "Polygon", "coordinates": [SQUARE]}),
        )
    )
    regions = shapes.to_regions(radius_m=100, vertices=8)
    assert len(regions) == 3
    assert len(regions[1].points) == 8
    small, big = regions[1], regions[2]
    assert max(abs(lat) for lat, _ in small.points) < 0.001
    assert max(abs(lat - 10) for lat, _ in big.points) > 0.008


def test_geoguessr_json_variants():
    items = [
        {"lat": 1, "lng": 2, "heading": 3, "panoId": "p", "extra": {"tags": ["x"]}},
        {"nope": 1},
    ]
    for obj in (items, {"name": "m", "customCoordinates": items}, {"coordinates": items}):
        shapes = geojson.from_geoguessr_json(obj)
        assert [(p.lat, p.lng, p.heading, p.pano_id) for p in shapes.locations] == [(1, 2, 3, "p")]
        assert len(shapes.warnings) == 1


def test_round_trip_through_both_export_formats(tmp_path):
    draft = Draft(
        id="m1",
        name="Round trip",
        mode="regions",
        regions=[Region.circle(51.5, -0.12, 300, 12)],
        locations=[Location(lat=48.85, lng=2.29, heading=45, pano_id="xyz")],
    )
    exports = {
        "map.geojson": geojson.draft_to_geojson(draft),
        "map.json": geojson.draft_to_geoguessr_json(draft),
    }
    for name, data in exports.items():
        path = tmp_path / name
        path.write_text(json.dumps(data))
        shapes = geojson.load(path)
        assert (shapes.regions, shapes.locations) == (draft.regions, draft.locations), name
