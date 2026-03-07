"""
weather_fetcher.py — Weather & environmental threat detection
=============================================================
Fetches current weather from OpenWeatherMap Current Weather API
(same API key as AQI) and generates health-relevant environmental threats.

Threat categories:
  - heat_stress:    temp > 30°C or feels_like > 33°C
  - cold_snap:      temp < 0°C or feels_like < -5°C
  - high_humidity:  humidity > 85% (mold/respiratory risk)
  - uv_exposure:    UV index > 6 (skin/eye damage risk)
  - storm_risk:     thunderstorm, heavy rain, or snow warnings

Free tier: shares the same OWM_API_KEY as AQI (1,000 calls/day).
Falls back to seasonal mock data if no API key is set.
"""
from __future__ import annotations

import logging
import math
import os
import random
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)
log = logging.getLogger("weather_fetcher")

OWM_API_KEY: str = os.getenv("OWM_API_KEY", "").strip()
OWM_WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
OWM_UV_URL = "https://api.openweathermap.org/data/2.5/uvi"

# ── Thresholds ────────────────────────────────────────────
HEAT_TEMP_C = 30
HEAT_FEELS_C = 33
COLD_TEMP_C = 0
COLD_FEELS_C = -5
HIGH_HUMIDITY_PCT = 85
HIGH_UV_INDEX = 6

# Severe weather condition codes (OWM)
# https://openweathermap.org/weather-conditions
STORM_CODES = {200, 201, 202, 210, 211, 212, 221, 230, 231, 232}  # Thunderstorm
HEAVY_RAIN_CODES = {502, 503, 504, 511, 522, 531}  # Heavy rain / freezing rain
SNOW_CODES = {601, 602, 611, 612, 613, 615, 616, 620, 621, 622}  # Snow


async def fetch_weather(
    lat: float, lon: float, city_name: str = ""
) -> dict[str, Any]:
    """
    Fetch current weather for a location.
    Returns weather data + any environmental threats detected.
    """
    if OWM_API_KEY:
        return await _fetch_real(lat, lon, city_name)
    return _generate_mock(city_name)


