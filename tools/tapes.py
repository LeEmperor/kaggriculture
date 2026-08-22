"""Deterministic action-tape generators for the Phase 4 differential runner.

A tape is raw JSON — exactly the shape a Kaggle agent returns — so the same values drive
the pinned oracle and the OCaml engine, the latter through
``Kag_serialize.player_action_of_json_tolerant``. Generators therefore never build a
"typed" action: reproducing upstream's collapse of malformed input is the parser's job,
and exercising that collapse is the point of the fuzz styles.

Generators read the full diagnostic state, including the opponent's. They are test
drivers, not policies, and nothing here is subject to the observation boundary that
``experiments/`` and ``submission/`` obey.

The excluded domain
-------------------

Upstream silently no-ops almost everything, but not quite everything. Two expressions
raise out of the interpreter, which the framework turns into an agent ERROR that the
oracle adapter does not reproduce (``docs/reference_semantics.md``):

* ``int(action[2])`` in PICKUP / PLACE is not wrapped in try/except — only
  ``_parse_order`` is — so a non-numeric count there raises ``ValueError``;
* ``op in FARMER_MOVES``, ``crop not in CROPS`` and ``shed.get(item)`` hash their
  argument, so a list or dict in ``action[0]``, or in ``action[1]`` of PLANT / PICKUP /
  PLACE, raises ``TypeError``.

Nothing here emits either, and the OCaml parser raises ``Undefined_mapping`` rather than
guessing if one ever reaches it. Market-order counts are exempt: ``_parse_order`` catches
both exceptions, so any JSON value is fair game there and the fuzz styles use that.
"""

from __future__ import annotations

import random
from typing import Any

from reference import oracle

_INTERP = oracle.load_interpreter()

CROPS: list[str] = list(_INTERP.CROPS)
ANIMALS: list[str] = list(_INTERP.ANIMALS)
PRODUCTS: list[str] = list(_INTERP.PRODUCTS)
SHED_ITEMS: list[str] = PRODUCTS + ANIMALS
DIRECTIONS = ["NORTH", "SOUTH", "EAST", "WEST"]

# Names outside every upstream table: they resolve to nothing on either backend and must
# collapse to the same no-op.
UNKNOWN_ITEMS = ["BANANA", "wheat", "", "COW ", "PLANT", "0"]

SCRIPTED_STYLES = ["gardener", "rancher", "trader", "hoarder", "settler", "drifter"]
FUZZ_STYLES = ["fuzz_typed", "fuzz_malformed", "fuzz_market"]
STYLES = SCRIPTED_STYLES + FUZZ_STYLES


# --------------------------------------------------------------------------- geometry


def _units(farm: dict) -> list[tuple[int, int]]:
    """Farmer first, then hands — the order the interpreter applies them in."""
    return [tuple(farm["farmer"]), *(tuple(pos) for pos in farm["hands"])]


def _tile_at(farm: dict, pos: tuple[int, int]) -> Any:
    x, y = pos
    return farm["tiles"][y][x]


def _step_toward(pos: tuple[int, int], target: tuple[int, int]) -> str | None:
    x, y = pos
    tx, ty = target
    if x < tx:
        return "EAST"
    if x > tx:
        return "WEST"
    if y < ty:
        return "SOUTH"
    if y > ty:
        return "NORTH"
    return None


def _shed_tiles(board_size: int) -> list[tuple[int, int]]:
    return [tuple(tile) for tile in _INTERP._shed_access_tiles(board_size)]


def _at_shed(pos: tuple[int, int], board_size: int) -> bool:
    return bool(_INTERP._is_shed_adjacent(pos, board_size))


def _owned_targets(farm: dict, board_size: int) -> list[tuple[int, int]]:
    return [
        (x, y)
        for y, row in enumerate(farm["tiles"])
        for x, tile in enumerate(row)
        if tile != "LOCKED"
    ]


# --------------------------------------------------------------------------- scripted


