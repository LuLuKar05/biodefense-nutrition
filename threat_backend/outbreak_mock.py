"""
outbreak_mock.py — Simulated disease/outbreak threat data
=========================================================
Generates realistic seasonal outbreak data for UK/Scotland cities.
Rotates by month to simulate changing threat landscape.

In production this would pull from WHO, UKHSA, PHE feeds, etc.
For MVP: plausible mock data that demonstrates the concept.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any


# ── Outbreak templates ──────────────────────────────────────
# Each has: name, season months, base severity, regions most affected

OUTBREAK_TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "Seasonal Influenza (H3N2)",
        "pathogen_type": "virus",
        "months": [10, 11, 12, 1, 2, 3],  # Oct–Mar
        "base_severity": "moderate",
        "description": "Seasonal flu activity elevated in the region",
        "regions": ["England", "Scotland"],
        "probability": 0.7,  # chance of being active in a matching month
    },
    {
        "name": "Norovirus Gastroenteritis",
        "pathogen_type": "virus",
        "months": [11, 12, 1, 2, 3],  # Nov–Mar (winter vomiting bug)
        "base_severity": "moderate",
        "description": "Norovirus cases increasing — common in closed settings",
        "regions": ["England", "Scotland"],
        "probability": 0.6,
    },
    {
        "name": "RSV (Respiratory Syncytial Virus)",
        "pathogen_type": "virus",
        "months": [10, 11, 12, 1, 2],
        "base_severity": "low",
        "description": "RSV circulating — primarily affects young children and elderly",
        "regions": ["England", "Scotland"],
        "probability": 0.5,
    },
    {
        "name": "COVID-19 Variant Surveillance",
        "pathogen_type": "virus",
        "months": list(range(1, 13)),  # year-round
        "base_severity": "low",
        "description": "Low-level COVID variant monitoring — new strains under watch",
        "regions": ["England", "Scotland"],
        "probability": 0.4,
    },
    {
        "name": "Group A Streptococcus (iGAS)",
        "pathogen_type": "bacteria",
        "months": [12, 1, 2, 3, 4],
        "base_severity": "moderate",
        "description": "Invasive Group A Strep cases reported in the region",
        "regions": ["England"],
        "probability": 0.3,
    },
    {
        "name": "Hay Fever / Pollen Alert",
        "pathogen_type": "allergen",
        "months": [4, 5, 6, 7],  # Spring–Summer
        "base_severity": "low",
        "description": "High pollen count — grass and tree pollen elevated",
        "regions": ["England", "Scotland"],
        "probability": 0.8,
    },
    {
        "name": "Legionella Risk (Heat Wave)",
        "pathogen_type": "bacteria",
        "months": [6, 7, 8],
        "base_severity": "low",
        "description": "Hot weather increases Legionella risk in water systems",
        "regions": ["England"],
        "probability": 0.2,
    },
    {
        "name": "E. coli O157 (Food Safety)",
        "pathogen_type": "bacteria",
        "months": [5, 6, 7, 8, 9],  # BBQ season
        "base_severity": "low",
        "description": "Seasonal increase in food-borne E. coli — food safety advisory",
        "regions": ["England", "Scotland"],
        "probability": 0.25,
    },
    {
        "name": "Measles Cluster",
        "pathogen_type": "virus",
        "months": list(range(1, 13)),
        "base_severity": "moderate",
        "description": "Measles cases in under-vaccinated communities",
        "regions": ["England"],
        "probability": 0.15,
        "urban_only": True,  # only in big cities
    },
    {
        "name": "Mpox Surveillance",
        "pathogen_type": "virus",
        "months": list(range(1, 13)),
        "base_severity": "low",
        "description": "Mpox under routine surveillance — low community transmission",
        "regions": ["England", "Scotland"],
        "probability": 0.1,
    },
]

# Big urban centres (for urban_only outbreaks)
BIG_CITIES = {"London", "Birmingham", "Manchester", "Leeds", "Liverpool",
              "Glasgow", "Edinburgh", "Sheffield", "Bristol", "Newcastle"}

SEVERITY_LEVELS = {"none": 0, "low": 1, "moderate": 2, "high": 3, "severe": 4}


def generate_outbreaks(city_name: str, country: str) -> list[dict[str, Any]]:
    """
    Generate plausible outbreak data for a city right now.

    Uses current month, city size, and region to determine which
    outbreaks are "active."  Deterministic seed based on city+date
    so the same city returns consistent results within a day.
    """
    now = datetime.now(timezone.utc)
    month = now.month
    day_seed = now.strftime("%Y-%m-%d") + city_name

    # Seed for reproducibility within a day (same city = same threats)
    rng = random.Random(day_seed)

    active: list[dict[str, Any]] = []

    for template in OUTBREAK_TEMPLATES:
        # Check month
        if month not in template["months"]:
            continue

        # Check region
        if country not in template["regions"]:
            continue

        # Check urban restriction
        if template.get("urban_only") and city_name not in BIG_CITIES:
            continue

        # Probability check
        if rng.random() > template["probability"]:
            continue

        # Slight severity variation
        base = SEVERITY_LEVELS.get(template["base_severity"], 1)
        # Big cities get +1 severity bump sometimes
        if city_name in BIG_CITIES and rng.random() < 0.3:
            severity_val = min(base + 1, 4)
        else:
            severity_val = base

        severity_label = {v: k for k, v in SEVERITY_LEVELS.items()}.get(severity_val, "low")

        active.append({
            "type": "outbreak",
            "source": "mock_ukhsa",
            "name": template["name"],
            "pathogen_type": template["pathogen_type"],
            "severity": severity_label,
            "description": template["description"],
            "is_threat": severity_val >= 2,
        })

    return active
