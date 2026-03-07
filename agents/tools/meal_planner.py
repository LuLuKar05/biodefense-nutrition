"""
meal_planner.py — FLock-powered per-meal plan generation
=========================================================
Generates individual meals with scheduling, respecting:
  - User macros (from profile)
  - Diet preferences and allergies
  - Previously rejected meals (today)
  - Recent meal history (7-day dedup window)
  - Active health threats (from Layer 3)
  - Configurable meal count (user preference or goal-based)

Returns structured schedule: [{meal_type, time_slot, items, macros}, ...]
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
# MEAL SCHEDULE — time slots per meal count
# ═════════════════════════════════════════════════════════════

MEAL_SCHEDULES: dict[int, list[dict[str, str]]] = {
    3: [
        {"meal_type": "breakfast", "time_slot": "07:30", "label": "Breakfast"},
        {"meal_type": "lunch",     "time_slot": "12:30", "label": "Lunch"},
        {"meal_type": "dinner",    "time_slot": "19:00", "label": "Dinner"},
    ],
    4: [
        {"meal_type": "breakfast", "time_slot": "07:30", "label": "Breakfast"},
        {"meal_type": "lunch",     "time_slot": "12:30", "label": "Lunch"},
        {"meal_type": "snack",     "time_slot": "15:30", "label": "Afternoon Snack"},
        {"meal_type": "dinner",    "time_slot": "19:00", "label": "Dinner"},
    ],
    5: [
        {"meal_type": "breakfast",      "time_slot": "07:30", "label": "Breakfast"},
        {"meal_type": "morning_snack",  "time_slot": "10:30", "label": "Morning Snack"},
        {"meal_type": "lunch",          "time_slot": "13:00", "label": "Lunch"},
        {"meal_type": "afternoon_snack","time_slot": "16:00", "label": "Afternoon Snack"},
        {"meal_type": "dinner",         "time_slot": "19:30", "label": "Dinner"},
    ],
    6: [
        {"meal_type": "breakfast",      "time_slot": "07:00", "label": "Breakfast"},
        {"meal_type": "morning_snack",  "time_slot": "10:00", "label": "Morning Snack"},
        {"meal_type": "lunch",          "time_slot": "12:30", "label": "Lunch"},
        {"meal_type": "afternoon_snack","time_slot": "15:30", "label": "Afternoon Snack"},
        {"meal_type": "dinner",         "time_slot": "18:30", "label": "Dinner"},
        {"meal_type": "evening_snack",  "time_slot": "21:00", "label": "Evening Snack"},
    ],
}


def determine_meal_count(profile: dict[str, Any]) -> int:
    """
    Determine optimal number of meals per day.
    Uses user preference if set, otherwise calculates from goal.
    """
    meals_pref = profile.get("meals_per_day")
    if meals_pref:
        try:
            n = int(meals_pref)
            if 3 <= n <= 6:
                return n
        except (ValueError, TypeError):
            pass

    goal = profile.get("goal", "maintain")
    if goal == "bulk":
        return 5
    elif goal == "cut":
        return 4
    else:
        return 3


def get_schedule_slots(meal_count: int) -> list[dict[str, str]]:
    """Get time slots for the given meal count."""
    return MEAL_SCHEDULES.get(meal_count, MEAL_SCHEDULES[3])


# ═════════════════════════════════════════════════════════════
# MAIN: Generate Full Day Schedule (individual meals)
# ═════════════════════════════════════════════════════════════

async def generate_plan(
    user_id: str,
    profile: dict[str, Any],
    *,
    regenerate: bool = False,
    threat_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Generate a per-meal schedule for the day.

    Returns:
        {
          "meal_count": N,
          "schedule": [
            {"meal_type": "breakfast", "time_slot": "07:30", "label": "...",
             "items": [...], "meal_macros": {...}, "delivered": False},
            ...
          ],
          "totals": {"calories": N, "protein_g": N, "carbs_g": N, "fat_g": N},
          "threat_adaptations": [...],
          "source": "flock" | "template"
        }
    """
    macros = calculate_macros(profile)
    diet = profile.get("diet", "standard")
    allergies = profile.get("allergies", "none")
    meal_count = determine_meal_count(profile)
    slots = get_schedule_slots(meal_count)

    rejected_names = get_rejected_meal_names(user_id) if regenerate else []
    recent_names = get_recent_meal_names(user_id, days=7)
    avoid_list = list(set(rejected_names + recent_names))
    consumed = get_today_consumed(user_id)
    threat_hints = _build_threat_hints(threat_context)

    plan = None
    if FLOCK_API_KEY and _plan_cb.should_call():
        plan = await _generate_flock_schedule(
            macros=macros,
            diet=diet,
            allergies=allergies,
            avoid_list=avoid_list,
            consumed=consumed,
            meal_count=meal_count,
            slots=slots,
            threat_hints=threat_hints,
        )

    if plan is None:
        plan = _generate_template_schedule(macros, diet, avoid_list, slots)

    return plan


