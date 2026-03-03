"""FLock federated weight aggregation — server side.

This service receives ONLY weight vectors from users' local training.
It NEVER receives raw user data. Aggregation uses Federated Averaging (FedAvg).
"""

from __future__ import annotations

from typing import Any

# In-memory store for hackathon (MongoDB in production)
_weight_store: list[dict[str, Any]] = []


async def store_weights(payload: dict[str, Any]) -> dict[str, str]:
    """Store a weight vector submitted by a user's local training.

    The payload contains ONLY: zone, model_version, weights[], num_samples, timestamp.
    No personal health data.
    """
    _weight_store.append(payload)
    return {"status": "accepted", "total_contributions": str(len(_weight_store))}


async def aggregate_weights() -> dict[str, Any]:
    """Aggregate all submitted weights using FedAvg.

    Returns the averaged weight vector for clients to download.
    """
    if not _weight_store:
        return {"status": "no_weights", "weights": []}

    # FedAvg: weighted average by num_samples
    total_samples: int = sum(int(w.get("num_samples", 1)) for w in _weight_store)
    if total_samples == 0:
        total_samples = len(_weight_store)

    # Determine weight vector length from first entry
    first_weights: list[float] = _weight_store[0].get("weights", [])
    vec_len: int = len(first_weights)

    if vec_len == 0:
        return {"status": "no_weights", "weights": []}

    aggregated: list[float] = [0.0] * vec_len

    for entry in _weight_store:
        w_vec: list[float] = entry.get("weights", [])
        n: int = int(entry.get("num_samples", 1))
        for i in range(min(vec_len, len(w_vec))):
            aggregated[i] += w_vec[i] * (n / total_samples)

    return {
        "status": "aggregated",
        "weights": aggregated,
        "num_contributors": len(_weight_store),
        "total_samples": total_samples,
        "privacy_note": "Aggregated from weight vectors only. No individual user data.",
    }
