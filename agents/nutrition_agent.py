"""
nutrition_agent.py — Personalized meal planning & tracking agent
================================================================
Handles:
  - /plan — Generate a per-meal schedule for the day
  - /next — Get your next upcoming meal (proactive delivery)
  - /accept — Accept the active plan
  - /regenerate — New plan with same macros, different foods
  - /balance — Weekly rolling macro balance report
  - /log <meal> — Log what the user actually ate (text evidence)
  - /today — Today's progress vs targets
  - Free-text meal logging & nutrition Q&A

Uses FLock API for intelligent plan generation and macro estimation.
Falls back to template plans + keyword estimation when FLock is down.

All data stays local: data/meals/<user_id>/
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from agents.tools.profile_manager import load_profile
from agents.tools.macro_calculator import calculate_macros, format_macros
from agents.tools.meal_manager import (
    get_active_plan,
    save_active_plan,
    accept_active_plan,
    add_rejected,
    log_meal,
    get_today_log,
    get_today_consumed,
    get_remaining_budget,
    get_weekly_balance,
    get_next_pending_meal_with_index,
    mark_meal_delivered,
)
from agents.tools.meal_planner import (
    generate_plan,
    estimate_meal_macros,
    format_plan,
    format_single_meal,
)

log = logging.getLogger("nutrition_agent")


# ═════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════

async def process_message(user_id: str, text: str) -> str:
    """
    Process a nutrition-related message.

    Called by the orchestrator after intent is classified as 'nutrition'.
    Handles commands and free-text.

    Returns a text reply for the user.
    """
    stripped = text.strip()
    lower = stripped.lower()

    # ── Load profile (required) ──
    profile = load_profile(user_id)
    if not profile:
        return (
            "I need your profile to plan meals!\n"
            "Send /start to set up your nutrition profile first."
        )

    # ── Chain context (from threat agent or other chain source) ──
    chain_context = _extract_chain_context(stripped)
    if chain_context:
        return await _handle_chain(user_id, profile, chain_context)

    # ── Slash commands ──
    if lower.startswith("/plan"):
        return await cmd_plan(user_id, profile)

    if lower.startswith("/next"):
        return cmd_next_meal(user_id)

    if lower.startswith("/accept"):
        return cmd_accept(user_id)

    if lower.startswith("/regenerate"):
        return await cmd_regenerate(user_id, profile)

    if lower.startswith("/balance"):
        return cmd_balance(user_id, profile)

    if lower.startswith("/log"):
        # /log I had grilled chicken with rice
        meal_text = stripped[4:].strip()
        if not meal_text:
            return (
                "Tell me what you ate! Example:\n"
                "/log Grilled chicken with rice and salad for lunch"
            )
        return await cmd_log_meal(user_id, profile, meal_text)

    if lower.startswith("/today"):
        return cmd_today_summary(user_id, profile)

    # ── Free-text: detect if user is logging a meal or asking a question ──
    return await _handle_free_text(user_id, profile, stripped)


# ═════════════════════════════════════════════════════════════
# COMMANDS
# ═════════════════════════════════════════════════════════════

async def cmd_plan(
    user_id: str,
    profile: dict[str, Any],
    threat_context: dict[str, Any] | None = None,
) -> str:
    """Generate a new per-meal schedule for the user."""
    active = get_active_plan(user_id)
    if active and active.get("status") == "accepted":
        meal, idx = get_next_pending_meal_with_index(user_id)
        if meal:
            return (
                "You already have an accepted plan today!\n\n"
                f"Your next meal:\n{format_single_meal(meal)}\n\n"
                "Use /next to see the next meal, or /today for progress."
            )
        return (
            "You already accepted a plan today and all meals are delivered!\n"
            "Use /today for progress, or /regenerate for a new plan."
        )

    if active and active.get("status") == "pending":
        return (
            "You have a pending plan:\n\n"
            f"{format_plan(active)}\n\n"
            "Use /accept to lock it in, or /regenerate for a new one."
        )

    # Generate new plan (with threat context if provided)
    plan = await generate_plan(user_id, profile, threat_context=threat_context)
    save_active_plan(user_id, plan)

    return format_plan(plan)


def cmd_next_meal(user_id: str) -> str:
    """Show the next pending meal from today's accepted schedule."""
    meal, idx = get_next_pending_meal_with_index(user_id)
    if meal is None:
        active = get_active_plan(user_id)
        if not active:
            return "No meal plan for today. Use /plan to generate one!"
        if active.get("status") == "pending":
            return "Your plan is pending. Use /accept first, then /next."
        return "All meals delivered for today! Great job tracking your nutrition."

    total = len(get_active_plan(user_id).get("schedule", []))  # type: ignore[union-attr]
    delivered = idx  # index = how many already delivered (0-based)

    lines = [
        f"Meal {delivered + 1} of {total}",
        "",
        format_single_meal(meal),
        "",
        "After eating, log it with:",
        f'  /log <what you actually ate>',
    ]
    return "\n".join(lines)