def _build_threat_hints(context: dict[str, Any] | None) -> str:
    """Build threat adaptation hints for the LLM prompt."""
    if not context:
        return ""
    lines = []
    threat_type = context.get("threat_type", "")
    if threat_type:
        lines.append(f"ACTIVE HEALTH THREATS: {threat_type}")
    boost = context.get("boost_nutrients", [])
    if boost:
        lines.append(f"PRIORITIZE THESE FOODS: {', '.join(boost[:8])}")
    recommendation = context.get("recommendation", "")
    if recommendation:
        lines.append(f"DIETARY GOAL: {recommendation}")
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════
# FLOCK-POWERED GENERATION (per-meal schedule)
# ═════════════════════════════════════════════════════════════

async def _generate_flock_schedule(
    *,
    macros: dict[str, Any],
    diet: str,
    allergies: str,
    avoid_list: list[str],
    consumed: dict[str, float],
    meal_count: int,
    slots: list[dict[str, str]],
    threat_hints: str,
) -> dict[str, Any] | None:
    """Call FLock to generate a per-meal schedule."""

    avoid_str = ", ".join(avoid_list[:30]) if avoid_list else "none"
    consumed_str = (
        f"Already consumed today: {consumed['calories']:.0f} cal, "
        f"{consumed['protein_g']:.0f}g protein, {consumed['carbs_g']:.0f}g carbs, "
        f"{consumed['fat_g']:.0f}g fat"
        if consumed["calories"] > 0
        else "No meals logged yet today"
    )

    slot_desc = "\n".join(
        f"  {i+1}. {s['label']} at {s['time_slot']} (type: {s['meal_type']})"
        for i, s in enumerate(slots)
    )

    threat_section = f"\n\nHEALTH THREAT ADAPTATIONS:\n{threat_hints}" if threat_hints else ""

    system_prompt = f"""You are a precision nutrition planner. Generate a {meal_count}-meal schedule.

TARGET MACROS (full day):
  Calories: {macros['target_calories']} kcal
  Protein: {macros['protein_g']}g
  Carbs: {macros['carbs_g']}g
  Fat: {macros['fat_g']}g

DIET TYPE: {diet}
ALLERGIES/RESTRICTIONS: {allergies}
{consumed_str}

MEAL SCHEDULE ({meal_count} meals):
{slot_desc}

MEALS TO AVOID (already eaten recently or rejected):
{avoid_str}{threat_section}

RULES:
1. Return ONLY valid JSON — no markdown, no explanation.
2. JSON format:
{{
  "meals": [
    {{"meal_type": "breakfast", "items": [{{"name": "...", "protein_g": N, "carbs_g": N, "fat_g": N, "calories": N}}]}}
  ]
}}
3. Generate exactly {meal_count} meal objects matching the schedule above.
4. Distribute macros intelligently across meals (bigger meals for lunch/dinner, lighter for snacks).
5. Total macros should closely match the targets (±10%).
6. Use realistic, common food items with accurate macro estimates.
7. DO NOT use any meals from the avoid list.
8. Make meals practical and appetizing.
9. If health threats are specified, incorporate protective foods where natural.
10. Adjust portions if the user already ate today.
11. Do NOT wrap in markdown code fences. Raw JSON only."""

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
                        {"role": "user", "content": f"Generate my {meal_count}-meal schedule for today."},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 1500,
                },
            )

        if resp.status_code != 200:
            log.warning(f"FLock plan API error: {resp.status_code} — {resp.text[:200]}")
            _plan_cb.record_failure()
            return None

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        raw_plan = json.loads(content)
        _plan_cb.record_success()

        raw_meals = raw_plan.get("meals", [])
        schedule = []
        for i, slot in enumerate(slots):
            meal_data = raw_meals[i] if i < len(raw_meals) else {}
            items = meal_data.get("items", [])
            schedule.append({
                **slot,
                "items": items,
                "meal_macros": _calc_meal_macros(items),
                "delivered": False,
            })

        totals = _calc_schedule_totals(schedule)
        plan = {
            "meal_count": meal_count,
            "schedule": schedule,
            "totals": totals,
            "threat_adaptations": [threat_hints] if threat_hints else [],
            "source": "flock",
        }

        log.info(f"FLock generated {meal_count}-meal schedule: {totals['calories']} kcal")
        return plan

    except json.JSONDecodeError as e:
        log.warning(f"FLock returned invalid JSON: {e}")
        _plan_cb.record_failure()
        return None
    except Exception as e:
        log.warning(f"FLock schedule request failed: {e}")
        _plan_cb.record_failure()
        return None


