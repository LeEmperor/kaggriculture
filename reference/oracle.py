"""Lightweight adapter around the exact pinned Kaggriculture interpreter.

This intentionally avoids importing the full ``kaggle_environments`` package,
whose top-level import loads every bundled environment. Game transitions still
execute the pinned upstream ``kaggriculture.py`` verbatim.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import random
import sys
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REFERENCE_DIR = Path(__file__).resolve().parent
LOCK_PATH = REFERENCE_DIR / "upstream.lock.json"
UPSTREAM_ROOT = REFERENCE_DIR / "upstream" / "kaggle-environments"
ENV_DIR = UPSTREAM_ROOT / "kaggle_environments" / "envs" / "kaggriculture"
ENV_PATH = ENV_DIR / "kaggriculture.py"
SPEC_PATH = ENV_DIR / "kaggriculture.json"

Action = dict[str, Any]
Policy = Callable[[dict[str, Any]], Action]


@dataclass(frozen=True)
class GameResult:
    """Compact terminal result for throughput evaluation without trace materialization."""

    final_money: tuple[float, float]
    status: tuple[str, str]
    turns: int


class Struct(dict[str, Any]):
    """Dictionary with the attribute access used by Kaggle's framework."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


def structify(value: Any) -> Any:
    if isinstance(value, dict):
        return Struct({key: structify(item) for key, item in value.items()})
    if isinstance(value, list):
        return [structify(item) for item in value]
    return value


def plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


def _resolve_episode_seed(env: Any, *, config_key: str = "seed", fallback=None) -> int:
    """Exact behavior of the pinned utility needed by the interpreter."""
    if not hasattr(env, "info") or env.info is None:
        env.info = {}
    seed = env.info.get("seed")
    config = env.configuration
    if seed is None:
        seed = getattr(config, config_key, None)
        if seed is None and isinstance(config, dict):
            seed = config.get(config_key)
    if seed is None:
        seed = fallback() if fallback is not None else random.randrange(2**31)
    try:
        setattr(config, config_key, None)
    except (AttributeError, TypeError):
        config[config_key] = None
    env.info["seed"] = seed
    return int(seed)


