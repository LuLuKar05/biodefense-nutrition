"""
outbreak_fetcher.py — Real WHO Disease Outbreak News fetcher
============================================================
Fetches real-time outbreak data from the WHO DON public OData API.
Combines three tiers of relevance for UK users:

  1. UK-specific DON items  (highest relevance)
  2. European Region (EURO) items
  3. Global situation alerts

The WHO API is public (no auth needed) and uses OData query params:
  Base: https://www.who.int/api/news/diseaseoutbreaknews

Region filtering uses the `regionscountries` taxonomy GUID field
with OData `any()` lambda syntax.

Falls back to outbreak_mock.py when WHO API is unreachable.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

log = logging.getLogger("threat_backend.who")

# ── WHO DON OData API ───────────────────────────────────────
WHO_DON_BASE = "https://www.who.int/api/news/diseaseoutbreaknews"
WHO_TIMEOUT = 20  # seconds

# Region taxonomy GUIDs (discovered via API exploration)
WHO_GUIDS = {
    "uk":     "b1943768-3f01-4c73-a51b-5aae6e1e368a",
    "euro":   "1c8e9fa9-fd11-40bb-bf06-0ca52d115683",
    "global": "c66558ce-c652-4132-8846-32168dc47b54",
}

# Fields we actually need from each DON item
DON_SELECT = (
    "Title,DonId,Summary,PublicationDate,regionscountries,"
    "Advice,Assessment,Epidemiology,Overview"
)

# How many items to fetch per tier
ITEMS_PER_TIER = 10

# Items older than this are too stale to show as active threats
MAX_AGE_DAYS = 180

# ── Disease → threat category mapping ───────────────────────
# Maps keywords found in WHO DON titles to our threat categories
DISEASE_CATEGORY_MAP: dict[str, dict[str, str]] = {
    # Respiratory viruses
    r"influenza|flu":           {"pathogen_type": "virus",    "category": "respiratory_virus"},
    r"covid|sars-cov|coronavirus": {"pathogen_type": "virus", "category": "respiratory_virus"},
    r"rsv|respiratory syncytial": {"pathogen_type": "virus",  "category": "respiratory_virus"},
    r"mers":                    {"pathogen_type": "virus",    "category": "respiratory_virus"},
    r"diphtheria":              {"pathogen_type": "bacteria", "category": "respiratory_virus"},

    # Haemorrhagic / severe viruses
    r"ebola":                   {"pathogen_type": "virus",    "category": "respiratory_virus"},
    r"marburg":                 {"pathogen_type": "virus",    "category": "respiratory_virus"},
    r"nipah":                   {"pathogen_type": "virus",    "category": "respiratory_virus"},

    # Pox viruses
    r"mpox|monkeypox":          {"pathogen_type": "virus",    "category": "respiratory_virus"},
    r"measles":                 {"pathogen_type": "virus",    "category": "respiratory_virus"},

    # GI / food-borne
    r"cholera":                 {"pathogen_type": "bacteria", "category": "gi_pathogen"},
    r"norovirus":               {"pathogen_type": "virus",    "category": "gi_pathogen"},
    r"e\.\s*coli":              {"pathogen_type": "bacteria", "category": "gi_pathogen"},
    r"salmonella":              {"pathogen_type": "bacteria", "category": "gi_pathogen"},

    # Vector-borne / tropical
    r"dengue":                  {"pathogen_type": "virus",    "category": "respiratory_virus"},
    r"chikungunya":             {"pathogen_type": "virus",    "category": "respiratory_virus"},
    r"zika":                    {"pathogen_type": "virus",    "category": "respiratory_virus"},
    r"yellow fever":            {"pathogen_type": "virus",    "category": "respiratory_virus"},
    r"rift valley":             {"pathogen_type": "virus",    "category": "respiratory_virus"},

    # Bacterial
    r"anthrax":                 {"pathogen_type": "bacteria", "category": "bacteria"},
    r"plague":                  {"pathogen_type": "bacteria", "category": "bacteria"},
    r"meningococcal":           {"pathogen_type": "bacteria", "category": "bacteria"},
    r"klebsiella|amr|antimicrobial": {"pathogen_type": "bacteria", "category": "bacteria"},
    r"streptococcus|strep":     {"pathogen_type": "bacteria", "category": "bacteria"},
    r"legionella":              {"pathogen_type": "bacteria", "category": "bacteria"},

    # Other
    r"rabies":                  {"pathogen_type": "virus",    "category": "respiratory_virus"},
    r"poliovirus|polio":        {"pathogen_type": "virus",    "category": "respiratory_virus"},
    r"oropouche":               {"pathogen_type": "virus",    "category": "respiratory_virus"},
}

# Severity scoring based on relevance tier
TIER_SEVERITY = {
    "uk":     "high",       # UK-specific → high priority
    "euro":   "moderate",   # European Region → moderate
    "global": "low",        # Global alerts → awareness level
}

# ── Shared cache for WHO data ───────────────────────────────
_who_cache: dict[str, Any] = {
    "items": [],
    "fetched_at": None,
    "error": None,
}

# Cache lifetime — re-fetch from WHO every 2 hours
WHO_CACHE_TTL = timedelta(hours=2)


# ═════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═════════════════════════════════════════════════════════════

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE = re.compile(r"\s{2,}")


def _strip_html(raw: str | None, max_chars: int = 600) -> str:
    """Strip HTML tags from WHO rich-text fields and truncate."""
    if not raw:
        return ""
    text = _HTML_TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text


def _classify_disease(title: str) -> dict[str, str]:
    """Extract disease category and pathogen type from a DON title."""
    title_lower = title.lower()
    for pattern, info in DISEASE_CATEGORY_MAP.items():
        if re.search(pattern, title_lower):
            return info
    return {"pathogen_type": "unknown", "category": "unknown"}


def extract_disease_key(title: str) -> str:
    """
    Extract a normalised disease key from a WHO DON title.
    Returns a key matching disease_nutrition_db.json, or "unknown".

    Examples:
      "Mpox – Multi-country outbreak" → "mpox"
      "Influenza A(H5N1) – United Kingdom" → "influenza"
      "Novel Virus X – Global situation" → "unknown"
    """
    title_lower = title.lower()
    KEY_PATTERNS: list[tuple[str, str]] = [
        (r"influenza|flu",                  "influenza"),
        (r"mpox|monkeypox",                 "mpox"),
        (r"covid|sars-cov-2|coronavirus",   "covid"),
        (r"ebola",                          "ebola"),
        (r"cholera",                        "cholera"),
        (r"measles",                        "measles"),
        (r"dengue",                         "dengue"),
        (r"nipah",                          "nipah"),
        (r"marburg",                        "marburg"),
        (r"norovirus",                      "norovirus"),
        (r"mers",                           "mers"),
    ]
    for pattern, key in KEY_PATTERNS:
        if re.search(pattern, title_lower):
            return key
    return "unknown"


def _extract_location(title: str) -> str:
    """
    Extract country/region from DON title.
    WHO titles follow patterns like:
      "Disease Name - Country/Region"
      "Disease Name – Global situation"
    """
    # Try dash separators (WHO uses both - and –)
    for sep in [" – ", " - ", "- ", " –"]:
        if sep in title:
            return title.split(sep, 1)[1].strip()
    return "Global"


def _calculate_age_days(pub_date_str: str) -> int:
    """Calculate days since publication."""
    try:
        pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - pub_date
        return max(0, delta.days)
    except (ValueError, TypeError):
        return 999  # treat unparseable dates as old


def _severity_for_age(base_severity: str, age_days: int) -> str:
    """Downgrade severity for older items."""
    levels = ["none", "low", "moderate", "high", "severe"]
    idx = levels.index(base_severity) if base_severity in levels else 1

    if age_days > 90:
        idx = max(idx - 2, 0)
    elif age_days > 30:
        idx = max(idx - 1, 0)

    return levels[idx]


# ═════════════════════════════════════════════════════════════
# WHO API FETCHING
# ═════════════════════════════════════════════════════════════

async def _fetch_tier(
    client: httpx.AsyncClient,
    tier: str,
    guid: str,
) -> list[dict[str, Any]]:
    """
    Fetch DON items for a single relevance tier.
    Uses OData filter: regionscountries/any(r: r eq <guid>)
    """
    params = {
        "$top": str(ITEMS_PER_TIER),
        "$select": DON_SELECT,
        "$orderby": "PublicationDate desc",
        "$filter": f"regionscountries/any(r: r eq {guid})",
    }

    try:
        resp = await client.get(WHO_DON_BASE, params=params, timeout=WHO_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("value", [])

        results = []
        for item in items:
            age = _calculate_age_days(item.get("PublicationDate", ""))
            if age > MAX_AGE_DAYS:
                continue

            disease_info = _classify_disease(item.get("Title", ""))
            base_severity = TIER_SEVERITY.get(tier, "low")
            severity = _severity_for_age(base_severity, age)
            location = _extract_location(item.get("Title", ""))

            results.append({
                "don_id": item.get("DonId", ""),
                "title": item.get("Title", ""),
                "summary": (item.get("Summary") or "")[:300],
                "published": item.get("PublicationDate", ""),
                "age_days": age,
                "tier": tier,
                "location": location,
                "pathogen_type": disease_info["pathogen_type"],
                "category": disease_info["category"],
                "severity": severity,
                # Rich WHO fields (HTML-stripped)
                "who_advice": _strip_html(item.get("Advice")),
                "who_assessment": _strip_html(item.get("Assessment")),
                "who_epidemiology": _strip_html(item.get("Epidemiology"), max_chars=400),
                "who_overview": _strip_html(item.get("Overview"), max_chars=400),
            })

        log.info(f"  WHO tier '{tier}': {len(results)} items (of {len(items)} fetched)")
        return results

    except httpx.HTTPStatusError as e:
        log.warning(f"  WHO tier '{tier}' HTTP error: {e.response.status_code}")
        return []
    except (httpx.RequestError, Exception) as e:
        log.warning(f"  WHO tier '{tier}' request error: {e}")
        return []


async def fetch_who_outbreaks() -> list[dict[str, Any]]:
    """
    Fetch outbreak data from all three WHO tiers.
    Results are cached and shared across all cities.
    Returns a combined, deduplicated list sorted by relevance.
    """
    global _who_cache

    # Check cache
    if (_who_cache["fetched_at"]
            and datetime.now(timezone.utc) - _who_cache["fetched_at"] < WHO_CACHE_TTL
            and _who_cache["items"]):
        log.debug("Using cached WHO data")
        return _who_cache["items"]

    log.info("Fetching fresh WHO DON data...")
    all_items: list[dict[str, Any]] = []
    seen_don_ids: set[str] = set()

    async with httpx.AsyncClient() as client:
        # Fetch tiers in priority order
        for tier, guid in WHO_GUIDS.items():
            tier_items = await _fetch_tier(client, tier, guid)
            for item in tier_items:
                don_id = item["don_id"]
                if don_id and don_id not in seen_don_ids:
                    seen_don_ids.add(don_id)
                    all_items.append(item)

    # Sort: UK first, then EURO, then global; within tier, newest first
    tier_priority = {"uk": 0, "euro": 1, "global": 2}
    all_items.sort(key=lambda x: (tier_priority.get(x["tier"], 9), x["age_days"]))

    # Update cache
    _who_cache = {
        "items": all_items,
        "fetched_at": datetime.now(timezone.utc),
        "error": None,
    }

    log.info(f"WHO DON: {len(all_items)} unique items cached "
             f"(UK={sum(1 for i in all_items if i['tier']=='uk')}, "
             f"EURO={sum(1 for i in all_items if i['tier']=='euro')}, "
             f"Global={sum(1 for i in all_items if i['tier']=='global')})")

    return all_items


# ═════════════════════════════════════════════════════════════
# PUBLIC API — drop-in compatible with outbreak_mock
# ═════════════════════════════════════════════════════════════

async def generate_outbreaks_from_who(
    city_name: str,
    country: str,
) -> list[dict[str, Any]]:
    """
    Generate outbreak data for a city using real WHO DON data,
    supplemented with UK seasonal patterns from the mock module.

    Returns the same dict structure as outbreak_mock.generate_outbreaks()
    so it's a drop-in replacement in server.py.

    Strategy:
      - WHO DON: real global/regional outbreak intelligence (verified data)
      - Mock seasonal: UK-specific patterns (flu season, norovirus, HAy fever etc.)
      - Both are clearly labelled by source so the UI can distinguish them.
    """
    # Big cities get a small severity boost
    BIG_CITIES = {
        "London", "Birmingham", "Manchester", "Leeds", "Liverpool",
        "Glasgow", "Edinburgh", "Sheffield", "Bristol", "Newcastle",
    }
    is_big_city = city_name in BIG_CITIES
    severity_levels = ["none", "low", "moderate", "high", "severe"]

    results: list[dict[str, Any]] = []

    # ── Part 1: Real WHO DON alerts ──────────────────────────
    who_items = await fetch_who_outbreaks()

    for item in who_items:
        severity = item["severity"]

        # Severity boost for big cities
        if is_big_city and severity != "none":
            idx = severity_levels.index(severity) if severity in severity_levels else 1
            severity = severity_levels[min(idx + 1, 4)]

        is_threat = severity_levels.index(severity) >= 2 if severity in severity_levels else False

        desc = f"[WHO {item['don_id']}] {item['title']}"
        if item["summary"]:
            first_sentence = item["summary"].split(".")[0].strip()
            if first_sentence:
                desc = f"{first_sentence}."

        results.append({
            "type": "outbreak",
            "source": f"who_don_{item['tier']}",
            "name": item["title"],
            "pathogen_type": item["pathogen_type"],
            "severity": severity,
            "description": desc,
            "is_threat": is_threat,
            "don_id": item["don_id"],
            "published": item["published"],
            "location": item["location"],
            "tier": item["tier"],
            "age_days": item["age_days"],
            # Rich WHO intelligence
            "who_advice": item.get("who_advice", ""),
            "who_assessment": item.get("who_assessment", ""),
            "who_epidemiology": item.get("who_epidemiology", ""),
            "who_overview": item.get("who_overview", ""),
        })

    # ── Part 2: UK seasonal patterns ─────────────────────────
    # These complement WHO data with local epidemiological knowledge
    from threat_backend.outbreak_mock import generate_outbreaks as mock_outbreaks
    seasonal = mock_outbreaks(city_name, country)

    # Avoid duplicating diseases already covered by WHO
    who_diseases = {r["name"].lower() for r in results}
    for item in seasonal:
        # Skip if WHO already has an alert for this disease family
        item_name_lower = item["name"].lower()
        is_duplicate = any(
            keyword in item_name_lower and any(keyword in wd for wd in who_diseases)
            for keyword in ["influenza", "flu", "mpox", "measles", "covid"]
        )
        if is_duplicate:
            continue

        # Mark seasonal items with their source
        item["source"] = "uk_seasonal"
        results.append(item)

    return results


# ═════════════════════════════════════════════════════════════
# CACHE INFO (for health endpoint)
# ═════════════════════════════════════════════════════════════

def get_who_cache_info() -> dict[str, Any]:
    """Return metadata about the WHO cache state."""
    return {
        "cached_items": len(_who_cache["items"]),
        "fetched_at": _who_cache["fetched_at"].isoformat() if _who_cache["fetched_at"] else None,
        "error": _who_cache["error"],
        "tiers": {
            tier: sum(1 for i in _who_cache["items"] if i.get("tier") == tier)
            for tier in WHO_GUIDS
        },
    }