# ═════════════════════════════════════════════════════════════
# TEMPLATE FALLBACK
# ═════════════════════════════════════════════════════════════

def _generate_template_schedule(
    macros: dict[str, Any],
    diet: str,
    avoid_list: list[str],
    slots: list[dict[str, str]],
) -> dict[str, Any]:
    """Generate a schedule from local templates when FLock is unavailable."""
    templates = _load_templates().get("templates", {})
    diet_meals = templates.get(diet, templates.get("standard", {}))
    avoid_lower = {n.lower() for n in avoid_list}

    type_to_template = {
        "breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner",
        "snack": "snacks", "morning_snack": "snacks",
        "afternoon_snack": "snacks", "evening_snack": "snacks",
    }

    schedule = []
    for slot in slots:
        tpl_key = type_to_template.get(slot["meal_type"], "snacks")
        options = diet_meals.get(tpl_key, [])
        chosen = None
        for opt in options:
            if opt.get("name", "").lower() not in avoid_lower:
                chosen = opt
                break
        if chosen is None and options:
            chosen = options[0]

        items = [dict(chosen)] if chosen else []
        schedule.append({
            **slot, "items": items,
            "meal_macros": _calc_meal_macros(items), "delivered": False,
        })

    # Scale to match target calories
    raw_totals = _calc_schedule_totals(schedule)
    raw_cal = raw_totals.get("calories", 1) or 1
    target_cal = macros.get("target_calories", raw_cal)
    scale = target_cal / raw_cal

    if abs(scale - 1.0) > 0.05:
        for meal in schedule:
            for item in meal["items"]:
                for key in ("protein_g", "carbs_g", "fat_g", "calories"):
                    item[key] = round(item.get(key, 0) * scale)
            meal["meal_macros"] = _calc_meal_macros(meal["items"])

    return {
        "meal_count": len(slots),
        "schedule": schedule,
        "totals": _calc_schedule_totals(schedule),
        "threat_adaptations": [],
        "source": "template",
    }


# ═════════════════════════════════════════════════════════════
# ESTIMATE MACROS FROM TEXT (for meal logging)
# ═════════════════════════════════════════════════════════════

