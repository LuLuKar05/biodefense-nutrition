"""
orchestrator.py — Personal Agent Orchestrator (Layer 2)
=======================================================
Per-user personal agent that:
  1. Routes Telegram messages to onboarding / nutrition / threat agents
  2. Receives proactive threat webhooks from Layer 3 backend
  3. Auto-subscribes to Layer 3 for the user's city
  4. Auto-chains threat alerts → meal adaptation

Design decisions:
  - Per-user personal agent instance (one per chat_id in demo)
  - Hybrid intent: slash commands route directly, free-text → FLock classifier
  - Orchestrator-managed chaining (threat→meal_adapt is automatic)
  - Force onboarding first (MVP)
  - resolve_user_id() called early so every agent gets canonical ID
  - Per-agent circuit breaker (closed → open → half-open)
  - Layer 3 webhook receiver on port 8200

Architecture:
  Layer 2 is PERSONAL — each user gets their own logical instance.
  Layer 3 sends anonymous webhooks (city change alerts).
  Layer 2 maps cities → users and pushes notifications.

Run modes:
  1. Standalone (Telegram polling + webhook receiver) — python -m agents.orchestrator
  2. Webhook (called by OpenClaw gateway) — import and call process_webhook()
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from agents.onboarding_agent import process_message as onboarding_process
from agents.nutrition_agent import process_message as nutrition_process
from agents.tools.profile_manager import (
    profile_exists,
    has_partial,
    resolve_user_id,
    load_profile,
)
from agents.tools.circuit_breaker import CircuitBreaker

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="  [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("orchestrator")

# ── Config ──────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=str(ROOT / ".env"))

TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_BASE: str = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

FLOCK_API_KEY: str = os.getenv("FLOCK_API_KEY", "").strip()
FLOCK_BASE_URL: str = os.getenv("FLOCK_BASE_URL", "https://api.flock.io/v1").strip()
FLOCK_MODEL: str = os.getenv("FLOCK_MODEL", "qwen3-30b-a3b-instruct-2507").strip()
THREAT_BACKEND_URL: str = os.getenv("THREAT_BACKEND_URL", "http://127.0.0.1:8100").strip()
WEBHOOK_RECEIVER_PORT: int = int(os.getenv("WEBHOOK_RECEIVER_PORT", "8200"))
GATEWAY_BRIDGE_PORT: int = int(os.getenv("GATEWAY_BRIDGE_PORT", "18790"))

# ── Gateway mode support ────────────────────────────────────
# These are set by gateway_bridge.py when running in OpenClaw mode.
# In standalone Telegram mode, they remain None and defaults are used.
_push_message_fn = None       # async fn(user_id, text) → None
_callback_url_override = None  # str — Layer 3 callback URL override


def set_push_fn(fn) -> None:
    """Set the outbound message push function (used by gateway_bridge)."""
    global _push_message_fn
    _push_message_fn = fn
    log.info("Push function set — gateway mode active")


def set_callback_url(url: str) -> None:
    """Override the Layer 3 webhook callback URL (used by gateway_bridge)."""
    global _callback_url_override
    _callback_url_override = url
    log.info(f"Callback URL overridden: {url}")


def _get_callback_url() -> str:
    """Get the callback URL for Layer 3 subscriptions."""
    if _callback_url_override:
        return _callback_url_override
    return f"http://127.0.0.1:{WEBHOOK_RECEIVER_PORT}/threat-alert"


async def push_message(user_id: str, text: str) -> None:
    """
    Send a message to the user via the appropriate channel.

    In gateway mode (OpenClaw): uses push_to_channel() via OpenClaw Gateway.
    In standalone mode: uses direct Telegram API.
    """
    if _push_message_fn:
        await _push_message_fn(user_id, text)
    else:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await send_telegram(client, int(user_id), text)


# ── Circuit breaker for intent classifier ───────────────────
_intent_cb = CircuitBreaker(name="intent_classifier", max_failures=3, cooldown_secs=60.0)

# ── Valid intents ───────────────────────────────────────────
VALID_INTENTS = {"onboarding", "nutrition", "threat", "meal_adapt", "general"}

# ── Slash command → agent mapping ───────────────────────────
COMMAND_ROUTES: dict[str, str] = {
    "/start": "onboarding",
    "/reset": "onboarding",
    "/help": "onboarding",
    "/commands": "onboarding",
    "/profile": "onboarding",
    "/plan": "nutrition",
    "/accept": "nutrition",
    "/regenerate": "nutrition",
    "/log": "nutrition",
    "/today": "nutrition",
    "/balance": "nutrition",
    "/threats": "threat",
}

# ═════════════════════════════════════════════════════════════
# PER-USER CITY → CHAT_ID REGISTRY (Layer 2 — personal mapping)
# ═════════════════════════════════════════════════════════════
# Maps city_lower → set of chat_ids that live there.
# When Layer 3 sends a webhook for a city, we look up which users to notify.
# This is Layer 2's PERSONAL data — never shared with Layer 3.

_city_users: dict[str, set[str]] = {}   # city_lower → {chat_id, ...}
_user_cities: dict[str, str] = {}       # chat_id → city_lower (for unsub when city changes)
_subscribed_cities: set[str] = set()    # cities we've subscribed to in Layer 3


# ═════════════════════════════════════════════════════════════
# INTENT CLASSIFICATION
# ═════════════════════════════════════════════════════════════

def _classify_intent_rules(user_id: str, text: str) -> str:
    """
    Rule-based intent fallback when FLock is unavailable.
    Fast, no API call, covers common patterns.
    """
    lower = text.strip().lower()

    # Update keywords → onboarding (profile edits)
    update_words = ("update", "change", "edit", "new weight", "new height", "new goal", "new diet")
    if any(w in lower for w in update_words):
        return "onboarding"

    # Nutrition keywords
    nutrition_words = ("meal", "food", "eat", "recipe", "calorie", "macro", "diet plan", "nutrition", "breakfast", "lunch", "dinner")
    if any(w in lower for w in nutrition_words):
        return "nutrition"

    # Threat keywords
    threat_words = ("threat", "outbreak", "virus", "aqi", "air quality", "pathogen", "disease", "pandemic")
    if any(w in lower for w in threat_words):
        return "threat"

    return "general"


async def _classify_intent_llm(text: str) -> str | None:
    """
    Use FLock API to classify user intent. Returns intent string or None on failure.
    Costs one short API call per free-text message.
    """
    if not FLOCK_API_KEY:
        return None
    if not _intent_cb.should_call():
        return None

    system_prompt = (
        "You are an intent classifier for a nutrition and biodefense assistant.\n"
        "Classify the user's message into exactly ONE of these intents:\n"
        "  onboarding — user is providing personal info, profile data, or wants to set up/edit profile\n"
        "  nutrition  — user asks about meals, recipes, macros, diet plans, food recommendations\n"
        "  threat     — user asks about health threats, outbreaks, air quality, pathogens\n"
        "  meal_adapt — user asks to adapt meals based on threats or health conditions\n"
        "  general    — greetings, small talk, or anything that doesn't fit above\n\n"
        "Respond with ONLY the intent word. No explanation, no punctuation, no extra text."
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{FLOCK_BASE_URL}/chat/completions",
                headers={
                    "x-litellm-api-key": FLOCK_API_KEY,
                    "Content-Type": "application/json",
                    "accept": "application/json",
                },
                json={
                    "model": FLOCK_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 20,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        raw = data["choices"][0]["message"]["content"].strip().lower()
        # Strip <think> tags if present
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        # Extract first word
        intent = raw.split()[0].strip(".,!?\"'") if raw else ""
        if intent in VALID_INTENTS:
            _intent_cb.record_success()
            log.info(f"LLM intent: {intent!r}")
            return intent
        else:
            log.warning(f"LLM returned invalid intent: {raw!r}, falling back to rules")
            _intent_cb.record_success()  # API worked, just unexpected output
            return None

    except Exception as exc:
        _intent_cb.record_failure()
        log.error(f"Intent classifier failed: {type(exc).__name__}: {exc}")
        return None


async def detect_intent(user_id: str, text: str) -> str:
    """
    Hybrid intent detection:
      1. Slash commands → route directly (no LLM call)
      2. No profile → force onboarding (MVP)
      3. Partial profile → continue onboarding
      4. Free-text → FLock classification → fallback to rules
    """
    stripped = text.strip()
    lower = stripped.lower()

    # ── Slash commands: instant routing ──
    if stripped.startswith("/"):
        # Handle /link specially
        if lower.startswith("/link"):
            return "onboarding"
        cmd = lower.split()[0]  # e.g., "/plan"
        if cmd in COMMAND_ROUTES:
            return COMMAND_ROUTES[cmd]
        return "onboarding"  # unknown commands → onboarding handles error

    # ── MVP: force onboarding if no complete profile ──
    if not profile_exists(user_id):
        return "onboarding"

    if has_partial(user_id):
        return "onboarding"

    # ── Profile complete: classify free-text via FLock ──
    llm_intent = await _classify_intent_llm(text)
    if llm_intent:
        return llm_intent

    # ── Fallback: rule-based classification ──
    return _classify_intent_rules(user_id, text)


# ═════════════════════════════════════════════════════════════
# AGENT ROUTING
# ═════════════════════════════════════════════════════════════

async def route_message(user_id: str, text: str) -> str:
    """
    Route a message to the appropriate agent and return the reply.
    Handles orchestrator-managed chaining for agents that return chain directives.
    """
    # ── Resolve canonical user ID early ──
    canonical_id = resolve_user_id(user_id)
    log.info(f"[{canonical_id}] routing (raw_id={user_id})")

    intent = await detect_intent(canonical_id, text)
    log.info(f"[{canonical_id}] intent={intent} text={text[:60]!r}")

    reply, chain = await _dispatch(canonical_id, intent, text)

    # ── Orchestrator-managed chaining ──
    # If an agent returns a chain directive, route to the next agent
    # (threat→meal_adapt is direct agent-to-agent, handled inside threat_agent)
    max_chains = 3  # safety limit
    while chain and max_chains > 0:
        next_intent = chain.get("to")
        context_data = chain.get("context", {})
        log.info(f"[{canonical_id}] chain: {intent} → {next_intent}")
        if next_intent not in VALID_INTENTS:
            break
        # Pass chain context as a special prefix
        chain_text = text
        if context_data:
            chain_text = f"[CHAIN_CONTEXT:{json.dumps(context_data)}] {text}"
        reply, chain = await _dispatch(canonical_id, next_intent, chain_text)
        max_chains -= 1

    return reply


async def _dispatch(
    user_id: str, intent: str, text: str
) -> tuple[str, dict[str, Any] | None]:
    """
    Dispatch to the correct agent. Returns (reply_text, chain_directive_or_None).
    Chain directive format: {"to": "agent_name", "context": {...}}
    """
    if intent == "onboarding":
        reply = await onboarding_process(user_id, text)
        # Auto-subscribe to Layer 3 when profile becomes complete with a city
        try:
            profile = load_profile(user_id)
            if profile:
                city = profile.get("city", "").strip()
                if city:
                    await _ensure_subscribed(user_id, city)
        except Exception:
            pass  # non-critical — subscribe will happen on /threats anyway
        return reply, None

    # ── Future agents (placeholder until implemented) ──

    if intent == "nutrition":
        if not profile_exists(user_id):
            return "Please complete your profile first! Send /start to begin.", None
        reply = await nutrition_process(user_id, text)
        return reply, None

    if intent == "threat":
        if not profile_exists(user_id):
            return "Please complete your profile first! Send /start to begin.", None
        reply, chain = await _handle_threat(user_id, text)
        return reply, chain

    if intent == "meal_adapt":
        # Meal adaptation is triggered via chain from threat handler
        if not profile_exists(user_id):
            return "Please complete your profile first! Send /start to begin.", None
        reply = await nutrition_process(user_id, text)
        return reply, None

    # general or unknown → onboarding handles it
    reply = await onboarding_process(user_id, text)
    return reply, None


# ═════════════════════════════════════════════════════════════
# THREAT HANDLER (slimmed — calls Layer 3 /report endpoint)
# ═════════════════════════════════════════════════════════════

_threat_cb = CircuitBreaker(name="threat_backend", max_failures=3, cooldown_secs=30.0)


async def _handle_threat(
    user_id: str, text: str
) -> tuple[str, dict[str, Any] | None]:
    """
    Handle /threats and threat-related requests.
    Calls Layer 3's /threats/{city}/report endpoint which returns:
      - Pre-formatted report text (ready for Telegram)
      - Chain context for meal adaptation
    Layer 2 just forwards the report and chains to nutrition agent.
    """
    profile = load_profile(user_id)
    if not profile:
        return "Profile not found. Send /start to set up.", None

    city = profile.get("city", "").strip()
    if not city:
        return (
            "I don't have your city on file. "
            "Please update your profile with your city so I can check local threats.\n"
            'Example: "update my city to London"',
            None,
        )

    # Ensure we're subscribed for proactive alerts
    await _ensure_subscribed(user_id, city)

    if not _threat_cb.should_call():
        return (
            "⚠️ Threat intelligence service is temporarily unavailable. "
            "Please try again in a minute.",
            None,
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{THREAT_BACKEND_URL}/threats/{city}/report")

        if resp.status_code == 404:
            _threat_cb.record_success()
            return (
                f"City '{city}' is not in our monitoring network yet.\n"
                f"We currently cover 25 UK cities. "
                f"Try updating your city to one of the major UK cities.",
                None,
            )

        if resp.status_code != 200:
            _threat_cb.record_failure()
            return "Threat service returned an error. Try again shortly.", None

        data = resp.json()
        _threat_cb.record_success()

    except Exception as e:
        _threat_cb.record_failure()
        log.error(f"Threat backend call failed: {e}")
        return (
            "⚠️ Can't reach the threat intelligence service.\n"
            "Make sure it's running: python -m threat_backend",
            None,
        )

    # Get pre-formatted report from Layer 3
    reply = data.get("report_text", "No report available.")

    # Chain context for meal adaptation (provided by Layer 3)
    chain = None
    chain_context = data.get("chain_context")
    if chain_context:
        chain = {"to": "meal_adapt", "context": chain_context}

    return reply, chain


# ═════════════════════════════════════════════════════════════
# AUTO-SUBSCRIBE — Register user's city with Layer 3
# ═════════════════════════════════════════════════════════════

async def _ensure_subscribed(user_id: str, city: str) -> None:
    """
    Ensure this user's city is subscribed to Layer 3 webhook alerts.
    Called on:
      - /threats command
      - Profile completion (city field set)
      - City update

    We track {chat_id → city} so we can unsub the old city when the user moves.
    Layer 3 only gets the anonymous callback URL — never the user_id.
    """
    city_lower = city.strip().lower()
    if not city_lower:
        return

    old_city = _user_cities.get(user_id, "")

    # If same city and already subscribed → no-op
    if old_city == city_lower and city_lower in _subscribed_cities:
        return

    # Remove from old city tracking
    if old_city and old_city != city_lower:
        _city_users.get(old_city, set()).discard(user_id)
        # If no users left for old city → could unsubscribe from Layer 3
        if not _city_users.get(old_city):
            _subscribed_cities.discard(old_city)
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"{THREAT_BACKEND_URL}/unsubscribe",
                        json={
                            "city": old_city,
                            "callback_url": _get_callback_url(),
                        },
                    )
                log.info(f"Unsubscribed from Layer 3 for: {old_city}")
            except Exception as e:
                log.warning(f"Failed to unsubscribe {old_city}: {e}")

    # Register user → city mapping
    _user_cities[user_id] = city_lower
    if city_lower not in _city_users:
        _city_users[city_lower] = set()
    _city_users[city_lower].add(user_id)

    # Subscribe to Layer 3 if not yet subscribed for this city
    if city_lower not in _subscribed_cities:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{THREAT_BACKEND_URL}/subscribe",
                    json={
                        "city": city,
                        "callback_url": _get_callback_url(),
                    },
                )
            if resp.status_code == 200:
                _subscribed_cities.add(city_lower)
                log.info(f"Subscribed to Layer 3 alerts for: {city}")
            else:
                log.warning(f"Subscribe failed ({resp.status_code}): {resp.text}")
        except Exception as e:
            log.warning(f"Could not subscribe to Layer 3 for {city}: {e}")


async def subscribe_on_profile_complete(user_id: str, city: str) -> None:
    """
    Called by onboarding agent when profile is completed or city is updated.
    Registers city subscription with Layer 3.
    """
    await _ensure_subscribed(user_id, city)


# ═════════════════════════════════════════════════════════════
# PROACTIVE ALERT HANDLER — webhook from Layer 3
# ═════════════════════════════════════════════════════════════

async def _handle_proactive_alert(payload: dict[str, Any]) -> None:
    """
    Handle a proactive threat alert webhook from Layer 3.
    Layer 3 fires this when threats CHANGE for a city.

    Steps:
      1. Look up which users live in that city
      2. Push the pre-formatted report to each user's Telegram
      3. Auto-chain: adapt their meal plan based on new threats
    """
    city = payload.get("city", "")
    city_lower = city.lower().strip()
    report_text = payload.get("report_text", "")
    active_threats = payload.get("active_threats", [])
    priority_foods = payload.get("priority_foods", [])
    nutrient_recs = payload.get("nutrient_recommendations", [])
    threat_count = payload.get("threat_count", 0)

    if not city_lower or not report_text:
        log.warning(f"Proactive alert missing city or report: {payload.keys()}")
        return

    # Find users in this city
    user_ids = _city_users.get(city_lower, set())
    if not user_ids:
        log.info(f"Proactive alert for {city} — no users subscribed in Layer 2")
        return

    log.info(f"🔔 Proactive alert for {city}: {threat_count} threats → "
             f"notifying {len(user_ids)} user(s)")

    # Push to each user (uses push_message which dispatches via gateway or Telegram)
    for user_id in user_ids:
        try:
            # 1. Send the threat report
            alert_header = "🔔 *Proactive Threat Alert*\n\n"
            full_report = alert_header + report_text
            await push_message(user_id, full_report)
            log.info(f"  Alert sent to {user_id}")

            # 2. Auto-chain: adapt meal plan based on threats
            if active_threats and priority_foods:
                chain_context = {
                    "threat_type": ", ".join(
                        t.get("name", t.get("type", ""))
                        for t in active_threats[:3]
                    ),
                    "recommendation": "Adapt meal plan based on new active health threats",
                    "boost_nutrients": [f["food"] for f in priority_foods[:5]],
                }
                chain_text = f"[CHAIN_CONTEXT:{json.dumps(chain_context)}] adapt meals for new threats"

                try:
                    adapted_reply = await nutrition_process(user_id, chain_text)
                    if adapted_reply:
                        await push_message(
                            user_id,
                            "🍽 *Auto-Adapted Meal Plan*\n\n" + adapted_reply,
                        )
                        log.info(f"  Auto-adapted meal plan sent to {user_id}")
                except Exception as e:
                    log.warning(f"  Meal adapt failed for {user_id}: {e}")

        except Exception as e:
            log.error(f"  Failed to notify {user_id}: {e}")


# ═════════════════════════════════════════════════════════════
# WEBHOOK MODE (for OpenClaw gateway)
# ═════════════════════════════════════════════════════════════

async def process_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Process a webhook payload from OpenClaw gateway.
    Expected format: {"channel": "telegram", "user_id": "...", "text": "..."}
    Returns: {"reply": "..."}
    """
    user_id = str(payload.get("user_id", payload.get("chat_id", "")))
    text = payload.get("text", payload.get("message", "")).strip()
    channel = payload.get("channel", "unknown")

    if not user_id or not text:
        return {"reply": "Invalid payload — missing user_id or text."}

    log.info(f"[webhook:{channel}] user={user_id} text={text[:60]!r}")
    reply = await route_message(user_id, text)
    return {"reply": reply}


