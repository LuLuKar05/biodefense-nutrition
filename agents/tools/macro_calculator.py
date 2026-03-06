"""
macro_calculator.py — TDEE & macronutrient calculation
======================================================
Mifflin-St Jeor equation with goal-based adjustments.
Used by both Onboarding Agent (initial calc) and Nutrition Agent.
"""
from __future__ import annotations

from typing import Any


# ── Activity multipliers ────────────────────────────────────
ACTIVITY_LEVELS: dict[str, float] = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}

# ── Goal calorie adjustments ───────────────────────────────
GOAL_ADJUSTMENTS: dict[str, int] = {
    "cut": -500,
    "bulk": 400,
    "maintain": 0,
}

# ── Macro splits per goal ──────────────────────────────────
MACRO_SPLITS: dict[str, dict[str, float]] = {
    "cut":      {"protein": 0.40, "carbs": 0.30, "fat": 0.30},
    "bulk":     {"protein": 0.30, "carbs": 0.45, "fat": 0.25},
    "maintain": {"protein": 0.30, "carbs": 0.40, "fat": 0.30},
}


def calculate_bmr(weight_kg: float, height_cm: float, age: int, sex: str) -> float:
    """Mifflin-St Jeor BMR calculation."""
    if sex == "male":
        return 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    else:
        return 10 * weight_kg + 6.25 * height_cm - 5 * age - 161


def calculate_tdee(bmr: float, activity: str = "moderate") -> int:
    """Apply activity multiplier to BMR."""
    multiplier = ACTIVITY_LEVELS.get(activity, ACTIVITY_LEVELS["moderate"])
    return round(bmr * multiplier)


def calculate_macros(profile: dict[str, str], activity: str = "moderate") -> dict[str, Any]:
    """
    Full macro calculation from a profile dict.

    Args:
        profile: Must contain keys: weight, height, age, sex, goal
        activity: Activity level (sedentary, light, moderate, active, very_active)

    Returns:
        Dict with bmr, tdee, target_calories, protein_g, carbs_g, fat_g, split
    """
    weight = float(profile["weight"])
    height = float(profile["height"])
    age = int(profile["age"])
    sex = profile["sex"]
    goal = profile.get("goal", "maintain")

    bmr = calculate_bmr(weight, height, age, sex)
    tdee = calculate_tdee(bmr, activity)
    target = tdee + GOAL_ADJUSTMENTS.get(goal, 0)

    split = MACRO_SPLITS.get(goal, MACRO_SPLITS["maintain"])

    return {
        "bmr": round(bmr),
        "tdee": tdee,
        "target_calories": target,
        "protein_g": round((target * split["protein"]) / 4),
        "carbs_g": round((target * split["carbs"]) / 4),
        "fat_g": round((target * split["fat"]) / 9),
        "split": split,
        "activity_level": activity,
    }


def format_macros(macros: dict[str, Any], name: str = "Your") -> str:
    """Format macro results as a readable string."""
    sp = macros["split"]
    return (
        f"{name}'s Daily Nutrition Targets\n"
        f"============================\n"
        f"BMR: {macros['bmr']} kcal\n"
        f"TDEE: {macros['tdee']} kcal\n"
        f"Target: {macros['target_calories']} kcal/day\n"
        f"----------------------------\n"
        f"Protein: {macros['protein_g']}g ({int(sp['protein']*100)}%)\n"
        f"Carbs: {macros['carbs_g']}g ({int(sp['carbs']*100)}%)\n"
        f"Fat: {macros['fat_g']}g ({int(sp['fat']*100)}%)\n"
        f"============================"
    )
