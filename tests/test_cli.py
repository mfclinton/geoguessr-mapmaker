import json
import re

import httpx
import pytest

from geoguessr_mapmaker import Client, Location, cli
from geoguessr_mapmaker.client import ENV_VAR

POINTS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
        }
        for lat, lng in [(0.5, 0.5), (10, 10), (20, 20), (30, 30), (40, 40), (50, 50)]
    ],
}
POLYGON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        }
    ],
}


def write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


def squash(text):
    """Typer's error box as one line, without its colors, borders and line wraps."""
    uncolored = re.sub(r"\x1b\[[0-9;]*m", "", text)  # Typer forces color in GitHub Actions
    return " ".join(re.sub(r"[│|]", " ", uncolored).split())


def test_upload_new_points_as_circles_with_coverage_and_publish(run, fake, tmp_path, capsys):
    # The first point has no Street View.
    fake.coverage = lambda coords: 0 if abs(coords[0]["lat"]) < 1 else 3
    path = write(tmp_path, "points.geojson", POINTS)
    code = run(
        "upload", path, "--new", "Test", "--mode", "regions", "--radius-m", "250",
        "--check-coverage", "--delay", "0", "--five-k-m", "50", "--publish",
    )  # fmt: skip
    assert code == 0
    (draft,) = fake.drafts.values()
    assert draft["mode"] == "regions"
    assert len(draft["regions"]) == 5
    assert draft["hasCustomErrorDistance"] is True
    assert draft["slug"] in fake.published
    assert "Published" in capsys.readouterr().out


def test_upload_new_infers_mode_from_file(run, fake, tmp_path):
    assert run("upload", write(tmp_path, "p.geojson", POINTS), "--new", "Spots") == 0
    assert run("upload", write(tmp_path, "r.geojson", POLYGON), "--new", "Areas") == 0
    assert sorted(draft["mode"] for draft in fake.drafts.values()) == ["coordinates", "regions"]


def test_upload_append_to_existing_coordinates_map(run, fake, tmp_path):
    id_ = fake.add_draft("Spots", "coordinates")
    path = write(tmp_path, "p.geojson", POINTS)
    assert run("upload", path, "--map", f"https://www.geoguessr.com/map-maker/{id_}") == 0
    assert run("upload", path, "--map", id_, "--append") == 0
    assert len(fake.drafts[id_]["coordinates"]) == 12


def test_upload_refuses_usage_problems(run, fake, tmp_path, capsys):
    id_ = fake.add_draft("Spots", "coordinates")
    points = write(tmp_path, "p.geojson", POINTS)
    assert run("upload", write(tmp_path, "r.geojson", POLYGON), "--map", id_) == 2
    assert run("upload", points, "--map", id_, "--mode", "regions") == 2
    assert "mode can't change" in squash(capsys.readouterr().err)
    assert run("upload", points, "--map", id_, "--new", "Both") == 2
    assert run("upload", points) == 2
    assert run("upload", points, "--new", "") == 2  # an empty name counts as not giving one
    assert fake.writes() == []


def test_dry_run_writes_nothing(run, fake, tmp_path, capsys):
    assert run("upload", write(tmp_path, "p.geojson", POINTS), "--new", "X", "--dry-run") == 0
    assert "would upload 6 coordinates" in capsys.readouterr().out
    assert fake.writes() == []
    assert not fake.drafts


def test_upload_refuses_to_publish_too_few_locations(run, fake, tmp_path, capsys):
    # Only 3 of the 6 points have Street View.
    fake.coverage = lambda coords: 0 if coords[0]["lat"] < 25 else 1
    path = write(tmp_path, "p.geojson", POINTS)
    code = run(
        "upload",
        path,
        "--new",
        "X",
        "--mode",
        "regions",
        "--check-coverage",
        "--delay",
        "0",
        "--publish",
    )
    assert code == 2
    assert fake.writes() == []  # refused before anything was created
    assert "at least 5 locations" in squash(capsys.readouterr().err)


def test_publish_refuses_too_few_locations(run, fake):
    triangle = {"coordinates": [{"lat": 0, "lng": 0}, {"lat": 0, "lng": 1}, {"lat": 1, "lng": 1}]}
    id_ = fake.add_draft("Tiny", regions=[triangle])
    assert run("publish", id_) == 1
    assert id_ not in fake.published


