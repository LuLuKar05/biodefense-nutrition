"""FLock federated learning router — weight vectors only, zero user data."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.services.flock_federated import aggregate_weights, store_weights

router = APIRouter()


@router.post("/weights")
async def submit_weights(payload: dict[str, Any]) -> dict[str, str]:
    """Accept weight vectors from a user's local FL training.

    The payload contains ONLY: zone, model_version, weights[], num_samples, timestamp.
    No personal health data is ever sent here.
    """
    return await store_weights(payload)


@router.get("/aggregated-weights")
async def get_aggregated_weights() -> dict[str, Any]:
    """Return FedAvg-aggregated weights for clients to download."""
    return await aggregate_weights()
