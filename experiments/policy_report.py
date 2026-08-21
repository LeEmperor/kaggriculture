"""Run one oracle episode and print a compact, human-readable policy report."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from reference.oracle import Policy, run_game
from reference.run_game import load_policy


def _action_name(action: list[Any]) -> str:
    return str(action[0]) if action else "NONE"


def _format_counts(counts: Counter[str]) -> str:
    return ", ".join(
        f"{name}={count}" for name, count in sorted(counts.items())
    )


def _policy_state(policy: Policy) -> dict[str, Any] | None:
    """Return a policy's optional diagnostic snapshot without requiring it."""
    owner = getattr(policy, "__self__", None)
    state = getattr(owner, "state", None)
    snapshot: Callable[[], dict[str, Any]] | None = getattr(
        state, "snapshot", None
    )
    return snapshot() if callable(snapshot) else None


def render_report(
    *,
    seed: int,
    policy_a_name: str,
    policy_b_name: str,
    policy_a: Policy,
    records: list[dict[str, Any]],
    trace_path: Path,
) -> str:
    """Render the episode data most useful while iterating on a policy."""
    final = records[-1]
    rewards = [float(reward) for reward in final["reward"]]
    margin = rewards[0] - rewards[1]
    outcome = "WIN" if margin > 0 else "LOSS" if margin < 0 else "DRAW"

    farmer_actions: Counter[str] = Counter()
    market_orders: Counter[str] = Counter()
    market_units: Counter[str] = Counter()
    for record in records:
        action = record["actions"][0]
        farmer_actions[_action_name(action.get("farmer", []))] += 1
        for order in action.get("market", []):
            name = _action_name(order)
            market_orders[name] += 1
            if len(order) >= 3 and isinstance(order[2], (int, float)):
                market_units[name] += int(order[2])

    lines = [
        "KAGGRICULTURE POLICY REPORT",
        "",
        f"policy       {policy_a_name}",
        f"opponent     {policy_b_name}",
        f"seed         {seed}",
        f"result       {outcome} ({margin:+.0f})",
        f"reward       {rewards[0]:.0f} vs {rewards[1]:.0f}",
        f"turns        {len(records)}",
        f"status       {' / '.join(final['status'])}",
        "",
        "PLAYER A ACTIONS",
        f"farmer       {_format_counts(farmer_actions)}",
        f"market       {_format_counts(market_orders) or 'none'}",
    ]
    if market_units:
        lines.append(f"market units {_format_counts(market_units)}")

    if state := _policy_state(policy_a):
        lines.extend(["", "FINAL POLICY STATE"])
        width = max(len(key) for key in state)
        lines.extend(f"{key:<{width}}  {value}" for key, value in state.items())

    lines.extend(["", f"trace        {trace_path.resolve()}"])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument(
        "--policy-a",
        default="experiments.policies.myfirststrategy.policy",
    )
    parser.add_argument("--policy-b", default="reference.policies.pass_policy")
    parser.add_argument(
        "--trace",
        type=Path,
        default=Path("experiments/results/myfirststrategy-latest.jsonl"),
    )
    args = parser.parse_args()

    policy_a = load_policy(args.policy_a)
    records = run_game(
        args.seed,
        policy_a,
        load_policy(args.policy_b),
        trace_path=args.trace,
    )
    print(
        render_report(
            seed=args.seed,
            policy_a_name=args.policy_a,
            policy_b_name=args.policy_b,
            policy_a=policy_a,
            records=records,
            trace_path=args.trace,
        )
    )


if __name__ == "__main__":
    main()
