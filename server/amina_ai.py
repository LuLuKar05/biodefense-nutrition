"""
amina_ai.py — Amino-acid Intelligence for Nutritional Antagonists
=================================================================
"Amina AI" — the core bioinformatics engine for unknown diseases.

When a new/unknown pathogen is detected via WHO and we have NO entry in
our static disease_nutrition_db, Amina AI takes the amino acid sequence
and predicts which food-derived phytochemicals could:

  1. BIND to the pathogen's key protein domains (block activity)
  2. INHIBIT enzymatic/structural functions (prevent replication)
  3. MODULATE immune response against the pathogen

Pipeline:
  NCBI Sequence → Amina AI Protein Analysis → Compound Scoring
    → FLock LLM Research Agent (enriched with binding predictions)
      → Nutritional Defence Strategy

Amina AI combines:
  A) Sequence analysis: identify motifs, domains, composition
  B) Compound-protein interaction scoring: predict binding affinity
     between each phytochemical (SMILES from phytochemicals.json)
     and the pathogen protein's structural features
  C) Ranking: which foods to prioritise based on predicted interaction

This is a computational prediction layer — NOT a replacement for
molecular dynamics or wet-lab validation. Scores represent estimated
interaction potential based on structural/chemical compatibility.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

log = logging.getLogger("threat_backend.amina_ai")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=str(ROOT / ".env"))

FLOCK_API_KEY: str = os.getenv("FLOCK_API_KEY", "").strip()
FLOCK_BASE_URL: str = os.getenv("FLOCK_BASE_URL", "https://api.flock.io/v1").strip()
FLOCK_MODEL: str = os.getenv("FLOCK_MODEL", "qwen3-30b-a3b-instruct-2507").strip()


# ═════════════════════════════════════════════════════════════
# AMINO ACID PROPERTY TABLES
# ═════════════════════════════════════════════════════════════

# Amino acid single-letter codes and properties
AA_PROPERTIES = {
    # aa: (hydrophobicity_index, molecular_weight, charge_at_pH7, category)
    "A": (1.8,   89.1,  0, "nonpolar"),
    "R": (-4.5, 174.2,  1, "positive"),
    "N": (-3.5, 132.1,  0, "polar"),
    "D": (-3.5, 133.1, -1, "negative"),
    "C": (2.5,  121.2,  0, "special"),   # disulphide bonds
    "E": (-3.5, 147.1, -1, "negative"),
    "Q": (-3.5, 146.2,  0, "polar"),
    "G": (-0.4,  75.0,  0, "nonpolar"),
    "H": (-3.2, 155.2,  0, "positive"),  # often in active sites
    "I": (4.5,  131.2,  0, "nonpolar"),
    "L": (3.8,  131.2,  0, "nonpolar"),
    "K": (-3.9, 146.2,  1, "positive"),
    "M": (1.9,  149.2,  0, "nonpolar"),
    "F": (2.8,  165.2,  0, "aromatic"),  # π-stacking target
    "P": (-1.6, 115.1,  0, "special"),   # helix breaker
    "S": (-0.8, 105.1,  0, "polar"),
    "T": (-0.7, 119.1,  0, "polar"),
    "W": (-0.9, 204.2,  0, "aromatic"),  # largest, π-stacking
    "Y": (-1.3, 181.2,  0, "aromatic"),  # phosphorylation target
    "V": (4.2,  117.1,  0, "nonpolar"),
}

# Common functional motifs in pathogen proteins
PATHOGEN_MOTIFS = {
    "RGD":      {"function": "Cell attachment (integrin binding)",   "druggable": True},
    "CXXC":     {"function": "Zinc finger / redox active site",     "druggable": True},
    "GXGXXG":   {"function": "Nucleotide binding (Rossmann fold)",  "druggable": True},
    "HXXEH":    {"function": "Metalloprotease active site",         "druggable": True},
    "GXSXG":    {"function": "Serine protease/lipase active site",  "druggable": True},
    "DXD":      {"function": "Glycosyltransferase active site",     "druggable": True},
    "KDEL":     {"function": "ER retention signal",                 "druggable": False},
    "LPXTG":    {"function": "Sortase anchor (gram+ bacteria)",     "druggable": True},
    "RXLR":     {"function": "Host-translocation signal",           "druggable": True},
    "LXCXE":    {"function": "Rb-binding (viral oncoproteins)",     "druggable": True},
    "NXS":      {"function": "N-glycosylation site",                "druggable": False},
    "NXT":      {"function": "N-glycosylation site",                "druggable": False},
    "CXXCH":    {"function": "Heme-binding motif",                  "druggable": True},
    "GDD":      {"function": "RNA-dependent RNA polymerase (RdRp)", "druggable": True},
    "SDD":      {"function": "Reverse transcriptase active site",   "druggable": True},
}

# Phytochemical structural classes and their protein interaction profiles
COMPOUND_INTERACTION_PROFILES = {
    "Quercetin": {
        "class": "flavonol",
        "interaction_types": ["hydrogen_bond", "pi_stacking", "hydrophobic"],
        "target_preferences": ["aromatic_pockets", "protease_active_sites", "glycoprotein_binding"],
        "num_OH_groups": 5,
        "aromatic_rings": 3,
        "molecular_weight": 302.24,
        "logP": 1.54,           # moderate lipophilicity
        "hbd": 5, "hba": 7,    # H-bond donors/acceptors
    },
    "EGCG": {
        "class": "catechin",
        "interaction_types": ["hydrogen_bond", "pi_stacking", "hydrophobic", "metal_chelation"],
        "target_preferences": ["protease_active_sites", "envelope_proteins", "polymerase"],
        "num_OH_groups": 8,
        "aromatic_rings": 4,
        "molecular_weight": 458.37,
        "logP": 0.64,
        "hbd": 8, "hba": 11,
    },
    "Curcumin": {
        "class": "curcuminoid",
        "interaction_types": ["hydrogen_bond", "hydrophobic", "michael_acceptor"],
        "target_preferences": ["nf_kb_pathway", "protease_active_sites", "membrane_proteins"],
        "num_OH_groups": 2,
        "aromatic_rings": 2,
        "molecular_weight": 368.38,
        "logP": 3.29,           # more lipophilic
        "hbd": 2, "hba": 6,
    },
    "Allicin": {
        "class": "thiosulfinate",
        "interaction_types": ["covalent_thiol", "redox"],
        "target_preferences": ["cysteine_residues", "thiol_enzymes", "bacterial_enzymes"],
        "num_OH_groups": 0,
        "aromatic_rings": 0,
        "molecular_weight": 162.27,
        "logP": 1.35,
        "hbd": 0, "hba": 1,
    },
    "Resveratrol": {
        "class": "stilbenoid",
        "interaction_types": ["hydrogen_bond", "pi_stacking", "hydrophobic"],
        "target_preferences": ["sirtuin_activation", "membrane_proteins", "dna_viruses"],
        "num_OH_groups": 3,
        "aromatic_rings": 2,
        "molecular_weight": 228.24,
        "logP": 3.10,
        "hbd": 3, "hba": 3,
    },
    "Sulforaphane": {
        "class": "isothiocyanate",
        "interaction_types": ["covalent_thiol", "nrf2_activation"],
        "target_preferences": ["nrf2_pathway", "cysteine_residues", "phase2_enzymes"],
        "num_OH_groups": 0,
        "aromatic_rings": 0,
        "molecular_weight": 177.29,
        "logP": 0.72,
        "hbd": 0, "hba": 2,
    },
    "Gingerol": {
        "class": "phenol",
        "interaction_types": ["hydrogen_bond", "hydrophobic", "vanilloid_receptor"],
        "target_preferences": ["cox_enzymes", "respiratory_epithelium", "gi_receptors"],
        "num_OH_groups": 2,
        "aromatic_rings": 1,
        "molecular_weight": 294.39,
        "logP": 3.85,
        "hbd": 2, "hba": 4,
    },
    "Lycopene": {
        "class": "carotenoid",
        "interaction_types": ["hydrophobic", "radical_scavenging"],
        "target_preferences": ["membrane_disruption", "lipid_peroxidation", "singlet_oxygen"],
        "num_OH_groups": 0,
        "aromatic_rings": 0,
        "molecular_weight": 536.87,
        "logP": 15.5,           # very lipophilic
        "hbd": 0, "hba": 0,
    },
    "Capsaicin": {
        "class": "capsaicinoid",
        "interaction_types": ["hydrogen_bond", "hydrophobic", "trpv1_agonist"],
        "target_preferences": ["trpv1_receptor", "membrane_proteins", "bacterial_efflux"],
        "num_OH_groups": 1,
        "aromatic_rings": 1,
        "molecular_weight": 305.41,
        "logP": 3.64,
        "hbd": 2, "hba": 3,
    },
    "Luteolin": {
        "class": "flavone",
        "interaction_types": ["hydrogen_bond", "pi_stacking", "metal_chelation"],
        "target_preferences": ["protease_active_sites", "inflammatory_enzymes", "viral_entry"],
        "num_OH_groups": 4,
        "aromatic_rings": 3,
        "molecular_weight": 286.24,
        "logP": 1.97,
        "hbd": 4, "hba": 6,
    },
    "Kaempferol": {
        "class": "flavonol",
        "interaction_types": ["hydrogen_bond", "pi_stacking", "hydrophobic"],
        "target_preferences": ["protease_active_sites", "glycoprotein_binding", "topoisomerase"],
        "num_OH_groups": 4,
        "aromatic_rings": 3,
        "molecular_weight": 286.24,
        "logP": 1.90,
        "hbd": 4, "hba": 6,
    },
    "Apigenin": {
        "class": "flavone",
        "interaction_types": ["hydrogen_bond", "pi_stacking"],
        "target_preferences": ["kinase_inhibition", "viral_rna_cap", "inflammatory_enzymes"],
        "num_OH_groups": 3,
        "aromatic_rings": 3,
        "molecular_weight": 270.24,
        "logP": 1.74,
        "hbd": 3, "hba": 5,
    },
    "Naringenin": {
        "class": "flavanone",
        "interaction_types": ["hydrogen_bond", "hydrophobic"],
        "target_preferences": ["viral_protease", "inflammatory_enzymes", "membrane_transport"],
        "num_OH_groups": 3,
        "aromatic_rings": 2,           # flavanone ring C is not aromatic
        "molecular_weight": 272.25,
        "logP": 2.52,
        "hbd": 3, "hba": 5,
    },
    "Diallyl Disulfide": {
        "class": "organosulfur",
        "interaction_types": ["covalent_thiol", "redox", "radical_scavenging"],
        "target_preferences": ["cysteine_residues", "bacterial_enzymes", "biofilm"],
        "num_OH_groups": 0,
        "aromatic_rings": 0,
        "molecular_weight": 146.28,
        "logP": 2.20,
        "hbd": 0, "hba": 0,
    },
    "Ellagic Acid": {
        "class": "polyphenol",
        "interaction_types": ["hydrogen_bond", "pi_stacking", "metal_chelation"],
        "target_preferences": ["dna_binding", "topoisomerase", "viral_integrase"],
        "num_OH_groups": 4,
        "aromatic_rings": 4,
        "molecular_weight": 302.19,
        "logP": 1.05,
        "hbd": 4, "hba": 8,
    },
}


# ═════════════════════════════════════════════════════════════
# PROTEIN SEQUENCE ANALYSIS
# ═════════════════════════════════════════════════════════════

def analyse_protein(sequence: str) -> dict[str, Any]:
    """
    Analyse an amino acid sequence to extract drug-targetable features.

    Returns:
        {
            "length": int,
            "composition": {aa: fraction, ...},
            "properties": {
                "hydrophobic_fraction": float,
                "aromatic_fraction": float,
                "charged_fraction": float,
                "cysteine_fraction": float,
                "avg_hydrophobicity": float,
                "net_charge": int,
                "molecular_weight_kda": float,
            },
            "motifs_found": [
                {"motif": "RGD", "position": 123, "function": "...", "druggable": bool},
            ],
            "binding_opportunities": {
                "pi_stacking_sites": int,    # F, W, Y, H count
                "hbond_sites": int,          # S, T, N, Q, Y count
                "cysteine_targets": int,     # C count (for thiol-reactive compounds)
                "charge_clusters": int,      # R, K, D, E clusters
                "hydrophobic_pockets": int,  # estimated from hydrophobic stretches
            },
            "structural_hints": [str, ...],  # human-readable notes
        }
    """
    seq = sequence.upper().replace(" ", "").replace("\n", "")
    length = len(seq)

    if length < 10:
        return {"error": "Sequence too short for analysis", "length": length}

    # ── Composition ──
    counts = Counter(seq)
    composition = {aa: counts.get(aa, 0) / length for aa in "ACDEFGHIKLMNPQRSTVWY"}

    # ── Aggregate properties ──
    hydrophobic_aas = set("AILMFVW")
    aromatic_aas = set("FWY")
    charged_aas = set("RDEK")
    polar_aas = set("STNQ")

    hydrophobic_frac = sum(counts.get(aa, 0) for aa in hydrophobic_aas) / length
    aromatic_frac = sum(counts.get(aa, 0) for aa in aromatic_aas) / length
    charged_frac = sum(counts.get(aa, 0) for aa in charged_aas) / length
    cysteine_frac = counts.get("C", 0) / length

    avg_hydro = sum(
        AA_PROPERTIES.get(aa, (0, 0, 0, ""))[0] * counts.get(aa, 0)
        for aa in AA_PROPERTIES
    ) / max(length, 1)

    net_charge = sum(
        AA_PROPERTIES.get(aa, (0, 0, 0, ""))[2] * counts.get(aa, 0)
        for aa in AA_PROPERTIES
    )

    mw_da = sum(
        AA_PROPERTIES.get(aa, (0, 110, 0, ""))[1] * counts.get(aa, 0)
        for aa in AA_PROPERTIES
    ) - (length - 1) * 18.015  # water loss from peptide bonds

    # ── Motif scanning ──
    motifs_found = []
    for motif_pattern, info in PATHOGEN_MOTIFS.items():
        # Convert motif pattern with X wildcards to regex
        regex_pattern = motif_pattern.replace("X", "[A-Z]")
        for match in re.finditer(regex_pattern, seq):
            motifs_found.append({
                "motif": motif_pattern,
                "matched": match.group(),
                "position": match.start() + 1,  # 1-based
                "function": info["function"],
                "druggable": info["druggable"],
            })

    # ── Binding opportunity counting ──
    pi_stacking_sites = sum(counts.get(aa, 0) for aa in "FWYH")
    hbond_sites = sum(counts.get(aa, 0) for aa in "STNQY")
    cysteine_targets = counts.get("C", 0)

    # Count charged clusters (3+ charged residues within 5 positions)
    charge_clusters = 0
    for i in range(length - 4):
        window = seq[i:i+5]
        if sum(1 for c in window if c in charged_aas) >= 3:
            charge_clusters += 1

    # Count hydrophobic stretches (5+ hydrophobic residues = potential pocket)
    hydrophobic_runs = re.findall(r"[AILMFVW]{5,}", seq)
    hydrophobic_pockets = len(hydrophobic_runs)

    # ── Structural hints ──
    hints = []
    if cysteine_frac > 0.03:
        hints.append(f"High cysteine content ({cysteine_frac:.1%}) — likely disulphide-rich, targetable by thiol-reactive compounds (Allicin, Sulforaphane)")
    if aromatic_frac > 0.10:
        hints.append(f"Aromatic-rich ({aromatic_frac:.1%}) — good target for π-stacking compounds (Quercetin, EGCG, flavonoids)")
    if hydrophobic_frac > 0.40:
        hints.append(f"Highly hydrophobic ({hydrophobic_frac:.1%}) — likely membrane-associated; lipophilic compounds (Curcumin, Resveratrol) may interact")
    if avg_hydro < -0.5:
        hints.append("Hydrophilic surface — polar compounds with H-bond capacity (EGCG, Ellagic Acid) preferred")
    if any(m["motif"] == "GDD" for m in motifs_found):
        hints.append("Contains GDD motif (RNA polymerase) — polymerase inhibitors like EGCG, Quercetin relevant")
    if any(m["motif"] in ("GXSXG", "HXXEH") for m in motifs_found):
        hints.append("Contains protease active site — protease inhibitors (Quercetin, EGCG, Luteolin) are strong candidates")
    if hydrophobic_pockets >= 3:
        hints.append(f"Multiple hydrophobic stretches ({hydrophobic_pockets}) — suggests transmembrane domains or hydrophobic binding pockets")
    druggable_motifs = [m for m in motifs_found if m["druggable"]]
    if druggable_motifs:
        hints.append(f"{len(druggable_motifs)} druggable motif(s) found — targeted compound selection possible")

    return {
        "length": length,
        "composition": {k: round(v, 4) for k, v in composition.items() if v > 0},
        "properties": {
            "hydrophobic_fraction": round(hydrophobic_frac, 3),
            "aromatic_fraction": round(aromatic_frac, 3),
            "charged_fraction": round(charged_frac, 3),
            "cysteine_fraction": round(cysteine_frac, 4),
            "avg_hydrophobicity": round(avg_hydro, 2),
            "net_charge": net_charge,
            "molecular_weight_kda": round(mw_da / 1000, 2),
        },
        "motifs_found": motifs_found,
        "binding_opportunities": {
            "pi_stacking_sites": pi_stacking_sites,
            "hbond_sites": hbond_sites,
            "cysteine_targets": cysteine_targets,
            "charge_clusters": charge_clusters,
            "hydrophobic_pockets": hydrophobic_pockets,
        },
        "structural_hints": hints,
    }


# ═════════════════════════════════════════════════════════════
# COMPOUND–PROTEIN INTERACTION SCORING
# ═════════════════════════════════════════════════════════════

def score_compounds_against_protein(
    analysis: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Score all 15 phytochemicals against a protein's analysed features.

    Uses a multi-factor scoring model:
      - π-stacking compatibility (aromatic compounds vs aromatic residues)
      - H-bond potential (polar compounds vs polar residues)
      - Thiol reactivity (sulfur compounds vs cysteine residues)
      - Hydrophobic fit (lipophilic compounds vs hydrophobic pockets)
      - Motif targeting (known compound–motif interactions)
      - Lipinski-like druglikeness

    Returns a sorted list of {compound, score, mechanisms, rank}.
    """
    binding = analysis.get("binding_opportunities", {})
    props = analysis.get("properties", {})
    motifs = analysis.get("motifs_found", [])
    length = analysis.get("length", 100)

    # Normalise binding site counts relative to protein length
    pi_density = binding.get("pi_stacking_sites", 0) / max(length, 1)
    hbond_density = binding.get("hbond_sites", 0) / max(length, 1)
    cys_density = binding.get("cysteine_targets", 0) / max(length, 1)
    hydro_pockets = binding.get("hydrophobic_pockets", 0)
    has_protease = any(m["motif"] in ("GXSXG", "HXXEH") for m in motifs)
    has_polymerase = any(m["motif"] in ("GDD", "SDD", "GXGXXG") for m in motifs)
    has_glycosyl = any(m["motif"] in ("NXS", "NXT", "DXD") for m in motifs)
    has_thiol_motif = any(m["motif"] in ("CXXC", "CXXCH") for m in motifs)

    results = []

    for name, profile in COMPOUND_INTERACTION_PROFILES.items():
        score = 0.0
        mechanisms = []

        # Factor 1: π-stacking (aromatic compounds vs aromatic protein residues)
        if profile["aromatic_rings"] > 0 and pi_density > 0:
            pi_score = min(profile["aromatic_rings"] * pi_density * 15, 0.25)
            score += pi_score
            if pi_score > 0.08:
                mechanisms.append(
                    f"π-stacking: {profile['aromatic_rings']} aromatic rings can interact "
                    f"with {binding.get('pi_stacking_sites', 0)} aromatic residues (F/W/Y/H)"
                )

        # Factor 2: Hydrogen bonding
        if profile["hbd"] + profile["hba"] > 0 and hbond_density > 0:
            hbond_score = min((profile["hbd"] + profile["hba"]) * hbond_density * 5, 0.25)
            score += hbond_score
            if hbond_score > 0.08:
                mechanisms.append(
                    f"H-bonding: {profile['hbd']} donors + {profile['hba']} acceptors "
                    f"vs {binding.get('hbond_sites', 0)} polar residues (S/T/N/Q/Y)"
                )

        # Factor 3: Thiol reactivity (covalent binders vs cysteine)
        if "covalent_thiol" in profile["interaction_types"]:
            thiol_score = min(cys_density * 40, 0.30)
            score += thiol_score
            if thiol_score > 0.05:
                mechanisms.append(
                    f"Covalent thiol binding: targets {binding.get('cysteine_targets', 0)} "
                    f"cysteine residues — can irreversibly inhibit enzyme activity"
                )
            # Boost if CXXC motif found
            if has_thiol_motif:
                score += 0.10
                mechanisms.append("Targets CXXC redox/zinc-finger motif (high specificity)")

        # Factor 4: Hydrophobic fit
        if profile["logP"] > 2.0 and hydro_pockets > 0:
            hydro_score = min(math.log2(profile["logP"]) * hydro_pockets * 0.05, 0.20)
            score += hydro_score
            if hydro_score > 0.05:
                mechanisms.append(
                    f"Hydrophobic fit: logP={profile['logP']:.1f} matches "
                    f"{hydro_pockets} hydrophobic pocket(s) in the protein"
                )

        # Factor 5: Protease targeting
        if has_protease and "protease_active_sites" in profile["target_preferences"]:
            score += 0.15
            mechanisms.append("Predicted protease inhibition — compound class known to target serine/metallo-protease active sites")

        # Factor 6: Polymerase targeting
        if has_polymerase and "polymerase" in profile.get("target_preferences", []):
            score += 0.15
            mechanisms.append("Predicted polymerase interaction — may interfere with viral RNA/DNA replication")

        # Factor 7: Glycoprotein targeting
        if has_glycosyl and "glycoprotein_binding" in profile.get("target_preferences", []):
            score += 0.10
            mechanisms.append("May interfere with glycoprotein processing — relevant to viral entry/attachment")

        # Factor 8: Druglikeness bonus (Lipinski's Rule of 5 compliance)
        lipinski_violations = 0
        if profile["molecular_weight"] > 500:
            lipinski_violations += 1
        if profile["logP"] > 5:
            lipinski_violations += 1
        if profile["hbd"] > 5:
            lipinski_violations += 1
        if profile["hba"] > 10:
            lipinski_violations += 1
        if lipinski_violations == 0:
            score += 0.05  # fully compliant bonus

        # Normalise score to 0–1 range
        score = min(round(score, 3), 1.0)

        results.append({
            "compound": name,
            "score": score,
            "class": profile["class"],
            "mechanisms": mechanisms,
            "interaction_types": profile["interaction_types"],
        })

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)

    # Add rank
    for i, r in enumerate(results):
        r["rank"] = i + 1

    return results


