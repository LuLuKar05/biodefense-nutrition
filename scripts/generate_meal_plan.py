#!/usr/bin/env python3
"""Local meal plan generator — runs entirely on user's device.

Selects meals from data/meal_templates.json that best fit the user's
macro targets, diet type, and allergy constraints. No network calls.

Usage:
    python scripts/generate_meal_plan.py \
        --diet standard --calories 2200 --protein 220 \
        --carbs 165 --fat 73 --allergies "dairy,gluten"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

SCRIPT_DIR: str = os.path.dirname(os.path.abspath(__file__))
DATA_DIR: str = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
TEMPLATES_PATH: str = os.path.join(DATA_DIR, "meal_templates.json")


def load_templates() -> dict[str, Any]:
    """Load meal templates from the bundled JSON."""
    with open(TEMPLATES_PATH, "r", encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return data.get("templates", {})


def has_allergen(food_name: str, allergies: list[str]) -> bool:
    """Naive allergen check — matches allergy keywords against food name."""
    name_lower: str = food_name.lower()
    allergen_keywords: dict[str, list[str]] = {
        "dairy": ["cheese", "yogurt", "milk", "butter", "cream"],
        "gluten": ["bread", "toast", "wrap", "oat", "pasta"],
        "nuts": ["nut", "peanut", "almond", "macadamia", "walnut"],
        "eggs": ["egg", "omelette", "scrambled"],
        "soy": ["tofu", "soy", "edamame"],
        "fish": ["salmon", "tuna", "fish"],
        "shellfish": ["shrimp", "crab", "lobster"],
    }
    for allergy in allergies:
        keywords: list[str] = allergen_keywords.get(allergy.lower(), [allergy.lower()])
        for keyword in keywords:
            if keyword in name_lower:
                return True
    return False


def pick_best(
    options: list[dict[str, Any]],
    target_cals: float,
    allergies: list[str],
) -> dict[str, Any] | None:
    """Pick the option closest to target calories that passes allergy filter."""
    safe: list[dict[str, Any]] = [
        o for o in options if not has_allergen(o.get("name", ""), allergies)
    ]
    if not safe:
        return None
    safe.sort(key=lambda o: abs(o.get("calories", 0) - target_cals))
    return safe[0]


def generate_plan(
    diet: str,
    calories: int,
    protein_g: int,
    carbs_g: int,
    fat_g: int,
    allergies: list[str],
) -> dict[str, Any]:
    """Generate a day's meal plan from templates."""
    templates: dict[str, Any] = load_templates()

    # Fallback to standard if diet not found
    diet_key: str = diet if diet in templates else "standard"
    diet_meals: dict[str, Any] = templates[diet_key]

    # Rough calorie split: 25% breakfast, 35% lunch, 30% dinner, 10% snacks
    splits: dict[str, float] = {
        "breakfast": 0.25,
        "lunch": 0.35,
        "dinner": 0.30,
        "snacks": 0.10,
    }

    plan: dict[str, Any] = {}
    total_cals: int = 0
    total_p: int = 0
    total_c: int = 0
    total_f: int = 0

    for meal_type, frac in splits.items():
        target: float = calories * frac
        options: list[dict[str, Any]] = diet_meals.get(meal_type, [])
        chosen: dict[str, Any] | None = pick_best(options, target, allergies)
        if chosen is not None:
            plan[meal_type] = chosen
            total_cals += chosen.get("calories", 0)
            total_p += chosen.get("protein_g", 0)
            total_c += chosen.get("carbs_g", 0)
            total_f += chosen.get("fat_g", 0)
        else:
            plan[meal_type] = {"name": f"No suitable {meal_type} (check allergies)", "calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}

    plan["daily_totals"] = {
        "calories": total_cals,
        "protein_g": total_p,
        "carbs_g": total_c,
        "fat_g": total_f,
    }
    plan["targets"] = {
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
    }
    plan["diet_type"] = diet_key
    plan["note"] = "Template-based plan. The LLM agent can refine this to better hit macro targets."

    return plan


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Generate a meal plan locally from templates."
    )
    parser.add_argument("--diet", type=str, default="standard", help="Diet type")
    parser.add_argument("--calories", type=int, required=True, help="Target kcal")
    parser.add_argument("--protein", type=int, required=True, help="Protein grams")
    parser.add_argument("--carbs", type=int, required=True, help="Carbs grams")
    parser.add_argument("--fat", type=int, required=True, help="Fat grams")
    parser.add_argument("--allergies", type=str, default="", help="Comma-separated allergies")

    args: argparse.Namespace = parser.parse_args()
    allergies: list[str] = [a.strip() for a in args.allergies.split(",") if a.strip()]

    plan: dict[str, Any] = generate_plan(
        diet=args.diet,
        calories=args.calories,
        protein_g=args.protein,
        carbs_g=args.carbs,
        fat_g=args.fat,
        allergies=allergies,
    )

    json.dump(plan, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
