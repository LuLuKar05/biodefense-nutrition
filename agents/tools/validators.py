"""
validators.py — Field validation for user health profiles
==========================================================
Each validator returns (is_valid, cleaned_value, error_message).
Used as safety-net checks after FLock API extraction and
as the sole validation layer in step-by-step fallback mode.
"""
from __future__ import annotations

import re


# ── Individual field validators ─────────────────────────────

def validate_name(text: str) -> tuple[bool, str, str]:
    name = text.strip()
    if len(name) < 2:
        return False, "", "Name must be at least 2 characters."
    if len(name) > 50:
        return False, "", "Name must be under 50 characters."
    if not re.match(r"^[a-zA-Z\s\-'.]+$", name):
        return False, "", "Name should only contain letters, spaces, hyphens, or apostrophes."
    return True, name.title(), ""


def validate_age(text: str) -> tuple[bool, str, str]:
    cleaned = text.strip().lower()
    for word in ("years", "year", "old", "yrs", "yo"):
        cleaned = cleaned.replace(word, "")
    cleaned = cleaned.strip()
    try:
        age = int(cleaned)
    except ValueError:
        return False, "", "Please enter a valid number for age (e.g., 25)."
    if age < 13:
        return False, "", "You must be at least 13 years old."
    if age > 120:
        return False, "", "Please enter a realistic age (13-120)."
    return True, str(age), ""


def validate_sex(text: str) -> tuple[bool, str, str]:
    lower = text.strip().lower()
    if lower in ("m", "male", "man", "boy"):
        return True, "male", ""
    if lower in ("f", "female", "woman", "girl"):
        return True, "female", ""
    return False, "", "Please specify male or female."


def validate_weight(text: str) -> tuple[bool, str, str]:
    cleaned = text.strip().lower()
    for unit in ("kg", "kgs", "kilos", "kilogram", "kilograms"):
        cleaned = cleaned.replace(unit, "")
    cleaned = cleaned.strip()
    try:
        weight = float(cleaned)
    except ValueError:
        return False, "", "Please enter a valid number for weight in kg (e.g., 75)."
    if weight < 20 or weight > 300:
        return False, "", "Weight must be between 20-300 kg."
    return True, str(round(weight, 1)), ""


def validate_height(text: str) -> tuple[bool, str, str]:
    cleaned = text.strip().lower()
    for unit in ("cm", "centimeters", "centimeter"):
        cleaned = cleaned.replace(unit, "")
    cleaned = cleaned.strip()
    try:
        height = float(cleaned)
    except ValueError:
        return False, "", "Please enter a valid number for height in cm (e.g., 175)."
    if height < 100 or height > 250:
        return False, "", "Height must be between 100-250 cm."
    return True, str(round(height, 1)), ""


def validate_allergies(text: str) -> tuple[bool, str, str]:
    lower = text.strip().lower()
    if lower in ("none", "no", "nope", "n/a", "na", "nothing", "-"):
        return True, "none", ""
    allergies = [a.strip() for a in text.split(",") if a.strip()]
    if not allergies:
        return False, "", "Please list allergies separated by commas, or type 'none'."
    return True, ", ".join(allergies), ""


def validate_diet(text: str) -> tuple[bool, str, str]:
    lower = text.strip().lower()
    diet_map = {
        "mediterranean": "mediterranean", "med": "mediterranean",
        "keto": "keto", "ketogenic": "keto",
        "vegan": "vegan", "plant": "vegan", "plant-based": "vegan",
        "standard": "standard", "normal": "standard",
        "regular": "standard", "balanced": "standard",
    }
    if lower in diet_map:
        return True, diet_map[lower], ""
    return False, "", "Please choose: mediterranean, keto, vegan, or standard."


def validate_goal(text: str) -> tuple[bool, str, str]:
    lower = text.strip().lower()
    goal_map = {
        "cut": "cut", "lose": "cut", "lose weight": "cut",
        "weight loss": "cut", "slim": "cut",
        "bulk": "bulk", "gain": "bulk", "gain weight": "bulk",
        "muscle": "bulk", "build": "bulk",
        "maintain": "maintain", "maintenance": "maintain",
        "keep": "maintain", "stay": "maintain",
    }
    if lower in goal_map:
        return True, goal_map[lower], ""
    return False, "", "Please choose: cut (lose weight), bulk (gain muscle), or maintain."


def validate_city(text: str) -> tuple[bool, str, str]:
    city = text.strip()
    if len(city) < 2:
        return False, "", "City name must be at least 2 characters."
    if len(city) > 100:
        return False, "", "City name too long."
    if not re.match(r"^[a-zA-Z\s\-'.]+$", city):
        return False, "", "City name should only contain letters, spaces, and hyphens."
    return True, city.title(), ""


# ── Validator registry ──────────────────────────────────────

FIELD_VALIDATORS: dict[str, callable] = {
    "name": validate_name,
    "age": validate_age,
    "sex": validate_sex,
    "weight": validate_weight,
    "height": validate_height,
    "allergies": validate_allergies,
    "diet": validate_diet,
    "goal": validate_goal,
    "city": validate_city,
}

REQUIRED_FIELDS: list[str] = list(FIELD_VALIDATORS.keys())


def validate_field(field: str, value: str) -> tuple[bool, str, str]:
    """Validate a single field by name. Returns (is_valid, cleaned, error)."""
    validator = FIELD_VALIDATORS.get(field)
    if validator is None:
        return False, "", f"Unknown field: {field}"
    return validator(value)


def validate_profile(profile: dict[str, str]) -> tuple[bool, dict[str, str], list[str]]:
    """
    Validate all fields in a profile dict.
    Returns (all_valid, cleaned_profile, list_of_errors).
    """
    cleaned: dict[str, str] = {}
    errors: list[str] = []
    for field in REQUIRED_FIELDS:
        value = profile.get(field, "")
        if not value:
            errors.append(f"Missing: {field}")
            continue
        ok, clean, err = validate_field(field, value)
        if ok:
            cleaned[field] = clean
        else:
            errors.append(f"{field}: {err}")
    return len(errors) == 0, cleaned, errors


def missing_fields(profile: dict[str, str]) -> list[str]:
    """Return list of required fields not yet in profile."""
    return [f for f in REQUIRED_FIELDS if not profile.get(f)]
