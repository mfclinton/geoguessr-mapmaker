"""In-memory stand-in for the map-maker API.

Behavior mirrors what the live site was observed to do (see docs/api.md), e.g. new drafts start at
version 0, a save must send version + 1, locations are read back under "coordinates" with extra
null fields, and a missing draft is HTTP 400 "No such draft".
"""

import json
import re
from collections.abc import Callable
from typing import Any

import httpx

from geoguessr_mapmaker import Client

COOKIE = "good-cookie"

_LOCATION_FIELDS = ("heading", "pitch", "zoom", "panoId", "countryCode", "stateCode", "cityCode")


def _ok(payload: Any) -> httpx.Response:
    return httpx.Response(200, json=payload)


def _bad(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(400, json=payload)


class FakeGeoGuessr:
    def __init__(self, coverage: Callable[[list[dict]], int] | None = None):
        self.drafts: dict[str, dict[str, Any]] = {}
        self.published: set[str] = set()
        self.ever_published: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.sleeps: list[float] = []
        self.fail_next: list[int] = []  # statuses to return before handling, for retry tests
        self.answer_all: httpx.Response | Exception | None = (
            None  # replaces all handling, for failure tests
        )
        self.coverage = coverage or (lambda coords: 10)
        self._seq = 0

    def client(self, cookie: str = COOKIE, max_retries: int = 3) -> Client:
        return Client(cookie, max_retries=max_retries, transport=httpx.MockTransport(self.handle))

    def add_draft(self, name: str = "Test map", mode: str = "regions", **fields: Any) -> str:
        self._seq += 1
        id_ = f"{self._seq:024x}"
        self.drafts[id_] = {
            "slug": id_,
            "name": name,
            "description": "",
            "avatar": {
                "background": "sunrise",
                "decoration": "tractor",
                "ground": "beige",
                "landscape": "houses",
            },
            "mode": mode,
            "regions": None,
            "coordinates": None,
            "tags": None,
            "maxErrorDistance": 0,
            "hasCustomErrorDistance": False,
            "version": 0,
            **fields,
        }
        return id_

    def writes(self) -> list[tuple[str, str]]:
        return [
            c
            for c in self.calls
            if c[0] in ("POST", "PUT", "DELETE") and not c[1].endswith("region-count")
        ]

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append((req.method, path))
        if isinstance(self.answer_all, Exception):
            raise self.answer_all
        if self.answer_all:
            return self.answer_all
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), headers={"Retry-After": "0"})
        if req.headers.get("cookie") != f"_ncfa={COOKIE}":
            return httpx.Response(401)
        body = json.loads(req.content) if req.content else None
        method = req.method

        if (method, path) == ("GET", "/api/v3/profiles/me"):
            return _ok({"id": "user1", "nick": "tester"})
        if (method, path) == ("GET", "/api/v4/user-maps/maps"):
            page, count = int(req.url.params["page"]), int(req.url.params["count"])
            items = [
                {
                    "id": k,
                    "slug": k,
                    "name": draft["name"],
                    "published": k in self.published,
                    "coordinateCount": str(len(draft["regions"] or draft["coordinates"] or [])),
                }
                for k, draft in reversed(self.drafts.items())
            ]
            return _ok(items[page * count : (page + 1) * count])
        if (method, path) == ("POST", "/api/v4/user-maps/drafts"):
            return _ok({"id": self.add_draft(body["name"], body["mode"])})
        if (method, path) == ("POST", "/api/v4/user-maps/region-count"):
            return _ok({"count": self.coverage(body["coordinates"])})

        if m := re.fullmatch(r"/api/v4/user-maps/drafts/(\w+)/publish/(\d*)", path):
            draft = self.drafts.get(m[1])
            if draft is None:
                return _bad({"message": "No such draft"})
            if m[2] != str(draft["version"]):
                return _bad({"message": "Version mismatch", "error": "MapMaker/VersionMismatch"})
            self.published.add(m[1])
            self.ever_published.add(m[1])
            return httpx.Response(200)

        if m := re.fullmatch(r"/api/v4/user-maps/drafts/(\w+)", path):
            draft = self.drafts.get(m[1])
            if draft is None:
                return _bad({"message": "No such draft"})
            if method == "GET":
                return _ok(draft)
            if method == "DELETE":
                del self.drafts[m[1]]
                return httpx.Response(200)
            if method == "PUT":
                if body["version"] != draft["version"] + 1:
                    return _bad(
                        {
                            "message": "Version mismatch, did someone else edit the map?",
                            "error": "MapMaker/VersionMismatch",
                        }
                    )
                draft.update(
                    name=body["name"],
                    description=body["description"],
                    avatar=body["avatar"],
                    tags=body["tags"],
                    maxErrorDistance=body["maxErrorDistance"],
                    hasCustomErrorDistance=body["hasCustomErrorDistance"],
                    version=body["version"],
                    regions=[{"coordinates": r["coordinates"]} for r in body["regions"]],
                    coordinates=[
                        {
                            **dict.fromkeys(_LOCATION_FIELDS),
                            **{k: v for k, v in c.items() if k != "extra"},
                        }
                        for c in body["customCoordinates"]
                    ],
                )
                return _ok({"message": "OK"})

        if m := re.fullmatch(r"/api/v4/user-maps/maps/(\w+)", path):
            draft = self.drafts.get(m[1])
            if (
                draft is None or m[1] not in self.ever_published
            ):  # live: fresh drafts have no map record
                return httpx.Response(404, text="Map not found")
            return _ok({**draft, "published": m[1] in self.published})
        if (m := re.fullmatch(r"/api/v4/user-maps/(\w+)/unpublish", path)) and method == "PUT":
            self.published.discard(m[1])
            return httpx.Response(200)
        if (m := re.fullmatch(r"/api/v4/user-maps/(\w+)", path)) and method == "DELETE":
            self.drafts.pop(m[1], None)
            self.published.discard(m[1])
            return httpx.Response(200)
        return httpx.Response(404)
