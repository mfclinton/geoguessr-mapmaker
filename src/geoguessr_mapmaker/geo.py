"""Geometry and scoring helpers."""

import math

from pyproj import Geod

WGS84 = Geod(ellps="WGS84")
CIRCLE_VERTICES = 16

# A score is 5000 * exp(-10 * d / maxErrorDistance), which rounds to 5000 until it falls to 4999.5,
# or 99.99%. The map maker's "5K distance" slider puts that point at fiveK:
# maxErrorDistance = fiveK / ln(1 / 0.9999) * 10. tests/test_models.py checks this.
_FIVE_K_DECAY = math.log(1 / 0.9999)
MIN_FIVE_K_M = 25  # the web slider's lower bound
DEFAULT_FIVE_K_M = 175  # what the web map maker sends until you move its slider


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance in meters along the Earth's surface."""
    return float(WGS84.inv(lng1, lat1, lng2, lat2)[2])


def circle_ring(
    lat: float, lng: float, radius_m: float, vertices: int = CIRCLE_VERTICES
) -> list[tuple[float, float]]:
    """(lat, lng) vertices approximating a circle. Open ring: the first point is not repeated."""
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    if vertices < 3:
        raise ValueError("vertices must be at least 3")
    # Counterclockwise, starting due east.
    azimuths = [90 - 360 * k / vertices for k in range(vertices)]
    lngs, lats, _ = WGS84.fwd([lng] * vertices, [lat] * vertices, azimuths, [radius_m] * vertices)
    # Longitudes run on past ±180 instead of wrapping, or a ring crossing it would span the globe.
    return [
        (round(point_lat, 7), round(lng + (point_lng - lng + 180) % 360 - 180, 7))
        for point_lat, point_lng in zip(lats, lngs, strict=True)
    ]


def five_k_to_max_error(five_k_m: float) -> int:
    """Distance (m) that should still score a perfect 5,000 -> the map's maxErrorDistance."""
    return round(five_k_m / _FIVE_K_DECAY * 10)


def max_error_to_five_k(max_error_distance: float) -> float:
    """Inverse of five_k_to_max_error()."""
    return max_error_distance * _FIVE_K_DECAY / 10
