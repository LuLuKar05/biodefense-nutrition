"""
research_agent.py — Biodefence Researcher Agent (Real Amina CLI Integration)
=============================================================================
The single "ResearcherAgent" that owns the ENTIRE unknown-disease pipeline.

When a disease detected by WHO is NOT in our static disease_nutrition_db,
this agent runs the full bioinformatics + AI pipeline:

  Phase 2:    Amino Acid Sequence Analysis  (via amina_ai local engine)
  Phase 3a:   ESMFold 3D Structure Prediction → .pdb (Amina cloud GPU)
  Phase 3b-e: Structure Enrichment (pdb-cleaner, quality-assessment, p2rank, sasa)
  Phase 4:    DiffDock Molecular Docking → phytochemical binding scores (Amina cloud)
  Phase 5:    FLock LLM Enrichment → human-readable nutrition strategy
  Fallback:   Computational docking if Amina CLI unavailable

Pipeline diagram:
  NCBI Amino Acid Sequence
      │
      ├──→ Phase 2: Amina AI sequence analysis (motifs, composition)
      │
      ├──→ Phase 3a: amina run esmfold → .pdb (cloud GPU)
      │         │
      │         ├──→ Phase 3b: amina run pdb-cleaner → cleaned .pdb
      │         ├──→ Phase 3c: amina run pdb-quality-assessment → validation report
      │         ├──→ Phase 3d: amina run p2rank → binding pockets (parallel)
      │         ├──→ Phase 3e: amina run sasa → surface accessibility (parallel)
      │         │
      │         ▼
      │    Phase 4: amina run diffdock --protein-pdb --ligand-smiles
      │         │         (Tier 1: Real DiffDock, Tier 2: Computational fallback)
      │         │
      │         ▼
      │    Merge: 40% sequence score + 60% docking score
      │
      ▼
  Phase 5: FLock LLM → nutrition strategy JSON

For KNOWN diseases → use disease_nutrition_db.json directly (skip all this).

Amina CLI Tools Used:
  - esmfold              (folding)      — Protein structure prediction
  - pdb-cleaner          (utilities)    — Clean PDB for analysis
  - pdb-quality-assessment (utilities)  — Validate structure quality
  - p2rank               (interactions) — Predict binding pockets
  - sasa                 (analysis)     — Solvent accessible surface area
  - diffdock             (interactions) — AI molecular docking

APIs used:
  - Amina CLI: pip install amina-cli → cloud GPU protein tools
  - FLock:     POST https://api.flock.io/v1/chat/completions
  - NCBI:      Handled by sequence_fetcher.py (upstream)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# ── Imports from amina_ai (core bioinformatics engine) ──
from threat_backend.amina_ai import (
    analyse_protein,
    score_compounds_against_protein,
)

# ── Amina CLI Python client (real cloud GPU compute) ──
try:
    from amina_cli.client import (
        run_tool as amina_run_tool,
        get_api_key as amina_get_api_key,
        AuthenticationError as AminaAuthError,
        InsufficientCreditsError as AminaCreditError,
        ToolExecutionError as AminaToolError,
        ToolNotFoundError as AminaToolNotFound,
    )
    AMINA_CLI_AVAILABLE = True
except ImportError:
    AMINA_CLI_AVAILABLE = False

log = logging.getLogger("threat_backend.research_agent")

# ── Config ──────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=str(ROOT / ".env"))

# FLock LLM
FLOCK_API_KEY: str = os.getenv("FLOCK_API_KEY", "").strip()
FLOCK_BASE_URL: str = os.getenv("FLOCK_BASE_URL", "https://api.flock.io/v1").strip()
FLOCK_MODEL: str = os.getenv("FLOCK_MODEL", "qwen3-30b-a3b-instruct-2507").strip()
FLOCK_TIMEOUT = 40  # seconds

# Amina CLI (real cloud GPU protein tools)
AMINA_API_KEY: str = os.getenv("AMINA_API_KEY", "").strip()
AMINA_TIMEOUT: int = int(os.getenv("AMINA_TIMEOUT", "600"))  # 10 min max per job
MAX_SEQUENCE_LENGTH: int = 800  # ESMFold practical limit
DOCK_CONCURRENT: int = int(os.getenv("DOCK_CONCURRENT", "3"))  # parallel docking jobs

# ── Directories ─────────────────────────────────────────────
PDB_DIR: Path = ROOT / "data" / "structures"
PDB_DIR.mkdir(parents=True, exist_ok=True)

DOCKING_RESULTS_DIR: Path = ROOT / "data" / "docking_results"
DOCKING_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

PHYTOCHEM_PATH: Path = ROOT / "data" / "phytochemicals.json"

# ── Caches ──────────────────────────────────────────────────
_pdb_cache: dict[str, dict[str, Any]] = {}          # sequence_hash → PDB result
PDB_CACHE_TTL = timedelta(days=30)

_research_cache: dict[str, dict[str, Any]] = {}     # disease_title → LLM strategy

_PHYTOCHEM_LIBRARY: list[dict[str, Any]] | None = None


# ═════════════════════════════════════════════════════════════
#  PHYTOCHEMICAL LIBRARY
# ═════════════════════════════════════════════════════════════

def _load_phytochemicals() -> list[dict[str, Any]]:
    """Load the 15-compound phytochemical library with SMILES strings."""
    if not PHYTOCHEM_PATH.exists():
        log.error(f"Phytochemical library not found: {PHYTOCHEM_PATH}")
        return []
    with open(PHYTOCHEM_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_phytochemical_library() -> list[dict[str, Any]]:
    """Get the phytochemical library, loading once."""
    global _PHYTOCHEM_LIBRARY
    if _PHYTOCHEM_LIBRARY is None:
        _PHYTOCHEM_LIBRARY = _load_phytochemicals()
    return _PHYTOCHEM_LIBRARY


# ═════════════════════════════════════════════════════════════
#  AMINA CLI HELPERS
# ═════════════════════════════════════════════════════════════

def _get_amina_key() -> str | None:
    """Return the Amina API key from .env or stored config, or None."""
    if AMINA_API_KEY:
        return AMINA_API_KEY
    if AMINA_CLI_AVAILABLE:
        try:
            return amina_get_api_key()
        except Exception:
            pass
    return None


def ensure_amina_auth() -> bool:
    """
    Ensure the Amina CLI is authenticated.
    Reads AMINA_API_KEY from .env and runs `amina auth set-key` if needed.
    Returns True if authenticated, False otherwise.
    """
    if not AMINA_CLI_AVAILABLE:
        log.warning("amina-cli package not installed — run: pip install amina-cli")
        return False

    key = _get_amina_key()
    if not key:
        log.warning(
            "AMINA_API_KEY not set in .env — "
            "get one at https://app.aminoanalytica.com/settings/api"
        )
        return False

    # Check if already authenticated
    try:
        stored = amina_get_api_key()
        if stored:
            return True
    except Exception:
        pass

    # Set the key via CLI subprocess (writes to amina config)
    try:
        result = subprocess.run(
            ["amina", "auth", "set-key", key],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            log.info("Amina CLI authenticated successfully")
            return True
        log.warning(f"Amina auth set-key failed: {result.stderr[:200]}")
    except FileNotFoundError:
        # Try with python -m
        try:
            amina_exe = shutil.which("amina")
            if amina_exe:
                subprocess.run(
                    [amina_exe, "auth", "set-key", key],
                    capture_output=True, text=True, timeout=10,
                )
                return True
        except Exception:
            pass
    except Exception as e:
        log.warning(f"Amina auth setup failed: {e}")
    return False


# ═════════════════════════════════════════════════════════════
#  PHASE 3: ESMFold 3D STRUCTURE PREDICTION (via Amina CLI)
# ═════════════════════════════════════════════════════════════
# Uses: amina run esmfold --sequence <seq> -o <dir>
# Output: .pdb structure, .csv pLDDT scores, .png pLDDT plot
# Fallback: None (ESMFold is the primary method)

def _seq_hash(sequence: str) -> str:
    """Deterministic short hash of a sequence for caching/filenames."""
    return hashlib.sha256(sequence.encode()).hexdigest()[:16]


async def predict_structure(
    sequence: str,
    *,
    protein_title: str = "",
    disease_title: str = "",
    cache_key: str | None = None,
) -> dict[str, Any]:
    """
    Predict the 3D structure of a protein using the REAL Amina CLI ESMFold.

    Uses the Amina platform's cloud GPU to run ESMFold (~$0.001 per fold).
    Falls back gracefully if Amina CLI is not available/authenticated.

    Returns:
        {
            "pdb_content": str,
            "pdb_path": str,
            "sequence_length": int,
            "sequence_hash": str,
            "protein_title": str,
            "disease_title": str,
            "confidence_info": {...},
            "plddt_csv_path": str,      # NEW: real pLDDT scores CSV
            "plddt_plot_path": str,      # NEW: pLDDT score plot PNG
            "predicted_at": str,
            "source": "amina_esmfold" | "file_cache",
            "amina_job_id": str,
            "error": ""
        }
    """
    seq = sequence.upper().replace(" ", "").replace("\n", "").replace("\r", "")
    seq = "".join(c for c in seq if c.isalpha())

    if len(seq) < 10:
        return {"error": "Sequence too short for structure prediction (need >= 10 aa)"}

    if len(seq) > MAX_SEQUENCE_LENGTH:
        log.warning(
            f"Sequence too long ({len(seq)} aa > {MAX_SEQUENCE_LENGTH}). "
            f"Truncating to first {MAX_SEQUENCE_LENGTH} residues."
        )
        seq = seq[:MAX_SEQUENCE_LENGTH]

    key = cache_key or _seq_hash(seq)
    seq_hash = _seq_hash(seq)

    # ── Check in-memory cache ──
    cached = _pdb_cache.get(key)
    if cached and not cached.get("error"):
        predicted_str = cached.get("predicted_at", "")
        if predicted_str:
            try:
                predicted_dt = datetime.fromisoformat(predicted_str)
                if datetime.now(timezone.utc) - predicted_dt < PDB_CACHE_TTL:
                    log.info(f"Structure cache hit: {key} ({protein_title or seq_hash})")
                    return cached
            except ValueError:
                pass

    # ── Check file cache ──
    pdb_filename = f"{key}_{seq_hash}.pdb"
    pdb_path = PDB_DIR / pdb_filename
    if pdb_path.exists():
        pdb_content = pdb_path.read_text(encoding="utf-8")
        if pdb_content.strip():
            result = _build_pdb_result(
                pdb_content=pdb_content, pdb_path=str(pdb_path),
                seq=seq, seq_hash=seq_hash,
                protein_title=protein_title, disease_title=disease_title,
                source="file_cache",
            )
            _pdb_cache[key] = result
            log.info(f"Structure loaded from file: {pdb_path.name}")
            return result

    # ── Call REAL Amina CLI ESMFold ──
    if not AMINA_CLI_AVAILABLE:
        return {
            "error": "amina-cli not installed — run: pip install amina-cli",
            "sequence_length": len(seq),
        }

    api_key = _get_amina_key()
    if not api_key:
        return {
            "error": "AMINA_API_KEY not set in .env — get one at https://app.aminoanalytica.com/settings/api",
            "sequence_length": len(seq),
        }

    log.info(
        f"🧬 Amina ESMFold predicting structure for: "
        f"{protein_title or 'unknown'} ({len(seq)} aa) — cloud GPU"
    )

    try:
        result_data = await amina_run_tool(
            "esmfold",
            {"sequence": seq},
            api_key=api_key,
            timeout=AMINA_TIMEOUT,
        )

        job_id = result_data.get("job_id", "")
        job_name = result_data.get("job_name", "")

        # ── Download PDB from signed URL ──
        # Amina returns signed_urls with temporary download links
        signed_urls = result_data.get("signed_urls", {})
        output_files = result_data.get("output_files", {})
        amina_data = result_data.get("data", {})

        pdb_url = signed_urls.get("pdb_filepath", "")
        csv_url = signed_urls.get("csv_filepath", "")
        plot_url = signed_urls.get("plot_filepath", "")

        pdb_content = ""
        plddt_csv_path = ""
        plddt_plot_path = ""

        if pdb_url:
            async with httpx.AsyncClient(timeout=60) as dl_client:
                resp = await dl_client.get(pdb_url)
                if resp.status_code == 200:
                    pdb_content = resp.text.strip()
                else:
                    log.error(f"Failed to download PDB from Amina: HTTP {resp.status_code}")

        if not pdb_content or "ATOM" not in pdb_content:
            log.error(f"Amina ESMFold returned no valid PDB. Keys: {list(result_data.keys())}")
            return {
                "error": f"Amina ESMFold returned no PDB content (job: {job_id})",
                "sequence_length": len(seq),
                "amina_job_id": job_id,
                "amina_raw_keys": list(result_data.keys()),
            }

        # ── Save PDB locally ──
        pdb_path.write_text(pdb_content, encoding="utf-8")
        log.info(f"Structure saved: {pdb_path.name} ({len(pdb_content)} bytes)")

        # ── Download CSV and plot if available ──
        if csv_url:
            try:
                async with httpx.AsyncClient(timeout=30) as dl_client:
                    resp = await dl_client.get(csv_url)
                    if resp.status_code == 200:
                        csv_dest = PDB_DIR / f"{key}_{seq_hash}_plddt.csv"
                        csv_dest.write_text(resp.text, encoding="utf-8")
                        plddt_csv_path = str(csv_dest)
            except Exception as e:
                log.debug(f"Could not download pLDDT CSV: {e}")

        if plot_url:
            try:
                async with httpx.AsyncClient(timeout=30) as dl_client:
                    resp = await dl_client.get(plot_url)
                    if resp.status_code == 200:
                        plot_dest = PDB_DIR / f"{key}_{seq_hash}_plddt.png"
                        plot_dest.write_bytes(resp.content)
                        plddt_plot_path = str(plot_dest)
            except Exception as e:
                log.debug(f"Could not download pLDDT plot: {e}")

        # ── Use Amina's computed pLDDT if available ──
        amina_mean_plddt = amina_data.get("mean_plddt")

        result = _build_pdb_result(
            pdb_content=pdb_content, pdb_path=str(pdb_path),
            seq=seq, seq_hash=seq_hash,
            protein_title=protein_title, disease_title=disease_title,
            source="amina_esmfold",
        )
        result["plddt_csv_path"] = plddt_csv_path
        result["plddt_plot_path"] = plddt_plot_path
        result["amina_job_id"] = job_id
        result["amina_job_name"] = job_name
        result["amina_cost_usd"] = result_data.get("cost_usd", 0)
        result["amina_execution_time"] = result_data.get("execution_time_seconds", 0)
        if amina_mean_plddt is not None:
            result["amina_mean_plddt"] = round(amina_mean_plddt, 4)
        _pdb_cache[key] = result
        return result

    except AminaAuthError as e:
        error_msg = f"Amina auth failed: {e} — check AMINA_API_KEY in .env"
        log.error(error_msg)
        return {"error": error_msg, "sequence_length": len(seq)}
    except AminaCreditError as e:
        error_msg = f"Amina insufficient credits: {e} — top up at https://app.aminoanalytica.com/topup"
        log.error(error_msg)
        return {"error": error_msg, "sequence_length": len(seq)}
    except AminaToolError as e:
        error_msg = f"Amina ESMFold execution failed: {e}"
        log.error(error_msg)
        return {"error": error_msg, "sequence_length": len(seq)}
    except Exception as e:
        error_msg = f"Amina ESMFold call failed: {type(e).__name__}: {e}"
        log.error(error_msg)
        return {"error": error_msg, "sequence_length": len(seq)}


def _build_pdb_result(
    *, pdb_content: str, pdb_path: str, seq: str, seq_hash: str,
    protein_title: str, disease_title: str, source: str,
) -> dict[str, Any]:
    """Build a standard result dict from PDB content."""
    return {
        "pdb_content": pdb_content,
        "pdb_path": pdb_path,
        "sequence_length": len(seq),
        "sequence_hash": seq_hash,
        "protein_title": protein_title,
        "disease_title": disease_title,
        "confidence_info": _extract_plddt(pdb_content),
        "predicted_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "error": "",
    }


def _extract_plddt(pdb_content: str) -> dict[str, Any]:
    """
    Extract pLDDT confidence scores from the PDB B-factor column.
    ESMFold stores per-residue pLDDT (0-100) in the B-factor column.
    """
    plddt_values: list[float] = []
    seen_residues: set[int] = set()

    for line in pdb_content.split("\n"):
        if not line.startswith("ATOM") and not line.startswith("HETATM"):
            continue
        atom_name = line[12:16].strip()
        if atom_name != "CA":
            continue
        try:
            res_seq = int(line[22:26].strip())
            if res_seq in seen_residues:
                continue
            seen_residues.add(res_seq)
            bfactor = float(line[60:66].strip())
            plddt_values.append(bfactor)
        except (ValueError, IndexError):
            continue

    if not plddt_values:
        return {
            "mean_plddt": 0.0, "min_plddt": 0.0, "max_plddt": 0.0,
            "high_confidence_fraction": 0.0, "very_high_confidence_fraction": 0.0,
            "residue_count": 0,
        }

    mean_p = sum(plddt_values) / len(plddt_values)
    return {
        "mean_plddt": round(mean_p, 1),
        "min_plddt": round(min(plddt_values), 1),
        "max_plddt": round(max(plddt_values), 1),
        "high_confidence_fraction": round(
            sum(1 for v in plddt_values if v > 70) / len(plddt_values), 3
        ),
        "very_high_confidence_fraction": round(
            sum(1 for v in plddt_values if v > 90) / len(plddt_values), 3
        ),
        "residue_count": len(plddt_values),
    }


# ═════════════════════════════════════════════════════════════
#  PHASE 3b–3e: AMINA STRUCTURE ENRICHMENT (post-ESMFold)
# ═════════════════════════════════════════════════════════════
# After ESMFold predicts a structure, we enrich it with:
#   3b: pdb-cleaner   — Clean PDB for analysis (remove waters, add hydrogens)
#   3c: pdb-quality   — Validate structure quality (Ramachandran, geometry)
#   3d: p2rank        — Predict ligand-binding pockets
#   3e: sasa          — Solvent-accessible surface area

ANALYSIS_DIR: Path = ROOT / "data" / "analysis"
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)


async def clean_pdb(
    pdb_path: str,
    *,
    preserve_bfactors: bool = True,
) -> dict[str, Any]:
    """
    Clean a PDB file using Amina pdb-cleaner (cloud).
    Removes waters/heterogens, adds hydrogens, standardises residues.

    Returns:
        {
            "cleaned_pdb_content": str,
            "cleaned_pdb_path": str,
            "cleaning_report": str,
            "amina_job_id": str,
            "amina_cost_usd": float,
            "error": "",
        }
    """
    if not AMINA_CLI_AVAILABLE:
        return {"error": "amina-cli not installed"}

    api_key = _get_amina_key()
    if not api_key:
        return {"error": "AMINA_API_KEY not set"}

    pdb_file = Path(pdb_path)
    if not pdb_file.exists():
        return {"error": f"PDB file not found: {pdb_path}"}

    pdb_content = pdb_file.read_text(encoding="utf-8")
    log.info(f"🧹 Amina pdb-cleaner: cleaning {pdb_file.name}...")

    try:
        result_data = await amina_run_tool(
            "pdb-cleaner",
            {
                "pdb_content": pdb_content,
                "input_filename": pdb_file.stem,
                "preserve_bfactors": preserve_bfactors,
            },
            api_key=api_key,
            timeout=AMINA_TIMEOUT,
        )

        signed_urls = result_data.get("signed_urls", {})
        job_id = result_data.get("job_id", "")

        cleaned_pdb = ""
        cleaned_pdb_path = ""
        cleaning_report = ""

        # Download cleaned PDB
        url = signed_urls.get("cleaned_pdb_filepath", "")
        if url:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    cleaned_pdb = resp.text.strip()
                    dest = PDB_DIR / f"{pdb_file.stem}_cleaned.pdb"
                    dest.write_text(cleaned_pdb, encoding="utf-8")
                    cleaned_pdb_path = str(dest)

        # Download cleaning report
        url = signed_urls.get("cleaning_report_filepath", "")
        if url:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    report_dest = ANALYSIS_DIR / f"{pdb_file.stem}_cleaning_report.csv"
                    report_dest.write_text(resp.text, encoding="utf-8")
                    cleaning_report = str(report_dest)

        log.info(f"PDB cleaned: {pdb_file.stem} (job: {job_id})")
        return {
            "cleaned_pdb_content": cleaned_pdb,
            "cleaned_pdb_path": cleaned_pdb_path,
            "cleaning_report": cleaning_report,
            "amina_job_id": job_id,
            "amina_cost_usd": result_data.get("cost_usd", 0),
            "error": "",
        }

    except Exception as e:
        log.warning(f"PDB cleaning failed: {type(e).__name__}: {e}")
        return {"error": str(e)}


async def assess_pdb_quality(
    pdb_path: str,
) -> dict[str, Any]:
    """
    Validate structure quality using Amina pdb-quality-assessment (cloud).
    Returns Ramachandran analysis, geometric validation, and quality scores.

    Returns:
        {
            "quality_score": float,
            "ramachandran_plot_path": str,
            "quality_plots_path": str,
            "quality_report": dict,
            "amina_job_id": str,
            "amina_cost_usd": float,
            "error": "",
        }
    """
    if not AMINA_CLI_AVAILABLE:
        return {"error": "amina-cli not installed"}

    api_key = _get_amina_key()
    if not api_key:
        return {"error": "AMINA_API_KEY not set"}

    pdb_file = Path(pdb_path)
    if not pdb_file.exists():
        return {"error": f"PDB file not found: {pdb_path}"}

    pdb_content = pdb_file.read_text(encoding="utf-8")
    log.info(f"📊 Amina pdb-quality-assessment: validating {pdb_file.name}...")

    try:
        result_data = await amina_run_tool(
            "pdb-quality-assessment",
            {
                "pdb_content": pdb_content,
                "input_filename": pdb_file.stem,
            },
            api_key=api_key,
            timeout=AMINA_TIMEOUT,
        )

        signed_urls = result_data.get("signed_urls", {})
        amina_data = result_data.get("data", {})
        job_id = result_data.get("job_id", "")

        ramachandran_path = ""
        quality_plots_path = ""
        quality_report: dict[str, Any] = {}

        # Download Ramachandran plot
        url = signed_urls.get("ramachandran_plot_filepath", "")
        if url:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    dest = ANALYSIS_DIR / f"{pdb_file.stem}_ramachandran.png"
                    dest.write_bytes(resp.content)
                    ramachandran_path = str(dest)

        # Download quality plots
        url = signed_urls.get("quality_plots_filepath", "")
        if url:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    dest = ANALYSIS_DIR / f"{pdb_file.stem}_quality_plots.png"
                    dest.write_bytes(resp.content)
                    quality_plots_path = str(dest)

        # Download quality report JSON
        url = signed_urls.get("report_filepath", "")
        if url:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    try:
                        quality_report = resp.json()
                    except Exception:
                        quality_report = {"raw": resp.text[:500]}
                    report_dest = ANALYSIS_DIR / f"{pdb_file.stem}_quality_report.json"
                    report_dest.write_text(resp.text, encoding="utf-8")

        # Extract overall quality score from report
        quality_score = amina_data.get("overall_quality_score", 0.0)

        log.info(f"Quality assessment done: {pdb_file.stem} (score: {quality_score}, job: {job_id})")
        return {
            "quality_score": quality_score,
            "ramachandran_plot_path": ramachandran_path,
            "quality_plots_path": quality_plots_path,
            "quality_report": quality_report,
            "amina_job_id": job_id,
            "amina_cost_usd": result_data.get("cost_usd", 0),
            "error": "",
        }

    except Exception as e:
        log.warning(f"PDB quality assessment failed: {type(e).__name__}: {e}")
        return {"error": str(e)}


async def predict_binding_sites(
    pdb_path: str,
) -> dict[str, Any]:
    """
    Predict ligand-binding pockets using Amina P2Rank (cloud).
    Uses machine learning to identify potential binding sites on the protein.

    Returns:
        {
            "pockets": [
                {
                    "rank": int,
                    "score": float,
                    "center_x": float,
                    "center_y": float,
                    "center_z": float,
                    "residues": list[str],
                }
            ],
            "top_pocket_score": float,
            "num_pockets": int,
            "predictions_csv_path": str,
            "residues_csv_path": str,
            "amina_job_id": str,
            "amina_cost_usd": float,
            "error": "",
        }
    """
    if not AMINA_CLI_AVAILABLE:
        return {"error": "amina-cli not installed", "pockets": []}

    api_key = _get_amina_key()
    if not api_key:
        return {"error": "AMINA_API_KEY not set", "pockets": []}

    pdb_file = Path(pdb_path)
    if not pdb_file.exists():
        return {"error": f"PDB file not found: {pdb_path}", "pockets": []}

    pdb_content = pdb_file.read_text(encoding="utf-8")
    log.info(f"🔍 Amina P2Rank: predicting binding sites on {pdb_file.name}...")

    try:
        result_data = await amina_run_tool(
            "p2rank",
            {
                "pdb_content": pdb_content,
                "pdb_filename": pdb_file.name,
            },
            api_key=api_key,
            timeout=AMINA_TIMEOUT,
        )

        signed_urls = result_data.get("signed_urls", {})
        amina_data = result_data.get("data", {})
        job_id = result_data.get("job_id", "")

        predictions_csv_path = ""
        residues_csv_path = ""
        pockets: list[dict[str, Any]] = []

        # Download predictions CSV
        url = signed_urls.get("predictions_csv_filepath", "")
        if url:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    dest = ANALYSIS_DIR / f"{pdb_file.stem}_pockets.csv"
                    dest.write_text(resp.text, encoding="utf-8")
                    predictions_csv_path = str(dest)
                    pockets = _parse_p2rank_predictions(resp.text)

        # Download residues CSV
        url = signed_urls.get("residues_csv_filepath", "")
        if url:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    dest = ANALYSIS_DIR / f"{pdb_file.stem}_residue_scores.csv"
                    dest.write_text(resp.text, encoding="utf-8")
                    residues_csv_path = str(dest)

        # Also extract pocket data from amina_data if available
        if not pockets and amina_data.get("pockets"):
            pockets = amina_data["pockets"]

        top_score = pockets[0]["score"] if pockets else 0.0
        log.info(
            f"P2Rank found {len(pockets)} binding pocket(s) "
            f"(top score: {top_score:.2f}, job: {job_id})"
        )

        return {
            "pockets": pockets,
            "top_pocket_score": top_score,
            "num_pockets": len(pockets),
            "predictions_csv_path": predictions_csv_path,
            "residues_csv_path": residues_csv_path,
            "amina_job_id": job_id,
            "amina_cost_usd": result_data.get("cost_usd", 0),
            "error": "",
        }

    except Exception as e:
        log.warning(f"P2Rank binding site prediction failed: {type(e).__name__}: {e}")
        return {"error": str(e), "pockets": []}


def _parse_p2rank_predictions(csv_text: str) -> list[dict[str, Any]]:
    """Parse P2Rank predictions CSV into pocket dicts."""
    pockets: list[dict[str, Any]] = []
    lines = csv_text.strip().split("\n")
    if len(lines) < 2:
        return pockets

    # Header: name,rank,score,sas_points,surf_atoms,center_x,center_y,center_z,...
    header = [h.strip().lower() for h in lines[0].split(",")]

    for line in lines[1:]:
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < len(header):
            continue
        row = dict(zip(header, cols))
        try:
            pocket = {
                "rank": int(row.get("rank", 0)),
                "score": float(row.get("score", 0)),
                "center_x": float(row.get("center_x", 0)),
                "center_y": float(row.get("center_y", 0)),
                "center_z": float(row.get("center_z", 0)),
                "residues": row.get("residue_ids", "").split() if row.get("residue_ids") else [],
            }
            pockets.append(pocket)
        except (ValueError, KeyError):
            continue

    pockets.sort(key=lambda p: p["score"], reverse=True)
    return pockets


async def calculate_sasa(
    pdb_path: str,
) -> dict[str, Any]:
    """
    Calculate Solvent Accessible Surface Area using Amina SASA (cloud).
    Identifies which residues are exposed (solvent-accessible), indicating
    potential binding targets for phytochemicals.

    Returns:
        {
            "total_sasa": float,
            "exposed_residues": int,
            "buried_residues": int,
            "exposed_fraction": float,
            "atom_csv_path": str,
            "residue_csv_path": str,
            "amina_job_id": str,
            "amina_cost_usd": float,
            "error": "",
        }
    """
    if not AMINA_CLI_AVAILABLE:
        return {"error": "amina-cli not installed"}

    api_key = _get_amina_key()
    if not api_key:
        return {"error": "AMINA_API_KEY not set"}

    pdb_file = Path(pdb_path)
    if not pdb_file.exists():
        return {"error": f"PDB file not found: {pdb_path}"}

    pdb_content = pdb_file.read_text(encoding="utf-8")
    log.info(f"🌊 Amina SASA: computing surface area for {pdb_file.name}...")

    try:
        result_data = await amina_run_tool(
            "sasa",
            {
                "pdb_content": pdb_content,
                "pdb_filename": pdb_file.name,
            },
            api_key=api_key,
            timeout=AMINA_TIMEOUT,
        )

        signed_urls = result_data.get("signed_urls", {})
        amina_data = result_data.get("data", {})
        job_id = result_data.get("job_id", "")

        atom_csv_path = ""
        residue_csv_path = ""
        total_sasa = 0.0
        exposed_residues = 0
        buried_residues = 0

        # Download residue CSV
        url = signed_urls.get("residue_csv_filepath", "")
        if url:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    dest = ANALYSIS_DIR / f"{pdb_file.stem}_sasa_residues.csv"
                    dest.write_text(resp.text, encoding="utf-8")
                    residue_csv_path = str(dest)
                    total_sasa, exposed_residues, buried_residues = _parse_sasa_residues(resp.text)

        # Download atom CSV
        url = signed_urls.get("atom_csv_filepath", "")
        if url:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    dest = ANALYSIS_DIR / f"{pdb_file.stem}_sasa_atoms.csv"
                    dest.write_text(resp.text, encoding="utf-8")
                    atom_csv_path = str(dest)

        # Use amina_data if CSV parsing didn't work
        if total_sasa == 0 and amina_data.get("total_sasa"):
            total_sasa = float(amina_data["total_sasa"])

        total_res = exposed_residues + buried_residues
        exposed_fraction = exposed_residues / max(total_res, 1)

        log.info(
            f"SASA computed: total={total_sasa:.1f} Å², "
            f"{exposed_residues} exposed / {buried_residues} buried residues (job: {job_id})"
        )

        return {
            "total_sasa": round(total_sasa, 1),
            "exposed_residues": exposed_residues,
            "buried_residues": buried_residues,
            "exposed_fraction": round(exposed_fraction, 3),
            "atom_csv_path": atom_csv_path,
            "residue_csv_path": residue_csv_path,
            "amina_job_id": job_id,
            "amina_cost_usd": result_data.get("cost_usd", 0),
            "error": "",
        }

    except Exception as e:
        log.warning(f"SASA calculation failed: {type(e).__name__}: {e}")
        return {"error": str(e)}


def _parse_sasa_residues(csv_text: str) -> tuple[float, int, int]:
    """Parse SASA residue CSV. Returns (total_sasa, exposed_count, buried_count)."""
    total = 0.0
    exposed = 0
    buried = 0
    lines = csv_text.strip().split("\n")
    if len(lines) < 2:
        return total, exposed, buried

    header = [h.strip().lower() for h in lines[0].split(",")]
    sasa_col = None
    for i, h in enumerate(header):
        if "sasa" in h or "area" in h:
            sasa_col = i
            break

    if sasa_col is None and len(header) > 1:
        sasa_col = len(header) - 1  # last column as fallback

    for line in lines[1:]:
        cols = [c.strip() for c in line.split(",")]
        if sasa_col is not None and sasa_col < len(cols):
            try:
                val = float(cols[sasa_col])
                total += val
                if val > 5.0:  # > 5 Å² is considered exposed
                    exposed += 1
                else:
                    buried += 1
            except ValueError:
                continue

    return total, exposed, buried


async def run_structure_enrichment(
    pdb_path: str,
    *,
    skip_clean: bool = False,
) -> dict[str, Any]:
    """
    Run the full Amina structure enrichment pipeline on a PDB file.
    Orchestrates: pdb-cleaner → pdb-quality-assessment → p2rank → sasa.

    These steps run in parallel where possible to minimise wall-clock time.

    Returns:
        {
            "cleaning": {...},
            "quality": {...},
            "binding_sites": {...},
            "sasa": {...},
            "enrichment_summary": str,
            "total_cost_usd": float,
            "error": "",
        }
    """
    api_key = _get_amina_key()
    if not AMINA_CLI_AVAILABLE or not api_key:
        return {
            "cleaning": {}, "quality": {}, "binding_sites": {"pockets": []},
            "sasa": {}, "enrichment_summary": "Amina CLI not available",
            "total_cost_usd": 0, "error": "Amina CLI not available or API key not set",
        }

    active_pdb = pdb_path
    cleaning_result: dict[str, Any] = {}

    # Step 1: Clean PDB (sequential — downstream steps use the cleaned PDB)
    if not skip_clean:
        cleaning_result = await clean_pdb(pdb_path)
        if cleaning_result.get("cleaned_pdb_path"):
            active_pdb = cleaning_result["cleaned_pdb_path"]
            log.info(f"Using cleaned PDB for enrichment: {active_pdb}")
    else:
        cleaning_result = {"skipped": True, "error": ""}

    # Step 2: Run quality, p2rank, sasa IN PARALLEL on the (cleaned) PDB
    quality_task = assess_pdb_quality(active_pdb)
    p2rank_task = predict_binding_sites(active_pdb)
    sasa_task = calculate_sasa(active_pdb)

    quality_result, p2rank_result, sasa_result = await asyncio.gather(
        quality_task, p2rank_task, sasa_task,
        return_exceptions=True,
    )

    # Handle exceptions from parallel tasks
    if isinstance(quality_result, Exception):
        quality_result = {"error": str(quality_result)}
    if isinstance(p2rank_result, Exception):
        p2rank_result = {"error": str(p2rank_result), "pockets": []}
    if isinstance(sasa_result, Exception):
        sasa_result = {"error": str(sasa_result)}

    # Calculate total cost
    total_cost = sum(
        r.get("amina_cost_usd", 0)
        for r in [cleaning_result, quality_result, p2rank_result, sasa_result]
        if isinstance(r, dict)
    )

    # Build summary
    summary_parts = []
    if cleaning_result and not cleaning_result.get("error"):
        summary_parts.append("PDB cleaned")
    q_score = quality_result.get("quality_score", 0) if isinstance(quality_result, dict) else 0
    if q_score:
        summary_parts.append(f"Quality score: {q_score}")
    n_pockets = p2rank_result.get("num_pockets", 0) if isinstance(p2rank_result, dict) else 0
    if n_pockets:
        top_p = p2rank_result.get("top_pocket_score", 0)
        summary_parts.append(f"{n_pockets} binding pocket(s) found (top: {top_p:.2f})")
    sasa_total = sasa_result.get("total_sasa", 0) if isinstance(sasa_result, dict) else 0
    if sasa_total:
        exposed = sasa_result.get("exposed_fraction", 0)
        summary_parts.append(f"SASA: {sasa_total:.0f} Å² ({exposed:.0%} exposed)")

    return {
        "cleaning": cleaning_result,
        "quality": quality_result,
        "binding_sites": p2rank_result,
        "sasa": sasa_result,
        "enrichment_summary": "; ".join(summary_parts) or "No enrichment data",
        "total_cost_usd": round(total_cost, 4),
        "error": "",
    }


# ═════════════════════════════════════════════════════════════
#  PHASE 4: MOLECULAR DOCKING (Real Amina DiffDock + Computational Fallback)
# ═════════════════════════════════════════════════════════════
# Tier 1: Real Amina DiffDock (cloud GPU) → Tier 2: Computational (local)
# Input:  .pdb + SMILES from phytochemicals.json
# Output: JSON with [threat_name, top_ligand, confidence_score]

# ── Compound molecular profiles for computational docking ──

COMPOUND_DOCK_PROFILES: dict[str, dict[str, Any]] = {
    "Quercetin": {
        "molecular_weight": 302.24, "logP": 1.54,
        "hbd": 5, "hba": 7, "aromatic_rings": 3,
        "rotatable_bonds": 1, "tpsa": 131.36,
        "pharmacophore": ["hbond_donor", "hbond_acceptor", "aromatic", "chelator"],
        "binding_modes": ["pi_stacking", "hydrogen_bond", "metal_chelation"],
    },
    "EGCG": {
        "molecular_weight": 458.37, "logP": 0.48,
        "hbd": 8, "hba": 11, "aromatic_rings": 4,
        "rotatable_bonds": 4, "tpsa": 197.37,
        "pharmacophore": ["hbond_donor", "hbond_acceptor", "aromatic", "chelator"],
        "binding_modes": ["hydrogen_bond", "pi_stacking", "hydrophobic", "chelation"],
    },
    "Curcumin": {
        "molecular_weight": 368.38, "logP": 3.29,
        "hbd": 2, "hba": 6, "aromatic_rings": 2,
        "rotatable_bonds": 8, "tpsa": 93.06,
        "pharmacophore": ["hbond_acceptor", "hydrophobic", "chelator"],
        "binding_modes": ["hydrophobic", "hydrogen_bond", "metal_chelation"],
    },
    "Allicin": {
        "molecular_weight": 162.28, "logP": 1.03,
        "hbd": 0, "hba": 2, "aromatic_rings": 0,
        "rotatable_bonds": 4, "tpsa": 61.58,
        "pharmacophore": ["thiol_reactive", "electrophilic"],
        "binding_modes": ["covalent_thiol", "redox"],
    },
    "Resveratrol": {
        "molecular_weight": 228.24, "logP": 3.10,
        "hbd": 3, "hba": 3, "aromatic_rings": 2,
        "rotatable_bonds": 2, "tpsa": 60.69,
        "pharmacophore": ["hbond_donor", "aromatic", "hydrophobic"],
        "binding_modes": ["pi_stacking", "hydrogen_bond", "hydrophobic"],
    },
    "Sulforaphane": {
        "molecular_weight": 177.29, "logP": 0.72,
        "hbd": 0, "hba": 3, "aromatic_rings": 0,
        "rotatable_bonds": 5, "tpsa": 86.96,
        "pharmacophore": ["electrophilic", "thiol_reactive"],
        "binding_modes": ["covalent_thiol", "electrophilic_addition"],
    },
    "Gingerol": {
        "molecular_weight": 294.39, "logP": 3.85,
        "hbd": 2, "hba": 4, "aromatic_rings": 1,
        "rotatable_bonds": 9, "tpsa": 66.76,
        "pharmacophore": ["hbond_donor", "hydrophobic", "aromatic"],
        "binding_modes": ["hydrophobic", "hydrogen_bond"],
    },
    "Lycopene": {
        "molecular_weight": 536.87, "logP": 9.16,
        "hbd": 0, "hba": 0, "aromatic_rings": 0,
        "rotatable_bonds": 12, "tpsa": 0.0,
        "pharmacophore": ["hydrophobic", "radical_scavenger"],
        "binding_modes": ["hydrophobic", "membrane_insertion"],
    },
    "Capsaicin": {
        "molecular_weight": 305.41, "logP": 3.04,
        "hbd": 2, "hba": 3, "aromatic_rings": 1,
        "rotatable_bonds": 9, "tpsa": 58.56,
        "pharmacophore": ["hbond_donor", "hydrophobic"],
        "binding_modes": ["hydrophobic", "hydrogen_bond"],
    },
    "Luteolin": {
        "molecular_weight": 286.24, "logP": 1.97,
        "hbd": 4, "hba": 6, "aromatic_rings": 3,
        "rotatable_bonds": 1, "tpsa": 111.13,
        "pharmacophore": ["hbond_donor", "hbond_acceptor", "aromatic"],
        "binding_modes": ["pi_stacking", "hydrogen_bond", "chelation"],
    },
    "Kaempferol": {
        "molecular_weight": 286.24, "logP": 1.90,
        "hbd": 4, "hba": 6, "aromatic_rings": 3,
        "rotatable_bonds": 1, "tpsa": 111.13,
        "pharmacophore": ["hbond_donor", "hbond_acceptor", "aromatic"],
        "binding_modes": ["pi_stacking", "hydrogen_bond"],
    },
    "Apigenin": {
        "molecular_weight": 270.24, "logP": 1.74,
        "hbd": 3, "hba": 5, "aromatic_rings": 3,
        "rotatable_bonds": 1, "tpsa": 90.90,
        "pharmacophore": ["hbond_donor", "aromatic"],
        "binding_modes": ["pi_stacking", "hydrogen_bond"],
    },
    "Naringenin": {
        "molecular_weight": 272.25, "logP": 2.52,
        "hbd": 3, "hba": 5, "aromatic_rings": 2,
        "rotatable_bonds": 1, "tpsa": 86.99,
        "pharmacophore": ["hbond_donor", "hbond_acceptor"],
        "binding_modes": ["hydrogen_bond", "hydrophobic"],
    },
    "Diallyl Disulfide": {
        "molecular_weight": 146.28, "logP": 2.20,
        "hbd": 0, "hba": 0, "aromatic_rings": 0,
        "rotatable_bonds": 4, "tpsa": 0.0,
        "pharmacophore": ["thiol_reactive", "radical_scavenger"],
        "binding_modes": ["covalent_thiol", "redox"],
    },
    "Ellagic Acid": {
        "molecular_weight": 302.19, "logP": 1.05,
        "hbd": 4, "hba": 8, "aromatic_rings": 4,
        "rotatable_bonds": 0, "tpsa": 141.34,
        "pharmacophore": ["hbond_donor", "hbond_acceptor", "aromatic", "chelator"],
        "binding_modes": ["pi_stacking", "hydrogen_bond", "chelation"],
    },
}


# ── Docking entry point ────────────────────────────────────

async def dock_phytochemicals(
    pdb_content: str,
    pdb_path: str = "",
    *,
    disease_title: str = "",
    protein_title: str = "",
    protein_analysis: dict[str, Any] | None = None,
    confidence_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Screen all 15 phytochemicals against a viral protein structure.
    Tier 1: Real Amina DiffDock (cloud GPU) → Tier 2: Computational fallback.

    Returns:
        {
            "threat_name": str, "protein_title": str,
            "top_ligand": str, "confidence_score": float,
            "docking_method": str,
            "all_results": [{compound, smiles, binding_score, ...}],
            "structure_quality": {...}, "screened_at": str, "error": "",
        }
    """
    library = get_phytochemical_library()
    if not library:
        return {"error": "No phytochemical library available"}
    if not pdb_content or "ATOM" not in pdb_content:
        return {"error": "Invalid or empty PDB content"}

    method = "computational"
    results: list[dict[str, Any]] = []

    # ── Tier 1: Real Amina DiffDock (cloud GPU) ──
    api_key = _get_amina_key()
    if AMINA_CLI_AVAILABLE and api_key and pdb_path:
        log.info("🔬 Attempting REAL Amina DiffDock docking (cloud GPU)...")
        results = await _dock_amina_diffdock(pdb_path, library, api_key)
        if results:
            method = "amina_diffdock"

    # ── Tier 2: Computational docking (always available) ──
    if not results:
        log.info("Using computational docking (Amina structural scoring)...")
        results = _dock_computational(
            pdb_content, library,
            protein_analysis=protein_analysis,
            confidence_info=confidence_info,
        )
        method = "computational"

    if not results:
        return {"error": "All docking methods failed"}

    results.sort(key=lambda r: r.get("binding_score", 0), reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    top = results[0]
    output = {
        "threat_name": disease_title or "Unknown Pathogen",
        "protein_title": protein_title or "Unknown Protein",
        "top_ligand": top["compound"],
        "confidence_score": round(top["binding_score"], 4),
        "docking_method": method,
        "all_results": results,
        "structure_quality": confidence_info or {},
        "screened_at": datetime.now(timezone.utc).isoformat(),
        "error": "",
    }

    # Save results to file
    safe_name = re.sub(r"[^\w\-]", "_", disease_title or "unknown")[:50]
    result_path = DOCKING_RESULTS_DIR / f"dock_{safe_name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    try:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, default=str)
        log.info(f"Docking results saved: {result_path.name}")
    except Exception as e:
        log.warning(f"Failed to save docking results: {e}")

    return output


# ── Tier 1: Real Amina DiffDock (cloud GPU) ────────────────

async def _dock_amina_diffdock(
    pdb_path: str,
    library: list[dict[str, Any]],
    api_key: str,
) -> list[dict[str, Any]]:
    """
    Dock all phytochemicals using the REAL Amina DiffDock service.
    Runs parallel jobs on cloud GPUs via amina_run_tool().
    """
    results: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(DOCK_CONCURRENT)

    async def dock_one(compound: dict[str, Any]) -> dict[str, Any] | None:
        async with sem:
            return await _amina_diffdock_single(pdb_path, compound, api_key)

    tasks = [dock_one(comp) for comp in library]
    raw = await asyncio.gather(*tasks, return_exceptions=True)

    for i, r in enumerate(raw):
        if isinstance(r, dict) and not r.get("error"):
            results.append(r)
        elif isinstance(r, Exception):
            log.warning(f"DiffDock failed for {library[i]['name']}: {r}")
        elif isinstance(r, dict) and r.get("error"):
            log.warning(f"DiffDock error for {r.get('compound', '?')}: {r['error']}")

    if results:
        log.info(
            f"Amina DiffDock completed: {len(results)}/{len(library)} compounds docked successfully"
        )
    return results


async def _amina_diffdock_single(
    pdb_path: str,
    compound: dict[str, Any],
    api_key: str,
) -> dict[str, Any] | None:
    """Dock a single compound using Amina DiffDock cloud GPU."""
    name = compound["name"]
    smiles = compound["smiles"]

    # Read the PDB content to send to Amina API
    pdb_file = Path(pdb_path)
    if not pdb_file.exists():
        log.warning(f"PDB file not found for {name}: {pdb_path}")
        return None
    pdb_content = pdb_file.read_text(encoding="utf-8")

    try:
        result_data = await amina_run_tool(
            "diffdock",
            {
                "protein_pdb_content": pdb_content,
                "input_filename": pdb_file.stem,
                "ligand_smiles": smiles,
                "samples_per_complex": 10,
                "inference_steps": 20,
            },
            api_key=api_key,
            timeout=AMINA_TIMEOUT,
        )

        # Parse DiffDock results
        # Amina returns: signed_urls, output_files, data
        signed_urls = result_data.get("signed_urls", {})
        amina_data = result_data.get("data", {})
        job_id = result_data.get("job_id", "")

        confidence = 0.0
        all_scores: list[float] = []

        # DiffDock confidence scores are negative log-likelihoods
        # Higher (less negative) = better. -1000.0 = failed pose.
        raw_scores = amina_data.get("confidence_scores", [])
        top_raw = amina_data.get("top_confidence", None)

        if isinstance(raw_scores, list) and raw_scores:
            # Filter out failed poses (-1000.0)
            valid_scores = [s for s in raw_scores
                           if isinstance(s, (int, float)) and s > -999]
            all_scores = valid_scores

        if top_raw is not None and isinstance(top_raw, (int, float)):
            raw_conf = float(top_raw)
        elif all_scores:
            raw_conf = max(all_scores)
        else:
            raw_conf = -10.0  # Very low default

        # Convert DiffDock confidence to 0-1 scale using sigmoid
        # DiffDock scores are typically in [-10, 2] range
        # More negative = worse binding, more positive = better binding
        confidence = 1.0 / (1.0 + math.exp(-raw_conf))

        # Download confidence JSON from signed URL if available
        confidence_json_url = signed_urls.get("confidence_json_filepath", "")

        # Get top pose URL
        top_pose_url = signed_urls.get("top_pose_filepath", "")

        food_sources = [s["food"] for s in compound.get("food_sources", [])]
        num_valid = len(all_scores)
        return {
            "compound": name,
            "smiles": smiles,
            "binding_score": round(confidence, 4),
            "binding_energy_kcal": round(_confidence_to_energy(confidence), 2),
            "raw_diffdock_confidence": round(raw_conf, 3),
            "mechanisms": [
                f"Amina DiffDock cloud GPU docking "
                f"({num_valid} valid poses, raw confidence: {raw_conf:.2f}, "
                f"normalized: {confidence:.3f})"
            ],
            "food_sources": food_sources,
            "amina_job_id": job_id,
            "amina_cost_usd": result_data.get("cost_usd", 0),
            "top_pose_url": top_pose_url,
            "all_confidence_scores": [round(s, 3) for s in all_scores[:5]],
        }

    except AminaCreditError as e:
        log.warning(f"Insufficient credits for DiffDock {name}: {e}")
        return {"compound": name, "error": f"Insufficient credits: {e}"}
    except AminaToolError as e:
        log.warning(f"Amina DiffDock failed for {name}: {e}")
        return {"compound": name, "error": str(e)}
    except Exception as e:
        log.warning(f"Amina DiffDock error for {name}: {type(e).__name__}: {e}")
        return None


# ── Tier 2: Computational docking (offline fallback) ───────

def _dock_computational(
    pdb_content: str,
    library: list[dict[str, Any]],
    *,
    protein_analysis: dict[str, Any] | None = None,
    confidence_info: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Computational docking using 3D structural features from the PDB.
    Scores each phytochemical based on shape/property complementarity.
    """
    pocket = _analyse_pdb_binding_features(pdb_content)

    structure_weight = 1.0
    if confidence_info:
        mean_plddt = confidence_info.get("mean_plddt", 50)
        structure_weight = max(0.3, min(1.0, mean_plddt / 100))

    results: list[dict[str, Any]] = []
    for compound in library:
        name = compound["name"]
        smiles = compound["smiles"]
        profile = COMPOUND_DOCK_PROFILES.get(name)
        if not profile:
            continue

        score, mechanisms = _score_compound_against_pocket(profile, pocket, protein_analysis)
        weighted_score = max(0.0, min(1.0, score * structure_weight))
        food_sources = [s["food"] for s in compound.get("food_sources", [])]

        results.append({
            "compound": name, "smiles": smiles,
            "binding_score": round(weighted_score, 4),
            "binding_energy_kcal": round(_confidence_to_energy(weighted_score), 2),
            "mechanisms": mechanisms,
            "food_sources": food_sources,
        })
    return results


# ── PDB structural analysis ───────────────────────────────

def _analyse_pdb_binding_features(pdb_content: str) -> dict[str, Any]:
    """Extract binding-relevant features from PDB ATOM records."""
    residues: list[dict[str, Any]] = []
    all_coords: list[tuple[float, float, float]] = []

    for line in pdb_content.split("\n"):
        if not line.startswith("ATOM"):
            continue
        atom_name = line[12:16].strip()
        if atom_name != "CA":
            continue
        try:
            res_name = line[17:20].strip()
            x = float(line[30:38].strip())
            y = float(line[38:46].strip())
            z = float(line[46:54].strip())
            bfactor = float(line[60:66].strip())
            residues.append({"name": res_name, "x": x, "y": y, "z": z, "plddt": bfactor})
            all_coords.append((x, y, z))
        except (ValueError, IndexError):
            continue

    if not residues:
        return {
            "total_residues": 0, "surface_aromatic_fraction": 0.0,
            "surface_polar_fraction": 0.0, "surface_hydrophobic_fraction": 0.0,
            "surface_charged_fraction": 0.0, "cysteine_count": 0,
            "charged_patches": 0, "aromatic_clusters": 0,
            "estimated_pocket_volume": 0.0,
        }

    total = len(residues)
    AROMATIC_3 = {"PHE", "TRP", "TYR", "HIS"}
    POLAR_3 = {"SER", "THR", "ASN", "GLN", "TYR"}
    HYDROPHOBIC_3 = {"ALA", "ILE", "LEU", "MET", "PHE", "VAL", "TRP"}
    CHARGED_3 = {"ARG", "LYS", "ASP", "GLU"}

    aromatic_count = sum(1 for r in residues if r["name"] in AROMATIC_3)
    polar_count = sum(1 for r in residues if r["name"] in POLAR_3)
    hydrophobic_count = sum(1 for r in residues if r["name"] in HYDROPHOBIC_3)
    charged_count = sum(1 for r in residues if r["name"] in CHARGED_3)
    cysteine_count = sum(1 for r in residues if r["name"] == "CYS")

    # Aromatic clusters (within 8 angstroms)
    aromatic_residues = [r for r in residues if r["name"] in AROMATIC_3]
    aromatic_clusters = 0
    for i in range(len(aromatic_residues)):
        for j in range(i + 1, len(aromatic_residues)):
            if _distance(aromatic_residues[i], aromatic_residues[j]) < 8.0:
                aromatic_clusters += 1

    # Charged patches (3+ charged within 10 angstroms)
    charged_residues = [r for r in residues if r["name"] in CHARGED_3]
    charged_patches = 0
    for i in range(len(charged_residues)):
        nearby = sum(
            1 for j in range(len(charged_residues))
            if i != j and _distance(charged_residues[i], charged_residues[j]) < 10.0
        )
        if nearby >= 2:
            charged_patches += 1

    # Pocket volume estimate from bounding box
    extent = 0.0
    if all_coords:
        xs = [c[0] for c in all_coords]
        ys = [c[1] for c in all_coords]
        zs = [c[2] for c in all_coords]
        extent = (max(xs) - min(xs)) * (max(ys) - min(ys)) * (max(zs) - min(zs))

    return {
        "total_residues": total,
        "surface_aromatic_fraction": round(aromatic_count / max(total, 1), 3),
        "surface_polar_fraction": round(polar_count / max(total, 1), 3),
        "surface_hydrophobic_fraction": round(hydrophobic_count / max(total, 1), 3),
        "surface_charged_fraction": round(charged_count / max(total, 1), 3),
        "cysteine_count": cysteine_count,
        "charged_patches": min(charged_patches, 20),
        "aromatic_clusters": min(aromatic_clusters, 50),
        "estimated_pocket_volume": round(extent, 1),
    }


def _distance(r1: dict, r2: dict) -> float:
    """Euclidean distance between two C-alpha atoms."""
    return math.sqrt(
        (r1["x"] - r2["x"]) ** 2 +
        (r1["y"] - r2["y"]) ** 2 +
        (r1["z"] - r2["z"]) ** 2
    )


# ── Compound-pocket scoring ───────────────────────────────

def _score_compound_against_pocket(
    profile: dict[str, Any],
    pocket: dict[str, Any],
    protein_analysis: dict[str, Any] | None,
) -> tuple[float, list[str]]:
    """Score a single compound against the binding pocket features. Returns (score, mechanisms)."""
    score = 0.0
    mechanisms: list[str] = []

    if pocket.get("total_residues", 0) == 0:
        return 0.0, ["No structural data available"]

    # Factor 1: pi-stacking complementarity (max 0.20)
    if profile["aromatic_rings"] > 0 and pocket["surface_aromatic_fraction"] > 0:
        pi_score = min(profile["aromatic_rings"] * pocket["surface_aromatic_fraction"] * 2.5, 0.20)
        score += pi_score
        if pi_score > 0.05:
            mechanisms.append(
                f"pi-stacking: {profile['aromatic_rings']} aromatic ring(s) "
                f"targeting {pocket['aromatic_clusters']} aromatic cluster(s) in 3D structure"
            )

    # Factor 2: Hydrogen bonding capacity (max 0.20)
    hbond_capacity = profile["hbd"] + profile["hba"]
    polar_frac = pocket["surface_polar_fraction"]
    if hbond_capacity > 0 and polar_frac > 0:
        hbond_score = min(hbond_capacity * polar_frac * 1.5, 0.20)
        score += hbond_score
        if hbond_score > 0.05:
            mechanisms.append(
                f"H-bonding: {profile['hbd']}D + {profile['hba']}A capacity "
                f"vs {polar_frac:.1%} polar surface residues"
            )

    # Factor 3: Hydrophobic fit (max 0.15)
    if profile["logP"] > 1.5 and pocket["surface_hydrophobic_fraction"] > 0.2:
        hydro_score = min((profile["logP"] / 5.0) * pocket["surface_hydrophobic_fraction"], 0.15)
        score += hydro_score
        if hydro_score > 0.04:
            mechanisms.append(
                f"Hydrophobic complementarity: logP={profile['logP']:.1f} "
                f"vs {pocket['surface_hydrophobic_fraction']:.1%} hydrophobic surface"
            )

    # Factor 4: Covalent thiol targeting (max 0.20)
    if "thiol_reactive" in profile["pharmacophore"] and pocket["cysteine_count"] > 0:
        thiol_score = min(pocket["cysteine_count"] * 0.05, 0.20)
        score += thiol_score
        mechanisms.append(
            f"Covalent thiol binding: targets {pocket['cysteine_count']} "
            f"cysteine residue(s) — potential irreversible inhibition"
        )

    # Factor 5: Charge complementarity (max 0.10)
    if pocket["charged_patches"] > 0 and hbond_capacity > 4:
        charge_score = min(pocket["charged_patches"] * 0.02, 0.10)
        score += charge_score
        if charge_score > 0.03:
            mechanisms.append(
                f"Electrostatic complementarity: {pocket['charged_patches']} "
                f"charged surface patches available for ionic interaction"
            )

    # Factor 6: Lipinski druglikeness bonus (max 0.05)
    if (profile["molecular_weight"] <= 500 and profile["logP"] <= 5.0
            and profile["hbd"] <= 5 and profile["hba"] <= 10):
        score += 0.05
        mechanisms.append("Passes Lipinski Rule-of-5 (good oral bioavailability)")

    # Factor 7: Motif-based boosting (max 0.10)
    if protein_analysis and protein_analysis.get("motifs_found"):
        druggable = [m for m in protein_analysis["motifs_found"] if m.get("druggable")]
        if any(m["motif"] in ("GXSXG", "HXXEH") for m in druggable):
            if "pi_stacking" in profile.get("binding_modes", []):
                score += 0.07
                mechanisms.append("Protease active site found — pi-stacking capability for active site occupancy")
        if any(m["motif"] in ("CXXC", "CXXCH") for m in druggable):
            if "covalent_thiol" in profile.get("binding_modes", []):
                score += 0.10
                mechanisms.append("CXXC redox motif found — thiol-reactive compound can covalently modify catalytic site")
        if any(m["motif"] in ("GDD", "SDD") for m in druggable):
            if "chelation" in profile.get("binding_modes", []) or "chelator" in profile.get("pharmacophore", []):
                score += 0.08
                mechanisms.append("Polymerase GDD/SDD motif — metal-chelating compound can disrupt catalytic metal coordination")

    # Factor 8: Pocket volume compatibility (max 0.05)
    vol = pocket.get("estimated_pocket_volume", 0)
    if vol > 0:
        size_ratio = profile["molecular_weight"] / (vol ** (1 / 3) + 1)
        if 10 < size_ratio < 50:
            score += 0.05
            mechanisms.append("Good size complementarity with binding cavity")

    return score, mechanisms


def _confidence_to_energy(confidence: float) -> float:
    """Convert 0.0-1.0 confidence to estimated delta-G (kcal/mol)."""
    return round(-10.0 * confidence, 2)


# ═════════════════════════════════════════════════════════════
#  SCORE MERGING (sequence-based + docking-based)
# ═════════════════════════════════════════════════════════════

def _merge_scores(
    sequence_scores: list[dict[str, Any]],
    docking_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Merge sequence-based scores with docking-based scores.
    Weighted: 40% sequence-based + 60% docking-based.
    """
    SEQUENCE_WEIGHT = 0.40
    DOCKING_WEIGHT = 0.60

    dock_lookup: dict[str, dict[str, Any]] = {}
    for dr in docking_result.get("all_results", []):
        dock_lookup[dr["compound"]] = dr

    merged = []
    for cs in sequence_scores:
        compound = cs["compound"]
        dock_data = dock_lookup.get(compound)

        if dock_data:
            seq_score = cs["score"]
            dock_score = dock_data.get("binding_score", 0)
            combined = (seq_score * SEQUENCE_WEIGHT) + (dock_score * DOCKING_WEIGHT)

            all_mechanisms = list(cs.get("mechanisms", []))
            all_mechanisms.extend(dock_data.get("mechanisms", []))

            merged.append({
                **cs,
                "score": round(combined, 4),
                "sequence_score": round(seq_score, 4),
                "docking_score": round(dock_score, 4),
                "binding_energy_kcal": dock_data.get("binding_energy_kcal", 0),
                "mechanisms": all_mechanisms,
            })
        else:
            merged.append(cs)

    merged.sort(key=lambda x: x["score"], reverse=True)
    for i, m in enumerate(merged):
        m["rank"] = i + 1
    return merged


# ═════════════════════════════════════════════════════════════
#  AMINA CLI PIPELINE ORCHESTRATOR (Phase 2 -> 3 -> 4 -> 5)
# ═════════════════════════════════════════════════════════════
# Main entry point for unknown/novel disease analysis.

async def amina_cli_pipeline(
    *,
    sequence: str,
    protein_title: str = "",
    protein_organism: str = "",
    disease_title: str = "",
    who_context: str = "",
) -> dict[str, Any]:
    """
    Full Amina CLI pipeline for unknown/novel diseases.

    Phases:
      2: Amino acid sequence analysis (via amina_ai)
      3: ESMFold 3D structure prediction -> .pdb
      4: DiffDock molecular docking -> binding scores
      5: FLock LLM enrichment -> nutrition strategy

    For KNOWN diseases, use disease_nutrition_db.json directly (skip all this).
    """
    log.info(
        f"Amina CLI pipeline starting for: {disease_title or 'unknown'} "
        f"({protein_title or 'unknown protein'}, {len(sequence)} aa) "
        f"[amina-cli: {'READY' if AMINA_CLI_AVAILABLE else 'NOT INSTALLED'}, "
        f"API key: {'SET' if _get_amina_key() else 'MISSING'}]"
    )

    phases_completed: list[str] = []
    result: dict[str, Any] = {
        "pipeline": "amina_cli",
        "phases_completed": phases_completed,
        "error": "",
    }

    # ── Phase 2: Sequence Analysis (amina_ai) ──────────────
    log.info("Phase 2: Amino acid sequence analysis...")
    protein_analysis = analyse_protein(sequence)
    if "error" in protein_analysis:
        result["error"] = f"Sequence analysis failed: {protein_analysis['error']}"
        return result

    phases_completed.append("sequence_analysis")
    result["protein_analysis"] = protein_analysis

    compound_scores = score_compounds_against_protein(protein_analysis)
    result["compound_scores"] = compound_scores

    # ── Phase 3: ESMFold Structure Prediction (Amina Cloud GPU) ──
    log.info("Phase 3: Amina ESMFold 3D structure prediction (cloud GPU)...")
    structure_result = await predict_structure(
        sequence,
        protein_title=protein_title,
        disease_title=disease_title,
        cache_key=f"{disease_title}_{protein_title}".replace(" ", "_")[:40] if disease_title else None,
    )

    result["structure_prediction"] = {
        k: v for k, v in structure_result.items() if k != "pdb_content"
    }

    if structure_result.get("error"):
        log.warning(f"Phase 3 failed: {structure_result['error']} — falling back to sequence-only analysis")
    else:
        phases_completed.append("structure_prediction")
        pdb_path = structure_result.get("pdb_path", "")

        # ── Phase 3b-e: Structure Enrichment (Amina Cloud) ─
        log.info(
            "Phase 3b-e: Amina structure enrichment "
            "(pdb-cleaner → quality-assessment → p2rank → sasa)..."
        )
        enrichment = await run_structure_enrichment(pdb_path)
        result["structure_enrichment"] = enrichment

        if not enrichment.get("error"):
            phases_completed.append("structure_enrichment")
            log.info(f"  Enrichment: {enrichment.get('enrichment_summary', 'done')}")

            # Use cleaned PDB for docking if available
            cleaned_path = enrichment.get("cleaning", {}).get("cleaned_pdb_path")
            if cleaned_path:
                pdb_path = cleaned_path
                structure_result["pdb_content"] = Path(cleaned_path).read_text(encoding="utf-8")

        # ── Phase 4: Molecular Docking (Amina DiffDock) ────
        log.info("Phase 4: Amina DiffDock molecular docking (cloud GPU)...")
        docking_result = await dock_phytochemicals(
            pdb_content=structure_result["pdb_content"],
            pdb_path=pdb_path,
            disease_title=disease_title,
            protein_title=protein_title,
            protein_analysis=protein_analysis,
            confidence_info=structure_result.get("confidence_info"),
        )

        result["docking_results"] = docking_result

        if docking_result.get("error"):
            log.warning(f"Phase 4 failed: {docking_result['error']} — using sequence-only scores")
        else:
            phases_completed.append("molecular_docking")
            compound_scores = _merge_scores(compound_scores, docking_result)
            result["compound_scores"] = compound_scores
            result["docking_summary"] = format_docking_summary(docking_result)

    # ── Top compounds with food sources ────────────────────
    top_5 = compound_scores[:5]
    phyto_db = get_phytochemical_library()
    phyto_lookup = {c["name"]: c for c in phyto_db}

    top_compounds_with_foods = []
    for cs in top_5:
        entry = {
            "compound": cs["compound"],
            "score": cs["score"],
            "class": cs.get("class", ""),
            "mechanisms": cs.get("mechanisms", []),
            "food_sources": phyto_lookup.get(cs["compound"], {}).get("food_sources", []),
        }
        if "docking_score" in cs:
            entry["docking_score"] = cs["docking_score"]
            entry["binding_energy_kcal"] = cs.get("binding_energy_kcal", 0)
        top_compounds_with_foods.append(entry)

    result["top_compounds"] = top_compounds_with_foods

    # ── Build summary ──────────────────────────────────────
    hints = protein_analysis.get("structural_hints", [])
    motif_count = len(protein_analysis.get("motifs_found", []))
    druggable_count = sum(1 for m in protein_analysis.get("motifs_found", []) if m.get("druggable"))

    summary_parts = [
        f"Amina CLI full pipeline for {protein_title or 'unknown protein'} "
        f"from {protein_organism or 'unknown organism'} ({protein_analysis['length']} aa):",
        f"  Phases completed: {', '.join(phases_completed)}",
        f"  MW: {protein_analysis['properties']['molecular_weight_kda']:.1f} kDa, "
        f"Net charge: {protein_analysis['properties']['net_charge']:+d}",
        f"  Motifs found: {motif_count} ({druggable_count} druggable)",
    ]

    if "structure_prediction" in phases_completed:
        conf = structure_result.get("confidence_info", {})
        summary_parts.append(
            f"  3D Structure: pLDDT={conf.get('mean_plddt', 0):.1f} "
            f"(high confidence: {conf.get('high_confidence_fraction', 0):.0%})"
        )

    if "structure_enrichment" in phases_completed:
        enrich = result.get("structure_enrichment", {})
        summary_parts.append(f"  Enrichment: {enrich.get('enrichment_summary', 'n/a')}")
        bs = enrich.get("binding_sites", {})
        if bs.get("num_pockets"):
            summary_parts.append(
                f"  Binding sites: {bs['num_pockets']} pocket(s) "
                f"(top score: {bs.get('top_pocket_score', 0):.2f})"
            )

    if "molecular_docking" in phases_completed:
        dock_res = result.get("docking_results", {})
        summary_parts.append(
            f"  Top docking hit: {dock_res.get('top_ligand', '?')} "
            f"(score: {dock_res.get('confidence_score', 0):.3f}, "
            f"method: {dock_res.get('docking_method', '?')})"
        )

    if hints:
        summary_parts.append("  Key findings:")
        for h in hints:
            summary_parts.append(f"    -> {h}")

    summary_parts.append(f"  Top predicted compounds: {', '.join(c['compound'] for c in top_5)}")
    amina_summary = "\n".join(summary_parts)
    result["amina_summary"] = amina_summary

    # ── Phase 5: FLock LLM enrichment ──────────────────────
    log.info("Phase 5: FLock LLM nutrition strategy enrichment...")
    nutrition_strategy = await _amina_llm_strategy(
        disease_title=disease_title,
        protein_title=protein_title,
        protein_organism=protein_organism,
        amina_summary=amina_summary,
        top_compounds=top_compounds_with_foods,
        structural_hints=hints,
        who_context=who_context,
    )

    result["nutrition_strategy"] = nutrition_strategy
    phases_completed.append("llm_enrichment")

    log.info(
        f"Amina CLI pipeline complete: {len(phases_completed)} phases, "
        f"top compound: {top_5[0]['compound'] if top_5 else 'none'}"
    )
    return result


# ═════════════════════════════════════════════════════════════
#  FLOCK LLM — AMINA-ENRICHED STRATEGY GENERATION
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
        return _build_fallback_strategy(disease_title, top_compounds, structural_hints)

    compounds_text = "\n".join(
        f"  {i+1}. {c['compound']} (score: {c['score']:.3f}, class: {c.get('class', '?')})\n"
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
                headers=headers, json=payload, timeout=FLOCK_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"].strip()

        result = _parse_llm_json(raw)
        if not result:
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
        mechanisms_text = "; ".join(c.get("mechanisms", [])[:2]) or \
            "Predicted interaction based on structural analysis"
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
            "primary_goal": f"Targeted nutritional defence based on protein structure analysis "
                            f"({'; '.join(hints[:2]) if hints else 'general immune support'})",
            "compounds": compounds,
            "additional_nutrients": [
                {"nutrient": "Vitamin C", "role": "Broad immune support; antioxidant protection",
                 "food_sources": ["Bell peppers", "Kiwi", "Citrus"]},
                {"nutrient": "Zinc", "role": "T-cell function; general antiviral",
                 "food_sources": ["Pumpkin seeds", "Lentils"]},
                {"nutrient": "Vitamin D", "role": "Immune modulation",
                 "food_sources": ["Fatty fish", "Egg yolks"]},
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


# ═════════════════════════════════════════════════════════════
#  FLOCK LLM — RESEARCH FALLBACK (for when bio-pipeline fails)
# ═════════════════════════════════════════════════════════════

RESEARCH_SYSTEM_PROMPT = """\
You are a biodefence nutrition research agent. You analyse disease outbreaks and \
generate evidence-based nutritional defence strategies.

Given information about a disease (WHO alert text, and optionally a protein sequence), \
produce a JSON object with this EXACT structure:

{
  "display_name": "Human-readable disease name",
  "pathogen_type": "virus" | "bacteria" | "parasite" | "fungus" | "unknown",
  "family": "Taxonomic family if known, else empty string",
  "transmission": "How it spreads",
  "nutrition_strategy": {
    "primary_goal": "One-line summary of nutritional defence strategy",
    "compounds": [
      {
        "name": "Compound name (must be a real phytochemical or nutrient)",
        "mechanism": "How it helps against THIS specific pathogen",
        "evidence": "Brief citation or evidence summary"
      }
    ],
    "additional_nutrients": [
      {
        "nutrient": "Nutrient name",
        "role": "Why it matters for this disease",
        "food_sources": ["Food 1", "Food 2"]
      }
    ],
    "dietary_advice": [
      "Practical, actionable dietary advice item 1",
      "Practical, actionable dietary advice item 2"
    ]
  }
}

RULES:
- Include 3-5 compounds and 2-4 additional nutrients
- Each compound MUST be a real, well-studied phytochemical (e.g., Quercetin, EGCG, Curcumin, Allicin, Resveratrol, Sulforaphane, Gingerol, etc.)
- Evidence must reference real mechanisms or studies (even if approximate)
- If a protein sequence is provided, consider its structure for potential binding targets
- Include a NOTE in dietary_advice if the disease requires urgent medical care
- Return ONLY the JSON object, no other text
- Do NOT wrap in markdown code blocks
"""


async def _call_flock(prompt: str) -> str:
    """Call FLock LLM and return the response text."""
    if not FLOCK_API_KEY:
        log.warning("FLOCK_API_KEY not set — cannot run research agent")
        return ""

    headers = {
        "Content-Type": "application/json",
        "x-litellm-api-key": FLOCK_API_KEY,
    }
    payload = {
        "model": FLOCK_MODEL,
        "messages": [
            {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{FLOCK_BASE_URL}/chat/completions",
            headers=headers, json=payload, timeout=FLOCK_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


def _parse_llm_json(raw: str) -> dict[str, Any] | None:
    """Extract JSON from LLM response, handling markdown wrappers."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    log.warning(f"Failed to parse LLM JSON response: {raw[:200]}...")
    return None


async def research_unknown_disease(
    *,
    disease_title: str,
    who_overview: str = "",
    who_advice: str = "",
    who_assessment: str = "",
    who_epidemiology: str = "",
    protein_sequence: str = "",
    protein_title: str = "",
    protein_organism: str = "",
    amina_summary: str = "",
    amina_top_compounds: list[dict] | None = None,
) -> dict[str, Any] | None:
    """
    Use FLock LLM to generate nutritional recommendations for an unknown disease.
    This is the FALLBACK when the full bio-pipeline (Phases 2-4) cannot run.
    """
    cache_key = disease_title.lower().strip()
    if cache_key in _research_cache:
        log.debug(f"Research cache hit: {cache_key}")
        return _research_cache[cache_key]

    prompt_parts = [f"Disease: {disease_title}\n"]
    if who_overview:
        prompt_parts.append(f"WHO Overview:\n{who_overview}\n")
    if who_epidemiology:
        prompt_parts.append(f"WHO Epidemiology:\n{who_epidemiology}\n")
    if who_assessment:
        prompt_parts.append(f"WHO Risk Assessment:\n{who_assessment}\n")
    if who_advice:
        prompt_parts.append(f"WHO Advice:\n{who_advice}\n")

    if protein_sequence:
        seq_preview = protein_sequence[:200]
        if len(protein_sequence) > 200:
            seq_preview += f"... ({len(protein_sequence)} total amino acids)"
        prompt_parts.append(
            f"Protein Sequence ({protein_title}, {protein_organism}):\n{seq_preview}\n"
        )

    # ── Amina AI enrichment ──
    if amina_summary:
        prompt_parts.append(f"\n=== AMINA AI PROTEIN ANALYSIS ===\n{amina_summary}\n")
    if amina_top_compounds:
        prompt_parts.append("=== AMINA AI TOP PREDICTED COMPOUNDS (by binding affinity) ===")
        for i, c in enumerate(amina_top_compounds[:5]):
            mechanisms = "; ".join(c.get("mechanisms", [])[:2]) if c.get("mechanisms") else "General interaction"
            prompt_parts.append(
                f"  {i+1}. {c.get('compound', '?')} (score: {c.get('score', 0):.3f}) — {mechanisms}"
            )
        prompt_parts.append(
            "\nUSE the Amina AI compound scores above to prioritise which "
            "phytochemicals to recommend. Higher scores = stronger predicted binding."
        )

    prompt_parts.append(
        "\nBased on this information, generate a nutritional defence strategy "
        "JSON object for this disease."
    )

    prompt = "\n".join(prompt_parts)

    try:
        raw_response = await _call_flock(prompt)
        if not raw_response:
            return None

        result = _parse_llm_json(raw_response)
        if not result:
            return None

        if "nutrition_strategy" not in result:
            log.warning("Research agent response missing nutrition_strategy")
            return None

        result["source"] = "research_agent"
        result["llm_model"] = FLOCK_MODEL
        _research_cache[cache_key] = result

        log.info(
            f"Research agent generated strategy for '{disease_title}': "
            f"{len(result.get('nutrition_strategy', {}).get('compounds', []))} compounds"
        )
        return result

    except httpx.HTTPStatusError as e:
        log.error(f"Research agent FLock HTTP error: {e.response.status_code}")
        return None
    except Exception as e:
        log.error(f"Research agent error: {e}")
        return None


# ═════════════════════════════════════════════════════════════
#  FORMATTING & CACHE INFO
# ═════════════════════════════════════════════════════════════

def format_docking_summary(result: dict[str, Any], top_n: int = 5) -> str:
    """Format docking results as a human-readable summary string."""
    if result.get("error"):
        return f"Docking failed: {result['error']}"

    lines = [
        f"Molecular Docking Report: {result.get('threat_name', 'Unknown')}",
        f"   Protein: {result.get('protein_title', 'Unknown')}",
        f"   Method:  {result.get('docking_method', 'unknown')}",
        f"   Top ligand: {result.get('top_ligand', '?')} "
        f"(score: {result.get('confidence_score', 0):.3f})",
        "",
        f"   Top {top_n} candidates:",
    ]

    for r in result.get("all_results", [])[:top_n]:
        rank = r.get("rank", "?")
        name = r.get("compound", "?")
        bscore = r.get("binding_score", 0)
        energy = r.get("binding_energy_kcal", 0)
        foods = ", ".join(r.get("food_sources", [])[:3])
        lines.append(
            f"   {rank}. {name:20s}  score={bscore:.3f}  "
            f"dG={energy:+.1f} kcal/mol  [{foods}]"
        )
        for mech in r.get("mechanisms", [])[:2]:
            lines.append(f"      -- {mech}")

    quality = result.get("structure_quality", {})
    if quality.get("mean_plddt"):
        lines.append(
            f"\n   Structure confidence: pLDDT={quality['mean_plddt']:.1f} "
            f"(high conf: {quality.get('high_confidence_fraction', 0):.0%})"
        )

    return "\n".join(lines)


def get_research_cache_info() -> dict[str, Any]:
    """Return metadata about all ResearcherAgent caches and Amina CLI status."""
    pdb_files = list(PDB_DIR.glob("*.pdb"))
    analysis_files = list(ANALYSIS_DIR.glob("*")) if ANALYSIS_DIR.exists() else []
    return {
        "amina_cli_installed": AMINA_CLI_AVAILABLE,
        "amina_api_key_set": bool(_get_amina_key()),
        "amina_cli_version": "0.2.5" if AMINA_CLI_AVAILABLE else None,
        "amina_tools_used": [
            "esmfold", "pdb-cleaner", "pdb-quality-assessment",
            "p2rank", "sasa", "diffdock",
        ],
        "research_strategies_cached": len(_research_cache),
        "research_diseases": list(_research_cache.keys()),
        "structure_memory_cached": len(_pdb_cache),
        "structure_file_cached": len(pdb_files),
        "analysis_files_cached": len(analysis_files),
        "pdb_dir": str(PDB_DIR),
        "analysis_dir": str(ANALYSIS_DIR),
        "docking_results_dir": str(DOCKING_RESULTS_DIR),
        "phytochemical_library_loaded": _PHYTOCHEM_LIBRARY is not None,
        "phytochemical_count": len(_PHYTOCHEM_LIBRARY) if _PHYTOCHEM_LIBRARY else 0,
    }