def cmd_accept(user_id: str) -> str:
    """Accept the active meal plan."""
    plan = accept_active_plan(user_id)
    if not plan:
        return "No active plan to accept. Use /plan to generate one!"

    meal_count = plan.get("meal_count", len(plan.get("schedule", [])))
    meal, _ = get_next_pending_meal_with_index(user_id)
    first_meal_preview = ""
    if meal:
        first_meal_preview = (
            f"\nYour first meal:\n{format_single_meal(meal)}\n"
        )

    return (
        f"Plan accepted! ({meal_count} meals scheduled)\n"
        f"{first_meal_preview}\n"
        "I'll send you each meal when it's time.\n"
        "Track what you eat: /log <meal description>\n"
        "/next — See next meal | /today — Today's progress"
    )


async def cmd_regenerate(user_id: str, profile: dict[str, Any]) -> str:
    """Reject current plan and generate a new one with same macros."""
    active = get_active_plan(user_id)
    if active:
        add_rejected(user_id, active)

    plan = await generate_plan(user_id, profile, regenerate=True)
    save_active_plan(user_id, plan)

    return f"Here's a new plan with the same targets:\n\n{format_plan(plan)}"


async def cmd_log_meal(user_id: str, profile: dict[str, Any], meal_text: str) -> str:
    """Log a meal the user ate with macro estimation."""
    meal_type = _detect_meal_type(meal_text)
    estimated = await estimate_meal_macros(meal_text)

    entry = log_meal(
        user_id=user_id,
        meal_type=meal_type,
        description=meal_text,
        estimated_macros=estimated,
    )

    # Auto-advance schedule: mark next pending meal as delivered
    _, idx = get_next_pending_meal_with_index(user_id)
    if idx >= 0:
        mark_meal_delivered(user_id, idx)

    macros = calculate_macros(profile)
    remaining = get_remaining_budget(user_id, macros)

    confidence = estimated.get("confidence", "low")
    confidence_note = "" if confidence == "high" else " (rough estimate)"

    # Check if there's a next meal coming
    next_meal, _ = get_next_pending_meal_with_index(user_id)
    next_hint = ""
    if next_meal:
        label = next_meal.get("label", "")
        time_slot = next_meal.get("time_slot", "")
        next_hint = f"\nNext up: {label} at {time_slot} (/next)"

    lines = [
        f"Meal logged! ({meal_type})",
        "",
        f"  {meal_text}",
        f"  ~{estimated.get('calories', 0)} cal | "
        f"P:{estimated.get('protein_g', 0)}g "
        f"C:{estimated.get('carbs_g', 0)}g "
        f"F:{estimated.get('fat_g', 0)}g{confidence_note}",
        "",
        "Remaining today:",
        f"  Calories: {remaining['calories']:.0f}",
        f"  Protein: {remaining['protein_g']:.0f}g",
        f"  Carbs: {remaining['carbs_g']:.0f}g",
        f"  Fat: {remaining['fat_g']:.0f}g",
    ]
    if next_hint:
        lines.append(next_hint)

    return "\n".join(lines)


def cmd_balance(user_id: str, profile: dict[str, Any]) -> str:
    """Show weekly nutrition balance report."""
    macros = calculate_macros(profile)
    balance = get_weekly_balance(user_id, macros)

    if balance["status"] == "no_data":
        return (
            "No meal data for the past week.\n"
            "Start logging with /log to track your nutrition balance!"
        )

    avg = balance["daily_avg"]
    days = balance["days_tracked"]

    lines = [
        f"Weekly Nutrition Balance ({days} day{'s' if days > 1 else ''} tracked)",
        "",
        "Daily Averages:",
        f"  Calories: {avg['calories']:.0f} / {macros['target_calories']}",
        f"  Protein: {avg['protein_g']:.0f}g / {macros['protein_g']}g",
        f"  Carbs: {avg['carbs_g']:.0f}g / {macros['carbs_g']}g",
        f"  Fat: {avg['fat_g']:.0f}g / {macros['fat_g']}g",
    ]

    if balance["deficiencies"]:
        lines.append("")
        lines.append("Deficiencies:")
        for d in balance["deficiencies"]:
            lines.append(f"  - Low {d}")

    if balance["excess"]:
        lines.append("")
        lines.append("Excess:")
        for e in balance["excess"]:
            lines.append(f"  - High {e}")

    if balance["status"] == "balanced":
        lines.append("")
        lines.append("Your nutrition is well balanced! Keep it up!")

    return "\n".join(lines)