# ═════════════════════════════════════════════════════════════
# AMINA AI — FULL ANALYSIS PIPELINE
# ═════════════════════════════════════════════════════════════

async def amina_analyse(
    *,
    sequence: str,
    protein_title: str = "",
    protein_organism: str = "",
    disease_title: str = "",
    who_context: str = "",
) -> dict[str, Any]:
    """
    Full Amina AI analysis pipeline for an unknown disease.

    Steps:
      1. Analyse protein sequence (composition, motifs, binding sites)
      2. Score all 15 phytochemicals against the protein
      3. Use FLock LLM to interpret results and generate nutrition strategy
      4. Return enriched result with both computational scores and LLM insights

    Args:
        sequence:        Amino acid sequence (one-letter codes)
        protein_title:   NCBI protein name
        protein_organism: Organism name
        disease_title:   Disease name from WHO
        who_context:     Combined WHO text (overview + advice)

    Returns:
        {
            "protein_analysis": {...},       # from analyse_protein()
            "compound_scores": [...],        # ranked compounds
            "top_compounds": [...],          # top 5 with food sources
            "nutrition_strategy": {...},     # LLM-generated strategy enriched with scores
            "amina_summary": "...",          # human-readable summary
        }
    """
    log.info(f"Amina AI analysing: {protein_title or disease_title} ({len(sequence)} aa)")

    # Step 1: Protein analysis
    protein_analysis = analyse_protein(sequence)
    if "error" in protein_analysis:
        return {"error": protein_analysis["error"]}

    # Step 2: Score compounds
    compound_scores = score_compounds_against_protein(protein_analysis)
    top_5 = compound_scores[:5]

    # Step 3: Load food sources from phytochemicals.json
    phyto_path = ROOT / "data" / "phytochemicals.json"
    phyto_db = []
    if phyto_path.exists():
        phyto_db = json.loads(phyto_path.read_text(encoding="utf-8"))

    phyto_lookup = {c["name"]: c for c in phyto_db}
    top_compounds_with_foods = []
    for cs in top_5:
        entry = {
            "compound": cs["compound"],
            "score": cs["score"],
            "class": cs["class"],
            "mechanisms": cs["mechanisms"],
            "food_sources": phyto_lookup.get(cs["compound"], {}).get("food_sources", []),
        }
        top_compounds_with_foods.append(entry)

    # Step 4: Build human-readable summary
    hints = protein_analysis.get("structural_hints", [])
    motif_count = len(protein_analysis.get("motifs_found", []))
    druggable_count = sum(1 for m in protein_analysis.get("motifs_found", []) if m.get("druggable"))

    summary_parts = [
        f"Amina AI analysis of {protein_title or 'unknown protein'} "
        f"from {protein_organism or 'unknown organism'} ({protein_analysis['length']} amino acids):",
        f"  MW: {protein_analysis['properties']['molecular_weight_kda']:.1f} kDa, "
        f"Net charge: {protein_analysis['properties']['net_charge']:+d}",
        f"  Motifs found: {motif_count} ({druggable_count} druggable)",
        f"  Binding sites: {protein_analysis['binding_opportunities']['pi_stacking_sites']} aromatic, "
        f"{protein_analysis['binding_opportunities']['hbond_sites']} polar, "
        f"{protein_analysis['binding_opportunities']['cysteine_targets']} cysteine",
    ]
    if hints:
        summary_parts.append("  Key findings:")
        for h in hints:
            summary_parts.append(f"    → {h}")
    summary_parts.append(f"  Top predicted compounds: {', '.join(c['compound'] for c in top_5)}")

    amina_summary = "\n".join(summary_parts)

    # Step 5: Call FLock LLM with Amina analysis for enriched strategy
    nutrition_strategy = await _amina_llm_strategy(
        disease_title=disease_title,
        protein_title=protein_title,
        protein_organism=protein_organism,
        amina_summary=amina_summary,
        top_compounds=top_compounds_with_foods,
        structural_hints=hints,
        who_context=who_context,
    )

    return {
        "protein_analysis": protein_analysis,
        "compound_scores": compound_scores,
        "top_compounds": top_compounds_with_foods,
        "nutrition_strategy": nutrition_strategy,
        "amina_summary": amina_summary,
    }


