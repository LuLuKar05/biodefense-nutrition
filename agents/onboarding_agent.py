"""
onboarding_agent.py — FLock-powered conversational onboarding
=============================================================
Collects user health profile through natural AI conversation.
Uses FLock API (OpenAI-compatible) as the LLM brain.
Falls back to step-by-step mode if FLock is unavailable.

All data stays local. Zero-knowledge.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from dotenv import load_dotenv

from agents.tools.validators import (
    REQUIRED_FIELDS,
    validate_field,
    missing_fields,
)
from agents.tools.profile_manager import (
    load_partial,
    save_partial,
    save_profile,
    load_profile,
    update_field,
    delete_profile,
    get_profile_or_partial,
    profile_exists,
    has_partial,
    get_link_code,
    find_by_link_code,
    link_channel,
)
from agents.tools.macro_calculator import calculate_macros, format_macros
from agents.tools.circuit_breaker import CircuitBreaker

# ── Logging ─────────────────────────────────────────────────
log = logging.getLogger("onboarding_agent")

# ── Paths & Config ──────────────────────────────────────────
load_dotenv(override=True)

FLOCK_API_KEY: str = os.getenv("FLOCK_API_KEY", "").strip()
FLOCK_BASE_URL: str = os.getenv("FLOCK_BASE_URL", "https://api.flock.io/v1").strip()
FLOCK_MODEL: str = os.getenv("FLOCK_MODEL", "qwen3-30b-a3b-instruct-2507").strip()

# ── Circuit Breaker (three-state: closed → open → half-open) ──
_flock_cb = CircuitBreaker(name="flock_onboarding", max_failures=3, cooldown_secs=60.0)

# ── Conversation History (in-memory, keyed by user_id) ─────
_conversations: dict[str, list[dict[str, str]]] = {}

# ── Track fields collected during fallback (for continuity on recovery) ──
_fallback_fields: dict[str, list[str]] = {}

# ── Step-by-step prompts (fallback mode) ────────────────────
STEP_PROMPTS: dict[str, str] = {
    "name": "What's your name?",
    "age": "How old are you? (e.g., 25)",
    "sex": "What's your biological sex? (male / female)",
    "weight": "What's your current weight in kg? (e.g., 75)",
    "height": "What's your height in cm? (e.g., 175)",
    "allergies": "Any food allergies? List them separated by commas, or say 'none'.",
    "diet": "What diet do you prefer? (mediterranean / keto / vegan / standard)",
    "goal": "What's your body goal? (cut / bulk / maintain)",
    "city": "What city are you in? (for local threat detection only, never shared)",
}


# ── System Prompt ───────────────────────────────────────────

def _build_system_prompt(profile: dict[str, str], missing: list[str]) -> str:
    """Build the system prompt for FLock API based on current profile state."""
    collected = {k: v for k, v in profile.items() if v}
    collected_str = json.dumps(collected, indent=2) if collected else "{}"
    missing_str = ", ".join(missing) if missing else "none"

    return f"""You are NutriShield, a friendly personal nutrition & biodefense assistant.
Your job is to collect the user's health profile through natural, warm conversation.

REQUIRED FIELDS: {', '.join(REQUIRED_FIELDS)}
ALREADY COLLECTED: {collected_str}
STILL MISSING: {missing_str}

RULES:
1. Be conversational and warm — NOT robotic or quiz-like.
2. Extract profile fields from whatever the user says naturally.
3. The user may provide multiple fields in one message. Extract ALL of them.
4. After extracting fields, respond ONLY with valid JSON (no markdown fences, no extra text).
5. JSON format: {{"extracted": {{"field": "value", ...}}, "reply": "your natural response"}}
6. "extracted" contains ONLY the NEW fields found in the user's latest message.
7. "reply" is your natural conversational response — acknowledge what they said and ask about remaining fields.
8. If the user isn't providing profile info (just chatting), set "extracted" to {{}}.
9. If ALL fields are collected, set "reply" to a summary and add "complete": true.
10. NEVER ask for a field already collected unless the user wants to change it.
11. If the user says something like "update my weight to 80", extract it and add "update": true.
12. Keep replies concise — 1-3 sentences max.
13. Do NOT wrap your response in markdown code blocks. Raw JSON only.

