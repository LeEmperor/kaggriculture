"""The Kaggriculture action seam: what a rule may emit, and how it is shaped.

``EMITS`` is the static half, consumed by ``family.load`` so that a misspelled
``["SEL", ...]`` or a ``["PLANT"]`` missing its crop fails at load rather than
mid-episode. ``build_action`` is the runtime half, turning the decide stage's
firings into the list-encoded action the environment expects.

Like ``vocabulary.py`` this file names crops and actions, and like that file it
is deliberately separable from ``submission/dsl/`` by deletion.
"""

from __future__ import annotations

from typing import Any, TypedDict

from submission.dsl.expr import INT, STR, Kind
from submission.dsl.family import FARMER, MARKET
from submission.dsl.interpreter import Turn

WorkerAction = list[Any]
MarketOrder = list[Any]


class PolicyAction(TypedDict):
    """The complete action returned by a policy for one turn."""

    farmer: WorkerAction
    hands: list[WorkerAction]
    market: list[MarketOrder]


WORKER_EMITS: dict[str, tuple[Kind, ...]] = {
    "PASS": (),
    "DROP": (),
    "HARVEST": (),
    "WATER": (),
    "PLANT": (STR,),
}

MARKET_EMITS: dict[str, tuple[Kind, ...]] = {
    "BUY_SEED": (STR, INT),
    "SELL": (STR, INT),
}

EMITS: dict[str, dict[str, tuple[Kind, ...]]] = {
    FARMER: WORKER_EMITS,
    MARKET: MARKET_EMITS,
}

# Market orders carry a unit count as their last operand; worker actions do not.
_UNIT_OPERAND = -1


def build_action(turn: Turn) -> PolicyAction:
    """Shape one turn's firings into the environment's action encoding.

    A farmer cascade with no matching rule passes, which is the same default the
    hand-written policy falls through to. A well-formed family ends its cascade
    with a catch-all, so this is a safety net rather than a normal path.
    """
    farmer: WorkerAction = (
        ["PASS"]
        if turn.farmer is None
        else [turn.farmer.op, *turn.farmer.operands]
    )
    market: list[MarketOrder] = []
    for firing in turn.market:
        units = firing.operands[_UNIT_OPERAND]
        if not isinstance(units, int) or isinstance(units, bool) or units < 1:
            raise ValueError(
                f"market rule '{firing.rule}' emitted {units!r} units; "
                "action units must be a positive integer"
            )
        market.append([firing.op, *firing.operands])
    return {"farmer": farmer, "hands": [], "market": market}
