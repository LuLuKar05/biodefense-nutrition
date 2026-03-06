"""
cities.py — MVP city list (20 England + 5 Scotland)
====================================================
Each entry has name, country, lat, lon for OpenWeatherMap Air Pollution API.
"""
from __future__ import annotations

CITIES: list[dict[str, str | float]] = [
    # ── England (20) ─────────────────────────────────────────
    {"name": "London",        "country": "England",  "lat": 51.5074, "lon": -0.1278},
    {"name": "Birmingham",    "country": "England",  "lat": 52.4862, "lon": -1.8904},
    {"name": "Manchester",    "country": "England",  "lat": 53.4808, "lon": -2.2426},
    {"name": "Leeds",         "country": "England",  "lat": 53.8008, "lon": -1.5491},
    {"name": "Liverpool",     "country": "England",  "lat": 53.4084, "lon": -2.9916},
    {"name": "Sheffield",     "country": "England",  "lat": 53.3811, "lon": -1.4701},
    {"name": "Bristol",       "country": "England",  "lat": 51.4545, "lon": -2.5879},
    {"name": "Newcastle",     "country": "England",  "lat": 54.9783, "lon": -1.6178},
    {"name": "Nottingham",    "country": "England",  "lat": 52.9548, "lon": -1.1581},
    {"name": "Leicester",     "country": "England",  "lat": 52.6369, "lon": -1.1398},
    {"name": "Coventry",      "country": "England",  "lat": 52.4068, "lon": -1.5197},
    {"name": "Bradford",      "country": "England",  "lat": 53.7960, "lon": -1.7594},
    {"name": "Southampton",   "country": "England",  "lat": 50.9097, "lon": -1.4044},
    {"name": "Brighton",      "country": "England",  "lat": 50.8225, "lon": -0.1372},
    {"name": "Plymouth",      "country": "England",  "lat": 50.3755, "lon": -4.1427},
    {"name": "Wolverhampton", "country": "England",  "lat": 52.5870, "lon": -2.1288},
    {"name": "Reading",       "country": "England",  "lat": 51.4543, "lon": -0.9781},
    {"name": "Derby",         "country": "England",  "lat": 52.9226, "lon": -1.4747},
    {"name": "Sunderland",    "country": "England",  "lat": 54.9069, "lon": -1.3838},
    {"name": "Norwich",       "country": "England",  "lat": 52.6309, "lon":  1.2974},

    # ── Scotland (5) ─────────────────────────────────────────
    {"name": "Edinburgh",     "country": "Scotland", "lat": 55.9533, "lon": -3.1883},
    {"name": "Glasgow",       "country": "Scotland", "lat": 55.8642, "lon": -4.2518},
    {"name": "Aberdeen",      "country": "Scotland", "lat": 57.1497, "lon": -2.0943},
    {"name": "Dundee",        "country": "Scotland", "lat": 56.4620, "lon": -2.9707},
    {"name": "Inverness",     "country": "Scotland", "lat": 57.4778, "lon": -4.2247},
]

# Quick lookup: city name (lowered) → full entry
CITY_LOOKUP: dict[str, dict] = {c["name"].lower(): c for c in CITIES}


def find_city(query: str) -> dict | None:
    """Find a city by name (case-insensitive, partial match)."""
    q = query.strip().lower()
    # Exact match first
    if q in CITY_LOOKUP:
        return CITY_LOOKUP[q]
    # Partial match
    for name, city in CITY_LOOKUP.items():
        if q in name or name in q:
            return city
    return None


def all_city_names() -> list[str]:
    """Return all city names."""
    return [c["name"] for c in CITIES]
