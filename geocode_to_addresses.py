"""
geocode_to_addresses.py

Reverse-geocodes lat/long pairs from boston_dispatch_data.csv to street addresses
using geopy's Nominatim, and infers which side of the road (left/right) each point
falls on using house-number parity (the most reliable signal Nominatim exposes).

Side-of-road logic
------------------
OSM / Nominatim addresses follow the standard US convention:
  - Odd  house numbers  → left  side of the road (ascending direction)
  - Even house numbers  → right side of the road (ascending direction)

When no house number is returned (e.g. the point is at an intersection or
Nominatim can only resolve a road name), road_side is set to "unknown".

NOTE: Nominatim's public API is rate-limited to 1 request/second.
If the dataset is large (thousands of rows), consider running this overnight
or using a local Nominatim instance / a commercial geocoding service.

Output
------
  boston_dispatch_geocoded.csv   — original columns + geocoded_address,
                                   house_number, geocoded_road, road_side

Prerequisites (pip install if missing)
--------------------------------------
  pip install geopy pandas

macOS Python SSL fix (run once if you see certificate errors)
-------------------------------------------------------------
  bash "/Applications/Python 3.x/Install Certificates.command"
"""

import time
from pathlib import Path

import pandas as pd
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
INPUT_CSV  = Path("boston_dispatch_data.csv")
OUTPUT_CSV = Path("boston_dispatch_geocoded.csv")

# Nominatim requires a descriptive user-agent so their admins can contact you.
USER_AGENT = "hackachusetts_2026_geocoder/1.0 (boston_vision_zero)"

# Nominatim's public API is capped at 1 request/second; the RateLimiter
# enforces this automatically.
REQUESTS_PER_SECOND = 1          # seconds between requests
MAX_RETRIES         = 3          # retries on transient errors
ERROR_WAIT_SECONDS  = 5          # extra pause after an error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def infer_road_side(house_number: str | None) -> str:
    """
    Returns 'left', 'right', or 'unknown' based on house-number parity.

    US addressing convention (ascending direction of the street):
        odd  → left
        even → right
    """
    if not house_number:
        return "unknown"

    # House numbers can be ranges like "123-125"; use the first numeric token.
    import re
    tokens = re.findall(r"\d+", house_number)
    if not tokens:
        return "unknown"

    number = int(tokens[0])
    return "left" if number % 2 == 1 else "right"


def reverse_geocode_row(reverse_fn, lat: float, lon: float) -> dict:
    """
    Calls Nominatim reverse geocoding and returns a dict with:
        address       – full display address string
        house_number  – raw house number string (may be None)
        road          – street/road name from Nominatim
        road_side     – 'left' | 'right' | 'unknown'
    """
    result = {
        "address":      None,
        "house_number": None,
        "road":         None,
        "road_side":    "unknown",
    }

    try:
        location = reverse_fn((lat, lon), language="en")
        if location is None:
            return result

        result["address"] = location.address

        raw = location.raw.get("address", {})
        house_number = raw.get("house_number")  # may be absent
        result["house_number"] = house_number
        result["road"]         = raw.get("road") or raw.get("pedestrian")
        result["road_side"]    = infer_road_side(house_number)

    except Exception as exc:  # noqa: BLE001
        print(f"    [warn] Geocoding ({lat}, {lon}) failed: {exc}")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Loading {INPUT_CSV} …")
    df = pd.read_csv(INPUT_CSV)

    # Validate required columns
    missing = {"lat", "long"} - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing expected columns: {missing}")

    print(f"  {len(df)} rows loaded.\n")

    # ------------------------------------------------------------------
    # Set up Nominatim with automatic rate limiting
    # ------------------------------------------------------------------
    geolocator = Nominatim(user_agent=USER_AGENT)
    reverse = RateLimiter(
        geolocator.reverse,
        min_delay_seconds=REQUESTS_PER_SECOND,
        max_retries=MAX_RETRIES,
        error_wait_seconds=ERROR_WAIT_SECONDS,
        swallow_exceptions=False,
    )

    # ------------------------------------------------------------------
    # Geocode each row
    # ------------------------------------------------------------------
    addresses      = []
    house_numbers  = []
    roads          = []
    road_sides     = []

    total = len(df)
    for i, row in df.iterrows():
        lat = row["lat"]
        lon = row["long"]

        pct = (i + 1) / total * 100
        print(f"  [{i+1:>5}/{total}  {pct:5.1f}%]  ({lat:.6f}, {lon:.6f})", end="  ")

        geo = reverse_geocode_row(reverse, lat, lon)

        addresses.append(geo["address"])
        house_numbers.append(geo["house_number"])
        roads.append(geo["road"])
        road_sides.append(geo["road_side"])

        print(f"→ {geo['road_side']:7s}  {geo['address']}")

    # ------------------------------------------------------------------
    # Attach results and save
    # ------------------------------------------------------------------
    df["geocoded_address"] = addresses
    df["house_number"]     = house_numbers
    df["geocoded_road"]    = roads
    df["road_side"]        = road_sides   # 'left' | 'right' | 'unknown'

    df.to_csv(geoded_boston_zero_crash_CSV, index=False)
    print(f"\nDone. Results written to {geoded_boston_zero_crash_CSV}")

    # Quick summary
    side_counts = df["road_side"].value_counts()
    print("\nRoad-side distribution:")
    for side, count in side_counts.items():
        print(f"  {side:>8s}: {count}")


if __name__ == "__main__":
    main()