def test_publish_and_unpublish(run, fake):
    spots = [Location(lat=i, lng=i).model_dump() for i in range(5)]
    id_ = fake.add_draft("Spots", "coordinates", coordinates=spots)
    assert run("publish", id_) == 0
    assert id_ in fake.published
    assert run("unpublish", id_) == 0
    assert id_ not in fake.published


def test_new_creates_an_empty_map(run, fake, capsys):
    assert run("new", "Empty", "--mode", "coordinates") == 0
    (draft,) = fake.drafts.values()
    assert draft["mode"] == "coordinates"
    assert "Created coordinates map" in capsys.readouterr().out


def test_malformed_file_warns_rather_than_crashing(run, tmp_path, capsys):
    path = write(tmp_path, "bad.geojson", {"type": "Polygon"})
    assert run("upload", path, "--new", "X", "--dry-run") == 1
    err = capsys.readouterr().err
    assert "malformed coordinates" in err
    assert "nothing to upload" in err


def test_export_formats(run, fake, tmp_path):
    id_ = fake.add_draft(
        "Spots",
        "coordinates",
        coordinates=[{**Location(lat=1, lng=2).model_dump(), "cityCode": None}],
        version=1,
    )
    out = tmp_path / "out.geojson"
    assert run("export", id_, "-o", str(out)) == 0
    data = json.loads(out.read_text())
    assert data["features"][0]["geometry"] == {"type": "Point", "coordinates": [2, 1]}
    assert run("export", id_, "-o", str(out), "--format", "geoguessr") == 0
    assert json.loads(out.read_text())["customCoordinates"][0]["lat"] == 1


def test_coverage_command_writes_covered_regions(run, fake, tmp_path):
    fake.coverage = lambda coords: 0 if abs(coords[0]["lat"]) < 1 else 1
    out = tmp_path / "covered.geojson"
    assert (
        run("coverage", write(tmp_path, "p.geojson", POINTS), "-o", str(out), "--delay", "0") == 0
    )
    assert len(json.loads(out.read_text())["features"]) == 5
    assert fake.writes() == []


def test_whoami(run, capsys):
    assert run("whoami") == 0
    assert "Logged in as tester" in capsys.readouterr().out


def test_maps_lists_your_maps(run, fake, capsys):
    fake.add_draft("My map")
    assert run("maps") == 0
    assert "My map" in capsys.readouterr().out


def test_show_summarizes_a_draft(run, fake, capsys):
    id_ = fake.add_draft("My map")
    assert run("show", id_) == 0
    assert "scoring  automatic" in capsys.readouterr().out


def test_delete_keeps_the_map_if_confirmation_fails(run, fake, monkeypatch):
    id_ = fake.add_draft("Keep me")
    monkeypatch.setattr("builtins.input", lambda prompt: "wrong name")
    assert run("delete", id_) == 1
    assert id_ in fake.drafts


def test_delete_with_yes_skips_confirmation(run, fake):
    id_ = fake.add_draft("Delete me")
    assert run("delete", id_, "--yes") == 0
    assert id_ not in fake.drafts


def test_auth_failure_exit_code(fake, monkeypatch, capsys):
    monkeypatch.setattr(Client, "from_env", lambda env_file: fake.client(cookie="expired"))
    assert cli.main(["whoami"]) == 4
    assert "not logged in" in capsys.readouterr().err


def test_running_without_a_cookie_exits_4(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert cli.main(["--env-file", str(tmp_path / "missing.env"), "whoami"]) == 4
    assert "no _ncfa cookie" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (httpx.Response(500, json={"message": "Oops"}), "Oops"),
        (httpx.Response(200, text="<html>hello</html>"), "expected JSON"),
        (httpx.Response(200, json=[1, 2]), "unexpected response"),
        (httpx.ConnectError("no route"), "network error"),
    ],
)
def test_server_and_network_problems_exit_3(run, fake, capsys, answer, expected):
    fake.answer_all = answer
    assert run("show", "abc") == 3
    assert expected in capsys.readouterr().err


def test_delete_published_map_uses_map_endpoint(run, fake):
    id_ = fake.add_draft("Live map", version=1)
    fake.published.add(id_)
    fake.ever_published.add(id_)
    assert run("delete", id_, "--yes") == 0
    assert ("DELETE", f"/api/v4/user-maps/{id_}") in fake.calls
