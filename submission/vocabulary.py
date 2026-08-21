"""The Kaggriculture observation vocabulary: the DSL's only game-aware seam.

``docs/policy_dsl.md`` is blunt about where the cost of a data-defined family
actually lives: not in the rules, which are free, but in the accessors. Adding
``opponent_money`` costs one line in every backend. Adding a guard that uses it
costs nothing. So this table is the file that gets ported, and everything under
``submission/dsl/`` is the part that does not.

It duplicates ``experiments/policies/common/observations.py`` and the
``SEED_COSTS`` table from ``common/game_data.py`` on purpose. The submission is
stdlib-only and self-contained, so it cannot import them; ``tests/test_dsl_
vocabulary.py`` asserts the two copies still agree rather than trusting them to.

Accessors take ``(observation, parameters)`` because several of them resolve
``param.crop`` at evaluation time, which is what keeps the family crop-generic
even while v1 pins the crop to WHEAT.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from submission.dsl.expr import BOOL, INT, Kind, Value
from submission.dsl.interpreter import Vocabulary

# Official fixed seed prices. Mirrors experiments/policies/common/game_data.py.
SEED_COSTS: Mapping[str, int] = {
    "WHEAT": 10,
    "CARROT": 20,
    "TOMATO": 50,
    "STRAWBERRY": 100,
    "MELON": 80,
}


class ObservationError(ValueError):
    """A game observation that does not match what the vocabulary expects."""


# --------------------------------------------------------------------------
# Shared readers
# --------------------------------------------------------------------------


def _own_farm(observation: Mapping[str, Any]) -> Mapping[str, Any]:
    return observation["farms"][int(observation["player"])]


def _item_count(container: Mapping[str, Any] | None, item: str) -> int:
    if container is None:
        return 0
    return max(0, int(container.get(item, 0)))


def _crop(parameters: Mapping[str, Value]) -> str:
    crop = parameters["crop"]
    assert isinstance(crop, str)
    return crop


def _current_tile(farm: Mapping[str, Any]) -> Any:
    x, y = farm["farmer"]
    return farm["tiles"][int(y)][int(x)]


def _plant_tile(observation: Mapping[str, Any]) -> Mapping[str, Any] | None:
    tile = _current_tile(_own_farm(observation))
    if isinstance(tile, Mapping) and tile.get("kind") == "PLANT":
        return tile
    return None


def _worker_inventory(
    private: Mapping[str, Any], worker_index: int = 0
) -> Mapping[str, Any]:
    inventories = private.get("inventories") or []
    if not 0 <= worker_index < len(inventories):
        return {}
    inventory = inventories[worker_index]
    return inventory if isinstance(inventory, Mapping) else {}


def _integral(value: Any, name: str) -> int:
    """Convert an integer-valued float to int, refusing to round.

    ``money`` is declared ``float`` in the observation schema but is integral by
    construction upstream: it starts at ``float(startingMoney)`` and every delta
    is an integer price, cost, or hire fee. Exposing it to the DSL as an ``int``
    is therefore exact, not an approximation, and it keeps floats out of a
    language that deliberately has no float arithmetic.

    If that ever stops being true, this raises rather than silently diverging
    from the hand-written policy's float comparison.
    """
    number = float(value)
    if number != int(number):
        raise ObservationError(
            f"{name} is {number!r}, which is not an integer; the DSL has no float "
            "arithmetic and money was expected to be integral"
        )
    return int(number)


# --------------------------------------------------------------------------
# Accessors
# --------------------------------------------------------------------------


def _step(observation: Mapping[str, Any], _: Mapping[str, Value]) -> int:
    return int(observation["step"])


def _day(observation: Mapping[str, Any], _: Mapping[str, Value]) -> int:
    return int(observation["day"])


def _hour(observation: Mapping[str, Any], _: Mapping[str, Value]) -> int:
    return int(observation["hour"])


def _money(observation: Mapping[str, Any], _: Mapping[str, Value]) -> int:
    return _integral(_own_farm(observation)["money"], "money")


def _seeds(observation: Mapping[str, Any], parameters: Mapping[str, Value]) -> int:
    return _item_count(observation["private"].get("seeds"), _crop(parameters))


def _shed_units(
    observation: Mapping[str, Any], parameters: Mapping[str, Value]
) -> int:
    return _item_count(observation["private"].get("shed"), _crop(parameters))


def _market_price(
    observation: Mapping[str, Any], parameters: Mapping[str, Value]
) -> int:
    return _item_count(observation["market"].get("prices"), _crop(parameters))


def _seed_cost(_: Mapping[str, Any], parameters: Mapping[str, Value]) -> int:
    crop = _crop(parameters)
    try:
        return SEED_COSTS[crop]
    except KeyError:
        raise ObservationError(f"unknown crop: {crop}") from None


def _carried_units(observation: Mapping[str, Any], _: Mapping[str, Value]) -> int:
    inventory = _worker_inventory(observation["private"])
    return sum(max(0, int(count)) for count in inventory.values())


def _on_shed_access(observation: Mapping[str, Any], _: Mapping[str, Value]) -> bool:
    farm = _own_farm(observation)
    tiles = farm["tiles"]
    height = len(tiles)
    width = len(tiles[0]) if height else 0
    if width < 2 or height < 2:
        return False
    x, y = farm["farmer"]
    middle_x = width // 2
    middle_y = height // 2
    return (int(x), int(y)) in {
        (middle_x - 1, middle_y - 1),
        (middle_x, middle_y - 1),
        (middle_x - 1, middle_y),
        (middle_x, middle_y),
    }


def _tile_is_empty(observation: Mapping[str, Any], _: Mapping[str, Value]) -> bool:
    return _current_tile(_own_farm(observation)) is None


def _tile_is_plant(observation: Mapping[str, Any], _: Mapping[str, Value]) -> bool:
    return _plant_tile(observation) is not None


def _tile_planted_day(observation: Mapping[str, Any], _: Mapping[str, Value]) -> int:
    tile = _plant_tile(observation)
    return -1 if tile is None else int(tile["planted_day"])


def _tile_yield_units(observation: Mapping[str, Any], _: Mapping[str, Value]) -> int:
    tile = _plant_tile(observation)
    return 0 if tile is None else int(tile.get("yield_units", 0))


def _tile_watered_today(
    observation: Mapping[str, Any], _: Mapping[str, Value]
) -> bool:
    tile = _plant_tile(observation)
    return False if tile is None else bool(tile.get("watered_today", False))


KINDS: Mapping[str, Kind] = {
    "step": INT,
    "day": INT,
    "hour": INT,
    "money": INT,
    "seeds": INT,
    "shed_units": INT,
    "market_price": INT,
    "seed_cost": INT,
    "carried_units": INT,
    "on_shed_access": BOOL,
    "tile_is_empty": BOOL,
    "tile_is_plant": BOOL,
    "tile_planted_day": INT,
    "tile_yield_units": INT,
    "tile_watered_today": BOOL,
}

ACCESSORS = {
    "step": _step,
    "day": _day,
    "hour": _hour,
    "money": _money,
    "seeds": _seeds,
    "shed_units": _shed_units,
    "market_price": _market_price,
    "seed_cost": _seed_cost,
    "carried_units": _carried_units,
    "on_shed_access": _on_shed_access,
    "tile_is_empty": _tile_is_empty,
    "tile_is_plant": _tile_is_plant,
    "tile_planted_day": _tile_planted_day,
    "tile_yield_units": _tile_yield_units,
    "tile_watered_today": _tile_watered_today,
}

VOCABULARY = Vocabulary(kinds=KINDS, accessors=ACCESSORS)