# ═════════════════════════════════════════════════════════════
# WEBHOOK RECEIVER (Layer 3 → Layer 2 proactive alerts)
# ═════════════════════════════════════════════════════════════

async def _start_webhook_receiver() -> None:
    """
    Start a lightweight FastAPI server on WEBHOOK_RECEIVER_PORT (8200)
    to receive proactive threat alert webhooks from Layer 3.

    This is the mechanism by which the autonomous threat backend
    pushes real-time alerts to per-user personal agents.
    """
    from fastapi import FastAPI as WebhookApp
    import uvicorn

    webhook_app = WebhookApp(
        title="Personal Agent Webhook Receiver",
        description="Receives proactive threat alerts from Layer 3",
        version="1.0.0",
    )

    @webhook_app.post("/threat-alert")
    async def receive_threat_alert(payload: dict[str, Any]):
        """Receive proactive threat alert from Layer 3."""
        log.info(f"Webhook received: threat alert for {payload.get('city', '?')}")
        # Handle in background so we respond to Layer 3 quickly
        asyncio.create_task(_handle_proactive_alert(payload))
        return {"status": "accepted"}

    @webhook_app.get("/health")
    async def webhook_health():
        total_users = sum(len(users) for users in _city_users.values())
        return {
            "status": "ok",
            "role": "personal_agent_webhook_receiver",
            "tracked_users": total_users,
            "tracked_cities": list(_city_users.keys()),
            "subscribed_to_layer3": list(_subscribed_cities),
        }

    config = uvicorn.Config(
        webhook_app,
        host="127.0.0.1",
        port=WEBHOOK_RECEIVER_PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)
    log.info(f"Webhook receiver starting on port {WEBHOOK_RECEIVER_PORT}")
    await server.serve()