def cmd_today_summary(user_id: str, profile: dict[str, Any]) -> str:
    """Show what's been eaten today vs targets."""
    macros = calculate_macros(profile)
    consumed = get_today_consumed(user_id)
    today_meals = get_today_log(user_id)

    if not today_meals:
        return (
            "No meals logged today yet.\n"
            "Use /log followed by what you ate, e.g.:\n"
            "/log Oatmeal with berries for breakfast"
        )

    lines = ["Today's Meals", ""]

    for entry in today_meals:
        m_type = entry.get("meal_type", "meal")
        desc = entry.get("description", "")
        m = entry.get("estimated_macros", {})
        lines.append(f"  - [{m_type}] {desc}")
        lines.append(f"    ~{m.get('calories', 0)} cal | "
                     f"P:{m.get('protein_g', 0)}g "
                     f"C:{m.get('carbs_g', 0)}g "
                     f"F:{m.get('fat_g', 0)}g")

    lines.append("")
    lines.append(f"Consumed: {consumed['calories']:.0f} / {macros['target_calories']} cal")
    lines.append(f"  Protein: {consumed['protein_g']:.0f}g / {macros['protein_g']}g")
    lines.append(f"  Carbs: {consumed['carbs_g']:.0f}g / {macros['carbs_g']}g")
    lines.append(f"  Fat: {consumed['fat_g']:.0f}g / {macros['fat_g']}g")

    remaining = get_remaining_budget(user_id, macros)
    pct = (consumed["calories"] / macros["target_calories"] * 100) if macros["target_calories"] else 0
    lines.append("")
    lines.append(f"  Progress: {pct:.0f}% of daily target")
    lines.append(f"  Remaining: {remaining['calories']:.0f} cal")

    # Show next scheduled meal if any
    meal, _ = get_next_pending_meal_with_index(user_id)
    if meal:
        lines.append(f"\nNext: {meal.get('label', '')} at {meal.get('time_slot', '')} (/next)")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════
# FREE-TEXT HANDLER
# ═════════════════════════════════════════════════════════════

async def _handle_free_text(user_id: str, profile: dict[str, Any], text: str) -> str:
    """
    Handle free-text messages that arrive with nutrition intent.
    Detect if user is logging a meal or asking a question.
    """
    lower = text.lower()

    # Meal logging patterns
    log_patterns = [
        r"^i (?:ate|had|just (?:ate|had)|finished)",
        r"^(?:ate|had|eating|just ate|just had)",
        r"^for (?:breakfast|lunch|dinner|snack)",
        r"^(?:breakfast|lunch|dinner|snack)[:\s]",
    ]
    for pat in log_patterns:
        if re.search(pat, lower):
            return await cmd_log_meal(user_id, profile, text)

    # Macro/target question
    if any(w in lower for w in ("my macros", "my targets", "my calories", "how much should")):
        macros = calculate_macros(profile)
        return format_macros(macros, profile.get("name", "Your"))

    # Balance question
    if any(w in lower for w in ("how am i doing", "my progress", "my balance", "week summary")):
        return cmd_balance(user_id, profile)

    # Today question
    if any(w in lower for w in ("what did i eat", "today so far", "today's meals")):
        return cmd_today_summary(user_id, profile)

    # Next meal question
    if any(w in lower for w in ("next meal", "what should i eat", "what's next")):
        return cmd_next_meal(user_id)

    # Default: suggest actions
    return (
        "I can help with your nutrition! Try:\n"
        "  /plan — Get a personalized meal schedule\n"
        "  /next — See your next scheduled meal\n"
        "  /log <meal> — Log what you ate\n"
        "  /today — See today's progress\n"
        "  /balance — Weekly nutrition overview\n"
        "  /regenerate — Get a different meal plan\n\n"
        "Or just tell me what you ate, like:\n"
        '  "I had grilled chicken with rice for lunch"'
    )


# ═════════════════════════════════════════════════════════════
# CHAIN HANDLING (from threat agent or other sources)
# ═════════════════════════════════════════════════════════════

def _extract_chain_context(text: str) -> dict[str, Any] | None:
    """Extract chain context if present in the message."""
    match = re.search(r"\[CHAIN_CONTEXT:({.*?})\]", text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


async def _handle_chain(
    user_id: str,
    profile: dict[str, Any],
    context: dict[str, Any],
) -> str:
    """
    Handle a chain directive from another agent.
    E.g., threat agent detected a threat and wants the nutrition agent
    to adapt the meal plan with immune-boosting foods.
    """
    threat_type = context.get("threat_type", "unknown")
    recommendation = context.get("recommendation", "")
    boost_nutrients = context.get("boost_nutrients", [])

    # Generate plan with threat context passed through to LLM
    plan = await generate_plan(user_id, profile, threat_context=context)
    save_active_plan(user_id, plan)

    lines = [
        f"Adapting your meal plan for: {threat_type}",
    ]

    if recommendation:
        lines.append(f"Recommendation: {recommendation}")

    if boost_nutrients:
        lines.append(f"Boosting: {', '.join(boost_nutrients[:6])}")

    lines.append("")
    lines.append(format_plan(plan))

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════

def _detect_meal_type(text: str) -> str:
    """Detect meal type from text description."""
    lower = text.lower()

    if any(w in lower for w in ("breakfast", "morning", "cereal", "oatmeal")):
        return "breakfast"
    if any(w in lower for w in ("lunch", "midday", "noon")):
        return "lunch"
    if any(w in lower for w in ("dinner", "evening", "supper")):
        return "dinner"
    if any(w in lower for w in ("snack", "treat", "snacking")):
        return "snack"

    # Default based on rough time of day
    from datetime import datetime, timezone
    hour = datetime.now(timezone.utc).hour
    if hour < 11:
        return "breakfast"
    elif hour < 15:
        return "lunch"
    elif hour < 20:
        return "dinner"
    else:
        return "snack"
