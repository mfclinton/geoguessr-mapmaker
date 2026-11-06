import httpx
import pytest

import geoguessr_mapmaker
from geoguessr_mapmaker import (
    AuthError,
    Client,
    GeoGuessrError,
    NotFoundError,
    Region,
    VersionMismatchError,
)
from geoguessr_mapmaker.client import ENV_VAR


@pytest.mark.parametrize("given", ["a=b+c/d", "a%3Db%2Bc%2Fd"])
def test_cookie_is_sent_percent_encoded_either_way(given):
    sent = []

    def handler(request):
        sent.append(request.headers["cookie"])
        return httpx.Response(200, json=[])

    Client(given, transport=httpx.MockTransport(handler)).list_maps()
    assert sent == ["_ncfa=a%3Db%2Bc%2Fd"]


class RecordsCookie(Client):
    def __init__(self, ncfa):
        self.ncfa = ncfa


def test_from_env_prefers_the_environment(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(f"{ENV_VAR}=from-file\n")
    monkeypatch.setenv(ENV_VAR, "from-environment")
    assert RecordsCookie.from_env(env_file=tmp_path / ".env").ncfa == "from-environment"


def test_from_env_falls_back_to_the_file(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(f"{ENV_VAR}=from-file\n")
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert RecordsCookie.from_env(env_file=tmp_path / ".env").ncfa == "from-file"


def test_from_env_without_a_cookie_is_an_auth_error(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_VAR, raising=False)
    with pytest.raises(AuthError, match="no _ncfa cookie"):
        Client.from_env(env_file=tmp_path / "missing.env")


def test_create_save_publish_flow(fake):
    gg = fake.client()
    draft = gg.create_draft("Lighthouses", "regions")
    assert (draft.version, draft.size) == (0, 0)

    draft.regions += [Region.circle(63.43 + i, 10.39, 300) for i in range(5)]
    gg.save_draft(draft)
    draft.name = "Lighthouses of Norway"
    gg.save_draft(draft)  # consecutive saves keep working
    gg.publish(draft)

    stored = gg.get_draft(draft.id)
    assert (stored.version, stored.size, stored.name) == (2, 5, "Lighthouses of Norway")
    assert draft.id in fake.published


def test_wrong_cookie_raises_auth_error(fake):
    with pytest.raises(AuthError):
        fake.client(cookie="wrong").me()


def test_missing_draft_raises_not_found(fake):
    with pytest.raises(NotFoundError):
        fake.client().get_draft("doesnotexist")


def test_saving_a_stale_draft_raises_version_mismatch(fake):
    gg = fake.client()
    current = gg.create_draft("m", "coordinates")
    stale = gg.get_draft(current.id)
    gg.save_draft(current)
    with pytest.raises(VersionMismatchError):
        gg.save_draft(stale)


def test_retries_honor_retry_after(fake):
    fake.fail_next = [429, 503]
    assert fake.client(max_retries=2).me().nick == "tester"
    assert fake.sleeps == [0.0, 0.0]


def test_retries_give_up_eventually(fake):
    fake.fail_next = [429, 429, 429]
    with pytest.raises(GeoGuessrError) as e:
        fake.client(max_retries=2).me()
    assert e.value.status == 429


def test_server_errors_do_not_retry_a_save(
    fake,
):  # it may have landed, so repeating it could misfire
    gg = fake.client()
    draft = gg.create_draft("m", "regions")
    fake.fail_next = [502]
    with pytest.raises(GeoGuessrError) as e:
        gg.save_draft(draft)
    assert e.value.status == 502
    assert fake.sleeps == []


def test_server_errors_retry_a_read_only_post(fake):
    fake.fail_next = [503]
    assert fake.client().region_count(Region.circle(0, 0, 300)) == 10
    assert len(fake.sleeps) == 1


def test_check_coverage_waits_between_requests_only(fake):
    regions = [Region.circle(0, 0, 300), Region.circle(1, 1, 300)]
    assert list(fake.client().check_coverage(regions, delay=0.5)) == [10, 10]
    assert fake.sleeps == [0.5]


def test_non_json_success_response_raises_a_geoguessr_error(fake):
    fake.answer_all = httpx.Response(200, text="<html>hello</html>")
    with pytest.raises(GeoGuessrError, match="expected JSON"):
        fake.client().me()


def test_empty_response_is_fine_when_none_is_needed(fake):
    id_ = fake.add_draft()
    fake.answer_all = httpx.Response(200)
    fake.client().delete_draft(id_)


def test_publish_needs_five_locations(fake):
    gg = fake.client()
    draft = gg.create_draft("Tiny", "regions")
    draft.regions.append(Region.circle(0, 0, 300))
    gg.save_draft(draft)
    with pytest.raises(ValueError, match="at least 5"):
        gg.publish(draft)
    assert draft.id not in fake.published


def test_list_and_iterate_maps(fake):
    for i in range(5):
        fake.add_draft(f"map {i}")
    gg = fake.client()
    assert [m.name for m in gg.list_maps(0, 2)] == ["map 4", "map 3"]
    assert len(list(gg.iter_maps(page_size=2))) == 5


def test_is_published(fake):
    gg = fake.client()
    never_published = fake.add_draft("Fresh")
    assert gg.is_published(never_published) is False

    live = fake.add_draft("Live")
    fake.published.add(live)
    fake.ever_published.add(live)
    assert gg.is_published(live) is True


def test_every_name_in_all_exists():  # ruff doesn't check the strings in __all__, so this does
    for name in geoguessr_mapmaker.__all__:
        assert hasattr(geoguessr_mapmaker, name), name
