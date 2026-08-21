"""Record oracle fixtures for the model's rule-group-7 differential test.

Run from the repository root:

    python3 fast_model/test/record_model_group7_fixture.py

Group-7 scope: marketParams configuration overrides and transition-ordering
edges. The fixture's `configuration.marketParams` carries the fully-resolved
per-item table (the upstream _resolve_market_params output, read back from
the oracle's market["params"]); the `params` echo is stripped from recorded
market state because it is configuration, not state.

- override_curves: overrides chosen to reach what default curves cannot:
  WHEAT's above-curve is made steep enough that selling a couple of units
  hits the price floor of 1 (covering the floor and the sales-at-$1-do-not-
  increase-supply branch), CARROT exercises log10 (unused by any default),
  EGG puts hinge on the above side, MELON shifts I0, TOMATO re-bases. Two
  wheat-farming traders sell into the floor all episode.
- daily_boundary: turnsPerDay=1 — every turn ends a day, so plants weed the
  moment they are planted, hands reset before ever acting, and the respawn /
  nightly-drop / RNG cadence runs at maximum frequency against live trading.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from reference import oracle

OUT = Path(__file__).with_name("model_group7_fixture.json")

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
        "name": "override_curves",
        "seed": 987654,
        "tape_seed": 51,
        "overrides": {
            "episodeSteps": 96,
            "turnsPerDay": 8,
            "startingMoney": 5000,
            # This case isolates the overridden curves; town demand (group-6
            # scope) would dig a below-I0 deficit faster than two gardeners
            # can fill it, keeping the above-I0 floor regime out of reach.
            "townShopUnlockInterval": 9999,
            "townCenterSellInterval": 9999,
            "marketParams": {
                "WHEAT": {"above_func": "linear", "above_target": 24.0, "T": 10},
                "CARROT": {"below_func": "log10", "below_target": 2.5},
                "EGG": {"above_func": "hinge", "above_target": 5.0},
                "MELON": {"I0": 9990},
                "TOMATO": {"base": 90},
            },
        },
        "style": "wheat_floor",
    },
    {
        "name": "daily_boundary",
        "seed": 24601,
        "tape_seed": 52,
        "overrides": {"episodeSteps": 30, "turnsPerDay": 1},
        "style": "traders",
    },
]


def unit_op(rng: random.Random, farm: dict, private: dict, unit_idx: int, wheat_only: bool) -> list:
    pos = farm["farmer"] if unit_idx == 0 else None
    hands = farm["hands"]
    if unit_idx > 0 and unit_idx - 1 < len(hands):
        pos = hands[unit_idx - 1]
    seeds_avail = [c for c, v in private["seeds"].items() if v > 0]
    if pos is not None:
        x, y = pos
        tile = farm["tiles"][y][x]
        if isinstance(tile, dict) and tile.get("kind") == "PLANT":
            if not tile["watered_today"] and rng.random() < 0.9:
                return ["WATER"]
            if tile["yield_units"] > 0 and rng.random() < 0.6:
                return ["HARVEST"]
        if isinstance(tile, dict) and tile.get("kind") == "WEED" and rng.random() < 0.5:
            return ["DIG"]
        if tile is None and seeds_avail and rng.random() < 0.6:
            return ["PLANT", rng.choice(seeds_avail)]
    r = rng.random()
    if r < 0.55:
        return [rng.choice(DIRECTIONS)]
    if r < 0.65:
        return ["PASS"]
    if r < 0.85:
        return ["DROP"]
    return ["HARVEST"]


def gen_action(rng: random.Random, farm: dict, private: dict, style: str) -> dict:
    shed = private["shed"]
    orders: list = []
    if style == "wheat_floor":
        # Accumulate, then dump: a single order large enough to cross I0
        # guarantees units committing at the floor price.
        if shed.get("WHEAT", 0) >= 4:
            orders.append(["SELL", "WHEAT", shed["WHEAT"]])
        if rng.random() < 0.7:
            orders.append(["BUY_SEED", "WHEAT", rng.choice([1, 2])])
    else:
        ripe = [k for k, v in shed.items() if v > 0 and k in CROPS]
        if ripe and rng.random() < 0.7:
            item = rng.choice(ripe)
            orders.append(["SELL", item, shed[item]])
        if rng.random() < 0.5:
            orders.append(["BUY_SEED", rng.choice(CROPS), 1])
        if rng.random() < 0.1:
            orders.append(["HIRE"])
    wheat_only = style == "wheat_floor"
    return {
        "farmer": unit_op(rng, farm, private, 0, wheat_only),
        "hands": [
            unit_op(rng, farm, private, i + 1, wheat_only)
            for i in range(len(farm["hands"]))
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


def market_state(diag: dict) -> dict:
    """Market inventory and prices; params is configuration, not state."""
    return {k: v for k, v in diag["market"].items() if k != "params"}


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
        "market": market_state(diag),
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
    diag = oracle.diagnostic_state(state, env)
    if "params" in diag["market"]:
        resolved = diag["market"]["params"]
        known = {"base", "I0", "T", "below_func", "below_target", "above_func", "above_target"}
        for item, entry in resolved.items():
            assert set(entry) == known, f"unexpected marketParams keys for {item}"
        configuration["marketParams"] = resolved

    rng = random.Random(case["tape_seed"])
    tape: list = []
    digests: list = []
    floor_hit = False
    floor_sale = False

    turn = 0
    while not env.done:
        diag = oracle.diagnostic_state(state, env)
        actions = [
            gen_action(rng, diag["farms"][p], diag["privates"][p], case["style"])
            for p in (0, 1)
        ]
        # A single SELL whose stock exceeds the below-I0 deficit by 2+ units
        # must push inventory past I0 and commit its tail at the floor.
        deficit = max(0, 10000 - diag["market"]["inventory"]["WHEAT"])
        selling_at_floor = any(
            o[0] == "SELL"
            and o[1] == "WHEAT"
            and diag["privates"][p]["shed"].get("WHEAT", 0) >= deficit + 2
            for p, a in enumerate(actions)
            for o in a["market"]
        )
        for player, action in enumerate(actions):
            state[player].action = oracle.structify(action)
        state = interpreter.interpreter(state, env)
        env.state = state
        state[0].observation.step = 0 if env.done else turn + 1

        diag = oracle.diagnostic_state(state, env)
        tape.append(actions)
        digests.append(digest(diag))
        if diag["market"]["prices"]["WHEAT"] == 1:
            floor_hit = True
        if selling_at_floor:
            floor_sale = True
        turn += 1

    final = oracle.diagnostic_state(state, env)
    final_diagnostic = {**final, "market": market_state(final)}

    if case["name"] == "override_curves":
        assert floor_hit, "override_curves: the wheat price never hit the floor"
        assert floor_sale, "override_curves: no sale ever committed at the floor"

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
