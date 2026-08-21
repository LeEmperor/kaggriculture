"""Record oracle fixtures for the model's rule-group-5 differential test.

Run from the repository root:

    python3 fast_model/test/record_model_group5_fixture.py

Group-5 scope: the pricing curves (all shape functions in play across the
nine products, Python's round-half-even, the price floor machinery), SELL and
BUY_PRODUCT through the per-unit lockstep (simultaneous quoting from the same
pre-commit inventory, money/stock exhaustion mid-order), per-order-index price
refresh, and the deterministic town-center consumption tick that market
inventory parity requires. From this group on, fixtures carry the market in
the per-turn digest and the full diagnostic (market and town included) at
episode end.

- traders: two state-aware gardener-merchants — grow, harvest, and SELL crops
  (frequently the same product in the same turn, exercising the lockstep
  interleaving), restock seeds, and buy fertilizer with BUY_PRODUCT to
  exercise FERTILIZE through a purchased path.
- dairy: player 0 runs a deterministic cow-and-sheep ranch fed entirely by
  BUY_PRODUCT WHEAT — closing group 4's coverage gap on the interval-2/3
  production schedules — and sells MILK / WOOL / collected FERTILIZER.
  Player 1 is a noise gardener-seller.

All cases pin weedSpawnChance to 0 and townShopUnlockInterval high: random
weeds and shop unlocks/consumption are the town/RNG group; with no shop ever
unlocked, the oracle town stays empty and the market moves only through
orders and the town-center tick. The price floor is practically unreachable
with organic play (it needs ~60+ units sold above I0 on the friendliest
curve); it gets deliberate coverage via marketParams overrides in group 7.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from reference import oracle

OUT = Path(__file__).with_name("model_group5_fixture.json")

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
        "name": "traders",
        "seed": 777777,
        "tape_seed": 31,
        "overrides": {
            "episodeSteps": 96,
            "turnsPerDay": 8,
            "startingMoney": 6000,
            "weedSpawnChance": 0,
            "townShopUnlockInterval": 9999,
        },
        "style": "traders",
    },
    {
        "name": "dairy",
        "seed": 888888,
        "tape_seed": 32,
        "overrides": {
            "episodeSteps": 160,
            "turnsPerDay": 10,
            "weedSpawnChance": 0,
            "townShopUnlockInterval": 9999,
        },
        "style": "dairy",
    },
]

COW_POS = (4, 4)
SHEEP_POS = (3, 4)


def tile_at(farm: dict, pos: tuple):
    return farm["tiles"][pos[1]][pos[0]]


def is_animal(tile) -> bool:
    return isinstance(tile, dict) and "animal" in tile


def dairy_action(turn: int, day: int, hour: int, farm: dict, private: dict) -> dict:
    inv = private["inventories"][0]
    shed = private["shed"]
    cow = tile_at(farm, COW_POS)
    sheep = tile_at(farm, SHEEP_POS)

    if day == 0:
        script = {
            0: (
                ["BUILD_PASTURE"],
                [
                    ["BUY_ANIMAL", "COW", 1],
                    ["BUY_ANIMAL", "SHEEP", 1],
                    ["BUY_PRODUCT", "WHEAT", 4],
                ],
            ),
            1: (["PICKUP", "COW", 1], []),
            2: (["PLACE", "COW"], []),
            3: (["PICKUP", "WHEAT", 2], []),
            4: (["FEED"], []),
            5: (["CARE"], []),
        }
        farmer, market = script.get(hour, (["PASS"], []))
        return {"farmer": farmer, "hands": [], "market": market}

    farmer = ["PASS"]
    market: list = []
    if hour == 0:
        if not is_animal(sheep) and shed.get("SHEEP", 0) > 0:
            farmer = ["PICKUP", "SHEEP", 1]
        elif shed.get("WHEAT", 0) > 0:
            farmer = ["PICKUP", "WHEAT", 2]
    elif hour == 1:
        if is_animal(cow) and not cow["fed_today"] and inv.get("WHEAT", 0) > 0:
            farmer = ["FEED"]
    elif hour == 2:
        if is_animal(cow) and cow["yield_units"] > 0:
            farmer = ["HARVEST"]
        elif is_animal(cow) and not cow["cared_today"]:
            farmer = ["CARE"]
    elif hour == 3:
        if is_animal(cow) and not cow["cared_today"]:
            farmer = ["CARE"]
        elif is_animal(cow) and cow["fertilizer_available"]:
            farmer = ["COLLECT_FERTILIZER"]
    elif hour == 4:
        farmer = ["WEST"]
    elif hour == 5:
        if sheep is None:
            farmer = ["BUILD_PASTURE"]
        elif not is_animal(sheep) and inv.get("SHEEP", 0) > 0:
            farmer = ["PLACE", "SHEEP"]
        elif is_animal(sheep) and not sheep["fed_today"] and inv.get("WHEAT", 0) > 0:
            farmer = ["FEED"]
    elif hour == 6:
        if not is_animal(sheep) and inv.get("SHEEP", 0) > 0:
            farmer = ["PLACE", "SHEEP"]
        elif is_animal(sheep) and not sheep["fed_today"] and inv.get("WHEAT", 0) > 0:
            farmer = ["FEED"]
        elif is_animal(sheep) and sheep["yield_units"] > 0:
            farmer = ["HARVEST"]
    elif hour == 7:
        if is_animal(sheep) and not sheep["cared_today"]:
            farmer = ["CARE"]
        elif is_animal(sheep) and sheep["yield_units"] > 0:
            farmer = ["HARVEST"]
    elif hour == 8:
        if is_animal(sheep) and sheep["fertilizer_available"]:
            farmer = ["COLLECT_FERTILIZER"]
        elif is_animal(sheep) and not sheep["cared_today"]:
            farmer = ["CARE"]
    elif hour == 9:
        market = [["BUY_PRODUCT", "WHEAT", 3]]
        for item in ("MILK", "WOOL", "FERTILIZER"):
            if shed.get(item, 0) > 0:
                market.append(["SELL", item, shed[item]])
    return {"farmer": farmer, "hands": [], "market": market}


def trader_unit_op(rng: random.Random, farm: dict, private: dict, unit_idx: int) -> list:
    pos = farm["farmer"] if unit_idx == 0 else None
    hands = farm["hands"]
    if unit_idx > 0 and unit_idx - 1 < len(hands):
        pos = hands[unit_idx - 1]
    inventories = private["inventories"]
    inv = inventories[unit_idx] if unit_idx < len(inventories) else {}
    seeds_avail = [c for c, v in private["seeds"].items() if v > 0]
    if pos is not None:
        x, y = pos
        tile = farm["tiles"][y][x]
        if isinstance(tile, dict) and tile.get("kind") == "PLANT":
            if inv.get("FERTILIZER", 0) > 0 and tile["fertilized_until_day"] < 0:
                return ["FERTILIZE"]
            if not tile["watered_today"] and rng.random() < 0.85:
                return ["WATER"]
            if tile["yield_units"] > 0 and rng.random() < 0.6:
                return ["HARVEST"]
        if isinstance(tile, dict) and tile.get("kind") == "WEED" and rng.random() < 0.4:
            return ["DIG"]
        if tile is None and seeds_avail and rng.random() < 0.55:
            return ["PLANT", rng.choice(seeds_avail)]
    r = rng.random()
    if r < 0.45:
        return [rng.choice(DIRECTIONS)]
    if r < 0.55:
        return ["PASS"]
    if r < 0.7:
        return ["DROP"]
    if r < 0.85:
        held = [k for k, v in private["shed"].items() if v > 0]
        return ["PICKUP", rng.choice(held or ["FERTILIZER"]), 1]
    return ["HARVEST"]


def trader_market(rng: random.Random, private: dict) -> list:
    orders: list = []
    shed = private["shed"]
    # Accumulate before selling so both sheds overlap on held crops, which the
    # forced simultaneous-sell below depends on.
    ripe = [k for k, v in shed.items() if v >= 3 and k in CROPS]
    if ripe and rng.random() < 0.6:
        item = rng.choice(ripe)
        count = shed[item] + (2 if rng.random() < 0.3 else 0)  # overshoot aborts
        orders.append(["SELL", item, count])
    if shed.get("FERTILIZER", 0) > 2:
        orders.append(["SELL", "FERTILIZER", shed["FERTILIZER"]])
    if rng.random() < 0.5:
        orders.append(
            ["BUY_SEED", rng.choice(["WHEAT", "WHEAT", "CARROT", "TOMATO"]), rng.choice([1, 2])]
        )
    if rng.random() < 0.05:
        orders.append(["BUY_PRODUCT", "FERTILIZER", 1])
    if rng.random() < 0.08:
        orders.append(["BUY_PRODUCT", "WHEAT", rng.choice([1, 2])])
    if rng.random() < 0.06:
        orders.append(["HIRE"])
    return orders


def trader_action(rng: random.Random, farm: dict, private: dict) -> dict:
    return {
        "farmer": trader_unit_op(rng, farm, private, 0),
        "hands": [
            trader_unit_op(rng, farm, private, i + 1) for i in range(len(farm["hands"]))
        ],
        "market": trader_market(rng, private),
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
    assert configuration["townShopUnlockInterval"] > configuration["episodeSteps"]
    tpd = configuration["turnsPerDay"]

    rng = random.Random(case["tape_seed"])
    tape: list = []
    digests: list = []
    simultaneous_sell = False
    sell_overshoot = False
    sold = {"MILK": False, "WOOL": False, "FERTILIZER": False}
    any_buy_product = False
    prices_moved = False

    turn = 0
    while not env.done:
        diag = oracle.diagnostic_state(state, env)
        day, hour = turn // tpd, turn % tpd
        if case["style"] == "dairy":
            a0 = dairy_action(turn, day, hour, diag["farms"][0], diag["privates"][0])
            a1 = trader_action(rng, diag["farms"][1], diag["privates"][1])
            for order in a0["market"]:
                if order[0] == "SELL" and diag["privates"][0]["shed"].get(order[1], 0) > 0:
                    sold[order[1]] = True
        else:
            a0 = trader_action(rng, diag["farms"][0], diag["privates"][0])
            a1 = trader_action(rng, diag["farms"][1], diag["privates"][1])
            # Whenever both sheds hold the same crop, make both players sell
            # it in the same turn — the lockstep interleaving under test.
            common = [
                c
                for c in CROPS
                if diag["privates"][0]["shed"].get(c, 0) > 0
                and diag["privates"][1]["shed"].get(c, 0) > 0
            ]
            if common:
                for player, action in ((0, a0), (1, a1)):
                    action["market"] = [
                        o for o in action["market"] if not (o[0] == "SELL" and o[1] == common[0])
                    ]
                    action["market"].insert(
                        0,
                        ["SELL", common[0], diag["privates"][player]["shed"][common[0]]],
                    )
        actions = [a0, a1]

        sell_items = []
        for player, action in enumerate(actions):
            for order in action["market"]:
                if order[0] == "SELL":
                    if diag["privates"][player]["shed"].get(order[1], 0) > 0:
                        sell_items.append((player, order[1]))
                    if order[2] > diag["privates"][player]["shed"].get(order[1], 0):
                        sell_overshoot = True
                if order[0] == "BUY_PRODUCT":
                    any_buy_product = True
        items0 = {i for p, i in sell_items if p == 0}
        items1 = {i for p, i in sell_items if p == 1}
        if items0 & items1:
            simultaneous_sell = True

        for player, action in enumerate(actions):
            state[player].action = oracle.structify(action)
        state = interpreter.interpreter(state, env)
        env.state = state
        state[0].observation.step = 0 if env.done else turn + 1

        diag = oracle.diagnostic_state(state, env)
        tape.append(actions)
        digests.append(digest(diag))
        base_prices = {
            "WHEAT": 25, "CARROT": 35, "TOMATO": 60, "STRAWBERRY": 120,
            "MELON": 250, "EGG": 50, "MILK": 160, "WOOL": 200, "FERTILIZER": 100,
        }
        if any(diag["market"]["prices"][k] != v for k, v in base_prices.items()):
            prices_moved = True
        turn += 1

    final_diagnostic = oracle.diagnostic_state(state, env)

    assert prices_moved, f"{case['name']}: market prices never moved"
    if case["name"] == "traders":
        assert simultaneous_sell, "traders: no simultaneous same-item SELL"
        assert sell_overshoot, "traders: no stock-exhausting SELL"
        assert any_buy_product, "traders: no BUY_PRODUCT order"
    if case["name"] == "dairy":
        assert sold["MILK"], "dairy: never sold milk"
        assert sold["WOOL"], "dairy: never sold wool"
        assert sold["FERTILIZER"], "dairy: never sold fertilizer"
        cow = tile_at(final_diagnostic["farms"][0], COW_POS)
        sheep = tile_at(final_diagnostic["farms"][0], SHEEP_POS)
        assert is_animal(cow) and cow["animal"] == "COW", "dairy: cow missing at end"
        assert is_animal(sheep) and sheep["animal"] == "SHEEP", "dairy: sheep missing"

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
