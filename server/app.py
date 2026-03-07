"""
app.py — Threat Intelligence FastAPI Backend (Layer 3)
======================================================
Fully autonomous microservice that:
  1. Fetches AQI + outbreak data for 25 UK cities every hour
  2. Runs Amina AI protein analysis for unknown diseases
  3. Maps threats → protective nutrients via hybrid pipeline
  4. Detects NEW/CHANGED threats and fires webhooks to subscribers
  5. Serves cached results + pre-formatted reports via REST API

Architecture:
  Layer 3 is SHARED INFRASTRUCTURE — it knows about cities, threats,
  proteins, nutrition. It NEVER knows about users. Zero user data.

  Subscribers (Layer 2 personal agent instances) register with:
    POST /subscribe {city, callback_url}
  and receive proactive alerts when threats change.

Run: python -m threat_backend
    or: uvicorn server.app:app --port 8100
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.cities import CITIES, find_city, all_city_names
from server.aqi_fetcher import fetch_aqi
from server.weather_fetcher import fetch_weather
from server.outbreak_fetcher import (
    generate_outbreaks_from_who,
    get_who_cache_info,
    extract_disease_key,
)
from server.nutrient_mapper import (
    map_all_threats,
    get_priority_foods,
    get_disease_db,
)
from server.sequence_fetcher import (
    fetch_protein_sequence,
    get_sequence_cache_info,
)
from server.research_pipeline import (
    research_unknown_disease,
    get_research_cache_info,
    amina_cli_pipeline,
    format_docking_summary,
    ensure_amina_auth,
    run_structure_enrichment,
    predict_binding_sites,
    calculate_sasa,
)

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="  [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("server")

# ── Config ──────────────────────────────────────────────────
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(dotenv_path=os.path.join(ROOT_DIR, ".env"))

REFRESH_INTERVAL = 3600  # 1 hour

# ── In-memory caches ───────────────────────────────────────
_cache: dict[str, dict[str, Any]] = {}          # city → full data
_report_cache: dict[str, str] = {}               # city → formatted report text
_threat_fingerprints: dict[str, str] = {}        # city → hash of threat names (for diff)
_last_refresh: str = "never"
_refresh_count: int = 0

# Persistent cross-refresh caches — unchanged outbreaks skip
# the expensive bio-pipeline (NCBI, Amina CLI, FLock LLM)
_persistent_sequences: dict[str, Any] = {}       # title_lower → NCBI sequence data
_persistent_research: dict[str, Any] = {}         # title_lower → research/strategy result
_persistent_amina: dict[str, Any] = {}            # title_lower → amina CLI analysis
_prev_outbreak_fps: dict[str, str] = {}           # don_id → content fingerprint


# ═════════════════════════════════════════════════════════════
# SUBSCRIBER REGISTRY
# ═════════════════════════════════════════════════════════════
# Stores anonymous callback URLs per city.
# Layer 3 never knows WHO is subscribing — only that someone
# cares about threats in a particular city.
#
# Structure: {city_lower: {callback_id: callback_url, ...}}
# callback_id is a hash of the URL (for dedup + unsubscribe)

_subscribers: dict[str, dict[str, str]] = {}


class SubscribeRequest(BaseModel):
    city: str
    callback_url: str


class UnsubscribeRequest(BaseModel):
    city: str
    callback_url: str


def _callback_id(url: str) -> str:
    """Deterministic ID for a callback URL (for dedup)."""
    return hashlib.sha256(url.encode()).hexdigest()[:12]


# ═════════════════════════════════════════════════════════════
# THREAT REPORT FORMATTER (moved from orchestrator)
# ═════════════════════════════════════════════════════════════

def format_threat_report(data: dict[str, Any]) -> str:
    """
    Format threat backend data into a rich human-readable report.
    This is the Telegram-ready text that gets pushed to subscribers
    and served via /threats/{city}/report.
    """
    city = data.get("city", "Unknown")
    aqi = data.get("aqi", {})
    weather = data.get("weather", {})
    outbreaks = data.get("outbreaks", [])
    active_threats = data.get("active_threats", [])
    priority_foods = data.get("priority_foods", [])
    nutrient_recs = data.get("nutrient_recommendations", [])
    sequences = data.get("sequences", {})
    amina = data.get("amina_analyses", {})

    lines = [f"🛡 Threat Report: {city}", ""]

    # ── Weather section ──
    if weather.get("temp_c") is not None:
        temp = weather["temp_c"]
        feels = weather.get("feels_like_c", temp)
        cond = weather.get("condition_detail", "")
        humidity = weather.get("humidity", 0)
        lines.append(f"🌡 Weather: {temp}°C (feels {feels}°C) — {cond}")
        lines.append(f"  Humidity: {humidity}%")
        env_threats = weather.get("environmental_threats", [])
        for et in env_threats:
            sev = et.get("severity", "low")
            sev_emoji = {"low": "🟡", "moderate": "🟠", "high": "🔴"}.get(sev, "⚪")
            lines.append(f"  {sev_emoji} {et['name']}: {et.get('description', '')}")
        lines.append("")

    # ── AQI section ──
    aqi_idx = aqi.get("aqi_index", 0)
    aqi_label = aqi.get("aqi_label", "Unknown")
    aqi_emoji = {
        1: "🟢", 2: "🟡", 3: "🟠", 4: "🔴", 5: "🟣"
    }.get(aqi_idx, "⚪")
    lines.append(f"{aqi_emoji} Air Quality: {aqi_label} ({aqi_idx}/5)")
    components = aqi.get("components", {})
    if components:
        pm25 = components.get("pm2_5", 0)
        lines.append(f"  PM2.5: {pm25} µg/m³")
    lines.append("")

    # ── Outbreaks section (with WHO intelligence) ──
    if outbreaks:
        lines.append("🧬 Active Monitoring:")
        for ob in outbreaks[:6]:
            sev = ob.get("severity", "low")
            sev_emoji = {"low": "🟢", "moderate": "🟠", "high": "🔴", "severe": "🟣"}.get(sev, "⚪")
            source_tag = ""
            if ob.get("source", "").startswith("who_don"):
                tier = ob.get("tier", "")
                source_tag = f" [WHO-{tier.upper()}]"

            lines.append(f"  {sev_emoji} {ob['name']} ({sev}){source_tag}")
            lines.append(f"    {ob.get('description', '')}")

            who_advice = ob.get("who_advice", "")
            if who_advice:
                advice_snippet = who_advice[:150]
                if len(who_advice) > 150:
                    advice_snippet += "…"
                lines.append(f"    📋 WHO Advice: {advice_snippet}")
        lines.append("")
    else:
        lines.append("🧬 No disease outbreaks currently active.")
        lines.append("")

    # ── Threat summary ──
    if active_threats:
        lines.append(f"⚠️ {len(active_threats)} active threat(s) detected")
        lines.append("")
    else:
        lines.append("✅ No significant threats for your area!")
        lines.append("")

    # ── Disease-specific nutrition ──
    disease_recs = [r for r in nutrient_recs if r.get("mapping_source") in ("disease_db", "research_agent")]
    fallback_recs = [r for r in nutrient_recs if r.get("mapping_source") not in ("disease_db", "research_agent")]

    if disease_recs:
        lines.append("🔬 Disease-Specific Nutrition Intelligence:")
        for rec in disease_recs[:3]:
            display = rec.get("display_name", rec.get("threat_name", ""))
            source_label = "📚 Evidence-based" if rec.get("mapping_source") == "disease_db" else "🤖 AI-researched"
            lines.append(f"  {source_label} — {display}")

            goal = rec.get("primary_goal", "")
            if goal:
                lines.append(f"  🎯 Goal: {goal}")

            compounds = rec.get("compounds", [])
            for comp in compounds[:3]:
                mech = comp.get("mechanism", "")
                if mech:
                    mech_short = mech[:80] + "…" if len(mech) > 80 else mech
                    lines.append(f"    • {comp['name']}: {mech_short}")
                else:
                    lines.append(f"    • {comp['name']}")

            extra = rec.get("additional_nutrients", [])
            if extra:
                extra_names = ", ".join(n.get("nutrient", "") for n in extra[:3])
                lines.append(f"  ➕ Also important: {extra_names}")

            advice = rec.get("general_advice", [])
            for tip in advice[:2]:
                lines.append(f"    💡 {tip}")
            lines.append("")

    if fallback_recs:
        lines.append("🥦 General Protective Nutrition:")
        for rec in fallback_recs[:2]:
            lines.append(f"  • {rec.get('description', '')}")
            advice = rec.get("general_advice", [])
            if advice:
                lines.append(f"    💡 {advice[0]}")
        lines.append("")

    # ── Priority foods ──
    if priority_foods:
        lines.append("🍽 Top Foods to Eat Now:")
        for food in priority_foods[:5]:
            compounds = ", ".join(food.get("compounds", [])[:2])
            lines.append(f"  • {food['food']} ({food['serving']})")
            lines.append(f"    Contains: {compounds}")
        lines.append("")

    # ── Protein sequence info ──
    if sequences:
        lines.append("🧪 Pathogen Protein Data (NCBI):")
        for disease_key, seq_info in list(sequences.items())[:3]:
            if seq_info.get("protein_id"):
                lines.append(
                    f"  • {disease_key}: {seq_info.get('title', '')[:50]} "
                    f"({seq_info.get('length', 0)} aa)"
                )
        lines.append("")

    # ── Amina AI analysis ──
    if amina:
        lines.append("🧬 Amina AI Protein Analysis:")
        for disease_key, ai_data in list(amina.items())[:2]:
            summary = ai_data.get("amina_summary", "")
            if summary:
                # Show first 2 lines of summary
                summary_lines = summary.strip().split("\n")[:2]
                for sl in summary_lines:
                    lines.append(f"  {sl}")
            top = ai_data.get("top_compounds", [])
            if top:
                names = ", ".join(c.get("compound", "?") for c in top[:3])
                lines.append(f"  🎯 Top compounds: {names}")
        lines.append("")

    # ── Source notes ──
    source = aqi.get("source", "unknown")
    if source == "mock":
        lines.append("ℹ️ AQI data is simulated (set OWM_API_KEY for real data)")

    return "\n".join(lines)


def _compute_threat_fingerprint(city_data: dict[str, Any]) -> str:
    """Hash the set of active threat names for change detection."""
    threats = city_data.get("active_threats", [])
    names = sorted(t.get("name", t.get("type", "?")) for t in threats)
    severity = sorted(t.get("severity", "low") for t in threats)
    raw = "|".join(names) + "||" + "|".join(severity)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ═════════════════════════════════════════════════════════════
# WEBHOOK FIRE — notify subscribers of changed threats
# ═════════════════════════════════════════════════════════════

async def _fire_webhooks(changed_cities: list[str]) -> int:
    """
    POST to all subscribers for cities with new/changed threats.
    Returns the number of successful webhook deliveries.
    """
    if not changed_cities:
        return 0

    delivered = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for city_lower in changed_cities:
            subs = _subscribers.get(city_lower, {})
            if not subs:
                continue

            city_data = _cache.get(city_lower, {})
            report_text = _report_cache.get(city_lower, "")
            active_threats = city_data.get("active_threats", [])
            priority_foods = city_data.get("priority_foods", [])
            nutrient_recs = city_data.get("nutrient_recommendations", [])

            payload = {
                "event": "threat_alert",
                "city": city_data.get("city", city_lower),
                "report_text": report_text,
                "active_threats": active_threats,
                "threat_count": len(active_threats),
                "priority_foods": priority_foods,
                "nutrient_recommendations": nutrient_recs,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Fire to each subscriber for this city
            dead_callbacks: list[str] = []
            for cb_id, cb_url in subs.items():
                try:
                    resp = await client.post(cb_url, json=payload, timeout=8.0)
                    if resp.status_code < 400:
                        delivered += 1
                        log.info(f"  Webhook OK → {cb_url[:40]}... for {city_lower}")
                    else:
                        log.warning(f"  Webhook {resp.status_code} → {cb_url[:40]}... for {city_lower}")
                except Exception as e:
                    log.warning(f"  Webhook failed → {cb_url[:40]}...: {e}")
                    # Don't remove on first failure — could be temporary

    return delivered


def _outbreak_content_fingerprint(ob: dict[str, Any]) -> str:
    """Hash key content of a WHO outbreak for incremental change detection."""
    raw = "|".join([
        ob.get("don_id", ""),
        ob.get("name", ""),
        ob.get("who_advice", "")[:200],
        ob.get("who_overview", "")[:200],
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ═════════════════════════════════════════════════════════════
# DATA REFRESH
# ═════════════════════════════════════════════════════════════

async def refresh_all_cities() -> int:
    """
    Fetch fresh threat data for all 25 cities.
    Full hybrid pipeline:
      1. WHO outbreaks + AQI
      2. NCBI protein sequences
      3. Amina AI + Research agent for unknowns
      4. Nutrient mapping
      5. Change detection + webhook fire
    """
    global _last_refresh, _refresh_count
    global _prev_outbreak_fps, _persistent_sequences, _persistent_research, _persistent_amina
    log.info(f"Refreshing threat data for {len(CITIES)} cities...")

    # ── Step 1: Get WHO outbreaks once (shared) ──
    sample_outbreaks = await generate_outbreaks_from_who(CITIES[0]["name"], CITIES[0]["country"])

    # ── Step 1b: Incremental diff — identify new/changed outbreaks ──
    current_fps: dict[str, str] = {}
    changed_don_ids: set[str] = set()
    for ob in sample_outbreaks:
        don_id = ob.get("don_id", "") or ob.get("name", "")
        fp = _outbreak_content_fingerprint(ob)
        current_fps[don_id] = fp
        if _prev_outbreak_fps.get(don_id) != fp:
            changed_don_ids.add(don_id)
    reused_count = len(sample_outbreaks) - len(changed_don_ids)
    if reused_count and _refresh_count > 0:
        log.info(f"  Incremental: {len(changed_don_ids)} new/changed, "
                 f"{reused_count} unchanged (reusing cached results)")
    _prev_outbreak_fps = current_fps

    # ── Step 2: Classify known vs unknown diseases ─────────
    # KNOWN diseases:  Use disease_nutrition_db.json directly
    #                  → no amino acid sequence, no structure prediction
    # UNKNOWN diseases: NCBI sequence → ESMFold → DiffDock → Amina CLI
    disease_db = get_disease_db()
    # Pre-populate from persistent caches so unchanged outbreaks reuse previous results
    sequences: dict[str, Any] = dict(_persistent_sequences)
    research_results: dict[str, Any] = dict(_persistent_research)
    amina_results: dict[str, Any] = dict(_persistent_amina)

    # Evict cached results for changed outbreaks so they get re-processed
    for ob in sample_outbreaks:
        don_id = ob.get("don_id", "") or ob.get("name", "")
        if don_id in changed_don_ids:
            title_lower = ob.get("name", "").lower().strip()
            sequences.pop(title_lower, None)
            research_results.pop(title_lower, None)
            amina_results.pop(title_lower, None)

    known_count = 0
    unknown_count = 0
    for ob in sample_outbreaks:
        dkey = extract_disease_key(ob.get("name", ""))
        title = ob.get("name", "")
        title_lower = title.lower().strip()

        if dkey != "unknown":
            # ──────────────────────────────────────────────
            # KNOWN DISEASE — use existing DB (skip all bio-pipeline)
            # ──────────────────────────────────────────────
            db_entry = disease_db.get(dkey)
            if db_entry and db_entry.get("nutrition_strategy"):
                research_results[title_lower] = db_entry
                log.info(f"  Known disease '{dkey}' → DB lookup (no sequence needed)")
                known_count += 1
            continue

    log.info(f"Disease classification: {known_count} known (DB lookup)")

    # ── Step 3: Amina CLI pipeline for UNKNOWN diseases ──
    # Phase 2: NCBI amino acid sequence
    # Phase 3: ESMFold 3D structure prediction → .pdb
    # Phase 4: DiffDock molecular docking → binding scores
    # Phase 5: FLock LLM enrichment → nutrition strategy
    for ob in sample_outbreaks:
        dkey = extract_disease_key(ob.get("name", ""))
        if dkey != "unknown":
            continue
        title = ob.get("name", "")
        title_lower = title.lower().strip()
        if title_lower in research_results:
            continue
        unknown_count += 1
        try:
            disease_word = title.split("–")[0].split("-")[0].strip()
            ncbi_search = f"{disease_word} virus protein" if disease_word else ""
            protein_seq = ""
            protein_title_str = ""
            protein_organism_str = ""

            # Phase 2a: Fetch amino acid sequence from NCBI
            if ncbi_search:
                try:
                    seq_data = await fetch_protein_sequence(
                        ncbi_search, cache_key=f"unknown_{title_lower}"
                    )
                    protein_seq = seq_data.get("sequence", "")
                    protein_title_str = seq_data.get("title", "")
                    protein_organism_str = seq_data.get("organism", "")
                    if protein_seq:
                        sequences[title_lower] = seq_data
                        log.info(f"  NCBI sequence for unknown '{disease_word}': {len(protein_seq)} aa")
                except Exception as e:
                    log.warning(f"NCBI fetch for unknown '{title}': {e}")

            who_context = " ".join(filter(None, [
                ob.get("who_overview", ""),
                ob.get("who_advice", ""),
            ]))

            # Phase 3+4+5: Full Amina CLI pipeline (ESMFold → DiffDock → LLM)
            amina_result = None
            if protein_seq and len(protein_seq) >= 20:
                try:
                    amina_result = await amina_cli_pipeline(
                        sequence=protein_seq,
                        protein_title=protein_title_str,
                        protein_organism=protein_organism_str,
                        disease_title=title,
                        who_context=who_context,
                    )
                    if amina_result and not amina_result.get("error"):
                        amina_results[title_lower] = amina_result
                        phases = amina_result.get("phases_completed", [])
                        top_comp = amina_result.get("top_compounds", [{}])
                        top_name = top_comp[0].get("compound", "?") if top_comp else "?"
                        log.info(
                            f"  Amina CLI done for '{title}' — "
                            f"phases: {', '.join(phases)}, top: {top_name}"
                        )
                except Exception as e:
                    log.warning(f"Amina CLI pipeline failed for '{title}': {e}")

            # Use Amina CLI strategy if available, else fall back to research agent
            if amina_result and amina_result.get("nutrition_strategy"):
                research_results[title_lower] = amina_result["nutrition_strategy"]
                log.info(f"Using Amina CLI strategy for: {title}")
            else:
                result = await research_unknown_disease(
                    disease_title=title,
                    who_overview=ob.get("who_overview", ""),
                    who_advice=ob.get("who_advice", ""),
                    who_assessment=ob.get("who_assessment", ""),
                    who_epidemiology=ob.get("who_epidemiology", ""),
                    protein_sequence=protein_seq,
                    protein_title=protein_title_str,
                    protein_organism=protein_organism_str,
                )
                if result:
                    research_results[title_lower] = result
                    log.info(f"Research agent fallback strategy for: {title}")
        except Exception as e:
            log.warning(f"Unknown disease pipeline failed for '{title}': {e}")

    log.info(f"Pipeline summary: {known_count} known (DB), {unknown_count} unknown (Amina CLI), "
             f"{reused_count} reused from cache")

    # Persist bio-pipeline results for next refresh cycle
    _persistent_sequences = dict(sequences)
    _persistent_research = dict(research_results)
    _persistent_amina = dict(amina_results)

    # ── Step 4: Refresh each city + detect changes ──
    updated = 0
    changed_cities: list[str] = []

    for city in CITIES:
        try:
            city_name = city["name"]
            country = city["country"]
            lat = city["lat"]
            lon = city["lon"]
            city_lower = city_name.lower()

            aqi_data = await fetch_aqi(lat, lon, city_name)
            weather_data = await fetch_weather(lat, lon, city_name)
            outbreaks = await generate_outbreaks_from_who(city_name, country)

            all_threats = []
            if aqi_data.get("is_threat"):
                all_threats.append(aqi_data)
            # Weather & environmental threats
            for wt in weather_data.get("threats", []):
                if wt.get("is_threat"):
                    all_threats.append(wt)
            all_threats.extend([o for o in outbreaks if o.get("is_threat")])

            nutrient_recs = (
                map_all_threats(all_threats, research_results=research_results)
                if all_threats else []
            )
            priority_foods = (
                get_priority_foods(all_threats, research_results=research_results)
                if all_threats else []
            )

            city_data = {
                "city": city_name,
                "country": country,
                "aqi": aqi_data,
                "weather": {
                    "temp_c": weather_data.get("temp_c"),
                    "feels_like_c": weather_data.get("feels_like_c"),
                    "humidity": weather_data.get("humidity"),
                    "condition": weather_data.get("condition"),
                    "condition_detail": weather_data.get("condition_detail"),
                    "wind_speed_ms": weather_data.get("wind_speed_ms"),
                    "source": weather_data.get("source", "unknown"),
                    "environmental_threats": weather_data.get("threats", []),
                },
                "outbreaks": outbreaks,
                "active_threats": all_threats,
                "threat_count": len(all_threats),
                "nutrient_recommendations": nutrient_recs,
                "priority_foods": priority_foods,
                "sequences": {
                    k: {
                        "protein_id": v.get("protein_id", ""),
                        "title": v.get("title", ""),
                        "organism": v.get("organism", ""),
                        "length": v.get("length", 0),
                    }
                    for k, v in sequences.items()
                },
                "amina_analyses": {
                    k: {
                        "pipeline": v.get("pipeline", "amina_ai"),
                        "phases_completed": v.get("phases_completed", []),
                        "amina_summary": v.get("amina_summary", ""),
                        "top_compounds": v.get("top_compounds", []),
                        "protein_length": v.get("protein_analysis", {}).get("length", 0),
                        "motifs_found": len(v.get("protein_analysis", {}).get("motifs_found", [])),
                        "structural_hints": v.get("protein_analysis", {}).get("structural_hints", []),
                        "structure_prediction": {
                            sp_k: sp_v
                            for sp_k, sp_v in v.get("structure_prediction", {}).items()
                            if sp_k != "pdb_content"
                        } if v.get("structure_prediction") else {},
                        "docking_summary": v.get("docking_summary", ""),
                    }
                    for k, v in amina_results.items()
                },
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }

            # Store in cache
            _cache[city_lower] = city_data

            # Generate and cache formatted report
            _report_cache[city_lower] = format_threat_report(city_data)

            # ── Change detection ──
            new_fp = _compute_threat_fingerprint(city_data)
            old_fp = _threat_fingerprints.get(city_lower, "")
            if new_fp != old_fp and all_threats:
                # First refresh (_refresh_count == 0) is not a "change" — it's initial load
                if _refresh_count > 0:
                    changed_cities.append(city_lower)
                    log.info(f"  CHANGED: {city_name} — threats differ from last cycle")
            _threat_fingerprints[city_lower] = new_fp

            updated += 1

        except Exception as e:
            log.error(f"Failed to update {city.get('name', '?')}: {e}")

    _last_refresh = datetime.now(timezone.utc).isoformat()
    _refresh_count += 1
    log.info(f"Refresh complete: {updated}/{len(CITIES)} cities updated "
             f"(refresh #{_refresh_count})")

    # ── Step 5: Fire webhooks for changed cities ──
    if changed_cities:
        log.info(f"Firing webhooks for {len(changed_cities)} changed cities: "
                 f"{', '.join(changed_cities)}")
        delivered = await _fire_webhooks(changed_cities)
        log.info(f"Webhooks delivered: {delivered}")
    else:
        if _refresh_count > 1:
            log.info("No threat changes detected this cycle — no webhooks fired")

    return updated


async def _refresh_loop():
    """Background loop: refresh data every REFRESH_INTERVAL seconds."""
    await refresh_all_cities()

    while True:
        await asyncio.sleep(REFRESH_INTERVAL)
        try:
            await refresh_all_cities()
        except Exception as e:
            log.error(f"Refresh loop error: {e}")
            await asyncio.sleep(60)


# ═════════════════════════════════════════════════════════════
# FASTAPI APP
# ═════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Amina CLI authentication ──
    if ensure_amina_auth():
        log.info("Amina CLI authenticated — real ESMFold + DiffDock enabled (cloud GPU)")
    else:
        log.warning(
            "Amina CLI not authenticated — will use computational fallback. "
            "Set AMINA_API_KEY in .env (get one at https://app.aminoanalytica.com/settings/api)"
        )
    task = asyncio.create_task(_refresh_loop())
    log.info("Background refresh scheduler started")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    log.info("Background refresh scheduler stopped")


app = FastAPI(
    title="Biodefense Threat Intelligence",
    description=(
        "Layer 3 — Autonomous threat detection, Amina AI protein analysis, "
        "and proactive webhook alerts. Zero user data."
    ),
    version="2.0.0",
    lifespan=lifespan,
)


# ═════════════════════════════════════════════════════════════
# API ENDPOINTS — Data
# ═════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    """Health check with system status."""
    total_subs = sum(len(s) for s in _subscribers.values())
    return {
        "status": "ok",
        "cities_cached": len(_cache),
        "last_refresh": _last_refresh,
        "refresh_count": _refresh_count,
        "subscribers": total_subs,
        "subscribed_cities": list(_subscribers.keys()),
        "pipeline_cache": {
            "cached_sequences": len(_persistent_sequences),
            "cached_research": len(_persistent_research),
            "cached_amina": len(_persistent_amina),
            "tracked_outbreaks": len(_prev_outbreak_fps),
        },
        "who_cache": get_who_cache_info(),
        "sequence_cache": get_sequence_cache_info(),
        "research_cache": get_research_cache_info(),
    }


def _resolve_city_data(city: str) -> dict[str, Any] | None:
    """Look up city data from cache, with fuzzy matching."""
    city_data = _cache.get(city.strip().lower())
    if not city_data:
        matched = find_city(city)
        if matched:
            city_data = _cache.get(matched["name"].lower())
    return city_data


@app.get("/threats/{city}")
async def get_threats(city: str):
    """Full threat data JSON for a city (for API consumers)."""
    city_data = _resolve_city_data(city)
    if not city_data:
        raise HTTPException(
            status_code=404,
            detail=f"City '{city}' not found. Available: {', '.join(all_city_names())}",
        )
    return city_data


@app.get("/threats/{city}/report")
async def get_threat_report(city: str):
    """
    Pre-formatted threat report for a city.
    The personal orchestrator calls this when user sends /threats.
    Returns report text + chain data for meal adaptation.
    """
    city_data = _resolve_city_data(city)
    if not city_data:
        raise HTTPException(status_code=404, detail=f"City '{city}' not found")

    city_lower = city_data["city"].lower()
    report = _report_cache.get(city_lower, format_threat_report(city_data))

    active_threats = city_data.get("active_threats", [])
    priority_foods = city_data.get("priority_foods", [])

    # Build chain context for meal adaptation
    chain_context = None
    if active_threats and priority_foods:
        chain_context = {
            "threat_type": ", ".join(
                t.get("name", t.get("type", "")) for t in active_threats[:3]
            ),
            "recommendation": "Adapt meal plan based on active health threats",
            "boost_nutrients": [f["food"] for f in priority_foods[:5]],
        }

    return {
        "city": city_data["city"],
        "report_text": report,
        "threat_count": len(active_threats),
        "chain_context": chain_context,
        "last_updated": city_data.get("last_updated", ""),
    }


@app.get("/threats")
async def get_all_threats():
    """Summary of all cities."""
    summary = []
    for city_key, data in _cache.items():
        summary.append({
            "city": data["city"],
            "country": data["country"],
            "threat_count": data["threat_count"],
            "aqi_index": data["aqi"]["aqi_index"],
            "aqi_label": data["aqi"]["aqi_label"],
            "active_outbreaks": [o["name"] for o in data["outbreaks"] if o.get("is_threat")],
            "last_updated": data["last_updated"],
        })
    return {
        "total_cities": len(summary),
        "last_refresh": _last_refresh,
        "cities": sorted(summary, key=lambda c: c["threat_count"], reverse=True),
    }


@app.get("/cities")
async def list_cities():
    """List all monitored cities."""
    return {
        "total": len(CITIES),
        "cities": [
            {"name": c["name"], "country": c["country"]}
            for c in CITIES
        ],
    }


@app.get("/nutrients/{city}")
async def get_nutrients(city: str):
    """Nutrient recommendations only (lighter response)."""
    city_data = _resolve_city_data(city)
    if not city_data:
        raise HTTPException(status_code=404, detail=f"City '{city}' not found")

    return {
        "city": city_data["city"],
        "threat_count": city_data["threat_count"],
        "nutrient_recommendations": city_data["nutrient_recommendations"],
        "priority_foods": city_data["priority_foods"],
    }


# ═════════════════════════════════════════════════════════════
# API ENDPOINTS — Subscriber Management
# ═════════════════════════════════════════════════════════════

@app.post("/subscribe")
async def subscribe(req: SubscribeRequest):
    """
    Register an anonymous callback URL to receive threat alerts for a city.
    Layer 3 stores ONLY {city → callback_url}. No user data.
    """
    city_lower = req.city.strip().lower()

    # Verify city is monitored
    matched = find_city(req.city)
    if not matched and city_lower not in {c["name"].lower() for c in CITIES}:
        raise HTTPException(
            status_code=404,
            detail=f"City '{req.city}' not monitored. Available: {', '.join(all_city_names())}",
        )

    canonical_city = matched["name"].lower() if matched else city_lower
    cb_id = _callback_id(req.callback_url)

    if canonical_city not in _subscribers:
        _subscribers[canonical_city] = {}
    _subscribers[canonical_city][cb_id] = req.callback_url

    total_subs = sum(len(s) for s in _subscribers.values())
    log.info(f"Subscribe: {canonical_city} ← {req.callback_url[:40]}... "
             f"(total subscribers: {total_subs})")

    return {
        "status": "subscribed",
        "city": canonical_city,
        "callback_id": cb_id,
        "total_subscribers_for_city": len(_subscribers[canonical_city]),
    }


@app.post("/unsubscribe")
async def unsubscribe(req: UnsubscribeRequest):
    """Remove a callback registration."""
    city_lower = req.city.strip().lower()
    matched = find_city(req.city)
    canonical_city = matched["name"].lower() if matched else city_lower
    cb_id = _callback_id(req.callback_url)

    subs = _subscribers.get(canonical_city, {})
    if cb_id in subs:
        del subs[cb_id]
        log.info(f"Unsubscribe: {canonical_city} ✕ {req.callback_url[:40]}...")
        return {"status": "unsubscribed", "city": canonical_city}
    else:
        return {"status": "not_found", "city": canonical_city}


@app.get("/subscribers")
async def list_subscribers():
    """Admin/debug: show subscriber counts per city (no URLs exposed)."""
    return {
        "total": sum(len(s) for s in _subscribers.values()),
        "by_city": {
            city: len(subs) for city, subs in _subscribers.items() if subs
        },
    }
