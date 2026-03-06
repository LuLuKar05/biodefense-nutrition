"""
meal_planner.py — FLock-powered meal plan generation
=====================================================
Generates one-day meal plans via FLock LLM, respecting:
  - User macros (from profile)
  - Diet preferences and allergies
  - Previously rejected meals (today)
  - Recent meal history (7-day dedup window)

Returns structured plan: {meals: {breakfast, lunch, dinner, snacks}, totals}
Includes fallback: template-based plan when FLock is down.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from dotenv import load_dotenv

from agents.tools.circuit_breaker import CircuitBreaker
from agents.tools.macro_calculator import calculate_macros
from agents.tools.meal_manager import (
    get_rejected_meal_names,
    get_recent_meal_names,
    get_today_consumed,
)

log = logging.getLogger("meal_planner")

# ── Config ──────────────────────────────────────────────────
load_dotenv(override=True)

FLOCK_API_KEY: str = os.getenv("FLOCK_API_KEY", "").strip()
FLOCK_BASE_URL: str = os.getenv("FLOCK_BASE_URL", "https://api.flock.io/v1").strip()
FLOCK_MODEL: str = os.getenv("FLOCK_MODEL", "qwen3-30b-a3b-instruct-2507").strip()

# ── Templates (loaded once) ────────────────────────────────
_TEMPLATES: dict[str, Any] | None = None


def _load_templates() -> dict[str, Any]:
    """Load meal_templates.json once."""
    global _TEMPLATES
    if _TEMPLATES is None:
        from pathlib import Path
        tpl_path = Path(__file__).resolve().parent.parent.parent / "data" / "meal_templates.json"
        if tpl_path.exists():
            _TEMPLATES = json.loads(tpl_path.read_text(encoding="utf-8"))
        else:
            _TEMPLATES = {"templates": {}}
    return _TEMPLATES


# ── Circuit Breaker ─────────────────────────────────────────
_plan_cb = CircuitBreaker(name="meal_planner", max_failures=3, cooldown_secs=60.0)


# ═════════════════════════════════════════════════════════════
# MAIN: Generate Meal Plan
# ═════════════════════════════════════════════════════════════

async def generate_plan(
    user_id: str,
    profile: dict[str, Any],
    *,
    regenerate: bool = False,
) -> dict[str, Any]:
    """
    Generate a one-day meal plan for the user.

    Args:
        user_id: User identifier
        profile: Full onboarding profile (must contain weight, height, age, sex, goal, diet, allergies)
        regenerate: If True, avoids rejected meals more aggressively

    Returns:
        Structured plan dict:
        {
          "meals": {"breakfast": [...], "lunch": [...], "dinner": [...], "snacks": [...]},
          "totals": {"calories": N, "protein_g": N, "carbs_g": N, "fat_g": N},
          "source": "flock" | "template"
        }
    """
    macros = calculate_macros(profile)
    diet = profile.get("diet", "standard")
    allergies = profile.get("allergies", "none")

    # Collect avoidance lists
    rejected_names = get_rejected_meal_names(user_id) if regenerate else []
    recent_names = get_recent_meal_names(user_id, days=7)
    avoid_list = list(set(rejected_names + recent_names))

    # Already consumed today (for partial-day plans)
    consumed = get_today_consumed(user_id)

    # Try FLock first, fall back to templates
    plan = None
    if FLOCK_API_KEY and _plan_cb.should_call():
        plan = await _generate_flock_plan(
            macros=macros,
            diet=diet,
            allergies=allergies,
            avoid_list=avoid_list,
            consumed=consumed,
        )

    if plan is None:
        plan = _generate_template_plan(macros, diet, avoid_list)

    return plan


# ═════════════════════════════════════════════════════════════
# FLOCK-POWERED GENERATION
# ═════════════════════════════════════════════════════════════

async def _generate_flock_plan(
    *,
    macros: dict[str, Any],
    diet: str,
    allergies: str,
    avoid_list: list[str],
    consumed: dict[str, float],
) -> dict[str, Any] | None:
    """Call FLock to generate a meal plan. Returns structured plan or None."""

    avoid_str = ", ".join(avoid_list[:30]) if avoid_list else "none"
    consumed_str = (
        f"Already consumed today: {consumed['calories']:.0f} cal, "
        f"{consumed['protein_g']:.0f}g protein, {consumed['carbs_g']:.0f}g carbs, "
        f"{consumed['fat_g']:.0f}g fat"
        if consumed["calories"] > 0
        else "No meals logged yet today"
    )

    system_prompt = f"""You are a precision nutrition planner. Generate a ONE-DAY meal plan.

TARGET MACROS:
  Calories: {macros['target_calories']} kcal
  Protein: {macros['protein_g']}g
  Carbs: {macros['carbs_g']}g
  Fat: {macros['fat_g']}g

