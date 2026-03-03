#!/usr/bin/env python3
"""Local TDEE and macronutrient calculator — runs entirely on user's device.

Uses the Mifflin-St Jeor equation. No network calls, no data leaves this machine.

Usage:
    python scripts/calculate_macros.py \
        --weight 80 --height 178 --age 30 --gender male \
        --activity moderate --goal cut
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

ACTIVITY_MULTIPLIERS: dict[str, float] = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
}

MACRO_SPLITS: dict[str, tuple[float, float, float]] = {
    "cut": (0.40, 0.30, 0.30),
    "bulk": (0.30, 0.45, 0.25),
    "maintain": (0.30, 0.40, 0.30),
}


def calculate_tdee(
    weight_kg: float,
    height_cm: float,
    age: int,
    gender: str,
    activity_level: str = "moderate",
) -> float:
    """Calculate Total Daily Energy Expenditure using Mifflin-St Jeor."""
    if gender.lower() == "male":
        bmr: float = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    else:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161

    multiplier: float = ACTIVITY_MULTIPLIERS.get(activity_level, 1.55)
    return bmr * multiplier


def compute_macros(tdee: float, body_goal: str) -> dict[str, int]:
    """Compute protein/carbs/fat grams from TDEE and goal."""
    split: tuple[float, float, float] = MACRO_SPLITS.get(
        body_goal, (0.30, 0.40, 0.30)
    )
    p_pct: float = split[0]
    c_pct: float = split[1]
    f_pct: float = split[2]

    return {
        "tdee": round(tdee),
        "protein_g": round((tdee * p_pct) / 4),
        "carbs_g": round((tdee * c_pct) / 4),
        "fat_g": round((tdee * f_pct) / 9),
    }


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Calculate TDEE and macros locally (Mifflin-St Jeor)."
    )
    parser.add_argument("--weight", type=float, required=True, help="Weight in kg")
    parser.add_argument("--height", type=float, required=True, help="Height in cm")
    parser.add_argument("--age", type=int, required=True, help="Age in years")
    parser.add_argument(
        "--gender",
        type=str,
        required=True,
        choices=["male", "female", "other"],
        help="Gender",
    )
    parser.add_argument(
        "--activity",
        type=str,
        default="moderate",
        choices=["sedentary", "light", "moderate", "active"],
        help="Activity level",
    )
    parser.add_argument(
        "--goal",
        type=str,
        default="maintain",
        choices=["cut", "bulk", "maintain"],
        help="Body goal",
    )

    args: argparse.Namespace = parser.parse_args()

    tdee: float = calculate_tdee(
        weight_kg=args.weight,
        height_cm=args.height,
        age=args.age,
        gender=args.gender,
        activity_level=args.activity,
    )

    result: dict[str, int] = compute_macros(tdee, args.goal)

    # Output JSON to stdout for OpenClaw agent to parse
    output: dict[str, Any] = {
        "tdee": result["tdee"],
        "protein_g": result["protein_g"],
        "carbs_g": result["carbs_g"],
        "fat_g": result["fat_g"],
        "goal": args.goal,
        "activity": args.activity,
    }
    json.dump(output, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
