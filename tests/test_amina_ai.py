"""Quick test for Amina AI protein analysis + compound scoring."""
import asyncio
import json
from threat_backend.amina_ai import analyse_protein, score_compounds_against_protein, amina_analyse

# Load H5N1 FASTA
with open("data/mock_sequences/h5n1_hemagglutinin.fasta") as f:
    lines = f.read().strip().split("\n")
seq = "".join(l.strip() for l in lines[1:] if not l.startswith(">"))
header = lines[0][1:]

print(f"Sequence: {header}")
print(f"Length: {len(seq)} aa")
print()

# Step 1: Protein analysis
analysis = analyse_protein(seq)
props = analysis["properties"]
print("=== PROTEIN ANALYSIS ===")
print(f"  MW: {props['molecular_weight_kda']} kDa")
print(f"  Net charge: {props['net_charge']}")
print(f"  Hydrophobic: {props['hydrophobic_fraction']}")
print(f"  Aromatic: {props['aromatic_fraction']}")
print(f"  Cysteine: {props['cysteine_fraction']}")
print()

print("=== MOTIFS FOUND ===")
for m in analysis["motifs_found"]:
    print(f"  {m['motif']} at pos {m['position']}: {m['function']} (druggable: {m['druggable']})")
if not analysis["motifs_found"]:
    print("  (none)")
print()

print("=== BINDING OPPORTUNITIES ===")
for k, v in analysis["binding_opportunities"].items():
    print(f"  {k}: {v}")
print()

print("=== STRUCTURAL HINTS ===")
for h in analysis["structural_hints"]:
    print(f"  -> {h}")
print()

# Step 2: Score compounds
scores = score_compounds_against_protein(analysis)
print("=== COMPOUND SCORES (all 15) ===")
for s in scores:
    print(f"  #{s['rank']:2d} {s['compound']:22s} score={s['score']:.3f}  ({s['class']})")
    for mech in s.get("mechanisms", [])[:2]:
        print(f"       -> {mech[:90]}")
print()

# Step 3: Full Amina AI pipeline (with LLM)
print("=== FULL AMINA AI PIPELINE (with FLock LLM) ===")
async def run_full():
    result = await amina_analyse(
        sequence=seq,
        protein_title="Hemagglutinin",
        protein_organism="Influenza A virus H5N1",
        disease_title="Avian Influenza A(H5N1) - Test",
        who_context="H5N1 avian influenza detected. Risk to humans from bird-to-human transmission.",
    )
    print(f"  Amina summary:\n{result.get('amina_summary', 'N/A')}")
    print()
    strat = result.get("nutrition_strategy", {})
    if strat:
        source = strat.get("source", "unknown")
        print(f"  Strategy source: {source}")
        ns = strat.get("nutrition_strategy", strat)
        compounds = ns.get("compounds", [])
        print(f"  Compounds: {len(compounds)}")
        for c in compounds[:5]:
            print(f"    - {c.get('name', '?')}: {c.get('mechanism', '')[:80]}")
    else:
        print("  No nutrition strategy generated")

asyncio.run(run_full())
print("\nDone!")
