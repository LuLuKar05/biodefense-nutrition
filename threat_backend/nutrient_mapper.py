"""
nutrient_mapper.py — Map threats to protective nutrients & foods
================================================================
HYBRID approach:
  1. Disease-specific lookup: Check disease_nutrition_db.json for exact match
  2. Research agent: For unknown diseases, use FLock LLM research agent result
  3. Category fallback: Last resort, use the coarse 5-category mapping

Cross-references active threats with phytochemicals.json to recommend
specific compounds and food sources for immune/respiratory protection.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("nutrient_mapper")

# ── Load phytochemicals database ────────────────────────────
_PHYTO_DB: list[dict] | None = None


def _load_phyto() -> list[dict]:
    global _PHYTO_DB
    if _PHYTO_DB is None:
        path = Path(__file__).resolve().parent.parent / "data" / "phytochemicals.json"
        if path.exists():
            _PHYTO_DB = json.loads(path.read_text(encoding="utf-8"))
        else:
            log.warning(f"phytochemicals.json not found at {path}")
            _PHYTO_DB = []
    return _PHYTO_DB


# ── Load disease-specific nutrition database ────────────────
_DISEASE_DB: dict[str, Any] | None = None


def _load_disease_db() -> dict[str, Any]:
    """Load disease_nutrition_db.json (diseases section)."""
    global _DISEASE_DB
    if _DISEASE_DB is None:
        path = Path(__file__).resolve().parent.parent / "data" / "disease_nutrition_db.json"
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            _DISEASE_DB = raw.get("diseases", {})
            log.info(f"Loaded disease nutrition DB: {len(_DISEASE_DB)} diseases")
        else:
            log.warning(f"disease_nutrition_db.json not found at {path}")
            _DISEASE_DB = {}
    return _DISEASE_DB


def get_disease_db() -> dict[str, Any]:
    """Public accessor for the disease DB (used by server.py)."""
    return _load_disease_db()


def _find_compound(name: str) -> dict | None:
    """Find a compound by name in the phytochemicals database."""
    for compound in _load_phyto():
        if compound["name"].lower() == name.lower():
            return compound
    return None


# ═════════════════════════════════════════════════════════════
# COARSE CATEGORY FALLBACK (original 5 categories)
# ═════════════════════════════════════════════════════════════

THREAT_NUTRIENT_MAP: dict[str, dict[str, Any]] = {
    "air_quality": {
        "description": "Antioxidants that protect lungs and reduce oxidative stress from air pollutants",
        "compounds": ["Quercetin", "EGCG", "Sulforaphane", "Lycopene", "Curcumin", "Resveratrol"],
        "general_advice": [
            "Increase antioxidant-rich foods (berries, leafy greens, tomatoes)",
            "Drink green tea — EGCG protects lung tissue from particulate damage",
            "Add cruciferous vegetables (broccoli, Brussels sprouts) for sulforaphane",
            "Stay hydrated to help your body clear inhaled particles",
        ],
    },
    "respiratory_virus": {
        "description": "Immune-boosting and antiviral compounds for respiratory protection",
        "compounds": ["Quercetin", "Allicin", "Gingerol", "EGCG", "Curcumin", "Ellagic Acid"],
        "general_advice": [
            "Eat raw garlic (allicin) — crush and wait 10min before cooking for max benefit",
            "Fresh ginger tea for anti-inflammatory airway support",
            "Quercetin-rich foods (red onions, apples, berries) support immune function",
            "Vitamin C and zinc-rich foods complement these phytochemicals",
        ],
    },
    "gi_pathogen": {
        "description": "Gut-protective and antimicrobial compounds for digestive defence",
        "compounds": ["Allicin", "Gingerol", "EGCG", "Capsaicin", "Curcumin"],
        "general_advice": [
            "Garlic and ginger have natural antimicrobial properties",
            "Green tea (EGCG) supports gut barrier function",
            "Turmeric reduces gut inflammation",
            "Ensure thorough cooking of meats and proper food hygiene",
        ],
    },
    "allergen": {
        "description": "Anti-inflammatory and antihistamine compounds for allergy relief",
        "compounds": ["Quercetin", "Luteolin", "Apigenin", "Naringenin", "Kaempferol"],
        "general_advice": [
            "Quercetin is a natural antihistamine — eat red onions, apples, capers",
            "Luteolin (celery, parsley) reduces inflammatory response",
            "Citrus fruits provide naringenin for anti-allergic effects",
            "Omega-3 rich foods (fatty fish, walnuts) complement these compounds",
        ],
    },
    "bacteria": {
        "description": "Antimicrobial and immune-supporting compounds",
        "compounds": ["Allicin", "EGCG", "Curcumin", "Ellagic Acid", "Diallyl Disulfide"],
        "general_advice": [
            "Garlic's allicin and DADS are natural broad-spectrum antimicrobials",
            "Green tea polyphenols support immune surveillance",
            "Pomegranates and berries provide ellagic acid for immune function",
            "Maintain good hygiene and ensure adequate protein for immune repair",
        ],
    },
}

PATHOGEN_TO_CATEGORY: dict[str, str] = {
    "virus": "respiratory_virus",
    "bacteria": "bacteria",
    "allergen": "allergen",
}

NAME_TO_CATEGORY: dict[str, str] = {
    "norovirus": "gi_pathogen",
    "e. coli": "gi_pathogen",
    "food safety": "gi_pathogen",
    "gastro": "gi_pathogen",
    "pollen": "allergen",
    "hay fever": "allergen",
}


def _resolve_category(threat: dict[str, Any]) -> str:
    """Determine the coarse nutrient mapping category for a threat."""
    if threat.get("type") == "air_quality":
        return "air_quality"
    name_lower = threat.get("name", "").lower()
    for keyword, category in NAME_TO_CATEGORY.items():
        if keyword in name_lower:
            return category
    pathogen_type = threat.get("pathogen_type", "")
    return PATHOGEN_TO_CATEGORY.get(pathogen_type, "respiratory_virus")


# ═════════════════════════════════════════════════════════════
# DISEASE-SPECIFIC MAPPING (primary path)
# ═════════════════════════════════════════════════════════════

def _disease_specific_mapping(
    threat: dict[str, Any],
    disease_key: str,
    disease_entry: dict[str, Any],
) -> dict[str, Any]:
    """
    Build a rich nutrient recommendation from a disease_nutrition_db entry.
    Enriches compounds with actual food source data from phytochemicals.json.
    """
    strategy = disease_entry.get("nutrition_strategy", {})
    compounds_raw = strategy.get("compounds", [])

    enriched_compounds = []
    for comp in compounds_raw:
        name = comp.get("name", "")
        phyto = _find_compound(name)
        enriched_compounds.append({
            "name": name,
            "mechanism": comp.get("mechanism", ""),
            "evidence": comp.get("evidence", ""),
            "food_sources": phyto["food_sources"] if phyto else [],
        })

    return {
        "threat_name": threat.get("name", "Unknown"),
        "disease_key": disease_key,
        "category": _resolve_category(threat),
        "mapping_source": "disease_db",
        "display_name": disease_entry.get("display_name", ""),
        "pathogen_type": disease_entry.get("pathogen_type", ""),
        "family": disease_entry.get("family", ""),
        "transmission": disease_entry.get("transmission", ""),
        "primary_goal": strategy.get("primary_goal", ""),
        "description": strategy.get("primary_goal", ""),
        "compounds": enriched_compounds,
        "additional_nutrients": strategy.get("additional_nutrients", []),
        "general_advice": strategy.get("dietary_advice", []),
    }


def _research_agent_mapping(
    threat: dict[str, Any],
    research_result: dict[str, Any],
) -> dict[str, Any]:
    """
    Build nutrient recommendation from a research agent (LLM) result.
    Same output structure as disease-specific mapping.
    """
    strategy = research_result.get("nutrition_strategy", {})
    compounds_raw = strategy.get("compounds", [])

    enriched_compounds = []
    for comp in compounds_raw:
        name = comp.get("name", "")
        phyto = _find_compound(name)
        enriched_compounds.append({
            "name": name,
            "mechanism": comp.get("mechanism", ""),
            "evidence": comp.get("evidence", ""),
            "food_sources": phyto["food_sources"] if phyto else [],
        })

    return {
        "threat_name": threat.get("name", "Unknown"),
        "disease_key": "unknown",
        "category": _resolve_category(threat),
        "mapping_source": "research_agent",
        "display_name": research_result.get("display_name", threat.get("name", "")),
        "pathogen_type": research_result.get("pathogen_type", "unknown"),
        "family": research_result.get("family", ""),
        "transmission": research_result.get("transmission", ""),
        "primary_goal": strategy.get("primary_goal", ""),
        "description": strategy.get("primary_goal", ""),
        "compounds": enriched_compounds,
        "additional_nutrients": strategy.get("additional_nutrients", []),
        "general_advice": strategy.get("dietary_advice", []),
    }


def _category_fallback_mapping(threat: dict[str, Any]) -> dict[str, Any]:
    """Coarse 5-category fallback (original behaviour)."""
    category = _resolve_category(threat)
    mapping = THREAT_NUTRIENT_MAP.get(category)

    if not mapping:
        return {
            "threat_name": threat.get("name", "Unknown"),
            "disease_key": "unknown",
            "category": "unknown",
            "mapping_source": "none",
            "description": "No specific nutrient mapping available",
            "compounds": [],
            "additional_nutrients": [],
            "general_advice": ["Maintain a balanced diet with plenty of fruits and vegetables"],
        }

    enriched_compounds = []
    for comp_name in mapping["compounds"]:
        compound = _find_compound(comp_name)
        if compound:
            enriched_compounds.append({
                "name": compound["name"],
                "mechanism": "",
                "evidence": "",
                "food_sources": compound["food_sources"],
            })
        else:
            enriched_compounds.append({
                "name": comp_name, "mechanism": "", "evidence": "", "food_sources": [],
            })

    return {
        "threat_name": threat.get("name", threat.get("type", "Unknown")),
        "disease_key": "unknown",
        "category": category,
        "mapping_source": "category_fallback",
        "description": mapping["description"],
        "compounds": enriched_compounds,
        "additional_nutrients": [],
        "general_advice": mapping["general_advice"],
    }


# ═════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════

def map_threat_to_nutrients(
    threat: dict[str, Any],
    *,
    research_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Given a single threat dict, return protective nutrient recommendations.
    Uses the 3-tier hybrid approach:
      1. Disease-specific DB (instant, evidence-based)
      2. Research agent result (LLM-generated, if available)
      3. Coarse category fallback

    Args:
        threat: A threat dict with 'type', 'name', 'pathogen_type', etc.
        research_results: Optional dict of disease_title_lower → research agent results.
    """
    from threat_backend.outbreak_fetcher import extract_disease_key

    # Air quality uses its own category
    if threat.get("type") == "air_quality":
        return _category_fallback_mapping(threat)

    # Try disease-specific lookup first
    disease_key = extract_disease_key(threat.get("name", ""))
    disease_db = _load_disease_db()

    if disease_key != "unknown" and disease_key in disease_db:
        return _disease_specific_mapping(threat, disease_key, disease_db[disease_key])

    # Try research agent result
    if research_results:
        title_key = threat.get("name", "").lower().strip()
        ra_result = research_results.get(disease_key) or research_results.get(title_key)
        if ra_result:
            return _research_agent_mapping(threat, ra_result)

    # Final fallback: coarse category
    return _category_fallback_mapping(threat)


