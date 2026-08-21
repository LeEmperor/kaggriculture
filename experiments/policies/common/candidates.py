"""Loading and validation of the common policy-candidate envelope."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_candidate(path: Path) -> dict[str, Any]:
    """Load a candidate artifact and require a JSON object at its root."""
    candidate = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be a JSON object")
    return candidate


def candidate_parameters(
    candidate: dict[str, Any],
    *,
    expected_policy_id: str,
    expected_schema_version: int,
) -> dict[str, Any]:
    """Validate the shared candidate envelope and return its parameters."""
    if candidate.get("policy_id") != expected_policy_id:
        raise ValueError(f"candidate policy_id must be {expected_policy_id}")
    schema_version = candidate.get("schema_version")
    if type(schema_version) is not int or schema_version != expected_schema_version:
        raise ValueError(
            f"candidate schema_version must be {expected_schema_version}"
        )

    parameters = candidate.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("candidate parameters must be a JSON object")
    return dict(parameters)
