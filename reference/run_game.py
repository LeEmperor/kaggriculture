"""Run deterministic policies in the pinned official interpreter."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any

from reference.oracle import Policy, run_game


def load_policy(module_name: str) -> Policy:
    module = importlib.import_module(module_name)
    factory: Any = getattr(module, "make_policy", None)
    if callable(factory):
        policy = factory()
        if not callable(policy):
            raise TypeError(f"{module_name}.make_policy() must return a callable")
        return policy
    policy: Any = getattr(module, "agent", None)
    if not callable(policy):
        raise TypeError(f"{module_name} must export a callable named 'agent'")
    return policy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--policy-a", default="reference.policies.pass_policy")
    parser.add_argument("--policy-b", default="reference.policies.pass_policy")
    parser.add_argument("--trace", type=Path, required=True)
    args = parser.parse_args()

    records = run_game(
        args.seed,
        load_policy(args.policy_a),
        load_policy(args.policy_b),
        trace_path=args.trace,
    )
    final = records[-1]
    print(
        json.dumps(
            {
                "trace": str(args.trace),
                "turns": len(records),
                "status": final["status"],
                "reward": final["reward"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