def _farm_unit_op(
    rng: random.Random,
    farm: dict,
    private: dict,
    pos: tuple[int, int],
    board_size: int,
    *,
    style: str,
) -> list:
    """One unit's turn under a scripted style: act on the tile underfoot when it offers
    something, otherwise head somewhere that will."""
    tile = _tile_at(farm, pos)
    inv_total = sum(private["inventories"][0].values()) if private["inventories"] else 0
    seeds = [crop for crop, count in private["seeds"].items() if count > 0]
    holding = {item: n for item, n in _held(private, farm, pos).items() if n > 0}

    if isinstance(tile, dict):
        kind = tile.get("kind")
        if "animal" in tile:
            if not tile["fed_today"] and holding.get("WHEAT", 0) > 0:
                return ["FEED"]
            if not tile["cared_today"] and rng.random() < 0.8:
                return ["CARE"]
            if tile["fertilizer_available"]:
                return ["COLLECT_FERTILIZER"]
            if tile["yield_units"] > 0:
                return ["HARVEST"]
        elif kind == "PLANT":
            if tile["yield_units"] > 0 and rng.random() < 0.7:
                return ["HARVEST"]
            if not tile["watered_today"]:
                return ["WATER"]
            if holding.get("FERTILIZER", 0) > 0:
                return ["FERTILIZE"]
        elif kind == "WEED":
            return ["DIG"]
        elif kind in ("COOP", "PASTURE"):
            wanted = [
                animal
                for animal in ANIMALS
                if _INTERP.ANIMALS[animal]["structure"] == kind
                and holding.get(animal, 0) > 0
            ]
            if wanted:
                return ["PLACE", rng.choice(wanted)]
    elif tile is None:
        if style == "rancher" and rng.random() < 0.4:
            return ["BUILD_COOP" if rng.random() < 0.5 else "BUILD_PASTURE"]
        if seeds and rng.random() < 0.75:
            return ["PLANT", rng.choice(seeds)]

    if holding.get("FERTILIZER", 0) > 0:
        # Fertilizer is only ever consumed while standing on a growing plant, so a unit
        # carrying some goes looking for one rather than wandering.
        plants = [
            (x, y)
            for y, row in enumerate(farm["tiles"])
            for x, tile in enumerate(row)
            if isinstance(tile, dict) and tile.get("kind") == "PLANT"
        ]
        if plants:
            return [_step_toward(pos, rng.choice(plants)) or "FERTILIZE"]

    at_shed = _at_shed(pos, board_size)
    if at_shed:
        if inv_total > 0 and rng.random() < 0.7:
            return ["DROP"]
        wanted = [item for item, n in private["shed"].items() if n > 0]
        if private["shed"].get("FERTILIZER", 0) > 0 and rng.random() < 0.7:
            wanted = ["FERTILIZER"]
        if wanted and rng.random() < 0.6:
            item = rng.choice(wanted)
            return ["PICKUP", item, rng.randint(1, min(4, private["shed"][item]))]
    elif holding and rng.random() < (0.5 if style == "hoarder" else 0.25):
        return [_step_toward(pos, rng.choice(_shed_tiles(board_size))) or "PASS"]

    if rng.random() < 0.75:
        target = rng.choice(_owned_targets(farm, board_size) or [pos])
        return [_step_toward(pos, target) or rng.choice(DIRECTIONS)]
    return [rng.choice(DIRECTIONS + ["PASS"])]


def _held(private: dict, farm: dict, pos: tuple[int, int]) -> dict:
    """The inventory of whichever unit stands at ``pos`` (ties resolve to the first)."""
    for index, unit in enumerate(_units(farm)):
        if unit == pos and index < len(private["inventories"]):
            return private["inventories"][index]
    return {}


def _scripted_orders(
    rng: random.Random, farm: dict, private: dict, market: dict, *, style: str
) -> list:
    shed = {item: n for item, n in private["shed"].items() if n > 0}
    money = farm["money"]
    orders: list = []

    sellable = [item for item in shed if item in PRODUCTS]
    if sellable and rng.random() < (0.9 if style == "trader" else 0.6):
        item = rng.choice(sellable)
        orders.append(["SELL", item, rng.randint(1, shed[item])])

    if style in ("gardener", "hoarder", "drifter") and rng.random() < 0.6:
        crop = rng.choice(CROPS)
        if money >= _INTERP.CROPS[crop]["seed"]:
            orders.append(["BUY_SEED", crop, rng.randint(1, 3)])

    if style == "rancher" and rng.random() < 0.35:
        animal = rng.choice(ANIMALS)
        if money >= _INTERP.ANIMALS[animal]["cost"]:
            orders.append(["BUY_ANIMAL", animal, 1])

    if (
        style in ("gardener", "hoarder", "rancher")
        and rng.random() < 0.3
        and money >= market["prices"]["FERTILIZER"]
    ):
        orders.append(["BUY_PRODUCT", "FERTILIZER", rng.randint(1, 3)])

    if style == "trader" and rng.random() < 0.5:
        item = rng.choice(["WHEAT", "FERTILIZER"])
        if money >= market["prices"][item]:
            orders.append(["BUY_PRODUCT", item, rng.randint(1, 4)])

    if style == "hoarder" and rng.random() < 0.5:
        # Deliberately overshoot shedCapacity so the deposit paths clamp.
        orders.append(["BUY_PRODUCT", "WHEAT", rng.randint(20, 60)])

    if rng.random() < (0.25 if style == "settler" else 0.06):
        orders.append(["HIRE"])
    if rng.random() < (0.3 if style == "settler" else 0.04):
        orders.append(["BUY_LAND"])
    return orders


