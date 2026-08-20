"""Load the pinned Kaggriculture interpreter without its heavyweight package.

The environment module itself only needs ``resolve_episode_seed``.  Providing
that tiny compatibility shim lets the oracle execute the exact pinned source
without installing the full Kaggle Environments dependency set.
"""

from __future__ import annotations

import importlib.util
import os
import random
import sys
import types
from pathlib import Path
from typing import Any


PINNED_COMMIT = "28b6d8af3ce73926b3d0fda1410c1ddd8384ab8c"


class Struct(dict[str, Any]):
    """Dict with the attribute behavior used by kaggle-environments."""

    def __init__(self, **entries: Any) -> None:
        super().__init__(entries)
        self.__dict__.update(entries)

    def __setattr__(self, name: str, value: Any) -> None:
        self.__dict__[name] = value
        self[name] = value


def structify(value: Any) -> Any:
    if isinstance(value, list):
        return [structify(item) for item in value]
    if isinstance(value, dict):
        return Struct(**{key: structify(item) for key, item in value.items()})
    return value


def resolve_episode_seed(env: Any, *, config_key: str = "seed", fallback=None) -> int:
    if not hasattr(env, "info") or env.info is None:
        env.info = {}
    seed = env.info.get("seed")
    if seed is None:
        seed = getattr(env.configuration, config_key, None)
    if seed is None:
        seed = fallback() if fallback is not None else random.randrange(2**31)
    setattr(env.configuration, config_key, None)
    env.info["seed"] = seed
    return int(seed)


def reference_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).resolve()
    configured = os.environ.get("KAGGRICULTURE_REFERENCE_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[1] / ".reference" / "kaggle-environments"


def load_environment(explicit_root: str | os.PathLike[str] | None = None):
    root = reference_root(explicit_root)
    source = root / "kaggle_environments" / "envs" / "kaggriculture" / "kaggriculture.py"
    if not source.is_file():
        raise FileNotFoundError(
            f"Pinned environment not found at {source}. Run: bash tools/fetch_reference.sh"
        )

    utils = types.ModuleType("kaggle_environments.utils")
    utils.resolve_episode_seed = resolve_episode_seed
    package = types.ModuleType("kaggle_environments")
    package.utils = utils
    sys.modules.setdefault("kaggle_environments", package)
    sys.modules["kaggle_environments.utils"] = utils

    spec = importlib.util.spec_from_file_location("kaggriculture_pinned", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEFAULT_CONFIG = {
    "episodeSteps": 720,
    "boardSize": 10,
    "startingMoney": 3000,
    "maxMarketOrdersPerTurn": 10,
    "turnsPerDay": 24,
    "shedCapacity": 100,
    "weedSpawnChance": 0.005,
    "townShopUnlockInterval": 3,
    "townShopSellInterval": 4,
    "townCenterSellInterval": 24,
    "farmHandCostMult": 1,
    "marketParams": {},
}


class OracleEnvironment:
    """Minimal framework adapter around the official interpreter."""

    def __init__(self, module: Any, seed: int, overrides: dict[str, Any] | None = None):
        config = {**DEFAULT_CONFIG, **(overrides or {}), "seed": seed}
        self.configuration = structify(config)
        self.info: dict[str, Any] = {}
        self.module = module
        self.state = [
            Struct(
                observation=Struct(step=0, player=player),
                action=None,
                status="ACTIVE",
                reward=None,
            )
            for player in range(2)
        ]
        self.module.interpreter(self.state, self)
        for player, state in enumerate(self.state):
            state.observation.step = 0
            state.observation.player = player

    @property
    def done(self) -> bool:
        return all(state.status == "DONE" for state in self.state)

    @property
    def seed(self) -> int:
        return int(self.info["seed"])

    def step(self, actions: list[dict[str, Any]]) -> list[Struct]:
        if self.done:
            raise RuntimeError("environment is already done")
        if len(actions) != 2:
            raise ValueError("exactly two actions are required")
        previous_step = int(self.state[0].observation.step)
        for state, action in zip(self.state, actions, strict=True):
            state.action = structify(action)
        self.module.interpreter(self.state, self)
        next_step = previous_step + 1
        for state in self.state:
            state.observation.step = next_step
        return self.state

