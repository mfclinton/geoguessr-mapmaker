import time

import pytest

from geoguessr_mapmaker import Client, cli

from .fake_geoguessr import FakeGeoGuessr


@pytest.fixture
def fake(monkeypatch) -> FakeGeoGuessr:
    server = FakeGeoGuessr()
    monkeypatch.setattr(time, "sleep", server.sleeps.append)
    return server


@pytest.fixture
def run(fake, monkeypatch):
    """Run the CLI against the fake server: run("maps") -> exit code."""
    monkeypatch.setattr(Client, "from_env", lambda env_file: fake.client())

    def _run(*argv: str) -> int:
        return cli.main(list(argv))

    return _run
