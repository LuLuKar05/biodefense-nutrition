"""
sequence_fetcher.py — NCBI Entrez amino-acid sequence fetcher
=============================================================
Fetches protein (amino-acid) sequences from NCBI GenBank / Protein DB
for pathogens detected in WHO DON alerts.

Uses the NCBI Entrez E-utilities (public, no API key required for
low-volume use — <3 requests/sec).

Workflow:
  1. esearch.fcgi  → search the 'protein' DB for a pathogen term
  2. efetch.fcgi   → download FASTA sequence for the top hit
  3. Cache results  — protein sequences rarely change

The fetcher is used in the hybrid pipeline:
  - Known diseases: the search term comes from disease_nutrition_db.json
  - Unknown diseases: the WHO title is used as a search hint

API docs: https://www.ncbi.nlm.nih.gov/books/NBK25499/
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

log = logging.getLogger("threat_backend.ncbi")

# ── NCBI Entrez E-utilities ─────────────────────────────────
NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_TIMEOUT = 15  # seconds
TOOL_NAME = "biodefense_nutrition"
TOOL_EMAIL = "biodefense@example.com"  # NCBI requests tool + email

# Cache for fetched sequences (pathogen_key → SequenceResult)
_seq_cache: dict[str, dict[str, Any]] = {}
SEQ_CACHE_TTL = timedelta(days=7)  # protein sequences don't change often


# ═════════════════════════════════════════════════════════════
# DATA MODEL
# ═════════════════════════════════════════════════════════════

def _make_result(
    *,
    query: str,
    protein_id: str = "",
    title: str = "",
    organism: str = "",
    sequence: str = "",
    length: int = 0,
    fetched_at: str = "",
    error: str = "",
) -> dict[str, Any]:
    """Standard result dict for a sequence fetch."""
    return {
        "query": query,
        "protein_id": protein_id,
        "title": title,
        "organism": organism,
        "sequence": sequence,
        "length": length,
        "fetched_at": fetched_at,
        "error": error,
    }


# ═════════════════════════════════════════════════════════════
# NCBI API CALLS
# ═════════════════════════════════════════════════════════════

async def _esearch(
    client: httpx.AsyncClient, term: str, retmax: int = 3
) -> list[str]:
    """
    Search NCBI Protein DB for a term.
    Returns a list of Protein GI/accession IDs.
    """
    params = {
        "db": "protein",
        "term": term,
        "retmax": str(retmax),
        "retmode": "json",
        "sort": "relevance",
        "tool": TOOL_NAME,
        "email": TOOL_EMAIL,
    }

    try:
        resp = await client.get(
            f"{NCBI_BASE}/esearch.fcgi", params=params, timeout=NCBI_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        id_list = data.get("esearchresult", {}).get("idlist", [])
        log.info(f"  NCBI esearch '{term}': {len(id_list)} hit(s)")
        return id_list
    except Exception as e:
        log.warning(f"  NCBI esearch error for '{term}': {e}")
        return []


async def _efetch_fasta(
    client: httpx.AsyncClient, protein_id: str
) -> dict[str, str]:
    """
    Fetch a single protein sequence in FASTA format.
    Returns {title, organism, sequence} parsed from FASTA.
    """
    params = {
        "db": "protein",
        "id": protein_id,
        "rettype": "fasta",
        "retmode": "text",
        "tool": TOOL_NAME,
        "email": TOOL_EMAIL,
    }

    try:
        resp = await client.get(
            f"{NCBI_BASE}/efetch.fcgi", params=params, timeout=NCBI_TIMEOUT
        )
        resp.raise_for_status()
        fasta_text = resp.text.strip()
        return _parse_fasta(fasta_text)
    except Exception as e:
        log.warning(f"  NCBI efetch error for id={protein_id}: {e}")
        return {"title": "", "organism": "", "sequence": ""}


def _parse_fasta(fasta: str) -> dict[str, str]:
    """
    Parse a FASTA-format protein sequence.
    Header line: >accession description [Organism]
    Remaining lines: amino acid sequence
    """
    lines = fasta.strip().split("\n")
    if not lines or not lines[0].startswith(">"):
        return {"title": "", "organism": "", "sequence": ""}

    header = lines[0][1:]  # strip leading '>'
    sequence = "".join(line.strip() for line in lines[1:] if not line.startswith(">"))

    # Extract organism from [brackets]
    org_match = re.search(r"\[(.+?)\]", header)
    organism = org_match.group(1) if org_match else ""

    # Title is everything before the organism bracket
    title = re.sub(r"\s*\[.+?\]", "", header).strip()

    return {
        "title": title,
        "organism": organism,
        "sequence": sequence,
    }


# ═════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════

async def fetch_protein_sequence(
    search_term: str,
    cache_key: str | None = None,
) -> dict[str, Any]:
    """
    Fetch a protein (amino acid) sequence from NCBI for a pathogen.

    Args:
        search_term: NCBI search query, e.g. "SARS-CoV-2 spike protein"
        cache_key:   Optional cache key (defaults to search_term)

    Returns:
        {
            "query": "...",
            "protein_id": "accession",
            "title": "protein description",
            "organism": "Species name",
            "sequence": "MFVFLVLLPL...",   # amino acid one-letter codes
            "length": 1273,
            "fetched_at": "ISO datetime",
            "error": ""  # empty if success
        }
    """
    key = cache_key or search_term.lower().strip()

    # Check cache
    cached = _seq_cache.get(key)
    if cached:
        fetched_str = cached.get("fetched_at", "")
        if fetched_str:
            try:
                fetched_dt = datetime.fromisoformat(fetched_str)
                if datetime.now(timezone.utc) - fetched_dt < SEQ_CACHE_TTL:
                    log.debug(f"NCBI cache hit: {key}")
                    return cached
            except ValueError:
                pass

    log.info(f"Fetching protein sequence: '{search_term}'")

    async with httpx.AsyncClient() as client:
        # Step 1: Search
        ids = await _esearch(client, search_term, retmax=3)
        if not ids:
            result = _make_result(
                query=search_term,
                error=f"No protein sequences found for '{search_term}'",
            )
            _seq_cache[key] = result
            return result

        # Step 2: Fetch best hit (first result by relevance)
        protein_id = ids[0]
        fasta = await _efetch_fasta(client, protein_id)

        if not fasta.get("sequence"):
            result = _make_result(
                query=search_term,
                protein_id=protein_id,
                error="FASTA sequence was empty",
            )
            _seq_cache[key] = result
            return result

        result = _make_result(
            query=search_term,
            protein_id=protein_id,
            title=fasta["title"],
            organism=fasta["organism"],
            sequence=fasta["sequence"],
            length=len(fasta["sequence"]),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

        _seq_cache[key] = result
        log.info(
            f"  Fetched: {fasta['title'][:60]} "
            f"({len(fasta['sequence'])} aa) [{fasta['organism']}]"
        )
        return result


async def fetch_sequences_for_outbreaks(
    outbreaks: list[dict[str, Any]],
    disease_db: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """
    Fetch protein sequences for all diseases detected in current outbreaks.

    Args:
        outbreaks:  List of outbreak dicts (from generate_outbreaks_from_who)
        disease_db: The disease_nutrition_db.json 'diseases' dict

    Returns:
        {disease_key: SequenceResult, ...}
    """
    from threat_backend.outbreak_fetcher import extract_disease_key

    # Collect unique disease keys that have NCBI search terms
    to_fetch: dict[str, str] = {}  # disease_key → search_term
    for ob in outbreaks:
        name = ob.get("name", "")
        dkey = extract_disease_key(name)
        if dkey == "unknown":
            continue
        if dkey in to_fetch:
            continue
        db_entry = disease_db.get(dkey, {})
        search_term = db_entry.get("ncbi_search_term", "")
        if search_term:
            to_fetch[dkey] = search_term

    # Fetch sequences (sequential to respect NCBI rate limit)
    results: dict[str, dict[str, Any]] = {}
    for dkey, term in to_fetch.items():
        results[dkey] = await fetch_protein_sequence(term, cache_key=dkey)

    return results


def get_sequence_cache_info() -> dict[str, Any]:
    """Return metadata about the sequence cache state."""
    return {
        "cached_sequences": len(_seq_cache),
        "diseases": list(_seq_cache.keys()),
        "total_aa_cached": sum(
            r.get("length", 0) for r in _seq_cache.values()
        ),
    }