DIET TYPE: {diet}
ALLERGIES/RESTRICTIONS: {allergies}
{consumed_str}

MEALS TO AVOID (already eaten recently or rejected):
{avoid_str}

RULES:
1. Return ONLY valid JSON — no markdown, no explanation.
2. JSON format:
{{
  "meals": {{
    "breakfast": [{{"name": "...", "protein_g": N, "carbs_g": N, "fat_g": N, "calories": N}}],
    "lunch": [{{"name": "...", "protein_g": N, "carbs_g": N, "fat_g": N, "calories": N}}],
    "dinner": [{{"name": "...", "protein_g": N, "carbs_g": N, "fat_g": N, "calories": N}}],
    "snacks": [{{"name": "...", "protein_g": N, "carbs_g": N, "fat_g": N, "calories": N}}]
  }}
}}
3. Each meal slot has 1-2 items.
4. Total macros should closely match the targets (±10%).
5. Use realistic, common food items with accurate macro estimates.
6. DO NOT use any meals from the avoid list.
7. Make meals practical and appetizing.
8. Adjust portions if the user already ate today to hit remaining targets.
9. Do NOT wrap in markdown code fences. Raw JSON only."""

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{FLOCK_BASE_URL}/chat/completions",
                headers={
                    "x-litellm-api-key": FLOCK_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "model": FLOCK_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": "Generate my meal plan for today."},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 1200,
                },
            )

        if resp.status_code != 200:
            log.warning(f"FLock plan API error: {resp.status_code} — {resp.text[:200]}")
            _plan_cb.record_failure()
            return None

        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        # Strip <think> tags from Qwen3
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

        # Strip markdown fences if present
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        plan = json.loads(content)
        _plan_cb.record_success()

        # Calculate totals
        plan["totals"] = _calc_totals(plan.get("meals", {}))
        plan["source"] = "flock"

        log.info(f"FLock generated plan: {plan['totals']['calories']} kcal")
        return plan

    except json.JSONDecodeError as e:
        log.warning(f"FLock returned invalid JSON: {e}")
        _plan_cb.record_failure()
        return None
    except Exception as e:
        log.warning(f"FLock plan request failed: {e}")
        _plan_cb.record_failure()
        return None


# ═════════════════════════════════════════════════════════════
# TEMPLATE FALLBACK
# ═════════════════════════════════════════════════════════════

def _generate_template_plan(
    macros: dict[str, Any],
    diet: str,
    avoid_list: list[str],
) -> dict[str, Any]:
    """Generate a plan from local templates when FLock is unavailable."""
    templates = _load_templates().get("templates", {})

    # Fall back to standard if diet not in templates
    diet_meals = templates.get(diet, templates.get("standard", {}))
    avoid_lower = {n.lower() for n in avoid_list}

    plan_meals: dict[str, list[dict]] = {}
    for meal_type in ("breakfast", "lunch", "dinner", "snacks"):
        options = diet_meals.get(meal_type, [])
        # Pick first option not in avoid list
        chosen = None
        for opt in options:
            if opt.get("name", "").lower() not in avoid_lower:
                chosen = opt
                break
        if chosen is None and options:
            chosen = options[0]  # fallback to first if all avoided
        plan_meals[meal_type] = [chosen] if chosen else []

    # Scale to match target calories approximately
    raw_totals = _calc_totals(plan_meals)
    raw_cal = raw_totals.get("calories", 1) or 1
    target_cal = macros.get("target_calories", raw_cal)
    scale = target_cal / raw_cal

    if abs(scale - 1.0) > 0.05:
        for meal_type, items in plan_meals.items():
            for item in items:
                item["protein_g"] = round(item.get("protein_g", 0) * scale)
                item["carbs_g"] = round(item.get("carbs_g", 0) * scale)
                item["fat_g"] = round(item.get("fat_g", 0) * scale)
                item["calories"] = round(item.get("calories", 0) * scale)
                item["name"] = item.get("name", "Meal") + " (adjusted)"

    return {
        "meals": plan_meals,
        "totals": _calc_totals(plan_meals),
        "source": "template",
    }


# ═════════════════════════════════════════════════════════════
# ESTIMATE MACROS FROM TEXT (for meal logging)
# ═════════════════════════════════════════════════════════════

async def estimate_meal_macros(description: str) -> dict[str, Any]:
    """
    Use FLock to estimate macros from a text description of what the user ate.
    Falls back to a rough estimate on failure.

    Args:
        description: User's text description (e.g., "grilled chicken with rice and salad")

    Returns:
        {"calories": N, "protein_g": N, "carbs_g": N, "fat_g": N, "confidence": "high"|"low"}
    """
    if FLOCK_API_KEY and _plan_cb.should_call():
        estimated = await _estimate_flock(description)
        if estimated:
            return estimated

    # Rough fallback estimate
    return _estimate_rough(description)


async def _estimate_flock(description: str) -> dict[str, Any] | None:
    """Ask FLock to estimate macros for a meal description."""
    system_prompt = """You are a nutrition estimation tool.