VALID FIELD VALUES:
- sex: "male" or "female"
- diet: "mediterranean", "keto", "vegan", or "standard"
- goal: "cut", "bulk", or "maintain"
- weight: number in kg (20-300)
- height: number in cm (100-250)
- age: number (13-120)
- allergies: comma-separated list or "none"
- name: text (2-50 chars)
- city: text (2-100 chars)"""


# ═════════════════════════════════════════════════════════════
# FLOCK API COMMUNICATION
# ═════════════════════════════════════════════════════════════

async def _call_flock(
    user_id: str,
    user_message: str,
    profile: dict[str, str],
    missing: list[str],
) -> dict[str, Any] | None:
    """
    Call FLock API with conversation history.
    Returns parsed JSON response or None on failure.
    Uses three-state circuit breaker: CLOSED → OPEN → HALF_OPEN.
    """
    if not FLOCK_API_KEY:
        log.warning("FLOCK_API_KEY not set — skipping LLM call")
        return None
    if not _flock_cb.should_call():
        log.debug(f"FLock circuit breaker {_flock_cb.state.value} — using fallback")
        return None

    # Build/retrieve conversation history
    if user_id not in _conversations:
        _conversations[user_id] = []

    history = _conversations[user_id]

    # ── Inject continuity note if recovering from fallback ──
    _inject_fallback_continuity(user_id, profile)

    # Build messages
    system_prompt = _build_system_prompt(profile, missing)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-10:])  # Keep last 10 turns for context
    messages.append({"role": "user", "content": user_message})

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{FLOCK_BASE_URL}/chat/completions",
                headers={
                    "x-litellm-api-key": FLOCK_API_KEY,
                    "Content-Type": "application/json",
                    "accept": "application/json",
                },
                json={
                    "model": FLOCK_MODEL,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 512,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"].strip()
        log.debug(f"FLock raw response: {content}")

        # Store conversation turn
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": content})

        # Strip Qwen3 <think> tags if present
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

        # Parse JSON — handle possible markdown fences
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        parsed = json.loads(content)
        _flock_cb.record_success()
        return parsed

    except httpx.HTTPStatusError as exc:
        _flock_cb.record_failure()
        log.error(f"FLock API HTTP error: {exc.response.status_code} — {exc.response.text[:200]} ({_flock_cb})")
        return None
    except json.JSONDecodeError as exc:
        # API worked but response wasn't valid JSON — don't penalize circuit breaker
        log.warning(f"FLock returned non-JSON: {exc}")
        return None
    except Exception as exc:
        _flock_cb.record_failure()
        log.error(f"FLock API call failed: {exc} ({_flock_cb})")
        return None


def _inject_fallback_continuity(user_id: str, profile: dict[str, str]) -> None:
    """
    When FLock recovers (half-open → closed), inject a synthetic history entry
    so FLock knows which fields were collected during fallback mode.
    This gives conversational continuity — FLock picks up naturally.
    """
    fb_fields = _fallback_fields.pop(user_id, None)
    if not fb_fields:
        return  # nothing collected during fallback

    # Build a summary of what happened during fallback
    field_summary = ", ".join(fb_fields)
    values_summary = ", ".join(f"{f}={profile.get(f, '?')}" for f in fb_fields)

    if user_id not in _conversations:
        _conversations[user_id] = []

    # Inject as an assistant message so FLock sees it in history
    _conversations[user_id].append({
        "role": "assistant",
        "content": (
            f"[System note: The following fields were collected via step-by-step mode "
            f"while the AI assistant was briefly unavailable: {field_summary}. "
            f"Current values: {values_summary}. "
            f"Continue the conversation naturally from here, acknowledging the progress.]"
        ),
    })
    log.info(f"[{user_id}] Injected fallback continuity note: {field_summary}")


# ═════════════════════════════════════════════════════════════
# AGENT MODE (FLock-powered)
# ═════════════════════════════════════════════════════════════

async def _handle_agent_mode(user_id: str, text: str) -> str:
    """
    Process message using FLock API for natural conversation.
    Returns the reply text to send back to the user.
    """
    profile, is_complete = get_profile_or_partial(user_id)
    miss = missing_fields(profile)

    # If profile is complete, handle updates or general chat
    if is_complete and not miss:
        # Check if user wants to update something
        lower = text.lower()
        if any(word in lower for word in ("update", "change", "edit", "new weight", "new height")):
            result = await _call_flock(user_id, text, profile, [])
            if result and result.get("extracted"):
                return await _apply_extracted_fields(user_id, profile, result, is_update=True)
            return result.get("reply", "What would you like to update?") if result else "What would you like to update?"

        # General post-onboarding response
        return (
            f"Hey {profile.get('name', 'there')}! Your profile is all set. "
            "You can ask me to update any field (e.g., 'update my weight to 80kg'), "
            "or use /plan for your meal targets, /threats to check local risks."
        )

    # Onboarding in progress — call FLock
    result = await _call_flock(user_id, text, profile, miss)

    if result is None:
        # FLock unavailable — fall back to step-by-step
        return await _handle_fallback_mode(user_id, text)

    extracted = result.get("extracted", {})
    reply = result.get("reply", "")

    if not extracted:
        # User didn't provide any profile info — just chatting
        if not reply:
            if miss:
                next_field = miss[0]
                return f"No worries! Whenever you're ready — {STEP_PROMPTS.get(next_field, f'What is your {next_field}?')}"
        return reply or "Tell me a bit about yourself so I can set up your nutrition profile!"

    return await _apply_extracted_fields(user_id, profile, result, is_update=False)


async def _apply_extracted_fields(
    user_id: str,
    profile: dict[str, str],
    result: dict[str, Any],
    is_update: bool = False,
) -> str:
    """Validate extracted fields, save, and build response."""
    extracted = result.get("extracted", {})
    reply = result.get("reply", "")
    errors: list[str] = []
    accepted: dict[str, str] = {}

    for field, value in extracted.items():
        if field not in REQUIRED_FIELDS:
            continue
        ok, cleaned, err = validate_field(field, str(value))
        if ok:
            accepted[field] = cleaned
        else:
            errors.append(f"{field}: {err}")
            log.info(f"Validator rejected {field}={value!r}: {err}")

    if not accepted and not errors:
        return reply or "Tell me more about yourself!"

    # Apply accepted fields
    if is_update:
        for field, value in accepted.items():
            update_field(user_id, field, value)
        updated_profile = load_profile(user_id) or profile
        return reply or f"Updated {', '.join(accepted.keys())}!"
    else:
        profile.update(accepted)
        save_partial(user_id, profile)

    # Check if now complete
    miss = missing_fields(profile)

    if not miss:
        # All fields collected — show summary for confirmation
        return _format_confirmation(profile)

    # Build response with progress
    progress = len(REQUIRED_FIELDS) - len(miss)
    total = len(REQUIRED_FIELDS)
    bar = "=" * progress + "-" * (total - progress)
    progress_str = f"[{bar}] {progress}/{total}"

    if errors:
        error_msg = "\n".join(f"  - {e}" for e in errors)
        return f"{reply}\n\nHmm, some values need fixing:\n{error_msg}\n\n{progress_str}"

    return f"{reply}\n\n{progress_str}" if reply else f"Got it! {progress_str}"


# ═════════════════════════════════════════════════════════════
# FALLBACK MODE (step-by-step, no LLM)
# ═════════════════════════════════════════════════════════════

async def _handle_fallback_mode(user_id: str, text: str) -> str:
    """Step-by-step field collection when FLock API is unavailable."""
    profile, is_complete = get_profile_or_partial(user_id)

    if is_complete:
        return f"Your profile is complete, {profile.get('name', 'there')}! Use /plan for nutrition targets."

    miss = missing_fields(profile)
    if not miss:
        return _format_confirmation(profile)

    # Validate the current field
    current_field = miss[0]
    ok, cleaned, err = validate_field(current_field, text)

    if not ok:
        return err

    # Accept field
    profile[current_field] = cleaned
    save_partial(user_id, profile)

    # ── Track fields collected during fallback for continuity on recovery ──
    if user_id not in _fallback_fields:
        _fallback_fields[user_id] = []
    _fallback_fields[user_id].append(current_field)

    # Progress
    remaining = missing_fields(profile)
    progress = len(REQUIRED_FIELDS) - len(remaining)
    total = len(REQUIRED_FIELDS)
    bar = "=" * progress + "-" * (total - progress)
    progress_str = f"[{bar}] {progress}/{total}"

    if not remaining:
        return f"Got it! {progress_str}\n\n{_format_confirmation(profile)}"

    next_field = remaining[0]
    return f"Got it! {progress_str}\n\n{STEP_PROMPTS.get(next_field, f'What is your {next_field}?')}"


# ═════════════════════════════════════════════════════════════
# CONFIRMATION & SUMMARY
# ═════════════════════════════════════════════════════════════

def _format_confirmation(profile: dict[str, str]) -> str:
    """Format profile summary for user confirmation."""
    return (
        "Here's your profile summary:\n"
        "--------------------\n"
        f"Name: {profile.get('name', '?')}\n"
        f"Age: {profile.get('age', '?')}\n"
        f"Sex: {profile.get('sex', '?')}\n"
        f"Weight: {profile.get('weight', '?')} kg\n"
        f"Height: {profile.get('height', '?')} cm\n"
        f"Allergies: {profile.get('allergies', '?')}\n"
        f"Diet: {profile.get('diet', '?')}\n"
        f"Goal: {profile.get('goal', '?')}\n"
        f"City: {profile.get('city', '?')}\n"
        "--------------------\n\n"
        "Does this look right?\n"
        "- Type 'yes' to confirm\n"
        "- Type 'no' to start over\n"
        "- Type 'edit <field>' to change one field (e.g., 'edit weight')"
    )


def _format_profile_complete(profile: dict[str, str], macros: dict[str, Any]) -> str:
    """Format the completion message with macros."""
    name = profile.get("name", "there")
    link_code = get_link_code(str(profile.get("_user_id", "unknown")))
    macro_text = format_macros(macros, name)
    return (
        f"Profile saved! Here are your numbers:\n\n"
        f"{macro_text}\n\n"
        f"Your link code: {link_code}\n"
        f"(Use this to connect your profile on other channels with /link {link_code})\n\n"
        "What you can do now:\n"
        "  /plan — Generate your meal plan\n"
        "  /threats — Check local health threats\n"
        "  /profile — View your profile\n"
        "  /reset — Start over"
    )


# ═════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═════════════════════════════════════════════════════════════

async def handle_command(user_id: str, command: str) -> str:
    """Handle slash commands. Returns reply text."""
    cmd = command.strip().lower()

    if cmd == "/start":
        return await cmd_start(user_id)
    elif cmd == "/reset":
        return cmd_reset(user_id)
    elif cmd == "/profile":
        return cmd_profile(user_id)
    elif cmd == "/plan":
        return cmd_plan(user_id)
    elif cmd == "/threats":
        return cmd_threats(user_id)
    elif cmd == "/help" or cmd == "/commands":
        return cmd_help()
    elif cmd.startswith("/link "):
        code = cmd[6:].strip()
        return cmd_link(user_id, code)
    else:
        return f"Unknown command: {command}. Type /help for available commands."


async def cmd_start(user_id: str) -> str:
    """Start or resume onboarding."""
    profile, is_complete = get_profile_or_partial(user_id)

    if is_complete:
        return (
            f"Welcome back, {profile.get('name', 'there')}! Your profile is already set up.\n"
            "Use /profile to view it, /reset to start over, or /help for all commands."
        )

    if profile:
        # Resume partial
        miss = missing_fields(profile)
        collected = len(REQUIRED_FIELDS) - len(miss)
        return (
            f"Welcome back! Looks like you left off at {collected}/{len(REQUIRED_FIELDS)} fields.\n"
            "Let's pick up where we left off! Just tell me about yourself and I'll fill in the rest."
        )

    # Fresh start
    delete_profile(user_id)
    _conversations.pop(user_id, None)
    _fallback_fields.pop(user_id, None)
    _flock_cb.force_reset()

    if FLOCK_API_KEY:
        return (
            "Welcome to NutriShield!\n\n"
            "I'm your personal nutrition & biodefense assistant. "
            "Tell me a bit about yourself — your name, age, goals, anything! "
            "I'll build your profile as we chat.\n\n"
            "Or if you prefer, I can ask you one question at a time. Just say 'step by step'."
        )
    else:
        return (
            "Welcome to NutriShield!\n\n"
            "I'm your personal nutrition & biodefense assistant. "
            "Let's set up your profile!\n\n"
            f"{STEP_PROMPTS['name']}"
        )


def cmd_reset(user_id: str) -> str:
    """Clear profile and restart."""
    delete_profile(user_id)
    _conversations.pop(user_id, None)
    _fallback_fields.pop(user_id, None)
    _flock_cb.force_reset()
    return "Profile cleared! Send /start to begin again."


def cmd_profile(user_id: str) -> str:
    """View current profile."""
    profile = load_profile(user_id)
    if profile is None:
        return "No profile found. Send /start to begin onboarding."
    link_code = get_link_code(user_id)
    return (
        "Your Profile:\n"
        "--------------------\n"
        f"Name: {profile.get('name', '?')}\n"
        f"Age: {profile.get('age', '?')}\n"
        f"Sex: {profile.get('sex', '?')}\n"
        f"Weight: {profile.get('weight', '?')} kg\n"
        f"Height: {profile.get('height', '?')} cm\n"
        f"Allergies: {profile.get('allergies', '?')}\n"
        f"Diet: {profile.get('diet', '?')}\n"
        f"Goal: {profile.get('goal', '?')}\n"
        f"City: {profile.get('city', '?')}\n"
        "--------------------\n"
        f"Link code: {link_code}\n\n"
        "To update a field, just tell me (e.g., 'update my weight to 80kg').\n"
        "Use /reset to start over."
    )


def cmd_plan(user_id: str) -> str:
    """Calculate and show macro targets."""
    profile = load_profile(user_id)
    if profile is None:
        return "Please complete your profile first! Send /start to begin."
    macros = calculate_macros(profile)
    return format_macros(macros, profile.get("name", "Your"))


def cmd_threats(user_id: str) -> str:
    """Check local threats (placeholder — will delegate to threat_agent)."""
    profile = load_profile(user_id)
    if profile is None:
        return "Please complete your profile first! Send /start to begin."
    city = profile.get("city", "Unknown")
    return (
        f"Scanning threats for {city}...\n\n"
        f"AQI: 42 (Good)\n"
        f"Active Outbreaks: None detected\n"
        f"Status: All clear!\n\n"
        f"Threat scanning runs every 6 hours automatically."
    )


def cmd_help() -> str:
    """Show available commands."""
    return (
        "NutriShield Commands\n"
        "--------------------\n"
        "/start   — Begin or resume profile setup\n"
        "/plan    — View daily macro targets\n"
        "/threats — Check local health threats\n"
        "/profile — View your profile\n"
        "/link <code> — Link this channel to existing profile\n"
        "/reset   — Clear profile & start over\n"
        "/help    — Show this message"
    )


def cmd_link(user_id: str, code: str) -> str:
    """Link this channel to an existing profile by link code."""
    primary_id = find_by_link_code(code)
    if primary_id is None:
        return f"No profile found for code '{code}'. Check the code and try again."
    if primary_id == user_id:
        return "That's already your profile!"
    link_channel(primary_id, user_id)
    profile = load_profile(primary_id)
    name = profile.get("name", "there") if profile else "there"
    return f"Linked! Welcome back, {name}. Your profile is now shared on this channel."


# ═════════════════════════════════════════════════════════════
# MAIN ENTRY POINT (called by orchestrator)
# ═════════════════════════════════════════════════════════════

async def process_message(user_id: str, text: str) -> str:
    """
    Main entry point for the onboarding agent.
    Called by the orchestrator for each incoming message.

    Args:
        user_id: Unique channel-specific user ID (e.g., telegram chat_id)
        text: Raw message text from the user

    Returns:
        Reply text to send back to the user
    """
    text = text.strip()
    if not text:
        return "I didn't catch that. Could you try again?"

    # Handle slash commands
    if text.startswith("/"):
        return await handle_command(user_id, text)

    # Handle confirmation flow
    profile, is_complete = get_profile_or_partial(user_id)
    miss = missing_fields(profile)

    if not miss and not is_complete:
        # All fields collected but not yet confirmed
        lower = text.lower().strip()

        if lower in ("yes", "y", "confirm", "correct", "yep", "yeah", "looks good"):
            path = save_profile(user_id, profile)
            macros = calculate_macros(profile)
            return _format_profile_complete(
                {**profile, "_user_id": user_id}, macros
            )

        if lower in ("no", "n", "nope", "restart"):
            delete_profile(user_id)
            _conversations.pop(user_id, None)
            return "Let's start over! Send /start to begin again."

        if lower.startswith("edit "):
            field = lower[5:].strip()
            if field in REQUIRED_FIELDS:
                # Remove field so it becomes "missing" again
                profile.pop(field, None)
                save_partial(user_id, profile)
                current_val = profile.get(field, "not set")
                return f"Editing {field}. What's the new value?"
            valid_fields = ", ".join(REQUIRED_FIELDS)
            return f"Unknown field '{field}'. Editable fields: {valid_fields}"

        return "Please type 'yes' to confirm, 'no' to start over, or 'edit <field>' to change a value."

    # Handle "step by step" request
    if text.lower().strip() in ("step by step", "step-by-step", "one at a time"):
        if miss:
            next_field = miss[0]
            return f"Sure! Let's go step by step.\n\n{STEP_PROMPTS.get(next_field, f'What is your {next_field}?')}"

    # Route to agent mode (FLock) or fallback
    if FLOCK_API_KEY:
        log.info(f"[{user_id}] routing to agent mode")
        reply = await _handle_agent_mode(user_id, text)
        log.info(f"[{user_id}] agent reply: {reply[:100]!r}")
        return reply
    else:
        return await _handle_fallback_mode(user_id, text)