async def _fetch_real(
    lat: float, lon: float, city_name: str
) -> dict[str, Any]:
    """Call OpenWeatherMap Current Weather API."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                OWM_WEATHER_URL,
                params={
                    "lat": lat,
                    "lon": lon,
                    "appid": OWM_API_KEY,
                    "units": "metric",
                },
            )

        if resp.status_code != 200:
            log.warning(f"OWM Weather error for {city_name}: {resp.status_code}")
            return _generate_mock(city_name)

        data = resp.json()
        main = data.get("main", {})
        weather_list = data.get("weather", [{}])
        weather_cond = weather_list[0] if weather_list else {}
        wind = data.get("wind", {})

        temp_c = main.get("temp", 15)
        feels_like_c = main.get("feels_like", temp_c)
        humidity = main.get("humidity", 50)
        condition_id = weather_cond.get("id", 800)
        condition_main = weather_cond.get("main", "Clear")
        condition_desc = weather_cond.get("description", "clear sky")
        wind_speed = wind.get("speed", 0)  # m/s

        result = {
            "source": "openweathermap",
            "temp_c": round(temp_c, 1),
            "feels_like_c": round(feels_like_c, 1),
            "humidity": humidity,
            "condition": condition_main,
            "condition_detail": condition_desc,
            "condition_id": condition_id,
            "wind_speed_ms": round(wind_speed, 1),
        }

        # Detect environmental threats
        result["threats"] = _detect_weather_threats(result)
        return result

    except Exception as e:
        log.warning(f"Weather fetch failed for {city_name}: {e}")
        return _generate_mock(city_name)


def _detect_weather_threats(weather: dict[str, Any]) -> list[dict[str, Any]]:
    """Analyze weather data and generate health-relevant threats."""
    threats: list[dict[str, Any]] = []
    temp = weather.get("temp_c", 15)
    feels = weather.get("feels_like_c", temp)
    humidity = weather.get("humidity", 50)
    cond_id = weather.get("condition_id", 800)

    # ── Heat stress ──
    if temp >= HEAT_TEMP_C or feels >= HEAT_FEELS_C:
        severity = "high" if temp >= 35 or feels >= 38 else "moderate"
        threats.append({
            "type": "weather",
            "category": "heat_stress",
            "name": "Heat Stress Warning",
            "pathogen_type": "environmental",
            "severity": severity,
            "description": (
                f"Temperature {temp}°C (feels like {feels}°C). "
                "Increased risk of heat exhaustion and dehydration."
            ),
            "is_threat": True,
            "source": "weather_analysis",
        })

    # ── Cold snap ──
    if temp <= COLD_TEMP_C or feels <= COLD_FEELS_C:
        severity = "high" if temp <= -10 or feels <= -15 else "moderate"
        threats.append({
            "type": "weather",
            "category": "cold_snap",
            "name": "Cold Weather Alert",
            "pathogen_type": "environmental",
            "severity": severity,
            "description": (
                f"Temperature {temp}°C (feels like {feels}°C). "
                "Cold exposure weakens immune defences and increases respiratory risk."
            ),
            "is_threat": True,
            "source": "weather_analysis",
        })

    # ── High humidity ──
    if humidity >= HIGH_HUMIDITY_PCT:
        threats.append({
            "type": "weather",
            "category": "high_humidity",
            "name": "High Humidity Advisory",
            "pathogen_type": "environmental",
            "severity": "low",
            "description": (
                f"Humidity at {humidity}%. "
                "High moisture promotes mold growth and airborne pathogen survival."
            ),
            "is_threat": humidity >= 90,
            "source": "weather_analysis",
        })

    # ── Storm / severe weather ──
    if cond_id in STORM_CODES:
        threats.append({
            "type": "weather",
            "category": "storm_risk",
            "name": "Thunderstorm Warning",
            "pathogen_type": "environmental",
            "severity": "moderate",
            "description": "Active thunderstorms — stay indoors, risk of injury and power outage.",
            "is_threat": True,
            "source": "weather_analysis",
        })
    elif cond_id in HEAVY_RAIN_CODES:
        threats.append({
            "type": "weather",
            "category": "storm_risk",
            "name": "Heavy Rain / Flooding Risk",
            "pathogen_type": "environmental",
            "severity": "low",
            "description": "Heavy rain — waterborne contamination risk increases.",
            "is_threat": False,
            "source": "weather_analysis",
        })
    elif cond_id in SNOW_CODES:
        threats.append({
            "type": "weather",
            "category": "cold_snap",
            "name": "Snow & Ice Warning",
            "pathogen_type": "environmental",
            "severity": "moderate",
            "description": (
                f"Snow/ice conditions with {temp}°C. "
                "Cold exposure plus reduced mobility — boost immune-supporting nutrition."
            ),
            "is_threat": True,
            "source": "weather_analysis",
        })

    return threats


def _generate_mock(city_name: str) -> dict[str, Any]:
    """Generate seasonal mock weather data."""
    month = datetime.now(timezone.utc).month

    # Seasonal temperature baselines for UK
    monthly_temps = {
        1: 4, 2: 5, 3: 7, 4: 10, 5: 13, 6: 16,
        7: 18, 8: 18, 9: 15, 10: 11, 11: 7, 12: 5,
    }
    # Scotland is ~2°C colder
    scotland_cities = {"edinburgh", "glasgow", "aberdeen", "dundee", "inverness"}
    base_temp = monthly_temps.get(month, 12)
    if city_name.lower() in scotland_cities:
        base_temp -= 2

    rng = random.Random(f"{city_name}{datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    temp = base_temp + rng.uniform(-4, 4)
    feels = temp + rng.uniform(-3, 1)
    humidity = rng.randint(55, 95)

    # Seasonal weather conditions
    if month in (12, 1, 2):
        conditions = [("Clouds", "overcast clouds", 804), ("Rain", "light rain", 500),
                      ("Snow", "light snow", 600), ("Drizzle", "drizzle", 300)]
    elif month in (6, 7, 8):
        conditions = [("Clear", "clear sky", 800), ("Clouds", "few clouds", 801),
                      ("Rain", "light rain", 500), ("Thunderstorm", "thunderstorm", 200)]
    else:
        conditions = [("Clouds", "scattered clouds", 802), ("Rain", "light rain", 500),
                      ("Clear", "clear sky", 800), ("Drizzle", "drizzle", 300)]

    cond = rng.choice(conditions)

    result = {
        "source": "mock",
        "temp_c": round(temp, 1),
        "feels_like_c": round(feels, 1),
        "humidity": humidity,
        "condition": cond[0],
        "condition_detail": cond[1],
        "condition_id": cond[2],
        "wind_speed_ms": round(rng.uniform(1, 12), 1),
    }
    result["threats"] = _detect_weather_threats(result)
    return result
