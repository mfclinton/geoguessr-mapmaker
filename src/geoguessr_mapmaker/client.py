"""HTTP client for GeoGuessr's map-maker API.

These are the private endpoints the geoguessr.com map maker itself uses; see docs/api.md.
Authentication is the ``_ncfa`` cookie from a logged-in browser session.
"""

import os
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Self
from urllib.parse import quote, unquote

import httpx
from dotenv import dotenv_values

from ._version import __version__
from .errors import AuthError, CheckedOutError, GeoGuessrError, NotFoundError, VersionMismatchError
from .models import (
    BASE_URL,
    MIN_LOCATIONS_TO_PUBLISH,
    Draft,
    MapInfo,
    Mode,
    Profile,
    Region,
    map_id,
)

ENV_VAR = "GEOGUESSR_NCFA"
COVERAGE_DELAY_S = 0.15
_TIMEOUT_S = 30.0
# 429 means the call wasn't processed, so any call can retry it. A 5xx can arrive after the server
# already acted, so those only retry calls that are harmless to repeat.
_RETRY_STATUSES = {429, 502, 503, 504}


class Client:
    """GeoGuessr map-maker client.

    >>> with Client.from_env() as gg:
    ...     draft = gg.create_draft("Lighthouses", mode="regions")
    ...     draft.regions.append(Region.circle(63.4305, 10.3951, 300))
    ...     gg.save_draft(draft)
    """

    def __init__(
        self, ncfa: str, *, max_retries: int = 3, transport: httpx.BaseTransport | None = None
    ):
        if not ncfa.strip():
            raise AuthError(f"no _ncfa cookie given (set {ENV_VAR})")
        self.max_retries = max_retries
        self._http = httpx.Client(
            base_url=BASE_URL,
            timeout=_TIMEOUT_S,
            transport=transport,
            headers={
                # Browsers send the cookie percent-encoded; accept either form.
                "Cookie": "_ncfa=" + quote(unquote(ncfa.strip()), safe=""),
                "User-Agent": f"geoguessr-mapmaker/{__version__}",
                "Accept": "application/json",
            },
        )

    @classmethod
    def from_env(cls, *, env_file: str | Path | None = ".env") -> Self:
        """Read the cookie from ``$GEOGUESSR_NCFA``, or from ``env_file`` if that isn't set."""
        ncfa = os.environ.get(ENV_VAR)
        if not ncfa and env_file:
            ncfa = dotenv_values(env_file).get(ENV_VAR)
        return cls(ncfa or "")

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        body: Any = None,
        params: dict[str, Any] | None = None,
        *,
        safe: bool = False,
    ) -> httpx.Response:
        """Send a call, raising GeoGuessrError if it fails.

        ``safe=True`` marks a non-GET call that's harmless to repeat, so server errors retry it too.
        """
        retry_on = _RETRY_STATUSES if method == "GET" or safe else {429}
        for attempt in range(self.max_retries + 1):
            response = self._http.request(method, path, json=body, params=params)
            if response.status_code in retry_on and attempt < self.max_retries:
                wait = _retry_after(response)
                time.sleep(2**attempt if wait is None else wait)
                continue
            break
        if response.status_code >= 400:
            raise _error(method, path, response)
        return response

    def _json(
        self,
        method: str,
        path: str,
        body: Any = None,
        params: dict[str, Any] | None = None,
        *,
        safe: bool = False,
    ) -> Any:
        """``_request`` for calls that must answer with JSON.

        Anything else, like a proxy's HTML page, is an error.
        """
        response = self._request(method, path, body, params, safe=safe)
        try:
            return response.json()
        except ValueError:
            where = f"{method} {path} -> HTTP {response.status_code}"
            message = f"{where}: expected JSON, got {response.text[:300]!r}"
            raise GeoGuessrError(message, status=response.status_code) from None

    def me(self) -> Profile:
        """Your GeoGuessr profile. Cheap way to check the cookie works."""
        return Profile.model_validate(self._json("GET", "/api/v3/profiles/me"))

    def list_maps(self, page: int = 0, count: int = 20) -> list[MapInfo]:
        """Your maps, published or not, newest first."""
        items = self._json("GET", "/api/v4/user-maps/maps", params={"page": page, "count": count})
        return [MapInfo.model_validate(m) for m in items]

    def iter_maps(self, page_size: int = 20) -> Iterator[MapInfo]:
        page = 0
        while True:
            batch = self.list_maps(page, page_size)
            yield from batch
            if len(batch) < page_size:
                return
            page += 1

    def is_published(self, id_or_url: str) -> bool:
        """Whether a map is live. A never-published draft has no map record, so it's a 404."""
        try:
            details = self._json("GET", f"/api/v4/user-maps/maps/{map_id(id_or_url)}")
        except NotFoundError:
            return False
        return bool(details.get("published"))

    def unpublish(self, id_or_url: str) -> None:
        self._request("PUT", f"/api/v4/user-maps/{map_id(id_or_url)}/unpublish")

    def delete_map(self, id_or_url: str) -> None:
        """Permanently delete a map. There is no undo."""
        self._request("DELETE", f"/api/v4/user-maps/{map_id(id_or_url)}")

    def create_draft(self, name: str, mode: Mode) -> Draft:
        """Create an empty map. ``mode`` ("regions" or "coordinates") can't be changed later."""
        created = self._json("POST", "/api/v4/user-maps/drafts", {"name": name, "mode": mode})
        return self.get_draft(created["id"])

    def get_draft(self, id_or_url: str) -> Draft:
        return Draft.model_validate(
            self._json("GET", f"/api/v4/user-maps/drafts/{map_id(id_or_url)}")
        )

    def save_draft(self, draft: Draft) -> None:
        """Save all fields of ``draft``. Raises VersionMismatchError if it was edited elsewhere."""
        # Optimistic locking: each save claims the next version number.
        body = draft.model_dump() | {"version": draft.version + 1}
        self._request("PUT", f"/api/v4/user-maps/drafts/{draft.id}", body)
        draft.version += 1

    def publish(self, draft: Draft) -> None:
        """Publish the saved draft.

        Raises ValueError if it has fewer than ``MIN_LOCATIONS_TO_PUBLISH`` locations.
        """
        if draft.size < MIN_LOCATIONS_TO_PUBLISH:
            need = f"GeoGuessr needs at least {MIN_LOCATIONS_TO_PUBLISH} locations to publish"
            raise ValueError(f"{need}; {draft.name!r} has {draft.size}")
        self._request("PUT", f"/api/v4/user-maps/drafts/{draft.id}/publish/{draft.version}")

    def delete_draft(self, id_or_url: str) -> None:
        """Permanently delete an unpublished draft. There is no undo."""
        self._request("DELETE", f"/api/v4/user-maps/drafts/{map_id(id_or_url)}")

    def region_count(self, region: Region) -> int:
        """How many Street View locations GeoGuessr can pick inside ``region``. 0 = unplayable."""
        body = region.model_dump(exclude={"id"})
        payload = self._json("POST", "/api/v4/user-maps/region-count", body, safe=True)  # read-only
        return int(payload["count"])

    def check_coverage(
        self, regions: Iterable[Region], *, delay: float = COVERAGE_DELAY_S
    ) -> Iterator[int]:
        """Yield ``region_count`` for each region, waiting ``delay`` seconds between requests."""
        for i, region in enumerate(regions):
            if i:
                time.sleep(delay)
            yield self.region_count(region)


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


def _error(method: str, path: str, response: httpx.Response) -> GeoGuessrError:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    code = payload.get("error") if isinstance(payload, dict) else None
    message = payload.get("message") if isinstance(payload, dict) else None
    where = f"{method} {path} -> HTTP {response.status_code}"
    fields = {"status": response.status_code, "code": code, "payload": payload}
    if response.status_code in (401, 403):
        return AuthError(
            f"{where}: not logged in. Is {ENV_VAR} set to a current _ncfa cookie?", **fields
        )
    if code == "MapMaker/VersionMismatch":
        return VersionMismatchError(
            f"{where}: draft changed since it was loaded; reload and retry", **fields
        )
    if code == "MapMaker/CheckedOut":
        return CheckedOutError(f"{where}: a collaborator is editing this map", **fields)
    if response.status_code == 404 or message == "No such draft":
        return NotFoundError(f"{where}: {message or 'not found'}", **fields)
    return GeoGuessrError(f"{where}: {message or response.text[:300]}", **fields)
