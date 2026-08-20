"""Run the pinned official interpreter and emit a deterministic JSONL trace."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from .official import OracleEnvironment, load_environment
from .policies import POLICIES, seeded_fuzz_policy


def plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


def visible_observation(state: Any) -> dict[str, Any]:
    return plain(copy.deepcopy(dict(state.observation)))


def canonical_state(env: OracleEnvironment) -> dict[str, Any]:
    obs0 = env.state[0].observation
    return {
        "step": int(obs0.step),
        "day": int(obs0.day),
        "hour": int(obs0.hour),
        "farms": plain(obs0.farms),
        "market": plain(obs0.market),
        "town": plain(obs0.town),
        "privates": [plain(state.observation.private) for state in env.state],
        "status": [state.status for state in env.state],
        "reward": [state.reward for state in env.state],
    }


def resolve_policy(name: str, seed: int):
    if name == "fuzz":
        return seeded_fuzz_policy(seed)
    try:
        return POLICIES[name]
    except KeyError as exc:
        raise ValueError(f"unknown policy {name!r}") from exc


def run(seed: int, policy_a: str, policy_b: str, reference_root: str | None = None):
    module = load_environment(reference_root)
    env = OracleEnvironment(module, seed)
    policies = [resolve_policy(policy_a, seed), resolve_policy(policy_b, seed + 1)]
    while not env.done:
        actions = [policies[i](visible_observation(env.state[i])) for i in range(2)]
        env.step(actions)
        yield {
            "turn": int(env.state[0].observation.step),
            "seed": seed,
            "actions": plain(actions),
            "state": canonical_state(env),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--policy-a", default="pass", choices=[*POLICIES, "fuzz"])
    parser.add_argument("--policy-b", default="pass", choices=[*POLICIES, "fuzz"])
    parser.add_argument("--reference-root")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    stream = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
    try:
        for record in run(args.seed, args.policy_a, args.policy_b, args.reference_root):
            print(json.dumps(record, sort_keys=True, separators=(",", ":")), file=stream)
    finally:
        if args.output:
            stream.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

