"""Celery task: predict 3D protein structure via Amina CLI (ESMFold)."""

# TODO: Accept amino acid sequence, call Amina CLI esmfold
# Store .pdb result in MongoDB GridFS or data/pdb_cache/
# Fallback to data/mock_pdb/ if Amina CLI unavailable
