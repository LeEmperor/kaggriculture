"""The deterministic no-op coverage policy."""

from __future__ import annotations

from typing import Any


def agent(_observation: dict[str, Any]) -> dict[str, list[Any]]:
    return {"farmer": ["PASS"], "hands": [], "market": []}
