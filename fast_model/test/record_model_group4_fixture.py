"""Record oracle fixtures for the model's rule-group-4 differential test.

Run from the repository root:

    python3 fast_model/test/record_model_group4_fixture.py

Group-4 scope: BUILD_COOP / BUILD_PASTURE, animal placement (PLACE onto a
matching empty structure), FEED / CARE / COLLECT_FERTILIZER, animal product
harvest, the nightly animal refresh (escape after two unfed days, the
production schedule, the care bonus), DIG on structures (removes) and on
placed animals (no-op), and — via collected fertilizer — the first real
FERTILIZE coverage. Two cases:

- goose_keeper: player 0 runs a deterministic hour-scheduled routine on a
  wheat plot at the spawn tile and a coop one tile west: grow and harvest
  wheat, buy and place a goose timed so the feed supply exists before the
  escape clock can reach two unfed days, then feed / care / collect / harvest
  eggs daily and fertilize the plot with collected fertilizer. Player 1 is a
  random gardener-rancher (mostly-no-op animal ops included as noise).
- escapes: player 0 builds a pasture on the spawn tile, places a cow, DIGs at
  it (a no-op on a placed animal), never feeds it (escape after two nights,
  leaving the bare pasture), then DIGs the pasture away. Player 1 is noise.

Known coverage gap, closed by Phase 4's bulk tapes once BUY_PRODUCT exists
(group 5): sustained COW / SHEEP production needs more wheat than a scripted
single-plot farm can grow, so only GOOSE exercises the production schedule
here; COW covers placement/escape.

All cases pin weedSpawnChance to 0 (random weeds are group 6). The fixture
format matches groups 2-3; the recorder asserts eggs were produced and
harvested, the care bonus was consumed, fertilizer was collected and applied,
and the escape and both DIG behaviours actually happened.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from reference import oracle

OUT = Path(__file__).with_name("model_group4_fixture.json")

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
        "name": "goose_keeper",
        "seed": 424242,
        "tape_seed": 21,
        "overrides": {"episodeSteps": 112, "turnsPerDay": 8, "weedSpawnChance": 0},
        "style": "keeper",
    },
    {
        "name": "escapes",
        "seed": 111,
        "tape_seed": 22,
        "overrides": {"episodeSteps": 36, "turnsPerDay": 6, "weedSpawnChance": 0},
        "style": "escapes",
    },
]

PLOT = (4, 4)  # the daily respawn tile — the routine never has to walk back
COOP = (3, 4)


def tile_at(farm: dict, pos: tuple) -> dict | str | None:
    return farm["tiles"][pos[1]][pos[0]]


def keeper_action(turn: int, day: int, hour: int, farm: dict, private: dict) -> dict:
    """Deterministic hour-scheduled goose ranch; see the module docstring.

    Timing that matters: seeds bought in turn 0's market phase are plantable
    from turn 1 (h1), so the wheat is planted on day 0 and first harvested on
    day 4 — the same day the goose (bought day 3, placed day 3, one unfed
    night on the clock) must be fed from the fresh harvest. A newly planted
    tile must be watered the same day or it weeds overnight, hence WATER
    directly after every PLANT.
    """
    inv = private["inventories"][0]
    shed = private["shed"]
    seeds = private["seeds"]
    plot = tile_at(farm, PLOT)
    coop = tile_at(farm, COOP)
    plot_plant = isinstance(plot, dict) and plot.get("kind") == "PLANT"
    plot_unwatered = plot_plant and not plot["watered_today"]
    coop_animal = isinstance(coop, dict) and "animal" in coop

    market = []
    if turn == 0:
        market = [["BUY_SEED", "WHEAT", 6]]
    if day == 3 and hour == 0:
        market = [["BUY_ANIMAL", "GOOSE", 1]]

    plantable = plot is None and seeds.get("WHEAT", 0) > 0
    fert_useful = (
        plot_plant and plot["fertilized_until_day"] < day and shed.get("FERTILIZER", 0) > 0
    )

    farmer = ["PASS"]
    if hour == 0:
        if plantable:
            farmer = ["PLANT", "WHEAT"]
        elif plot_plant and plot["yield_units"] > 0 and day - plot["planted_day"] >= 4:
            farmer = ["HARVEST"]
        elif plot_plant and inv.get("FERTILIZER", 0) > 0:
            farmer = ["FERTILIZE"]
        elif plot_unwatered:
            farmer = ["WATER"]
    elif hour == 1:
        if plantable:
            farmer = ["PLANT", "WHEAT"]
        elif plot_unwatered:
            farmer = ["WATER"]
        elif fert_useful:
            farmer = ["PICKUP", "FERTILIZER", 1]
        elif not coop_animal and shed.get("GOOSE", 0) > 0:
            farmer = ["PICKUP", "GOOSE", 1]
        elif coop_animal and shed.get("WHEAT", 0) > 0:
            farmer = ["PICKUP", "WHEAT", 1]
    elif hour == 2:
        if plot_plant and inv.get("FERTILIZER", 0) > 0:
            farmer = ["FERTILIZE"]
        elif plot_unwatered:
            farmer = ["WATER"]
        elif coop_animal and shed.get("WHEAT", 0) > 0 and inv.get("WHEAT", 0) == 0:
            farmer = ["PICKUP", "WHEAT", 1]
    elif hour == 3:
        farmer = ["WEST"]
    elif hour == 4:
        if coop is None:
            farmer = ["BUILD_COOP"]
        elif isinstance(coop, dict) and "animal" not in coop and inv.get("GOOSE", 0) > 0:
            farmer = ["PLACE", "GOOSE"]
        elif coop_animal and not coop["fed_today"] and inv.get("WHEAT", 0) > 0:
            farmer = ["FEED"]
    elif hour == 5:
        if coop_animal and not coop["fed_today"] and inv.get("WHEAT", 0) > 0:
            farmer = ["FEED"]
        elif coop_animal and coop["yield_units"] > 0:
            farmer = ["HARVEST"]
    elif hour == 6:
        if coop_animal and not coop["cared_today"]:
            farmer = ["CARE"]
        elif coop_animal and coop["fertilizer_available"]:
            farmer = ["COLLECT_FERTILIZER"]
    elif hour == 7:
        if coop_animal and coop["fertilizer_available"]:
            farmer = ["COLLECT_FERTILIZER"]
        elif coop_animal and not coop["cared_today"]:
            farmer = ["CARE"]

    return {"farmer": farmer, "hands": [], "market": market}


def escapes_action(turn: int, farm: dict, private: dict) -> dict:
    plot = tile_at(farm, PLOT)
    script = {
        0: (["PASS"], [["BUY_ANIMAL", "COW", 1]]),
        1: (["PICKUP", "COW", 1], []),
        2: (["BUILD_PASTURE"], []),
        3: (["PLACE", "COW"], []),
        4: (["DIG"], []),  # no-op: DIG never removes a placed animal
    }
    if turn in script:
        farmer, market = script[turn]
        return {"farmer": farmer, "hands": [], "market": market}
    # After the escape leaves a bare pasture on the spawn tile, dig it away.
    if plot is not None and isinstance(plot, dict) and "animal" not in plot:
        return {"farmer": ["DIG"], "hands": [], "market": []}
    if isinstance(plot, dict) and "animal" in plot:
        return {"farmer": ["CARE"], "hands": [], "market": []}  # cared but unfed
    return {"farmer": ["PASS"], "hands": [], "market": []}


def gen_noise_op(rng: random.Random, farm: dict, private: dict, unit_idx: int) -> list:
    pos = farm["farmer"] if unit_idx == 0 else None
    hands = farm["hands"]
    if unit_idx > 0 and unit_idx - 1 < len(hands):
        pos = hands[unit_idx - 1]
    seeds_avail = [c for c, v in private["seeds"].items() if v > 0]
    if pos is not None:
        x, y = pos
        tile = farm["tiles"][y][x]
        if isinstance(tile, dict):
            kind = tile.get("kind")
            if "animal" in tile:
                r = rng.random()
                if r < 0.3 and not tile["fed_today"]:
                    return ["FEED"]
                if r < 0.5 and not tile["cared_today"]:
                    return ["CARE"]
                if r < 0.7 and tile["fertilizer_available"]:
                    return ["COLLECT_FERTILIZER"]
                if r < 0.85 and tile["yield_units"] > 0:
                    return ["HARVEST"]
            elif kind in ("COOP", "PASTURE"):
                held = [a for a in ANIMALS if private["inventories"][unit_idx].get(a, 0) > 0] if unit_idx < len(private["inventories"]) else []
                if held and rng.random() < 0.6:
                    return ["PLACE", rng.choice(held)]
                if rng.random() < 0.3:
                    return ["DIG"]
            elif kind == "PLANT":
                if not tile["watered_today"] and rng.random() < 0.8:
                    return ["WATER"]
                if tile["yield_units"] > 0 and rng.random() < 0.5:
                    return ["HARVEST"]
            elif kind == "WEED" and rng.random() < 0.4:
                return ["DIG"]
        if tile is None:
            r = rng.random()
            if seeds_avail and r < 0.35:
                return ["PLANT", rng.choice(seeds_avail)]
            if r < 0.45:
                return ["BUILD_COOP"]
            if r < 0.55:
                return ["BUILD_PASTURE"]
    r = rng.random()
    if r < 0.5:
        return [rng.choice(DIRECTIONS)]
    if r < 0.6:
        return ["PASS"]
    if r < 0.7:
        held = [a for a in ANIMALS if private["shed"].get(a, 0) > 0]
        return ["PICKUP", rng.choice(held or ["WHEAT"]), 1]
    if r < 0.8:
        return ["FEED"]  # usually a no-op on purpose
    if r < 0.9:
        return ["CARE"]
    return ["DROP"]


def gen_noise_action(rng: random.Random, farm: dict, private: dict) -> dict:
    market: list = []
    if rng.random() < 0.4:
        r = rng.random()
        if r < 0.4:
            market.append(["BUY_SEED", rng.choice(CROPS), rng.choice([1, 2])])
        elif r < 0.7:
            market.append(["BUY_ANIMAL", rng.choice(ANIMALS), 1])
        else:
            market.append(["HIRE"])
    return {
        "farmer": gen_noise_op(rng, farm, private, 0),
        "hands": [
            gen_noise_op(rng, farm, private, i + 1) for i in range(len(farm["hands"]))
        ],
        "market": market,
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
    tpd = configuration["turnsPerDay"]

    rng = random.Random(case["tape_seed"])
    tape: list = []
    digests: list = []
    any_egg = False
    any_fert_collected = False
    any_fert_applied = False
    any_care_bonus = False
    any_escape = False
    dig_noop_on_animal = False
    prev_plot_p0: dict | None = None

    turn = 0
    while not env.done:
        diag = oracle.diagnostic_state(state, env)
        day, hour = turn // tpd, turn % tpd
        if case["style"] == "keeper":
            a0 = keeper_action(turn, day, hour, diag["farms"][0], diag["privates"][0])
        else:
            a0 = escapes_action(turn, diag["farms"][0], diag["privates"][0])
        a1 = gen_noise_action(rng, diag["farms"][1], diag["privates"][1])
        actions = [a0, a1]

        was_animal_at_plot = (
            isinstance(tile_at(diag["farms"][0], PLOT), dict)
            and "animal" in tile_at(diag["farms"][0], PLOT)
        )
        for player, action in enumerate(actions):
            state[player].action = oracle.structify(action)
        state = interpreter.interpreter(state, env)
        env.state = state
        state[0].observation.step = 0 if env.done else turn + 1

        diag = oracle.diagnostic_state(state, env)
        tape.append(actions)
        digests.append(digest(diag))

        if case["style"] == "escapes" and a0["farmer"] == ["DIG"] and was_animal_at_plot:
            still = tile_at(diag["farms"][0], PLOT)
            if isinstance(still, dict) and "animal" in still:
                dig_noop_on_animal = True
        for farm_diag, private in zip(diag["farms"], diag["privates"]):
            held = dict(private["shed"])
            for inv in private["inventories"]:
                for item, n in inv.items():
                    held[item] = held.get(item, 0) + n
            if held.get("EGG", 0) > 0:
                any_egg = True
            if held.get("FERTILIZER", 0) > 0:
                any_fert_collected = True
            for _x, _y, tile in sparse_tiles(farm_diag):
                if tile.get("kind") == "PLANT" and tile["fertilized_until_day"] >= 0:
                    any_fert_applied = True
        coop = tile_at(diag["farms"][0], COOP)
        if isinstance(coop, dict) and "animal" in coop:
            if prev_plot_p0 is not None and coop["yield_units"] - prev_plot_p0.get("yield_units", 0) >= 2:
                any_care_bonus = True
            prev_plot_p0 = coop
        plot = tile_at(diag["farms"][0], PLOT)
        if (
            case["style"] == "escapes"
            and isinstance(plot, dict)
            and plot.get("kind") == "PASTURE"
            and "animal" not in plot
        ):
            any_escape = True
        turn += 1

    final_diagnostic = {
        k: v
        for k, v in oracle.diagnostic_state(state, env).items()
        if k not in ("market", "town")
    }

    if case["name"] == "goose_keeper":
        assert any_egg, "goose_keeper never produced/held an egg"
        assert any_fert_collected, "goose_keeper never collected fertilizer"
        assert any_fert_applied, "goose_keeper never applied fertilizer"
        assert any_care_bonus, "goose_keeper never consumed a care bonus"
    if case["name"] == "escapes":
        assert any_escape, "escapes: the cow never escaped"
        assert dig_noop_on_animal, "escapes: DIG-on-animal no-op never observed"
        assert tile_at(
            json.loads(json.dumps(final_diagnostic))["farms"][0], PLOT
        ) is None, "escapes: the bare pasture was never dug away"

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
