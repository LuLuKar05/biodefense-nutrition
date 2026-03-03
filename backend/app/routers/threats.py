from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_threats(zone: str | None = None):
    """Phase 2: List active threats for a geographic zone."""
    # TODO: Query MongoDB threats collection
    return {"message": "threats list endpoint — not yet implemented"}


@router.get("/{threat_id}/structure")
async def get_structure(threat_id: str):
    """Phase 3: Get PDB structure file or computation status."""
    # TODO: Return PDB from GridFS or status
    return {"message": "structure endpoint — not yet implemented"}


@router.get("/{threat_id}/docking-results")
async def get_docking_results(threat_id: str):
    """Phase 4: Get top-N ligand results with food sources."""
    # TODO: Query MongoDB docking_results collection
    return {"message": "docking-results endpoint — not yet implemented"}
