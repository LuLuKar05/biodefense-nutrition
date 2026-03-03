"""Celery task: molecular docking via Amina CLI (DiffDock)."""

# TODO: For each phytochemical in data/phytochemicals.json,
# call Amina CLI diffdock with .pdb + SMILES (using --background)
# Rank by confidence score, store in MongoDB docking_results
# Fallback to data/mock_docking/ if Amina CLI unavailable
