"""Record oracle fixtures for the model's rule-group-6 differential test.

Run from the repository root:

    python3 fast_model/test/record_model_group6_fixture.py

Group-6 scope: BUY_LAND (quadrant unlock order and prices), town shop
unlocks (the daily (seed * 1_000_003) ^ day generator's choice from the
sorted shop table, drawn with replacement, capped at 8 instances), per-shop
consumption (single-product shops pull double, duplicates consume
independently), and random weed spawning (one draw per empty tile per farm
per night, in row-major order — the draw stream feeds the shop choice, so
the consumed-even-at-zero-chance semantics matter). From this group the whole
per-turn state enters the comparison: farms, privates, market, and town.

- pass_rng: a PASS-only episode on a small board with a raised weed chance
  and default town intervals — pure end-of-day RNG and town-demand coverage,
  including the 8-instance shop cap and duplicate unlocks.
- land_and_shops: rich traders (from the group-5 generator family) plus
  BUY_LAND whenever affordable until all quadrants unlock, gardening into
  newly unlocked quadrants, weeds spawning and being dug, shops consuming
  against live trading.

marketParams overrides remain the only excluded configuration (group 7).
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from reference import oracle

OUT = Path(__file__).with_name("model_group6_fixture.json")

SCALAR_KEYS = [
    "episodeSteps",
    "turnsPerDay",
    "boardSize",
    "startingMoney",
    "shedCapacity",
    "maxMarketOrdersPerTurn",
    "farmHandCostMult",
    "weedSpawnChance",
    "townShopUnlockInterval",
    "townShopSellInterval",
    "townCenterSellInterval",
]

CROPS = ["WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON"]
DIRECTIONS = ["NORTH", "SOUTH", "EAST", "WEST"]

CASES = [
    {
        "name": "pass_rng",
        "seed": 20260821,
        "tape_seed": 41,
        "overrides": {
            "episodeSteps": 150,
            "turnsPerDay": 6,
            "boardSize": 8,
            "weedSpawnChance": 0.02,
        },
        "style": "pass",
    },
    {
        "name": "land_and_shops",
        "seed": 5150,
        "tape_seed": 42,
        "overrides": {
            "episodeSteps": 120,
            "turnsPerDay": 8,
            "startingMoney": 10000,
        },
        "style": "traders",
    },
]


def trader_unit_op(rng: random.Random, farm: dict, private: dict, unit_idx: int) -> list:
    pos = farm["farmer"] if unit_idx == 0 else None
    hands = farm["hands"]
    if unit_idx > 0 and unit_idx - 1 < len(hands):
        pos = hands[unit_idx - 1]
    seeds_avail = [c for c, v in private["seeds"].items() if v > 0]
    if pos is not None:
        x, y = pos
        tile = farm["tiles"][y][x]
        if isinstance(tile, dict) and tile.get("kind") == "PLANT":
            if not tile["watered_today"] and rng.random() < 0.85:
                return ["WATER"]
            if tile["yield_units"] > 0 and rng.random() < 0.6:
                return ["HARVEST"]
        if isinstance(tile, dict) and tile.get("kind") == "WEED" and rng.random() < 0.5:
            return ["DIG"]
        if tile is None and seeds_avail and rng.random() < 0.55:
            return ["PLANT", rng.choice(seeds_avail)]
    r = rng.random()
    if r < 0.5:
        return [rng.choice(DIRECTIONS)]
    if r < 0.6:
        return ["PASS"]
    if r < 0.75:
        return ["DROP"]
    return ["HARVEST"]


def trader_action(rng: random.Random, farm: dict, private: dict) -> dict:
    shed = private["shed"]
    orders: list = []
    if len(farm["unlocked_quadrants"]) < 4 and rng.random() < 0.3:
        orders.append(["BUY_LAND"])
    ripe = [k for k, v in shed.items() if v >= 2 and k in CROPS]
    if ripe and rng.random() < 0.6:
        item = rng.choice(ripe)
        orders.append(["SELL", item, shed[item]])
    if rng.random() < 0.5:
        orders.append(["BUY_SEED", rng.choice(CROPS), rng.choice([1, 2])])
    if rng.random() < 0.08:
        orders.append(["HIRE"])
    return {
        "farmer": trader_unit_op(rng, farm, private, 0),
        "hands": [
            trader_unit_op(rng, farm, private, i + 1) for i in range(len(farm["hands"]))
        ],
        "market": orders,
    }


def sparse_tiles(farm: dict) -> list:
    return [
        [x, y, tile]
        for y, row in enumerate(farm["tiles"])
        for x, tile in enumerate(row)
        if tile is not None and tile != "LOCKED"
    ]


def digest(diag: dict) -> dict:
    return {
        "farms": [
            {
                "money": farm["money"],
                "farmer": farm["farmer"],
                "hands": farm["hands"],
                "hires_today": farm["hires_today"],
                "unlocked_quadrants": farm["unlocked_quadrants"],
                "tiles": sparse_tiles(farm),
            }
            for farm in diag["farms"]
        ],
        "privates": [
            {
                "shed": {k: v for k, v in p["shed"].items() if v},
                "seeds": {k: v for k, v in p["seeds"].items() if v},
                "inventories": [dict(inv) for inv in p["inventories"]],
            }
            for p in diag["privates"]
        ],
        "market": diag["market"],
        "town": diag["town"],
    }


def record_case(case: dict) -> dict:
    interpreter = oracle.load_interpreter()
    env = oracle.OracleEnvironment({**case["overrides"], "seed": case["seed"]})
    state = [oracle._initial_agent_state(0), oracle._initial_agent_state(1)]
    env.state = state
    state = interpreter.interpreter(state, env)
    env.state = state
    state[0].observation.step = 0

    configuration = {key: env.configuration[key] for key in SCALAR_KEYS}
    rng = random.Random(case["tape_seed"])
    tape: list = []
    digests: list = []
    any_weed = False
    pass_action = {"farmer": ["PASS"], "hands": [], "market": []}

    turn = 0
    while not env.done:
        diag = oracle.diagnostic_state(state, env)
        if case["style"] == "pass":
            actions = [dict(pass_action), dict(pass_action)]
        else:
            actions = [
                trader_action(rng, diag["farms"][p], diag["privates"][p]) for p in (0, 1)
            ]
        for player, action in enumerate(actions):
            state[player].action = oracle.structify(action)
        state = interpreter.interpreter(state, env)
        env.state = state
        state[0].observation.step = 0 if env.done else turn + 1

        diag = oracle.diagnostic_state(state, env)
        tape.append(actions)
        digests.append(digest(diag))
        for farm in diag["farms"]:
            for _x, _y, tile in sparse_tiles(farm):
                if tile.get("kind") == "WEED":
                    any_weed = True
        turn += 1

    final_diagnostic = oracle.diagnostic_state(state, env)

    assert any_weed, f"{case['name']}: no random weed ever spawned"
    shops = final_diagnostic["town"]["unlocked_shops"]
    if case["name"] == "pass_rng":
        assert len(shops) == 8, f"pass_rng: shop cap not reached ({len(shops)})"
        assert len(set(shops)) < len(shops), "pass_rng: no duplicate shop unlock drawn"
    if case["name"] == "land_and_shops":
        assert len(shops) > 0, "land_and_shops: no shop unlocked"
        for player, farm in enumerate(final_diagnostic["farms"]):
            assert farm["unlocked_quadrants"] == ["NW", "NE", "SW", "SE"], (
                f"land_and_shops: player {player} never unlocked all quadrants"
            )

    return {
        "name": case["name"],
        "seed": case["seed"],
        "resolved_seed": env.info["seed"],
        "configuration": configuration,
        "tape": tape,
        "digests": digests,
        "final_diagnostic": final_diagnostic,
    }


if __name__ == "__main__":
    fixture = [record_case(case) for case in CASES]
    with OUT.open("w") as out:
        json.dump(fixture, out, sort_keys=True)
        out.write("\n")
    turns = sum(len(case["tape"]) for case in fixture)
    print(f"wrote {OUT}: {len(fixture)} cases, {turns} recorded turns")
