"""TDEE and macronutrient calculation service."""

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
    """Calculate Total Daily Energy Expenditure using Mifflin-St Jeor.

    Activity multipliers:
        sedentary: 1.2, light: 1.375, moderate: 1.55, active: 1.725
    """
    if gender.lower() == "male":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    else:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161

    multiplier: float = ACTIVITY_MULTIPLIERS.get(activity_level, 1.55)
    return bmr * multiplier


def compute_macros(tdee: float, body_goal: str) -> dict[str, int]:
    """Compute protein/carbs/fat grams from TDEE and goal.

    Splits:
        cut:      40% protein / 30% carbs / 30% fat
        bulk:     30% protein / 45% carbs / 25% fat
        maintain: 30% protein / 40% carbs / 30% fat
    """
    split: tuple[float, float, float] = MACRO_SPLITS.get(
        body_goal, (0.30, 0.40, 0.30)
    )
    p_pct: float = split[0]
    c_pct: float = split[1]
    f_pct: float = split[2]

    result: dict[str, int] = {
        "calories": round(tdee),
        "protein_g": round((tdee * p_pct) / 4),   # 4 cal/g
        "carbs_g": round((tdee * c_pct) / 4),      # 4 cal/g
        "fat_g": round((tdee * f_pct) / 9),         # 9 cal/g
    }
    return result
