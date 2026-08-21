"""Record oracle fixtures for the model's rule-group-3 differential test.

Run from the repository root:

    python3 fast_model/test/record_model_group3_fixture.py

Group-3 scope: PLANT / WATER / HARVEST / DIG, the atomic per-crop PLANT
validation, watering windows and fertilizer state, ongoing-crop production,
neglect (two unwatered days -> weed), and overripe decay. Three cases:

- decay_stakeout: player 0 is scripted — buy one wheat seed, plant it on the
  spawn tile (the daily respawn conveniently returns the farmer to it), water
  every turn, never harvest. The plant matures, then decays unit by unit into
  a weed — deterministic coverage of the decay clock. Player 1 gardens
  randomly.
- gardeners: two state-aware random gardeners over 12 longer days — plant,
  water (mostly), harvest, dig, hire hands, restock seeds. Ongoing crops
  (tomato/strawberry) reach production.
- crops_congest: boardSize 8, sloppier watering, and deliberate bursts where
  every unit PLANTs the same crop at once to trip the atomic seed validation.

FERTILIZE is implemented in the model but cannot be exercised yet: fertilizer
only enters play via BUY_PRODUCT (market-curve pricing, group 5) or
COLLECT_FERTILIZER (animals, group 4). Its differential coverage lands with
those groups. All cases pin weedSpawnChance to 0 (random weeds are group 6);
weeds still appear here through neglect and decay, which are group-3 rules.

The digest/tape format matches the group-2 fixture exactly, and the recorder
asserts the tapes exercised harvests, neglect weeds, decay, and a blocked
PLANT burst.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from reference import oracle

OUT = Path(__file__).with_name("model_group3_fixture.json")

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
        "name": "decay_stakeout",
        "seed": 31337,
        "tape_seed": 11,
        "overrides": {"episodeSteps": 48, "turnsPerDay": 6, "weedSpawnChance": 0},
        "style": "stakeout",
        "water_prob": 0.85,
        "harvest_prob": 0.5,
    },
    {
        "name": "gardeners",
        "seed": 90210,
        "tape_seed": 12,
        "overrides": {"episodeSteps": 96, "turnsPerDay": 8, "weedSpawnChance": 0},
        "style": "garden",
        "water_prob": 0.85,
        "harvest_prob": 0.6,
    },
    {
        "name": "crops_congest",
        "seed": 60606,
        "tape_seed": 13,
        "overrides": {
            "episodeSteps": 60,
            "turnsPerDay": 6,
            "boardSize": 8,
            "weedSpawnChance": 0,
        },
        "style": "congest",
        "water_prob": 0.6,
        "harvest_prob": 0.4,
    },
]


def unit_position(farm: dict, unit_idx: int) -> list | None:
    if unit_idx == 0:
        return farm["farmer"]
    hands = farm["hands"]
    return hands[unit_idx - 1] if unit_idx - 1 < len(hands) else None


def gen_garden_op(
    rng: random.Random, farm: dict, private: dict, unit_idx: int, case: dict
) -> list:
    pos = unit_position(farm, unit_idx)
    seeds_avail = [c for c, v in private["seeds"].items() if v > 0]
    if pos is not None:
        x, y = pos
        tile = farm["tiles"][y][x]
        if isinstance(tile, dict) and tile.get("kind") == "PLANT":
            if not tile["watered_today"] and rng.random() < case["water_prob"]:
                return ["WATER"]
            if tile["yield_units"] > 0 and rng.random() < case["harvest_prob"]:
                return ["HARVEST"]
        if isinstance(tile, dict) and tile.get("kind") == "WEED" and rng.random() < 0.4:
            return ["DIG"]
        if tile is None and seeds_avail and rng.random() < 0.5:
            return ["PLANT", rng.choice(seeds_avail)]
    r = rng.random()
    if r < 0.5:
        return [rng.choice(DIRECTIONS)]
    if r < 0.6:
        return ["PASS"]
    if r < 0.7:
        return ["WATER"]  # frequently a no-op on purpose
    if r < 0.8:
        return ["HARVEST"]  # frequently premature on purpose
    if r < 0.9:
        return ["DIG"]
    return ["PLANT", rng.choice(seeds_avail or CROPS)]


def gen_market(rng: random.Random) -> list:
    orders: list = []
    if rng.random() < 0.6:
        for _ in range(rng.choice([1, 1, 2])):
            r = rng.random()
            if r < 0.2:
                orders.append(["HIRE"])
            else:
                orders.append(["BUY_SEED", rng.choice(CROPS), rng.choice([1, 2, 3])])
    return orders


def gen_action(
    rng: random.Random, farm: dict, private: dict, case: dict, turn: int, player: int
) -> dict:
    if case["style"] == "stakeout" and player == 0:
        # Deterministic decay coverage; the daily respawn keeps the farmer on
        # the spawn tile, so no movement is ever needed.
        if turn == 0:
            return {"farmer": ["PASS"], "hands": [], "market": [["BUY_SEED", "WHEAT", 1]]}
        if turn == 1:
            return {"farmer": ["PLANT", "WHEAT"], "hands": [], "market": []}
        return {"farmer": ["WATER"], "hands": [], "market": []}

    hand_count = len(farm["hands"])
    if case["style"] == "congest" and rng.random() < 0.15:
        crop = rng.choice(CROPS)
        return {
            "farmer": ["PLANT", crop],
            "hands": [["PLANT", crop] for _ in range(hand_count + 1)],
            "market": gen_market(rng),
        }
    return {
        "farmer": gen_garden_op(rng, farm, private, 0, case),
        "hands": [
            gen_garden_op(rng, farm, private, i + 1, case) for i in range(hand_count)
        ],
        "market": gen_market(rng),
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
    assert configuration["weedSpawnChance"] == 0

    rng = random.Random(case["tape_seed"])
    tape: list = []
    digests: list = []
    any_harvest = False
    any_weed = False
    any_decay = False
    any_blocked_plant = False
    previous_tiles: list = [{}, {}]

    turn = 0
    while not env.done:
        diag = oracle.diagnostic_state(state, env)
        actions = [
            gen_action(rng, diag["farms"][p], diag["privates"][p], case, turn, p)
            for p in (0, 1)
        ]
        for player, action in enumerate(actions):
            units = [action["farmer"], *action["hands"]]
            demand: dict = {}
            for a in units:
                if isinstance(a, list) and len(a) >= 2 and a[0] == "PLANT":
                    demand[a[1]] = demand.get(a[1], 0) + 1
            seeds = diag["privates"][player]["seeds"]
            if any(n > seeds.get(crop, 0) for crop, n in demand.items()):
                any_blocked_plant = True
        for player, action in enumerate(actions):
            state[player].action = oracle.structify(action)
        state = interpreter.interpreter(state, env)
        env.state = state
        state[0].observation.step = 0 if env.done else turn + 1

        diag = oracle.diagnostic_state(state, env)
        tape.append(actions)
        digests.append(digest(diag))
        for player, farm in enumerate(diag["farms"]):
            current: dict = {}
            for x, y, tile in sparse_tiles(farm):
                current[(x, y)] = tile
                if tile.get("kind") == "WEED":
                    any_weed = True
                prev = previous_tiles[player].get((x, y))
                if (
                    prev is not None
                    and prev.get("kind") == "PLANT"
                    and tile.get("kind") == "PLANT"
                    and 0 < tile["yield_units"] < prev["yield_units"]
                ):
                    any_decay = True  # harvest zeroes; only decay decrements
            previous_tiles[player] = current
        for p in diag["privates"]:
            held = dict(p["shed"])
            for inv in p["inventories"]:
                for item, n in inv.items():
                    held[item] = held.get(item, 0) + n
            if any(held.get(crop, 0) > 0 for crop in CROPS):
                any_harvest = True
        turn += 1

    final_diagnostic = {
        k: v
        for k, v in oracle.diagnostic_state(state, env).items()
        if k not in ("market", "town")
    }

    # Case-targeted coverage: each case must prove the thing it exists for.
    if case["name"] == "decay_stakeout":
        assert any_decay, "decay_stakeout never observed overripe decay"
        assert any_weed, "decay_stakeout never decayed into a weed"
    if case["name"] == "gardeners":
        assert any_harvest, "gardeners never harvested a crop"
        assert any_weed, "gardeners never produced a neglect weed"
    if case["name"] == "crops_congest":
        assert any_blocked_plant, "crops_congest never tripped the atomic PLANT block"
        assert any_harvest, "crops_congest never harvested a crop"

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
