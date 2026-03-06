"""
aqi_fetcher.py — Real AQI data from OpenWeatherMap Air Pollution API
====================================================================
Free tier: 1,000 calls/day (25 cities × 24h = 600 calls/day — fits easily).

If OWM_API_KEY is not set, returns mock AQI data so the system still works.

API docs: https://openweathermap.org/api/air-pollution
"""
from __future__ import annotations

import logging
import os
import random
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)
log = logging.getLogger("aqi_fetcher")

OWM_API_KEY: str = os.getenv("OWM_API_KEY", "").strip()
OWM_AQI_URL = "http://api.openweathermap.org/data/2.5/air_pollution"

# ── AQI Index → human label ────────────────────────────────
AQI_LABELS = {
    1: "Good",
    2: "Fair",
    3: "Moderate",
    4: "Poor",
    5: "Very Poor",
}

# ── AQI Index → threat level ───────────────────────────────
AQI_THREAT = {
    1: "none",
    2: "low",
    3: "moderate",
    4: "high",
    5: "severe",
}


async def fetch_aqi(lat: float, lon: float, city_name: str = "") -> dict[str, Any]:
    """
    Fetch AQI for a location. Returns structured threat data.

    Uses real OpenWeatherMap API if OWM_API_KEY is set,
    otherwise returns realistic mock data.
    """
    if OWM_API_KEY:
        return await _fetch_real(lat, lon, city_name)
    return _generate_mock(city_name)


async def _fetch_real(lat: float, lon: float, city_name: str) -> dict[str, Any]:
    """Call OpenWeatherMap Air Pollution API."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                OWM_AQI_URL,
                params={"lat": lat, "lon": lon, "appid": OWM_API_KEY},
            )

        if resp.status_code != 200:
            log.warning(f"OWM API error for {city_name}: {resp.status_code}")
            return _generate_mock(city_name)

        data = resp.json()
        item = data["list"][0]
        aqi_index = item["main"]["aqi"]  # 1-5 scale
        components = item["components"]  # pm2_5, pm10, no2, so2, co, o3, etc.

        return {
            "type": "air_quality",
            "source": "openweathermap",
            "aqi_index": aqi_index,
            "aqi_label": AQI_LABELS.get(aqi_index, "Unknown"),
            "threat_level": AQI_THREAT.get(aqi_index, "unknown"),
            "components": {
                "pm2_5": round(components.get("pm2_5", 0), 1),
                "pm10": round(components.get("pm10", 0), 1),
                "no2": round(components.get("no2", 0), 1),
                "so2": round(components.get("so2", 0), 1),
                "co": round(components.get("co", 0), 1),
                "o3": round(components.get("o3", 0), 1),
            },
            "is_threat": aqi_index >= 3,
        }

    except Exception as e:
        log.warning(f"AQI fetch failed for {city_name}: {e}")
        return _generate_mock(city_name)


def _generate_mock(city_name: str) -> dict[str, Any]:
    """Generate realistic mock AQI data for demo/fallback."""
    # Big cities tend to have worse air quality
    big_cities = {"london", "birmingham", "manchester", "leeds", "glasgow"}
    name_lower = city_name.lower()

    if name_lower in big_cities:
        # Higher chance of moderate/poor AQI
        aqi_index = random.choices([2, 3, 4, 3], weights=[20, 40, 25, 15])[0]
    else:
        # Smaller cities / Scotland tend to be cleaner
        aqi_index = random.choices([1, 2, 3, 2], weights=[30, 40, 20, 10])[0]

    # Generate plausible component values based on AQI
    base_pm25 = {1: 5, 2: 15, 3: 35, 4: 65, 5: 120}
    pm25 = base_pm25.get(aqi_index, 20) + random.uniform(-3, 3)

    return {
        "type": "air_quality",
        "source": "mock",
        "aqi_index": aqi_index,
        "aqi_label": AQI_LABELS.get(aqi_index, "Unknown"),
        "threat_level": AQI_THREAT.get(aqi_index, "unknown"),
        "components": {
            "pm2_5": round(max(0, pm25), 1),
            "pm10": round(max(0, pm25 * 1.4 + random.uniform(-5, 5)), 1),
            "no2": round(max(0, aqi_index * 12 + random.uniform(-5, 5)), 1),
            "so2": round(max(0, aqi_index * 4 + random.uniform(-2, 2)), 1),
            "co": round(max(0, aqi_index * 80 + random.uniform(-20, 20)), 1),
            "o3": round(max(0, aqi_index * 20 + random.uniform(-10, 10)), 1),
        },
        "is_threat": aqi_index >= 3,
    }
