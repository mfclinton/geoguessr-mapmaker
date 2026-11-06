"""Exceptions raised by the client."""

from typing import Any


class GeoGuessrError(RuntimeError):
    """An API call failed. ``status`` is the HTTP status, ``code`` GeoGuessr's error code if any."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        payload: Any = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload


class AuthError(GeoGuessrError):
    """The ``_ncfa`` cookie is missing, wrong, or expired."""


class NotFoundError(GeoGuessrError):
    """No such map or draft, or it isn't yours."""


class VersionMismatchError(GeoGuessrError):
    """The draft was saved elsewhere since you loaded it. Reload with ``get_draft`` and retry."""


class CheckedOutError(GeoGuessrError):
    """A collaborator currently has the map open for editing."""