Given a meal description, estimate its macronutrients.
Return ONLY valid JSON:
{"calories": N, "protein_g": N, "carbs_g": N, "fat_g": N}
Be realistic. If unsure, estimate conservatively. No markdown, just JSON."""

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{FLOCK_BASE_URL}/chat/completions",
                headers={
                    "x-litellm-api-key": FLOCK_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "model": FLOCK_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Estimate macros for: {description}"},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 200,
                },
            )

        if resp.status_code != 200:
            _plan_cb.record_failure()
            return None

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        result = json.loads(content)
        result["confidence"] = "high"
        _plan_cb.record_success()
        return result

    except Exception as e:
        log.warning(f"FLock macro estimation failed: {e}")
        _plan_cb.record_failure()
        return None


def _estimate_rough(description: str) -> dict[str, Any]:
    """Very rough keyword-based macro estimate as last resort."""
    desc = description.lower()

    # Base estimate: a moderate meal
    cal, prot, carbs, fat = 400, 20, 40, 15

    # Protein-heavy keywords
    protein_words = ["chicken", "fish", "salmon", "tuna", "beef", "steak", "egg",
                     "tofu", "turkey", "shrimp", "pork", "protein"]
    # Carb-heavy keywords
    carb_words = ["rice", "bread", "pasta", "noodle", "potato", "oat", "cereal",
                  "tortilla", "wrap", "sandwich"]
    # Fat-heavy keywords
    fat_words = ["butter", "cheese", "avocado", "nut", "oil", "cream", "bacon",
                 "fried"]
    # Light keywords
    light_words = ["salad", "soup", "fruit", "yogurt", "smoothie", "snack"]

    protein_hits = sum(1 for w in protein_words if w in desc)
    carb_hits = sum(1 for w in carb_words if w in desc)
    fat_hits = sum(1 for w in fat_words if w in desc)
    light_hits = sum(1 for w in light_words if w in desc)

    if protein_hits:
        prot += 15 * protein_hits
        cal += 60 * protein_hits
    if carb_hits:
        carbs += 20 * carb_hits
        cal += 80 * carb_hits
    if fat_hits:
        fat += 10 * fat_hits
        cal += 90 * fat_hits
    if light_hits:
        cal = max(200, cal - 100 * light_hits)
        carbs = max(10, carbs - 10 * light_hits)

    return {
        "calories": cal,
        "protein_g": prot,
        "carbs_g": carbs,
        "fat_g": fat,
        "confidence": "low",
    }


# ═════════════════════════════════════════════════════════════
# FORMAT PLAN FOR DISPLAY
# ═════════════════════════════════════════════════════════════

def format_plan(plan: dict[str, Any]) -> str:
    """Format a meal plan for Telegram display."""
    meals = plan.get("meals", {})
    totals = plan.get("totals", {})
    source = plan.get("source", "unknown")

    lines = ["🍽 Your Meal Plan for Today", ""]

    for meal_type, emoji in [("breakfast", "🌅"), ("lunch", "☀️"), ("dinner", "🌙"), ("snacks", "🍎")]:
        items = meals.get(meal_type, [])
        if not items:
            continue
        lines.append(f"{emoji} {meal_type.capitalize()}")
        for item in items:
            name = item.get("name", "Unknown")
            cal = item.get("calories", 0)
            p = item.get("protein_g", 0)
            c = item.get("carbs_g", 0)
            f = item.get("fat_g", 0)
            lines.append(f"  • {name}")
            lines.append(f"    {cal} cal | P:{p}g C:{c}g F:{f}g")
        lines.append("")

    lines.append("📊 Daily Totals")
    lines.append(f"  Calories: {totals.get('calories', 0)}")
    lines.append(f"  Protein: {totals.get('protein_g', 0)}g")
    lines.append(f"  Carbs: {totals.get('carbs_g', 0)}g")
    lines.append(f"  Fat: {totals.get('fat_g', 0)}g")

    if source == "template":
        lines.append("\n⚠️ Generated from templates (AI was unavailable)")

    lines.append("\n✅ /accept — Accept this plan")
    lines.append("🔄 /regenerate — Generate a different plan")

    return "\n".join(lines)


# ── Helper ──────────────────────────────────────────────────

def _calc_totals(meals: dict[str, list[dict]]) -> dict[str, int]:
    totals = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
    for items in meals.values():
        if isinstance(items, list):
            for item in items:
                for key in totals:
                    totals[key] += int(item.get(key, 0))
    return totals
