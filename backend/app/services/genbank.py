"""Phase 2: NCBI GenBank sequence fetching via Biopython."""

# TODO: Use Entrez.esearch + Entrez.efetch to pull amino acid sequences
# Fallback: serve from data/mock_sequences/


async def fetch_sequence(threat_name: str) -> str | None:
    """Fetch the latest amino acid sequence for a threat's target protein.

    Args:
        threat_name: e.g. "H5N1", "SARS-CoV-2"

    Returns:
        Amino acid sequence string or None if unavailable.
    """
    # TODO: Implement Biopython Entrez queries
    # Fallback to data/mock_sequences/{threat_name}.fasta
    return None
