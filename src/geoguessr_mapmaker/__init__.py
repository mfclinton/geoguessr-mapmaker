"""Unofficial client for the GeoGuessr map maker."""

from ._version import __version__
from .client import Client
from .errors import AuthError, CheckedOutError, GeoGuessrError, NotFoundError, VersionMismatchError
from .models import MIN_LOCATIONS_TO_PUBLISH, Draft, Location, MapInfo, Mode, Profile, Region

__all__ = [
    "MIN_LOCATIONS_TO_PUBLISH",
    "AuthError",
    "CheckedOutError",
    "Client",
    "Draft",
    "GeoGuessrError",
    "Location",
    "MapInfo",
    "Mode",
    "NotFoundError",
    "Profile",
    "Region",
    "VersionMismatchError",
    "__version__",
]