def load_interpreter() -> types.ModuleType:
    if not ENV_PATH.is_file():
        raise FileNotFoundError(
            f"pinned source is missing at {ENV_PATH}; run: python3 reference/bootstrap.py"
        )

    module_name = "_kaggriculture_pinned_interpreter"
    if module_name in sys.modules:
        return sys.modules[module_name]

    # Satisfy the interpreter's one framework import without triggering the
    # heavyweight kaggle_environments package initializer.
    package = types.ModuleType("kaggle_environments")
    package.__path__ = [str(UPSTREAM_ROOT / "kaggle_environments")]
    utilities = types.ModuleType("kaggle_environments.utils")
    utilities.resolve_episode_seed = _resolve_episode_seed
    package.utils = utilities
    sys.modules["kaggle_environments"] = package
    sys.modules["kaggle_environments.utils"] = utilities

    spec = importlib.util.spec_from_file_location(module_name, ENV_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {ENV_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def default_configuration() -> Struct:
    specification = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    configuration: dict[str, Any] = {}
    for key, declaration in specification["configuration"].items():
        configuration[key] = (
            declaration.get("default") if isinstance(declaration, dict) else declaration
        )
    return structify(configuration)


class OracleEnvironment:
    """The small framework surface read by the official interpreter."""

    def __init__(self, configuration: Mapping[str, Any] | None = None):
        self.configuration = default_configuration()
        if configuration:
            self.configuration.update(structify(dict(configuration)))
        self.info: dict[str, Any] = {}
        self.state: list[Struct] = []

    @property
    def done(self) -> bool:
        return bool(self.state) and all(
            agent.status != "ACTIVE" for agent in self.state
        )


def _initial_agent_state(player: int) -> Struct:
    return structify(
        {
            "action": None,
            "reward": 0,
            "info": {},
            "observation": {
                "player": player,
                "private": {},
                "remainingOverageTime": 60,
                "step": 0,
            },
            "status": "ACTIVE",
        }
    )


def player_observation(state: list[Struct], player: int) -> dict[str, Any]:
    shared = state[0].observation
    own = state[player].observation
    observation = {
        "player": player,
        "farms": plain(shared.farms),
        "private": plain(own.private),
        "market": plain(shared.market),
        "town": plain(shared.town),
        "day": shared.day,
        "hour": shared.hour,
        "step": shared.step,
        "remainingOverageTime": own.remainingOverageTime,
    }
    return copy.deepcopy(observation)


def diagnostic_state(state: list[Struct], env: OracleEnvironment) -> dict[str, Any]:
    shared = state[0].observation
    return {
        "farms": plain(shared.farms),
        "market": plain(shared.market),
        "town": plain(shared.town),
        "privates": [plain(agent.observation.private) for agent in state],
        "day": shared.day,
        "hour": shared.hour,
        "resolved_seed": env.info["seed"],
    }


def _drive_game(
    seed: int,
    policy_a: Policy,
    policy_b: Policy,
    *,
    configuration: Mapping[str, Any] | None = None,
    records: list[dict[str, Any]] | None = None,
) -> GameResult:
    interpreter_module = load_interpreter()
    options = dict(configuration or {})
    options["seed"] = seed
    env = OracleEnvironment(options)
    state = [_initial_agent_state(0), _initial_agent_state(1)]
    env.state = state

    state = interpreter_module.interpreter(state, env)
    env.state = state
    state[0].observation.step = 0

    lock = (
        json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        if records is not None
        else None
    )
    policies = (policy_a, policy_b)
    turn = 0
    while not env.done:
        actions = [
            copy.deepcopy(policy(player_observation(state, player)))
            for player, policy in enumerate(policies)
        ]
        for player, action in enumerate(actions):
            state[player].action = structify(action)

        state = interpreter_module.interpreter(state, env)
        env.state = state
        # Match core.Environment: the shared framework step is assigned after
        # the interpreter returns, and is reset to zero once the episode is done.
        state[0].observation.step = 0 if env.done else turn + 1

        if records is not None:
            assert lock is not None
            records.append(
                {
                    "reference_commit": lock["commit"],
                    "seed": seed,
                    "turn": turn,
                    "actions": plain(actions),
                    "observations": [
                        player_observation(state, 0),
                        player_observation(state, 1),
                    ],
                    "diagnostic_state": diagnostic_state(state, env),
                    "status": [agent.status for agent in state],
                    "reward": [agent.reward for agent in state],
                }
            )
        turn += 1

    return GameResult(
        final_money=(float(state[0].reward), float(state[1].reward)),
        status=(str(state[0].status), str(state[1].status)),
        turns=turn,
    )


def evaluate_game(
    seed: int,
    policy_a: Policy,
    policy_b: Policy,
    *,
    configuration: Mapping[str, Any] | None = None,
) -> GameResult:
    """Run one official game while retaining only its terminal aggregate.

    This is the oracle side of the Phase 5 throughput benchmark. It deliberately
    avoids constructing diagnostic states and post-turn observations, work that a
    native ``evaluate`` run does not perform either.
    """

    return _drive_game(
        seed,
        policy_a,
        policy_b,
        configuration=configuration,
    )


def run_game(
    seed: int,
    policy_a: Policy,
    policy_b: Policy,
    *,
    configuration: Mapping[str, Any] | None = None,
    trace_path: Path | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    _drive_game(
        seed,
        policy_a,
        policy_b,
        configuration=configuration,
        records=records,
    )

    if trace_path is not None:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open("w", encoding="utf-8", newline="\n") as trace:
            for record in records:
                trace.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
                trace.write("\n")
    return records
