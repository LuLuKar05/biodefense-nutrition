"""
meal_manager.py — Meal plan & meal log storage
===============================================
Local JSON storage for:
  - Active meal plan (today's plan, pending or accepted)
  - Meal schedule with per-meal delivery tracking
  - Plan history (all accepted plans, date-indexed)
  - Meal log (actual meals eaten, user evidence)
  - Rejected plans (to avoid regenerating similar meals)

All data stays local: data/meals/<user_id>/
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("meal_manager")

# ── Paths ───────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
MEALS_DIR = ROOT / "data" / "meals"


def _user_dir(user_id: str) -> Path:
    d = MEALS_DIR / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_json(path: Path) -> Any:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ═════════════════════════════════════════════════════════════
# ACTIVE PLAN (today's plan — pending or accepted)
# ═════════════════════════════════════════════════════════════

def get_active_plan(user_id: str) -> dict[str, Any] | None:
    """Load today's active meal plan, or None."""
    data = _load_json(_user_dir(user_id) / "active_plan.json")
    if data and data.get("date") == _today_str():
        return data
    return None  # stale plan from a different day


def save_active_plan(user_id: str, plan: dict[str, Any]) -> Path:
    """Save a meal plan as the active (pending) plan for today."""
    plan["date"] = _today_str()
    plan["created_at"] = _now_iso()
    plan["status"] = plan.get("status", "pending")  # pending | accepted
    path = _user_dir(user_id) / "active_plan.json"
    _save_json(path, plan)
    log.info(f"[{user_id}] Active plan saved ({plan['status']})")
    return path


def accept_active_plan(user_id: str) -> dict[str, Any] | None:
    """
    Accept the current active plan:
      - Mark as accepted
      - Add to plan history
      - Clear rejected list for today
      - Return the accepted plan
    """
    plan = get_active_plan(user_id)
    if not plan:
        return None

    plan["status"] = "accepted"
    plan["accepted_at"] = _now_iso()

    # Save back as active
    save_active_plan(user_id, plan)

    # Add to history
    _append_to_plan_history(user_id, plan)

    # Clear today's rejected plans (no longer needed)
    clear_rejected(user_id)

    log.info(f"[{user_id}] Plan accepted and added to history")
    return plan


def _append_to_plan_history(user_id: str, plan: dict[str, Any]) -> None:
    path = _user_dir(user_id) / "plan_history.json"
    history = _load_json(path) or []
    history.append(plan)
    # Keep last 90 days of history
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    history = [p for p in history if p.get("created_at", "") >= cutoff]
    _save_json(path, history)


# ═════════════════════════════════════════════════════════════
# PLAN HISTORY
# ═════════════════════════════════════════════════════════════

def get_plan_history(user_id: str, days: int = 7) -> list[dict[str, Any]]:
    """Get accepted meal plans from the last N days."""
    path = _user_dir(user_id) / "plan_history.json"
    history = _load_json(path) or []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return [p for p in history if p.get("date", "") >= cutoff]