def scripted_action(
    rng: random.Random, farm: dict, private: dict, market: dict, config: dict, style: str
) -> dict:
    board_size = int(config["boardSize"])
    positions = _units(farm)
    return {
        "farmer": _farm_unit_op(
            rng, farm, private, positions[0], board_size, style=style
        ),
        "hands": [
            _farm_unit_op(rng, farm, private, pos, board_size, style=style)
            for pos in positions[1:]
        ],
        "market": _scripted_orders(rng, farm, private, market, style=style),
    }


# ------------------------------------------------------------------------------- fuzz

UNIT_OPS = [
    "PASS",
    "NORTH",
    "SOUTH",
    "EAST",
    "WEST",
    "DROP",
    "PICKUP",
    "PLACE",
    "PLANT",
    "WATER",
    "HARVEST",
    "FERTILIZE",
    "DIG",
    "BUILD_COOP",
    "BUILD_PASTURE",
    "FEED",
    "CARE",
    "COLLECT_FERTILIZER",
]

# int() accepts every one of these, so PICKUP / PLACE may carry them as a count.
SAFE_COUNTS: list[Any] = [0, 1, 2, 5, 99, -1, -7, True, False, 2.9, -0.5, "3", " 12 "]

# _parse_order catches int()'s exceptions, so a market order may carry anything.
WILD_COUNTS: list[Any] = SAFE_COUNTS + ["abc", "", None, [1], {"n": 2}, "0x10", "1_000"]

# Hashable non-string values in action[0] / action[1] fall through every branch.
HASHABLE_JUNK: list[Any] = [None, 0, 7, True, False, 1.5, "", "plant", "MOVE"]


def _fuzz_unit_op(rng: random.Random, *, malformed_rate: float) -> Any:
    if rng.random() >= malformed_rate:
        op = rng.choice(UNIT_OPS)
        if op == "PLANT":
            return ["PLANT", rng.choice(CROPS)]
        if op in ("PICKUP", "PLACE"):
            return [op, rng.choice(SHED_ITEMS), rng.randint(1, 6)]
        return [op]

    pick = rng.randrange(11)
    if pick == 0:
        return rng.choice([None, 0, "PASS", 3.5, True, {}, {"farmer": ["PASS"]}, []])
    if pick == 1:
        return [rng.choice(HASHABLE_JUNK)]
    if pick == 2:
        return [rng.choice(["FLY", "pass", "MOVE", "NORTH ", "HARVEST_ALL"])]
    if pick == 3:
        return [rng.choice(["PLANT", "PICKUP", "PLACE"])]  # arity below the minimum
    if pick == 4:
        return ["PLANT", rng.choice(UNKNOWN_ITEMS + ANIMALS + ["EGG"])]
    if pick == 5:
        return ["PLANT", rng.choice(HASHABLE_JUNK)]
    if pick == 6:
        return [
            rng.choice(["PICKUP", "PLACE"]),
            rng.choice(UNKNOWN_ITEMS),
            rng.choice(SAFE_COUNTS),
        ]
    if pick == 7:
        return [
            rng.choice(["PICKUP", "PLACE"]),
            rng.choice(SHED_ITEMS),
            rng.choice(SAFE_COUNTS),
        ]
    if pick == 8:
        # PLACE resolves an animal onto a matching structure before it ever reads the
        # count, so a zero or negative count still places the animal.
        return ["PLACE", rng.choice(ANIMALS), rng.choice([0, -3, False])]
    if pick == 9:
        return [rng.choice(["PICKUP", "PLACE"]), rng.choice(HASHABLE_JUNK), 1]
    # Trailing junk every op ignores. Safe even as a dict: only PLANT / PICKUP / PLACE
    # ever hash action[1].
    return [rng.choice(["WATER", "HARVEST", "DIG", "DROP", "CARE"]), {"junk": [1]}, [2]]