# ═════════════════════════════════════════════════════════════
# AMINA + LLM ENRICHMENT
# ═════════════════════════════════════════════════════════════

AMINA_SYSTEM_PROMPT = """\
You are Amina AI — a biodefence nutrition intelligence agent. You combine \
protein sequence analysis with phytochemical interaction predictions to \
generate evidence-based nutritional defence strategies against novel pathogens.

You will receive:
1. Amina AI computational analysis (protein properties, motif scan, compound scores)
2. Top predicted compounds with interaction mechanisms and food sources
3. WHO disease context (if available)

Generate a JSON object with this structure:
{
  "display_name": "Human-readable disease name",
  "pathogen_type": "virus" | "bacteria" | "parasite" | "fungus" | "unknown",
  "family": "Taxonomic family if identifiable",
  "transmission": "How it spreads (from WHO context)",
  "nutrition_strategy": {
    "primary_goal": "One-line strategy based on protein analysis + compound scores",
    "compounds": [
      {
        "name": "Compound name (use the top-scored compounds from Amina analysis)",
        "mechanism": "Specific mechanism based on protein interaction prediction",
        "evidence": "Combine Amina score with literature evidence",
        "amina_score": 0.85
      }
    ],
    "additional_nutrients": [
      {"nutrient": "Name", "role": "Why it matters", "food_sources": ["Food1", "Food2"]}
    ],
    "dietary_advice": ["Practical advice 1", "Practical advice 2"]
  },
  "amina_analysis": {
    "protein_features": "Brief summary of key protein characteristics",
    "druggable_motifs": "What motifs were found",
    "binding_strategy": "How the top compounds target this protein"
  }
}

RULES:
- Use the TOP 3-5 compounds from the Amina scoring results
- Include the amina_score for each compound
- Mechanism must reference the specific protein features (motifs, binding sites)
- Include 2-4 additional supportive nutrients
- Include a NOTE in dietary_advice that this is AI-predicted, not clinically validated
- Return ONLY the JSON object
"""


