"""
gateway_bridge.py — OpenClaw Gateway Bridge (Layer 2 HTTP Server)
=================================================================

Bridges OpenClaw Gateway ↔ Agent Orchestrator for multi-channel support.
This is the "Option B" deployment: self-hosted with OpenClaw Gateway.

Endpoints:
  POST /hooks/agent   — Receive user messages from OpenClaw Gateway
  POST /threat-alert  — Receive proactive threat alerts from Layer 3
  GET  /health        — Health check
  GET  /conversations — Debug: list tracked conversations

Architecture:
  User (Telegram / Discord / WhatsApp / WebChat / ...)
      │
      ▼
  OpenClaw Gateway (:18789) ── connects to all channels
      │ forwards user messages
      ▼
  Gateway Bridge (:18790) ← THIS SERVER
      │ calls orchestrator.route_message()
      │ returns reply → OpenClaw → user's channel
      │
      │ Also receives Layer 3 threat alerts
      │ and pushes proactive notifications via OpenClaw
      ▼
  Layer 3 Threat Backend (:8100)
      fires webhooks when threats change

Run:
  python gateway_bridge.py

  (start Layer 3 first, then this bridge, then OpenClaw gateway)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request

# ── Setup ───────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
load_dotenv(dotenv_path=str(ROOT / ".env"), override=True)

logging.basicConfig(
    level=logging.INFO,
    format="  [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("gateway_bridge")

# ── Config ──────────────────────────────────────────────────
GATEWAY_BRIDGE_PORT: int = int(os.getenv("GATEWAY_BRIDGE_PORT", "18790"))
OPENCLAW_GATEWAY_URL: str = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789").strip()
OPENCLAW_HOOKS_TOKEN: str = os.getenv("OPENCLAW_HOOKS_TOKEN", "").strip()
THREAT_BACKEND_URL: str = os.getenv("THREAT_BACKEND_URL", "http://127.0.0.1:8100").strip()

# ── FastAPI App ─────────────────────────────────────────────
app = FastAPI(
    title="OpenClaw Gateway Bridge",
    description="Bridges OpenClaw Gateway ↔ Agent Orchestrator for multi-channel support",
    version="1.0.0",
)


# ═════════════════════════════════════════════════════════════
# CONVERSATION REGISTRY
# ═════════════════════════════════════════════════════════════
# Maps channel-specific user_id → conversation metadata.
# Needed so proactive alerts can push back to the correct channel/user.

_conversations: dict[str, dict[str, Any]] = {}


# ═════════════════════════════════════════════════════════════
# INBOUND — OpenClaw sends user messages to us
# ═════════════════════════════════════════════════════════════

@app.post("/hooks/agent")
async def receive_from_openclaw(request: Request):
    """
    Receive a user message forwarded by OpenClaw Gateway.

    OpenClaw sends messages here when a user talks on any channel.
    We process through our orchestrator and return the reply.
    OpenClaw sends the reply back to the user's channel automatically.

    Expected payload (flexible — handles various OpenClaw shapes):
    {
        "type": "message",
        "text": "Hey I'm Sarah, 28, trying to lose weight",
        "userId": "12345678",
        "channel": "telegram",
        "conversationId": "conv_abc123",
        "metadata": { ... }
    }
    """
    try:
        payload = await request.json()
    except Exception:
        return {"error": "Invalid JSON payload", "reply": ""}

    # Extract fields — flexible to handle various OpenClaw payload shapes
    text = str(
        payload.get("text", payload.get("message", payload.get("content", "")))
    ).strip()
    channel = str(
        payload.get("channel", payload.get("platform", payload.get("source", "unknown")))
    )
    user_id = str(
        payload.get("userId", payload.get("user_id", payload.get("chatId", payload.get("chat_id", ""))))
    )
    conversation_id = str(
        payload.get("conversationId", payload.get("conversation_id", ""))
    )

    if not text or not user_id:
        return {"error": "Missing text or userId", "reply": "Please send a message to get started."}

    # Store conversation metadata for outbound pushes (proactive alerts)
    _conversations[user_id] = {
        "channel": channel,
        "conversation_id": conversation_id,
        "last_seen": __import__("time").time(),
        "raw_keys": list(payload.keys()),
    }

    log.info(f"[{channel}] user={user_id} text={text[:60]!r}")

    # Route through orchestrator
    from agents.orchestrator import process_webhook

    result = await process_webhook({
        "user_id": user_id,
        "text": text,
        "channel": channel,
    })

    reply = result.get("reply", "Something went wrong. Please try again.")
    log.info(f"[{channel}] user={user_id} reply_len={len(reply)}")

    return {"reply": reply, "status": "ok"}


# ═════════════════════════════════════════════════════════════
# OUTBOUND — Push messages to users via OpenClaw Gateway
# ═════════════════════════════════════════════════════════════

async def push_to_channel(user_id: str, text: str) -> bool:
    """
    Push a message to a user via OpenClaw Gateway.

    Used for proactive alerts (threat notifications, adapted meal plans).
    Posts to OpenClaw's /hooks endpoint, which sends to the correct channel.

    Args:
        user_id: Channel-specific user ID (e.g., Telegram chat_id)
        text: Message text to send

    Returns:
        True if message was sent successfully, False otherwise
    """
    conv = _conversations.get(user_id)
    if not conv:
        log.warning(f"No conversation metadata for {user_id} — cannot push via gateway")
        return False

    hooks_url = f"{OPENCLAW_GATEWAY_URL}/hooks"

    payload = {
        "type": "message",
        "text": text,
        "userId": user_id,
        "channel": conv["channel"],
        "conversationId": conv.get("conversation_id", ""),
    }

    headers = {"Content-Type": "application/json"}
    if OPENCLAW_HOOKS_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_HOOKS_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(hooks_url, json=payload, headers=headers)
            if resp.status_code == 200:
                log.info(f"Pushed to {user_id} via OpenClaw ({conv['channel']}): OK")
                return True
            else:
                log.warning(f"OpenClaw push failed ({resp.status_code}): {resp.text[:200]}")
                return False
    except Exception as e:
        log.error(f"OpenClaw push error for {user_id}: {e}")
        return False


# ═════════════════════════════════════════════════════════════
# LAYER 3 PROACTIVE ALERTS (threat-alert webhook receiver)
# ═════════════════════════════════════════════════════════════

@app.post("/threat-alert")
async def receive_threat_alert(request: Request):
    """
    Receive proactive threat alert from Layer 3.

    Layer 3 fires this when threats CHANGE for a city.
    We look up which users live in that city and push alerts via OpenClaw.
    """
    try:
        payload = await request.json()
    except Exception:
        return {"error": "Invalid JSON"}

    log.info(f"🔔 Threat alert received for {payload.get('city', '?')}")

    # Handle in background so we respond to Layer 3 quickly
    asyncio.create_task(_handle_gateway_proactive_alert(payload))
    return {"status": "accepted"}


async def _handle_gateway_proactive_alert(payload: dict[str, Any]) -> None:
    """
    Handle a proactive threat alert in gateway mode.

    Same logic as orchestrator._handle_proactive_alert() but pushes
    via OpenClaw Gateway instead of direct Telegram API.
    """
    from agents.orchestrator import (
        _city_users,
        nutrition_process,
    )

    city = payload.get("city", "")
    city_lower = city.lower().strip()
    report_text = payload.get("report_text", "")
    active_threats = payload.get("active_threats", [])
    priority_foods = payload.get("priority_foods", [])
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
             f"notifying {len(user_ids)} user(s) via OpenClaw")

    for user_id in user_ids:
        try:
            # 1. Send the threat report via OpenClaw
            alert_header = "🔔 *Proactive Threat Alert*\n\n"
            full_report = alert_header + report_text
            sent = await push_to_channel(user_id, full_report)

            if not sent:
                log.warning(f"  Could not push alert to {user_id} — no conversation metadata")
                continue

            log.info(f"  Alert sent to {user_id} via OpenClaw")

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
                        await push_to_channel(
                            user_id,
                            "🍽 *Auto-Adapted Meal Plan*\n\n" + adapted_reply,
                        )
                        log.info(f"  Auto-adapted meal plan sent to {user_id}")
                except Exception as e:
                    log.warning(f"  Meal adapt failed for {user_id}: {e}")

        except Exception as e:
            log.error(f"  Failed to notify {user_id}: {e}")


# ═════════════════════════════════════════════════════════════
# HEALTH & DEBUG
# ═════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    """Health check endpoint."""
    from agents.orchestrator import _city_users, _subscribed_cities

    total_users = sum(len(users) for users in _city_users.values())
    return {
        "status": "ok",
        "role": "gateway_bridge",
        "mode": "openclaw",
        "openclaw_gateway": OPENCLAW_GATEWAY_URL,
        "tracked_conversations": len(_conversations),
        "tracked_users": total_users,
        "channels_active": list(set(c["channel"] for c in _conversations.values())),
        "subscribed_cities": list(_subscribed_cities),
    }


@app.get("/conversations")
async def list_conversations():
    """Debug endpoint — list tracked conversations."""
    return {
        "count": len(_conversations),
        "conversations": {
            uid: {
                "channel": c["channel"],
                "conversation_id": c.get("conversation_id", ""),
            }
            for uid, c in _conversations.items()
        },
    }


# ═════════════════════════════════════════════════════════════
# STARTUP
# ═════════════════════════════════════════════════════════════

async def _auto_subscribe_existing_profiles() -> None:
    """
    On startup, scan existing profiles and subscribe their cities to Layer 3.
    Uses this bridge's /threat-alert endpoint as the callback URL.
    """
    from agents.orchestrator import _ensure_subscribed

    profiles_dir = ROOT / "data" / "profiles"
    if not profiles_dir.exists():
        return

    for profile_file in profiles_dir.glob("*.json"):
        if profile_file.name.endswith(".partial.json") or profile_file.name == "links.json":
            continue
        try:
            data = json.loads(profile_file.read_text(encoding="utf-8"))
            user_id = data.get("user_id", profile_file.stem)
            city = data.get("profile", {}).get("city", "")
            if city:
                await _ensure_subscribed(str(user_id), city)
        except Exception as e:
            log.warning(f"Auto-subscribe failed for {profile_file.name}: {e}")


@app.on_event("startup")
async def on_startup():
    """
    On startup:
    1. Configure orchestrator to use gateway bridge callback URL
    2. Auto-subscribe existing profiles to Layer 3
    """
    from agents import orchestrator

    # Tell orchestrator to use this bridge's URL for Layer 3 callbacks
    orchestrator.set_callback_url(f"http://127.0.0.1:{GATEWAY_BRIDGE_PORT}/threat-alert")

    # Tell orchestrator to use push_to_channel for outbound messages
    orchestrator.set_push_fn(push_to_channel)

    log.info(f"Gateway bridge configured — callback URL: http://127.0.0.1:{GATEWAY_BRIDGE_PORT}/threat-alert")

    # Auto-subscribe existing profiles
    try:
        await _auto_subscribe_existing_profiles()
    except Exception as e:
        log.warning(f"Auto-subscribe on startup failed: {e}")


# ═════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════

def main():
    """Start the gateway bridge server."""
    print("=" * 62)
    print("  NutriShield Gateway Bridge (OpenClaw ↔ Orchestrator)")
    print("=" * 62)
    print(f"  Bridge Port  : {GATEWAY_BRIDGE_PORT}")
    print(f"  OpenClaw     : {OPENCLAW_GATEWAY_URL}")
    print(f"  Layer 3      : {THREAT_BACKEND_URL}")
    print(f"  Hooks Token  : {'SET' if OPENCLAW_HOOKS_TOKEN else 'NOT SET'}")
    print()
    print("  Endpoints:")
    print(f"    POST /hooks/agent   — receive messages from OpenClaw")
    print(f"    POST /threat-alert  — receive alerts from Layer 3")
    print(f"    GET  /health        — health check")
    print(f"    GET  /conversations — debug: tracked conversations")
    print()
    print("  Make sure Layer 3 is running (python -m threat_backend)")
    print("  Then start OpenClaw:  cd openclaw && openclaw gateway --port 18789")
    print("=" * 62)
    print()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=GATEWAY_BRIDGE_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