def _fuzz_order(rng: random.Random, *, malformed_rate: float) -> Any:
    if rng.random() >= malformed_rate:
        op = rng.choice(["SELL", "BUY_SEED", "BUY_ANIMAL", "BUY_PRODUCT", "HIRE", "BUY_LAND"])
        if op == "HIRE":
            return ["HIRE"]
        if op == "BUY_LAND":
            return ["BUY_LAND"]
        if op == "BUY_SEED":
            return ["BUY_SEED", rng.choice(CROPS), rng.randint(1, 4)]
        if op == "BUY_ANIMAL":
            return ["BUY_ANIMAL", rng.choice(ANIMALS), rng.randint(1, 2)]
        if op == "BUY_PRODUCT":
            return ["BUY_PRODUCT", rng.choice(["WHEAT", "FERTILIZER"]), rng.randint(1, 8)]
        return ["SELL", rng.choice(PRODUCTS), rng.randint(1, 8)]

    pick = rng.randrange(8)
    if pick == 0:
        return rng.choice([None, 5, "SELL", [], {}, {"op": "HIRE"}, [1, 2, 3], True])
    if pick == 1:
        return [rng.choice(["FROB", "sell", "HIRE ", "BUY", ""]), "WHEAT", 2]
    if pick == 2:
        return [rng.choice(["SELL", "BUY_SEED", "BUY_PRODUCT", "BUY_ANIMAL"])]
    if pick == 3:
        return [rng.choice(["SELL", "BUY_SEED", "BUY_PRODUCT", "BUY_ANIMAL"]), "WHEAT"]
    if pick == 4:
        # An item outside the op's own domain reaches the lockstep's abort branch.
        return [
            rng.choice(["SELL", "BUY_SEED", "BUY_PRODUCT", "BUY_ANIMAL"]),
            rng.choice(UNKNOWN_ITEMS + SHED_ITEMS + [None, 3, True]),
            rng.randint(1, 5),
        ]
    if pick == 5:
        return [
            rng.choice(["SELL", "BUY_SEED", "BUY_PRODUCT", "BUY_ANIMAL"]),
            rng.choice(PRODUCTS + ANIMALS),
            rng.choice(WILD_COUNTS),
        ]
    if pick == 6:
        return [rng.choice(["HIRE", "BUY_LAND"]), "EXTRA", {"ignored": True}]
    return rng.choice(
        [
            ["SELL", rng.choice(PRODUCTS), rng.choice([10**4, 10**6])],
            # Against a large enough bankroll this single order commits past upstream's
            # 99,999-iteration runaway guard.
            ["BUY_SEED", "WHEAT", 10**6],
        ]
    )


def fuzz_action(
    rng: random.Random, farm: dict, private: dict, market: dict, config: dict, style: str
) -> Any:
    malformed_rate = {"fuzz_typed": 0.0, "fuzz_malformed": 0.55, "fuzz_market": 0.35}[style]
    max_orders = int(config["maxMarketOrdersPerTurn"])

    if style == "fuzz_malformed" and rng.random() < 0.04:
        # The whole action, not just one entry, is malformed.
        return rng.choice([None, 5, "PASS", ["farmer"], {"farmer": "NORTH"}])

    hand_count = len(farm["hands"])
    # Entries past the live hand count are applied to a unit that does not exist. They
    # no-op, but the atomic-PLANT pre-pass still counts their PLANT demand.
    extra_hands = rng.randrange(3) if style != "fuzz_typed" else 0
    hands: Any = [
        _fuzz_unit_op(rng, malformed_rate=malformed_rate)
        for _ in range(hand_count + extra_hands)
    ]
    if style == "fuzz_malformed" and rng.random() < 0.05:
        hands = rng.choice(["hands", 3, None, {"0": ["PASS"]}])

    orders: Any = [
        _fuzz_order(rng, malformed_rate=malformed_rate)
        for _ in range(rng.randrange(max_orders + 3))
    ]
    if style == "fuzz_market" and rng.random() < 0.05:
        orders = rng.choice(["market", 7, None, {"0": ["HIRE"]}])

    action: dict[str, Any] = {
        "farmer": _fuzz_unit_op(rng, malformed_rate=malformed_rate),
        "hands": hands,
        "market": orders,
    }
    if style == "fuzz_malformed" and rng.random() < 0.05:
        del action["farmer"]  # action.get("farmer", ["PASS"])
    if rng.random() < 0.02:
        action["nonsense"] = {"ignored": [1, 2]}
    return action


def action_for(
    rng: random.Random, farm: dict, private: dict, market: dict, config: dict, style: str
) -> Any:
    if style in FUZZ_STYLES:
        return fuzz_action(rng, farm, private, market, config, style)
    return scripted_action(rng, farm, private, market, config, style)