def get_recent_meal_names(user_id: str, days: int = 7) -> list[str]:
    """Get all meal names from recent accepted plans (to avoid duplicates)."""
    plans = get_plan_history(user_id, days)
    names = []
    for plan in plans:
        # New schedule-based format
        schedule = plan.get("schedule", [])
        if schedule:
            for meal in schedule:
                for item in meal.get("items", []):
                    if isinstance(item, dict):
                        names.append(item.get("name", ""))
                    elif isinstance(item, str):
                        names.append(item)
            continue
        # Legacy {meals: {breakfast: [...]}} format
        meals = plan.get("meals", {})
        for meal_type in ("breakfast", "lunch", "dinner", "snacks"):
            items = meals.get(meal_type, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        names.append(item.get("name", ""))
                    elif isinstance(item, str):
                        names.append(item)
            elif isinstance(items, str):
                names.append(items)
    return [n for n in names if n]


# ═════════════════════════════════════════════════════════════
# REJECTED PLANS (avoid regenerating similar)
# ═════════════════════════════════════════════════════════════

def add_rejected(user_id: str, plan: dict[str, Any]) -> None:
    """Add a rejected plan so the regenerator avoids similar meals."""
    path = _user_dir(user_id) / "rejected.json"
    rejected = _load_json(path) or []
    rejected.append({
        "date": _today_str(),
        "rejected_at": _now_iso(),
        "meals": plan.get("meals", {}),
    })
    # Keep only today's rejections
    today = _today_str()
    rejected = [r for r in rejected if r.get("date") == today]
    _save_json(path, rejected)
    log.info(f"[{user_id}] Plan rejected ({len(rejected)} today)")


def get_rejected_meal_names(user_id: str) -> list[str]:
    """Get meal names from all rejected plans today."""
    path = _user_dir(user_id) / "rejected.json"
    rejected = _load_json(path) or []
    today = _today_str()
    names = []
    for r in rejected:
        if r.get("date") != today:
            continue
        meals = r.get("meals", {})
        for meal_type in ("breakfast", "lunch", "dinner", "snacks"):
            items = meals.get(meal_type, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        names.append(item.get("name", ""))
                    elif isinstance(item, str):
                        names.append(item)
    return [n for n in names if n]


def clear_rejected(user_id: str) -> None:
    path = _user_dir(user_id) / "rejected.json"
    _save_json(path, [])


# ═════════════════════════════════════════════════════════════
# MEAL LOG (actual meals eaten — user evidence)
# ═════════════════════════════════════════════════════════════

def log_meal(
    user_id: str,
    meal_type: str,
    description: str,
    estimated_macros: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Log a meal the user actually ate.

    Args:
        user_id: User ID
        meal_type: breakfast | lunch | dinner | snack
        description: What the user said they ate (text, vision-ready)
        estimated_macros: Optional estimated calories/protein/carbs/fat

    Returns:
        The logged meal entry
    """
    path = _user_dir(user_id) / "meal_log.json"
    logs = _load_json(path) or []

    entry = {
        "date": _today_str(),
        "logged_at": _now_iso(),
        "meal_type": meal_type,
        "description": description,
        "estimated_macros": estimated_macros or {},
    }
    logs.append(entry)

    # Keep last 90 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    logs = [m for m in logs if m.get("date", "") >= cutoff]
    _save_json(path, logs)

    log.info(f"[{user_id}] Meal logged: {meal_type} — {description[:50]}")
    return entry


def get_meal_log(user_id: str, days: int = 7) -> list[dict[str, Any]]:
    """Get meal logs from the last N days."""
    path = _user_dir(user_id) / "meal_log.json"
    logs = _load_json(path) or []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return [m for m in logs if m.get("date", "") >= cutoff]


def get_today_log(user_id: str) -> list[dict[str, Any]]:
    """Get only today's meal logs."""
    path = _user_dir(user_id) / "meal_log.json"
    logs = _load_json(path) or []
    today = _today_str()
    return [m for m in logs if m.get("date") == today]


def get_today_consumed(user_id: str) -> dict[str, float]:
    """Sum up today's consumed macros from meal log."""
    today_logs = get_today_log(user_id)
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    for entry in today_logs:
        macros = entry.get("estimated_macros", {})
        for key in totals:
            totals[key] += float(macros.get(key, 0))
    return totals


def get_remaining_budget(user_id: str, target_macros: dict[str, Any]) -> dict[str, float]:
    """Calculate remaining macro budget for today."""
    consumed = get_today_consumed(user_id)
    return {
        "calories": max(0, target_macros.get("target_calories", 0) - consumed["calories"]),
        "protein_g": max(0, target_macros.get("protein_g", 0) - consumed["protein_g"]),
        "carbs_g": max(0, target_macros.get("carbs_g", 0) - consumed["carbs_g"]),
        "fat_g": max(0, target_macros.get("fat_g", 0) - consumed["fat_g"]),
    }


# ═════════════════════════════════════════════════════════════
# WEEKLY BALANCE ANALYSIS
# ═════════════════════════════════════════════════════════════

def get_weekly_balance(user_id: str, target_macros: dict[str, Any]) -> dict[str, Any]:
    """
    Analyze last 7 days of meal logs vs targets.
    Returns daily breakdown + averages + deficiency alerts.
    """
    logs = get_meal_log(user_id, days=7)
    if not logs:
        return {"days_tracked": 0, "status": "no_data"}

    # Group by date
    by_date: dict[str, dict[str, float]] = {}
    for entry in logs:
        date = entry.get("date", "")
        if date not in by_date:
            by_date[date] = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
        macros = entry.get("estimated_macros", {})
        for key in by_date[date]:
            by_date[date][key] += float(macros.get(key, 0))

    days_tracked = len(by_date)
    if days_tracked == 0:
        return {"days_tracked": 0, "status": "no_data"}

    # Calculate averages
    avg = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    for day_totals in by_date.values():
        for key in avg:
            avg[key] += day_totals[key]
    for key in avg:
        avg[key] = round(avg[key] / days_tracked, 1)

    # Compare to targets
    deficiencies = []
    target_cal = target_macros.get("target_calories", 0)
    if target_cal and avg["calories"] < target_cal * 0.8:
        deficiencies.append(f"calories ({avg['calories']:.0f} vs {target_cal} target)")
    if target_macros.get("protein_g") and avg["protein_g"] < target_macros["protein_g"] * 0.8:
        deficiencies.append(f"protein ({avg['protein_g']:.0f}g vs {target_macros['protein_g']}g target)")
    if target_macros.get("fat_g") and avg["fat_g"] < target_macros["fat_g"] * 0.8:
        deficiencies.append(f"fat ({avg['fat_g']:.0f}g vs {target_macros['fat_g']}g target)")

    excess = []
    if target_cal and avg["calories"] > target_cal * 1.2:
        excess.append(f"calories ({avg['calories']:.0f} vs {target_cal} target)")
    if target_macros.get("fat_g") and avg["fat_g"] > target_macros["fat_g"] * 1.3:
        excess.append(f"fat ({avg['fat_g']:.0f}g vs {target_macros['fat_g']}g target)")

    return {
        "days_tracked": days_tracked,
        "daily_avg": avg,
        "by_date": by_date,
        "deficiencies": deficiencies,
        "excess": excess,
        "status": "deficient" if deficiencies else ("excess" if excess else "balanced"),
    }


# ═════════════════════════════════════════════════════════════
# MEAL SCHEDULE DELIVERY TRACKING
# ═════════════════════════════════════════════════════════════

def get_meal_schedule(user_id: str) -> list[dict[str, Any]]:
    """Get today's meal schedule from the active plan. Returns [] if none."""
    plan = get_active_plan(user_id)
    if not plan or plan.get("status") != "accepted":
        return []
    return plan.get("schedule", [])


def get_next_pending_meal(user_id: str) -> dict[str, Any] | None:
    """
    Get the next undelivered meal from today's accepted schedule.
    Returns the meal dict or None if all delivered / no schedule.
    """
    schedule = get_meal_schedule(user_id)
    for meal in schedule:
        if not meal.get("delivered", False):
            return meal
    return None


def mark_meal_delivered(user_id: str, meal_index: int) -> bool:
    """
    Mark a specific meal slot as delivered (by index in schedule).
    Returns True if successfully marked.
    """
    plan = get_active_plan(user_id)
    if not plan or plan.get("status") != "accepted":
        return False
    schedule = plan.get("schedule", [])
    if meal_index < 0 or meal_index >= len(schedule):
        return False
    schedule[meal_index]["delivered"] = True
    schedule[meal_index]["delivered_at"] = _now_iso()
    save_active_plan(user_id, plan)
    log.info(f"[{user_id}] Meal {meal_index} marked delivered")
    return True


def get_next_pending_meal_with_index(user_id: str) -> tuple[dict[str, Any] | None, int]:
    """Get next undelivered meal and its index. Returns (meal, index) or (None, -1)."""
    schedule = get_meal_schedule(user_id)
    for i, meal in enumerate(schedule):
        if not meal.get("delivered", False):
            return meal, i
    return None, -1


def get_all_users_with_pending_meals() -> list[tuple[str, dict[str, Any], int]]:
    """
    Scan all user meal directories for accepted plans with pending meals.
    Returns [(user_id, next_meal, meal_index), ...] for the background scheduler.
    """
    results = []
    if not MEALS_DIR.exists():
        return results
    for user_dir in MEALS_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        user_id = user_dir.name
        meal, idx = get_next_pending_meal_with_index(user_id)
        if meal is not None:
            results.append((user_id, meal, idx))
    return results
