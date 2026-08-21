"""Record oracle fixtures for the model's rule-group-1 differential test.

Run from the repository root:

    python3 fast_model/test/record_model_fixture.py

For each case this drives the pinned upstream interpreter (via the
reference/ oracle adapter) through a full PASS-only episode and records:

- the resolved scalar configuration and resolved seed;
- the exact post-initialization diagnostic state and both players' initial
  observations (remainingOverageTime stripped — framework timing, outside
  the differential scope);
- per-turn day / hour / framework-step arrays for the whole episode;
- the terminal turn, statuses, and rewards.

Intermediate statuses/rewards are asserted constant here (ACTIVE / 0), so the
fixture can store them compactly. Group-1 scope deliberately excludes the
evolving market/town/weed state under PASS — those fields are compared only at
initialization until their rule groups land in fast_model/lib/model.ml.

The OCaml test (kag_model_test.ml) compares against this file exactly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from reference import oracle

OUT = Path(__file__).with_name("model_group1_fixture.json")

# The scalar configuration surface the OCaml model consumes (everything in the
# upstream spec except marketParams, seed, and the framework-only actTimeout).
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

CASES = [
    {"name": "default", "seed": 1234, "overrides": {}},
    {
        "name": "short_days",
        "seed": 7,
        "overrides": {"episodeSteps": 48, "turnsPerDay": 6},
    },
    {
        "name": "board8",
        "seed": 99,
        "overrides": {
            "boardSize": 8,
            "startingMoney": 1500,
            "episodeSteps": 30,
            "turnsPerDay": 5,
        },
    },
]

PASS_ACTION = {"farmer": ["PASS"], "hands": [], "market": []}


def _strip_overage(observation: dict) -> dict:
    del observation["remainingOverageTime"]
    return observation


def record_case(case: dict) -> dict:
    interpreter = oracle.load_interpreter()
    env = oracle.OracleEnvironment({**case["overrides"], "seed": case["seed"]})
    state = [oracle._initial_agent_state(0), oracle._initial_agent_state(1)]
    env.state = state

    state = interpreter.interpreter(state, env)
    env.state = state
    state[0].observation.step = 0

    assert oracle.plain(env.configuration.get("marketParams") or {}) == {}, (
        "marketParams overrides are outside group-1 scope"
    )
    configuration = {key: env.configuration[key] for key in SCALAR_KEYS}

    initial_diagnostic = oracle.diagnostic_state(state, env)
    initial_observations = [
        _strip_overage(oracle.player_observation(state, player)) for player in (0, 1)
    ]

    days: list[int] = []
    hours: list[int] = []
    steps: list[int] = []
    turn = 0
    while not env.done:
        for player in (0, 1):
            state[player].action = oracle.structify(dict(PASS_ACTION))
        state = interpreter.interpreter(state, env)
        env.state = state
        state[0].observation.step = 0 if env.done else turn + 1

        days.append(state[0].observation.day)
        hours.append(state[0].observation.hour)
        steps.append(state[0].observation.step)
        if not env.done:
            assert [agent.status for agent in state] == ["ACTIVE", "ACTIVE"]
            assert [agent.reward for agent in state] == [0, 0]
        turn += 1

    return {
        "name": case["name"],
        "seed": case["seed"],
        "resolved_seed": env.info["seed"],
        "configuration": configuration,
        "initial_diagnostic": initial_diagnostic,
        "initial_observations": initial_observations,
        "turns": {"day": days, "hour": hours, "step": steps},
        "final": {
            "turn": turn - 1,
            "statuses": [agent.status for agent in state],
            "rewards": [agent.reward for agent in state],
        },
    }


if __name__ == "__main__":
    fixture = [record_case(case) for case in CASES]
    with OUT.open("w") as out:
        json.dump(fixture, out, sort_keys=True)
        out.write("\n")
    turns = sum(len(case["turns"]["day"]) for case in fixture)
    print(f"wrote {OUT}: {len(fixture)} cases, {turns} recorded turns")
