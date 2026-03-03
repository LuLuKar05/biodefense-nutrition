#!/usr/bin/env python3
"""Local adaptive meal planner — rewrites meals with biodefense foods.

Takes macro targets + compounds to boost and outputs a meal plan that
features phytochemical-rich foods. Runs entirely on user's device.

Usage:
    python scripts/adapt_meal_plan.py \
        --calories 2200 --protein 220 --carbs 165 --fat 73 \
        --diet standard --allergies "dairy" \
        --boost "Quercetin,EGCG,Sulforaphane"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

SCRIPT_DIR: str = os.path.dirname(os.path.abspath(__file__))
DATA_DIR: str = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
PHYTO_PATH: str = os.path.join(DATA_DIR, "phytochemicals.json")


def load_phytochemicals() -> list[dict[str, Any]]:
    """Load phytochemical database from bundled JSON."""
    with open(PHYTO_PATH, "r", encoding="utf-8") as f:
        data: list[dict[str, Any]] = json.load(f)
    return data


def find_boost_foods(
    compounds: list[str],
    phyto_db: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find food sources for the requested boost compounds."""
    results: list[dict[str, Any]] = []
    compound_lower: list[str] = [c.lower().strip() for c in compounds]

    for entry in phyto_db:
        name: str = entry.get("name", "")
        if name.lower() in compound_lower:
            for source in entry.get("food_sources", []):
                results.append({
                    "compound": name,
                    "food": source.get("food", ""),
                    "serving": source.get("serving", ""),
                    "amount_mg": source.get("amount_mg", 0),
                })
    return results


def build_adapted_plan(
    calories: int,
    protein_g: int,
    carbs_g: int,
    fat_g: int,
    diet: str,
    allergies: list[str],
    boost_foods: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a meal plan skeleton featuring biodefense foods.

    This is a template that the LLM agent will refine.
    """
    # Group boost foods by compound
    by_compound: dict[str, list[dict[str, Any]]] = {}
    for bf in boost_foods:
        compound: str = bf["compound"]
        if compound not in by_compound:
            by_compound[compound] = []
        by_compound[compound].append(bf)

    # Build biodefense food suggestions
    suggestions: list[str] = []
    for compound, foods in by_compound.items():
        food_names: list[str] = [f"{fd['food']} ({fd['serving']})" for fd in foods]
        suggestions.append(f"{compound}: {', '.join(food_names)}")

    plan: dict[str, Any] = {
        "type": "biodefense_adapted",
        "diet": diet,
        "targets": {
            "calories": calories,
            "protein_g": protein_g,
            "carbs_g": carbs_g,
            "fat_g": fat_g,
        },
        "biodefense_foods": suggestions,
        "boost_compounds": list(by_compound.keys()),
        "allergies_excluded": allergies,
        "meals": {
            "breakfast": f"Include biodefense foods: {suggestions[0] if suggestions else 'N/A'}",
            "lunch": f"Feature immune-boosting ingredients from: {', '.join(by_compound.keys())}",
            "dinner": f"Maximize phytochemical intake while hitting {calories} kcal target",
            "snacks": "Add compound-rich snacks (e.g., green tea, berries, raw garlic)",
        },
        "note": "This is a structured template. The LLM agent should compose the full meal plan using these biodefense foods while meeting macro targets.",
    }
    return plan


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Adapt meal plan with biodefense foods locally."
    )
    parser.add_argument("--calories", type=int, required=True, help="Target kcal")
    parser.add_argument("--protein", type=int, required=True, help="Protein grams")
    parser.add_argument("--carbs", type=int, required=True, help="Carbs grams")
    parser.add_argument("--fat", type=int, required=True, help="Fat grams")
    parser.add_argument("--diet", type=str, default="standard", help="Diet type")
    parser.add_argument("--allergies", type=str, default="", help="Comma-separated allergies")
    parser.add_argument("--boost", type=str, required=True, help="Comma-separated compound names to boost")

    args: argparse.Namespace = parser.parse_args()
    allergies: list[str] = [a.strip() for a in args.allergies.split(",") if a.strip()]
    compounds: list[str] = [c.strip() for c in args.boost.split(",") if c.strip()]

    phyto_db: list[dict[str, Any]] = load_phytochemicals()
    boost_foods: list[dict[str, Any]] = find_boost_foods(compounds, phyto_db)

    plan: dict[str, Any] = build_adapted_plan(
        calories=args.calories,
        protein_g=args.protein,
        carbs_g=args.carbs,
        fat_g=args.fat,
        diet=args.diet,
        allergies=allergies,
        boost_foods=boost_foods,
    )

    json.dump(plan, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
