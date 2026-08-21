"""Record oracle fixtures for the model's rule-group-2 differential test.

Run from the repository root:

    python3 fast_model/test/record_model_group2_fixture.py

Each case drives the pinned upstream interpreter with a deterministic,
seeded action tape exercising group-2 scope: movement (including board-edge
bumps), PASS, PICKUP / DROP / PLACE at and away from the shed, farm-hand
hiring (Fibonacci costs, spawn tie-breaking, daily reset), and the
constant-price market orders BUY_SEED / BUY_ANIMAL that give the sheds and
inventories something to hold. Recorded per turn: the action pair (the tape
the OCaml side replays) and a digest of everything group-2 rules can mutate —
farm money/positions/hands/hires plus private shed/seeds/inventories. Tiles
cannot change under this scope, so the full diagnostic state (minus the
market/town evolution, which belongs to later groups) is compared at episode
end only.

All cases pin weedSpawnChance to 0: weeds are end-of-day RNG (group 6) and
would otherwise mutate tiles under comparison. The recorder asserts the tape
actually exercised what it claims (hires happened, items flowed, the tight
case hit shed capacity) so a regressed generator cannot quietly record a
vacuous fixture.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from reference import oracle

OUT = Path(__file__).with_name("model_group2_fixture.json")

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
ANIMALS = ["GOOSE", "COW", "SHEEP"]
DIRECTIONS = ["NORTH", "SOUTH", "EAST", "WEST"]

CASES = [
    {
        "name": "mixed_default",
        "seed": 4242,
        "tape_seed": 1,
        "overrides": {"episodeSteps": 72, "turnsPerDay": 8, "weedSpawnChance": 0},
    },
    {
        "name": "tight_shed",
        "seed": 555,
        "tape_seed": 2,
        "overrides": {
            "episodeSteps": 48,
            "turnsPerDay": 6,
            "shedCapacity": 3,
            "startingMoney": 5000,
            "weedSpawnChance": 0,
        },
    },
    {
        "name": "board8_truncate",
        "seed": 777,
        "tape_seed": 3,
        "overrides": {
            "episodeSteps": 40,
            "turnsPerDay": 5,
            "boardSize": 8,
            "maxMarketOrdersPerTurn": 2,
            "farmHandCostMult": 2,
            "weedSpawnChance": 0,
        },
    },
]


def gen_unit_op(rng: random.Random, private: dict, unit_idx: int) -> list:
    r = rng.random()
    if r < 0.45:
        return [rng.choice(DIRECTIONS)]
    if r < 0.55:
        return ["PASS"]
    if r < 0.70:
        held = [k for k, v in private["shed"].items() if v > 0]
        items = held or list(private["shed"])
        return ["PICKUP", rng.choice(items), rng.choice([1, 2, 3])]
    if r < 0.80:
        return ["DROP"]
    inventories = private["inventories"]
    inv = inventories[unit_idx] if unit_idx < len(inventories) else {}
    items = list(inv) or ["WHEAT", "GOOSE"]
    return ["PLACE", rng.choice(items), rng.choice([1, 2])]


def gen_market(rng: random.Random) -> list:
    orders: list = []
    if rng.random() < 0.5:
        for _ in range(rng.choice([1, 1, 2, 3, 4])):
            r = rng.random()
            if r < 0.3:
                orders.append(["HIRE"])
            elif r < 0.7:
                orders.append(["BUY_SEED", rng.choice(CROPS), rng.choice([1, 2, 5])])
            else:
                orders.append(["BUY_ANIMAL", rng.choice(ANIMALS), rng.choice([1, 2])])
    return orders


def gen_action(rng: random.Random, farm: dict, private: dict) -> dict:
    hand_count = len(farm["hands"]) + (1 if rng.random() < 0.1 else 0)
    return {
        "farmer": gen_unit_op(rng, private, 0),
        "hands": [gen_unit_op(rng, private, i + 1) for i in range(hand_count)],
        "market": gen_market(rng),
    }


def sparse_tiles(farm: dict) -> list:
    """Row-major [x, y, tile] for every tile that is not empty/locked."""
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
    assert configuration["weedSpawnChance"] == 0, (
        "group-2 farm-state comparison requires weedSpawnChance=0 (weeds are group 6)"
    )

    rng = random.Random(case["tape_seed"])
    tape: list = []
    digests: list = []
    max_shed_total = 0
    total_hires = 0
    any_inventory = False
    any_truncated = False

    turn = 0
    while not env.done:
        diag = oracle.diagnostic_state(state, env)
        actions = [
            gen_action(rng, diag["farms"][player], diag["privates"][player])
            for player in (0, 1)
        ]
        max_orders = configuration["maxMarketOrdersPerTurn"]
        any_truncated |= any(len(a["market"]) > max_orders for a in actions)
        for player, action in enumerate(actions):
            state[player].action = oracle.structify(action)
        state = interpreter.interpreter(state, env)
        env.state = state
        state[0].observation.step = 0 if env.done else turn + 1

        diag = oracle.diagnostic_state(state, env)
        tape.append(actions)
        digests.append(digest(diag))
        for p in diag["privates"]:
            max_shed_total = max(max_shed_total, sum(p["shed"].values()))
            any_inventory |= any(inv for inv in p["inventories"])
        total_hires += sum(farm["hires_today"] for farm in diag["farms"])
        turn += 1

    final_diagnostic = {
        k: v
        for k, v in oracle.diagnostic_state(state, env).items()
        if k not in ("market", "town")
    }

    # Coverage: a tape that stopped exercising the rules must fail loudly.
    assert total_hires > 0, f"{case['name']}: tape never hired a hand"
    assert any_inventory, f"{case['name']}: tape never carried an item"
    assert max_shed_total > 0, f"{case['name']}: tape never stocked a shed"
    if case["name"] == "tight_shed":
        assert max_shed_total == configuration["shedCapacity"], (
            "tight_shed never hit shed capacity"
        )
    if case["name"] == "board8_truncate":
        assert any_truncated, "board8_truncate never exceeded maxMarketOrdersPerTurn"

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