async def estimate_meal_macros(description: str) -> dict[str, Any]:
    """Use FLock to estimate macros from a text description."""
    if FLOCK_API_KEY and _plan_cb.should_call():
        estimated = await _estimate_flock(description)
        if estimated:
            return estimated
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
    cal, prot, carbs, fat = 400, 20, 40, 15

    protein_words = ["chicken", "fish", "salmon", "tuna", "beef", "steak", "egg",
                     "tofu", "turkey", "shrimp", "pork", "protein"]
    carb_words = ["rice", "bread", "pasta", "noodle", "potato", "oat", "cereal",
                  "tortilla", "wrap", "sandwich"]
    fat_words = ["butter", "cheese", "avocado", "nut", "oil", "cream", "bacon", "fried"]
    light_words = ["salad", "soup", "fruit", "yogurt", "smoothie", "snack"]

    protein_hits = sum(1 for w in protein_words if w in desc)
    carb_hits = sum(1 for w in carb_words if w in desc)
    fat_hits = sum(1 for w in fat_words if w in desc)
    light_hits = sum(1 for w in light_words if w in desc)

    if protein_hits:
        prot += 15 * protein_hits; cal += 60 * protein_hits
    if carb_hits:
        carbs += 20 * carb_hits; cal += 80 * carb_hits
    if fat_hits:
        fat += 10 * fat_hits; cal += 90 * fat_hits
    if light_hits:
        cal = max(200, cal - 100 * light_hits); carbs = max(10, carbs - 10 * light_hits)

    return {"calories": cal, "protein_g": prot, "carbs_g": carbs, "fat_g": fat, "confidence": "low"}


# ═════════════════════════════════════════════════════════════
# FORMAT — individual meal & full schedule
# ═════════════════════════════════════════════════════════════

MEAL_EMOJIS = {
    "breakfast": "🌅", "morning_snack": "🥜", "lunch": "☀️",
    "afternoon_snack": "🍎", "dinner": "🌙", "evening_snack": "🫖",
    "snack": "🍎",
}


def format_single_meal(meal: dict[str, Any]) -> str:
    """Format a single meal for proactive delivery to the user."""
    emoji = MEAL_EMOJIS.get(meal.get("meal_type", ""), "🍽")
    label = meal.get("label", meal.get("meal_type", "Meal").capitalize())
    time_slot = meal.get("time_slot", "")
    items = meal.get("items", [])
    macros = meal.get("meal_macros", {})

    lines = [f"{emoji} {label} ({time_slot})", ""]
    for item in items:
        name = item.get("name", "Unknown")
        cal = item.get("calories", 0)
        p = item.get("protein_g", 0)
        c = item.get("carbs_g", 0)
        f = item.get("fat_g", 0)
        lines.append(f"  • {name}")
        lines.append(f"    {cal} cal | P:{p}g C:{c}g F:{f}g")
    lines.append("")
    lines.append(f"  Meal total: {macros.get('calories', 0)} cal")

    return "\n".join(lines)


def format_plan(plan: dict[str, Any]) -> str:
    """Format the full day schedule overview."""
    schedule = plan.get("schedule", [])
    totals = plan.get("totals", {})
    source = plan.get("source", "unknown")
    meal_count = plan.get("meal_count", len(schedule))
    threat_adaptations = plan.get("threat_adaptations", [])

    lines = [f"🍽 Your {meal_count}-Meal Plan", ""]

    if threat_adaptations:
        lines.append("⚠️ Adapted for active health threats")
        lines.append("")

    for meal in schedule:
        emoji = MEAL_EMOJIS.get(meal.get("meal_type", ""), "🍽")
        label = meal.get("label", "Meal")
        time_slot = meal.get("time_slot", "")
        items = meal.get("items", [])

        lines.append(f"{emoji} {label} ({time_slot})")
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


# ── Helpers ──────────────────────────────────────────────────

def _calc_meal_macros(items: list[dict]) -> dict[str, int]:
    totals = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
    for item in items:
        for key in totals:
            totals[key] += int(item.get(key, 0))
    return totals


def _calc_schedule_totals(schedule: list[dict]) -> dict[str, int]:
    totals = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
    for meal in schedule:
        m = meal.get("meal_macros", {})
        for key in totals:
            totals[key] += int(m.get(key, 0))
    return totals


def _calc_totals(meals: dict[str, list[dict]]) -> dict[str, int]:
    """Legacy compat: calc totals from {meal_type: [items]} dict."""
    totals = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
    for items in meals.values():
        if isinstance(items, list):
            for item in items:
                for key in totals:
                    totals[key] += int(item.get(key, 0))
    return totals
