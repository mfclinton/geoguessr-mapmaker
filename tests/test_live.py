"""Checks the client against the real GeoGuessr API, which fake_geoguessr.py only imitates.

    uv run pytest -m live

Uses your GEOGUESSR_NCFA cookie. It creates unpublished throwaway maps and deletes them, and never
publishes anything.
"""

import pytest

from geoguessr_mapmaker import (
    AuthError,
    Client,
    Location,
    NotFoundError,
    Region,
    VersionMismatchError,
)

pytestmark = pytest.mark.live

THROWAWAY_NAME = "ggmm live test (safe to delete)"
REYKJAVIK = (64.1466, -21.9426)


@pytest.fixture(scope="module")
def gg():
    try:
        client = Client.from_env()
    except AuthError:
        pytest.skip("needs GEOGUESSR_NCFA, or a .env file with it")
    with client:
        yield client


@pytest.fixture
def make_draft(gg):
    """Create throwaway drafts, and delete them when the test ends even if it fails."""
    created = []

    def _make(mode):
        draft = gg.create_draft(THROWAWAY_NAME, mode)
        created.append(draft.id)
        return draft

    yield _make
    for id_ in created:
        gg.delete_draft(id_)


def test_cookie_works(gg):
    assert gg.me().nick


def test_maps_can_be_listed(gg):
    assert all(map_info.id for map_info in gg.list_maps())


def test_page_past_the_end_is_empty(gg):
    assert gg.list_maps(page=9999) == []


def test_region_count_finds_street_view_in_a_city(gg):
    assert gg.region_count(Region.circle(*REYKJAVIK, 400)) > 0


def test_new_draft_starts_empty_and_unpublished(gg, make_draft):
    draft = make_draft("regions")
    assert (draft.mode, draft.version, draft.size) == ("regions", 0, 0)
    assert gg.is_published(draft.id) is False


def test_regions_and_tags_survive_a_save(gg, make_draft):
    draft = make_draft("regions")
    region = Region.circle(*REYKJAVIK, 400)
    draft.regions = [region]
    draft.tags = ["ggmm-live-test"]
    gg.save_draft(draft)

    stored = gg.get_draft(draft.id)
    assert stored.version == 1
    assert stored.regions[0].points == region.points
    assert stored.tags == ["ggmm-live-test"]


def test_locations_survive_a_save(gg, make_draft):
    draft = make_draft("coordinates")
    draft.locations = [Location(lat=REYKJAVIK[0], lng=REYKJAVIK[1], heading=90)]
    gg.save_draft(draft)

    (stored,) = gg.get_draft(draft.id).locations
    assert (stored.lat, stored.lng, stored.heading) == (*REYKJAVIK, 90)


def test_five_k_distance_survives_a_save(gg, make_draft):
    draft = make_draft("regions")
    draft.set_five_k_distance(50)
    gg.save_draft(draft)
    assert round(gg.get_draft(draft.id).five_k_distance_m) == 50


def test_saving_a_stale_draft_is_rejected(gg, make_draft):
    current = make_draft("coordinates")
    stale = gg.get_draft(current.id)
    gg.save_draft(current)
    with pytest.raises(VersionMismatchError):
        gg.save_draft(stale)


def test_deleted_draft_is_not_found(gg):
    draft = gg.create_draft(THROWAWAY_NAME, "regions")
    gg.delete_draft(draft.id)
    with pytest.raises(NotFoundError):
        gg.get_draft(draft.id)
