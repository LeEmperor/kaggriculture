"""Immutable, non-configurable values from the Kaggriculture game rules."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

SEED_COSTS: Final = MappingProxyType(
    {
        "WHEAT": 10,
        "CARROT": 20,
        "TOMATO": 50,
        "STRAWBERRY": 100,
        "MELON": 80,
    }
)


def seed_cost(crop: str) -> int:
    """Return the official fixed purchase price of one crop seed."""
    try:
        return SEED_COSTS[crop]
    except KeyError as error:
        raise ValueError(f"unknown crop: {crop}") from error