async def _amina_llm_strategy(
    *,
    disease_title: str,
    protein_title: str,
    protein_organism: str,
    amina_summary: str,
    top_compounds: list[dict[str, Any]],
    structural_hints: list[str],
    who_context: str,
) -> dict[str, Any] | None:
    """Use FLock LLM to generate a nutrition strategy enriched by Amina analysis."""
    if not FLOCK_API_KEY:
        log.warning("FLOCK_API_KEY not set — returning Amina-only result (no LLM)")
        # Fallback: build strategy from Amina scores alone
        return _build_fallback_strategy(disease_title, top_compounds, structural_hints)

    # Build prompt
    compounds_text = "\n".join(
        f"  {i+1}. {c['compound']} (score: {c['score']:.3f}, class: {c['class']})\n"
        f"     Mechanisms: {'; '.join(c['mechanisms'][:2]) if c['mechanisms'] else 'General interaction'}\n"
        f"     Foods: {', '.join(f['food'] for f in c['food_sources'][:3]) if c['food_sources'] else 'N/A'}"
        for i, c in enumerate(top_compounds)
    )

    hints_text = "\n".join(f"  - {h}" for h in structural_hints) if structural_hints else "  No specific structural hints"

    prompt = f"""Disease: {disease_title}
Protein: {protein_title} ({protein_organism})

=== AMINA AI COMPUTATIONAL ANALYSIS ===
{amina_summary}

=== TOP PREDICTED COMPOUNDS (by binding affinity score) ===
{compounds_text}

=== STRUCTURAL HINTS ===
{hints_text}

=== WHO CONTEXT ===
{who_context[:500] if who_context else 'No WHO context available'}

Based on this Amina AI protein analysis and compound scoring, generate a \
nutritional defence strategy JSON that specifically targets this pathogen's \
protein vulnerabilities using the top-scored food compounds."""

    try:
        headers = {
            "Content-Type": "application/json",
            "x-litellm-api-key": FLOCK_API_KEY,
        }
        payload = {
            "model": FLOCK_MODEL,
            "messages": [
                {"role": "system", "content": AMINA_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 2500,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{FLOCK_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=40,
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"].strip()

        # Parse
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()

        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                result = json.loads(match.group())
            else:
                log.warning("Amina LLM response not parseable — using fallback")
                return _build_fallback_strategy(disease_title, top_compounds, structural_hints)

        result["source"] = "amina_ai"
        result["llm_model"] = FLOCK_MODEL
        log.info(f"Amina AI + LLM strategy generated for '{disease_title}'")
        return result

    except Exception as e:
        log.error(f"Amina LLM call failed: {e}")
        return _build_fallback_strategy(disease_title, top_compounds, structural_hints)


def _build_fallback_strategy(
    disease_title: str,
    top_compounds: list[dict[str, Any]],
    hints: list[str],
) -> dict[str, Any]:
    """Build a nutrition strategy from Amina scores alone (no LLM needed)."""
    compounds = []
    for c in top_compounds[:5]:
        mechanisms_text = "; ".join(c.get("mechanisms", [])[:2]) or "Predicted interaction based on structural analysis"
        compounds.append({
            "name": c["compound"],
            "mechanism": mechanisms_text,
            "evidence": f"Amina AI predicted binding score: {c['score']:.3f}",
            "amina_score": c["score"],
        })

    return {
        "display_name": disease_title,
        "pathogen_type": "unknown",
        "family": "",
        "transmission": "See WHO advisory",
        "source": "amina_ai_fallback",
        "nutrition_strategy": {
            "primary_goal": f"Targeted nutritional defence based on protein structure analysis ({'; '.join(hints[:2]) if hints else 'general immune support'})",
            "compounds": compounds,
            "additional_nutrients": [
                {"nutrient": "Vitamin C", "role": "Broad immune support; antioxidant protection", "food_sources": ["Bell peppers", "Kiwi", "Citrus"]},
                {"nutrient": "Zinc", "role": "T-cell function; general antiviral", "food_sources": ["Pumpkin seeds", "Lentils"]},
                {"nutrient": "Vitamin D", "role": "Immune modulation", "food_sources": ["Fatty fish", "Egg yolks"]},
            ],
            "dietary_advice": [
                f"Prioritise foods containing: {', '.join(c['compound'] for c in top_compounds[:3])}",
                "These are the top Amina AI-predicted compounds against this pathogen's protein targets",
                "NOTE: These are computational predictions — not yet clinically validated",
                "Maintain a balanced anti-inflammatory diet alongside targeted foods",
            ],
        },
        "amina_analysis": {
            "protein_features": "; ".join(hints[:3]) if hints else "Standard protein profile",
            "druggable_motifs": "See full Amina analysis",
            "binding_strategy": f"Top binding candidates: {', '.join(c['compound'] for c in top_compounds[:3])}",
        },
    }
