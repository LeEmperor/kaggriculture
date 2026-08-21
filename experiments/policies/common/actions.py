"""Typed constructors for Kaggriculture's list-encoded actions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypedDict

WorkerAction = list[Any]
MarketOrder = list[Any]


class PolicyAction(TypedDict):
    """The complete action returned by a policy for one turn."""

    farmer: WorkerAction
    hands: list[WorkerAction]
    market: list[MarketOrder]


def _positive_units(units: int) -> int:
    value = int(units)
    if value < 1:
        raise ValueError("action units must be positive")
    return value


def pass_worker() -> WorkerAction:
    return ["PASS"]


def drop() -> WorkerAction:
    return ["DROP"]


def harvest() -> WorkerAction:
    return ["HARVEST"]


def water() -> WorkerAction:
    return ["WATER"]


def plant(crop: str) -> WorkerAction:
    return ["PLANT", crop]


def buy_seed(crop: str, units: int) -> MarketOrder:
    return ["BUY_SEED", crop, _positive_units(units)]


def sell(item: str, units: int) -> MarketOrder:
    return ["SELL", item, _positive_units(units)]


def complete_action(
    *,
    farmer: WorkerAction | None = None,
    hands: Iterable[WorkerAction] = (),
    market: Iterable[MarketOrder] = (),
) -> PolicyAction:
    """Build a fresh complete action without sharing mutable action lists."""
    return {
        "farmer": list(farmer) if farmer is not None else pass_worker(),
        "hands": [list(action) for action in hands],
        "market": [list(order) for order in market],
    }
