"""
Test the REAL Amina CLI integration (ESMFold + DiffDock via cloud GPU).
Requires: pip install amina-cli  +  AMINA_API_KEY in .env

This test uses a short peptide to minimize cost (~$0.001 per fold).
"""
import asyncio
import json
import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parent))

from threat_backend.research_agent import (
    predict_structure,
    dock_phytochemicals,
    ensure_amina_auth,
    get_research_cache_info,
    get_phytochemical_library,
    AMINA_CLI_AVAILABLE,
    _get_amina_key,
)


# Short test sequence (22 aa) — cheap to fold
TEST_SEQUENCE = "MKFLILLFNILCLFPVLAADNH"
TEST_PROTEIN = "Test Peptide"
TEST_DISEASE = "Amina CLI Integration Test"


async def test_esmfold():
    """Test real Amina ESMFold structure prediction."""
    print("=" * 60)
    print("TEST 1: Amina ESMFold (cloud GPU)")
    print("=" * 60)

    result = await predict_structure(
        TEST_SEQUENCE,
        protein_title=TEST_PROTEIN,
        disease_title=TEST_DISEASE,
        cache_key="amina_test",
    )

    if result.get("error"):
        print(f"  ERROR: {result['error']}")
        return None

    print(f"  Source: {result['source']}")
    print(f"  PDB path: {result['pdb_path']}")
    print(f"  Sequence length: {result['sequence_length']} aa")
    print(f"  Amina job ID: {result.get('amina_job_id', 'N/A')}")
    print(f"  pLDDT CSV: {result.get('plddt_csv_path', 'N/A')}")
    print(f"  pLDDT plot: {result.get('plddt_plot_path', 'N/A')}")

    conf = result.get("confidence_info", {})
    print(f"  Mean pLDDT: {conf.get('mean_plddt', 0):.1f}")
    print(f"  High confidence: {conf.get('high_confidence_fraction', 0):.0%}")
    print(f"  Residues: {conf.get('residue_count', 0)}")

    # Verify PDB file exists and has content
    pdb_path = Path(result["pdb_path"])
    if pdb_path.exists():
        size = pdb_path.stat().st_size
        print(f"  PDB file size: {size:,} bytes")
        # Show first 3 ATOM lines
        lines = pdb_path.read_text().split("\n")
        atom_lines = [l for l in lines if l.startswith("ATOM")][:3]
        for l in atom_lines:
            print(f"    {l[:72]}")
    print()
    return result


async def test_diffdock(structure_result: dict | None):
    """Test real Amina DiffDock docking (only if structure available)."""
    print("=" * 60)
    print("TEST 2: Amina DiffDock (cloud GPU)")
    print("=" * 60)

    if not structure_result or structure_result.get("error"):
        print("  SKIPPED: No structure available from ESMFold")
        return

    pdb_content = structure_result.get("pdb_content", "")
    pdb_path = structure_result.get("pdb_path", "")

    if not pdb_content:
        print("  SKIPPED: No PDB content")
        return

    # Only dock top 3 compounds to save credits
    library = get_phytochemical_library()[:3]
    print(f"  Docking {len(library)} compounds (limited to save credits)...")

    result = await dock_phytochemicals(
        pdb_content=pdb_content,
        pdb_path=pdb_path,
        disease_title=TEST_DISEASE,
        protein_title=TEST_PROTEIN,
    )

    if result.get("error"):
        print(f"  ERROR: {result['error']}")
        return

    print(f"  Method: {result['docking_method']}")
    print(f"  Top ligand: {result['top_ligand']}")
    print(f"  Top score: {result['confidence_score']:.4f}")
    print()

    for r in result.get("all_results", [])[:5]:
        print(
            f"  #{r['rank']} {r['compound']:20s} "
            f"score={r['binding_score']:.4f} "
            f"dG={r['binding_energy_kcal']:+.1f} kcal/mol"
        )
        if r.get("amina_job_id"):
            print(f"       job: {r['amina_job_id']}")
    print()


async def main():
    print("\n🧬 Biodefense Nutrition — Real Amina CLI Integration Test\n")

    # Check prerequisites
    info = get_research_cache_info()
    print(f"  amina-cli installed: {info['amina_cli_installed']}")
    print(f"  API key set: {info['amina_api_key_set']}")
    print(f"  Phytochemicals: {info['phytochemical_count']}")
    print()

    if not AMINA_CLI_AVAILABLE:
        print("FAIL: amina-cli not installed. Run: pip install amina-cli")
        return

    if not _get_amina_key():
        print("FAIL: AMINA_API_KEY not set in .env")
        print("  Get one at: https://app.aminoanalytica.com/settings/api")
        return

    # Authenticate
    if not ensure_amina_auth():
        print("FAIL: Could not authenticate with Amina CLI")
        return

    print("Amina CLI authenticated — running tests...\n")

    # Test 1: ESMFold
    structure = await test_esmfold()

    # Test 2: DiffDock (only if ESMFold succeeded)
    await test_diffdock(structure)

    print("=" * 60)
    print("All tests complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