# ═════════════════════════════════════════════════════════════
# TELEGRAM POLLING MODE (standalone + webhook receiver)
# ═════════════════════════════════════════════════════════════

async def send_telegram(
    client: httpx.AsyncClient, chat_id: int, text: str
) -> None:
    """Send a Telegram message with Markdown, fallback to plain text."""
    try:
        resp = await client.post(
            f"{TELEGRAM_BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        )
        if resp.status_code != 200:
            # Markdown might have failed — retry as plain
            await client.post(
                f"{TELEGRAM_BASE}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
    except Exception as exc:
        log.error(f"Send error ({type(exc).__name__}): {exc!r}")


async def _auto_subscribe_existing_profiles() -> None:
    """
    On startup, scan existing profiles and subscribe their cities to Layer 3.
    This handles the case where the bot restarts and needs to re-register.
    """
    profiles_dir = ROOT / "data" / "profiles"
    if not profiles_dir.exists():
        return

    import glob
    for profile_file in profiles_dir.glob("*.json"):
        if profile_file.name.endswith(".partial.json"):
            continue
        try:
            import json as _json
            data = _json.loads(profile_file.read_text(encoding="utf-8"))
            user_id = data.get("user_id", profile_file.stem)
            city = data.get("profile", {}).get("city", "")
            if city:
                await _ensure_subscribed(str(user_id), city)
        except Exception as e:
            log.warning(f"Auto-subscribe failed for {profile_file.name}: {e}")


async def telegram_polling() -> None:
    """
    Run as a standalone Telegram bot with long-polling.
    Also starts the webhook receiver for proactive Layer 3 alerts.
    """
    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    # Start webhook receiver in background
    webhook_task = asyncio.create_task(_start_webhook_receiver())
    log.info("Webhook receiver task created")

    # Wait a moment for webhook server to start
    await asyncio.sleep(1.0)

    # Auto-subscribe existing profiles
    try:
        await _auto_subscribe_existing_profiles()
    except Exception as e:
        log.warning(f"Auto-subscribe on startup failed: {e}")

    offset = 0
    consecutive_errors = 0

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Verify bot
        try:
            resp = await client.get(f"{TELEGRAM_BASE}/getMe")
            bot_info = resp.json()["result"]
        except Exception as exc:
            print(f"ERROR: Cannot connect to Telegram: {exc}")
            sys.exit(1)

        bot_name = bot_info.get("username", "unknown")
        print("=" * 58)
        print("  NutriShield Personal Agent Orchestrator")
        print("=" * 58)
        print(f"  Bot      : @{bot_name}")
        print(f"  Mode     : Telegram polling + webhook receiver")
        flock_status = "CONNECTED" if FLOCK_API_KEY else "NOT SET (fallback mode)"
        print(f"  FLock API: {flock_status}")
        print(f"  Intent   : Hybrid (commands=direct, text=LLM+rules)")
        print(f"  Breaker  : {_intent_cb}")
        print(f"  Profiles : {ROOT / 'data' / 'profiles'}")
        print(f"  Webhook  : http://127.0.0.1:{WEBHOOK_RECEIVER_PORT}/threat-alert")
        print(f"  Layer 3  : {THREAT_BACKEND_URL}")
        print()
        print("  Commands: /start /plan /threats /profile /link /reset /help")
        print(f"  Send a message to @{bot_name} in Telegram.")
        print("  Press Ctrl+C to stop.")
        print("=" * 58)
        print()

        while True:
            try:
                resp = await client.get(
                    f"{TELEGRAM_BASE}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                    timeout=40.0,
                )
                updates = resp.json().get("result", [])
                consecutive_errors = 0

                for update in updates:
                    offset = update["update_id"] + 1
                    msg = update.get("message")
                    if msg is None:
                        continue

                    text = msg.get("text", "").strip()
                    chat_id = msg["chat"]["id"]
                    username = msg.get("from", {}).get("username", "unknown")

                    if not text:
                        continue

                    log.info(f"[@{username}] {text}")

                    # Route through orchestrator
                    reply = await route_message(str(chat_id), text)
                    log.info(f"[{chat_id}] reply length={len(reply)}, sending...")
                    await send_telegram(client, chat_id, reply)
                    log.info(f"[{chat_id}] sent OK")

            except httpx.ReadTimeout:
                continue
            except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
                consecutive_errors += 1
                wait = min(consecutive_errors * 3, 30)
                log.warning(f"Network error: {type(exc).__name__}, retry in {wait}s")
                await asyncio.sleep(wait)
            except KeyboardInterrupt:
                print("\n  Stopping orchestrator...")
                webhook_task.cancel()
                break
            except Exception as exc:
                consecutive_errors += 1
                wait = min(consecutive_errors * 3, 30)
                import traceback
                log.error(f"Error ({type(exc).__name__}): {exc!r}, retry in {wait}s\n{traceback.format_exc()}")
                await asyncio.sleep(wait)


# ═════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════

if __name__ == "__main__":
    asyncio.run(telegram_polling())
