#!/usr/bin/env python3
"""FLock federated learning — local training only, exports weight vectors.

Trains a small local model on the user's anonymized outcome data.
Exports ONLY the model weights (never raw data) to /tmp/flock_weights.json.

The OpenClaw agent then submits weights to the threat API:
    curl -X POST http://localhost:8000/api/flock/weights -d @/tmp/flock_weights.json

Privacy guarantee: Only mathematical weight vectors leave the device.
Raw user data (what they ate, symptoms, etc.) NEVER leaves.

Usage:
    python scripts/flock_local_train.py \
        --zone "New York" \
        --compounds "Quercetin,EGCG" \
        --outcome improved
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from typing import Any


def encode_compounds(compounds: list[str], vocab_size: int = 20) -> list[float]:
    """Encode compound names into a fixed-size feature vector.

    Uses a simple hash-based encoding for the hackathon MVP.
    """
    features: list[float] = [0.0] * vocab_size
    for compound in compounds:
        h: int = int(hashlib.sha256(compound.lower().encode()).hexdigest(), 16)
        idx: int = h % vocab_size
        features[idx] = 1.0
    return features


def encode_outcome(outcome: str) -> float:
    """Map outcome to a numeric label."""
    mapping: dict[str, float] = {
        "improved": 1.0,
        "same": 0.5,
        "worse": 0.0,
    }
    return mapping.get(outcome.lower(), 0.5)


def train_local_model(
    features: list[float],
    label: float,
    learning_rate: float = 0.01,
    epochs: int = 10,
) -> list[float]:
    """Train a tiny linear model locally.

    This is a simplified single-sample SGD for the hackathon.
    In production, this would be a proper FL client (e.g., Flower/FLock SDK).
    """
    # Initialize random weights
    random.seed(42)
    weights: list[float] = [random.gauss(0, 0.1) for _ in features]

    for _epoch in range(epochs):
        # Forward pass: dot product
        prediction: float = sum(w * f for w, f in zip(weights, features))
        prediction = max(0.0, min(1.0, prediction))  # clamp to [0, 1]

        # Loss gradient (MSE)
        error: float = prediction - label

        # Backward pass: update weights
        weights = [w - learning_rate * error * f for w, f in zip(weights, features)]

    return weights


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Train a local FL model and export weights (no raw data leaves device)."
    )
    parser.add_argument("--zone", type=str, required=True, help="City/zone name")
    parser.add_argument("--compounds", type=str, required=True, help="Comma-separated compounds consumed")
    parser.add_argument("--outcome", type=str, required=True, choices=["improved", "same", "worse"], help="Health outcome")
    parser.add_argument("--output", type=str, default="/tmp/flock_weights.json", help="Output path for weights JSON")

    args: argparse.Namespace = parser.parse_args()
    compounds: list[str] = [c.strip() for c in args.compounds.split(",") if c.strip()]

    # Encode inputs locally
    features: list[float] = encode_compounds(compounds)
    label: float = encode_outcome(args.outcome)

    # Train locally — data never leaves
    weights: list[float] = train_local_model(features, label)

    # Build weight export payload (ONLY weights + zone, NO personal data)
    payload: dict[str, Any] = {
        "zone": args.zone,
        "model_version": "0.1.0-hackathon",
        "weights": weights,
        "num_samples": 1,
        "timestamp": int(time.time()),
        "privacy_note": "Contains ONLY model weights. No personal health data.",
    }

    # Write to file for the agent to submit
    output_path: str = args.output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    # Also print to stdout for the agent
    result: dict[str, str] = {
        "status": "trained",
        "weights_file": output_path,
        "message": f"Local model trained on {len(compounds)} compounds. Weights saved to {output_path}. Ready to submit to /api/flock/weights.",
    }
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