def map_all_threats(
    threats: list[dict[str, Any]],
    *,
    research_results: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Map a list of threats to nutrient recommendations. Deduplicates by disease_key."""
    seen_keys: set[str] = set()
    results = []

    for threat in threats:
        mapped = map_threat_to_nutrients(threat, research_results=research_results)
        dedup_key = f"{mapped.get('disease_key', 'unknown')}|{mapped.get('category', 'unknown')}"
        if dedup_key not in seen_keys:
            seen_keys.add(dedup_key)
            results.append(mapped)

    return results


def get_priority_foods(
    threats: list[dict[str, Any]],
    top_n: int = 5,
    *,
    research_results: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    From all active threats, find the most impactful foods to eat.
    Returns top N foods ranked by how many threat categories they cover.
    """
    food_scores: dict[str, dict[str, Any]] = {}
    mappings = map_all_threats(threats, research_results=research_results)

    for mapping in mappings:
        for compound in mapping.get("compounds", []):
            for source in compound.get("food_sources", []):
                food_name = source.get("food", "") if isinstance(source, dict) else str(source)
                if not food_name:
                    continue
                serving = source.get("serving", "") if isinstance(source, dict) else ""
                if food_name not in food_scores:
                    food_scores[food_name] = {
                        "food": food_name,
                        "serving": serving,
                        "covers_threats": set(),
                        "compounds": set(),
                    }
                food_scores[food_name]["covers_threats"].add(
                    mapping.get("disease_key", mapping.get("category", "general"))
                )
                food_scores[food_name]["compounds"].add(compound["name"])

    ranked = sorted(
        food_scores.values(), key=lambda f: len(f["covers_threats"]), reverse=True
    )

    result = []
    for item in ranked[:top_n]:
        result.append({
            "food": item["food"],
            "serving": item["serving"],
            "covers_threats": sorted(item["covers_threats"]),
            "compounds": sorted(item["compounds"]),
            "score": len(item["covers_threats"]),
        })

    return result
