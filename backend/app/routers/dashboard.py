"""Dashboard router — pipeline visualization for judges (zero user data)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/pipeline-status")
async def pipeline_status() -> dict[str, Any]:
    """Return status of the threat intelligence pipeline stages.

    Used by the Next.js dashboard to visualize pipeline health for judges.
    """
    # TODO: Query MongoDB for actual pipeline state
    return {
        "stages": {
            "threat_scanning": {"status": "active", "last_run": None},
            "sequence_fetch": {"status": "idle", "last_run": None},
            "structure_prediction": {"status": "idle", "last_run": None},
            "molecular_docking": {"status": "idle", "last_run": None},
            "weight_aggregation": {"status": "idle", "last_run": None},
        },
        "active_threats": 0,
        "total_docking_runs": 0,
        "flock_contributors": 0,
    }


@router.get("/zone-summary")
async def zone_summary(zone: str | None = None) -> dict[str, Any]:
    """Return threat summary for a zone (public data only)."""
    # TODO: Aggregate from MongoDB threats + docking_results
    return {
        "zone": zone or "global",
        "active_threats": [],
        "top_compounds": [],
        "total_contributions": 0,
    }
