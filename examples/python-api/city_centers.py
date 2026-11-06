"""Build a map from code: a circle around each city center, coverage-checked, then saved as a draft.

    uv run python examples/python-api/city_centers.py "City Centers"

Needs GEOGUESSR_NCFA (or a .env file). Open the printed link to review, then publish from the map
maker or with `ggmm publish <id>`.
"""

import sys

from geoguessr_mapmaker import Client, Region

CITY_CENTERS = {
    "Reykjavík": (64.1466, -21.9426),
    "Lisbon": (38.7110, -9.1366),
    "Kyoto": (35.0037, 135.7788),
    "Cape Town": (-33.9249, 18.4241),
    "Buenos Aires": (-34.6083, -58.3712),
    "Montréal": (45.5088, -73.5540),
    "Wellington": (-41.2865, 174.7762),
}


def main(name: str) -> None:
    regions = [Region.circle(lat, lng, radius_m=1500) for lat, lng in CITY_CENTERS.values()]

    with Client.from_env() as gg:
        counts = list(gg.check_coverage(regions))
        for city, count in zip(CITY_CENTERS, counts, strict=True):
            print(f"  {city:<13} {count:>6} Street View locations")

        draft = gg.create_draft(name, mode="regions")
        draft.regions = [region for region, count in zip(regions, counts, strict=True) if count > 0]
        draft.description = "Generated with geoguessr-mapmaker"
        draft.set_five_k_distance(50)  # a perfect score needs a guess within 50 m
        gg.save_draft(draft)
        print(f"Saved {draft.size} regions: {draft.edit_url}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "City Centers")
