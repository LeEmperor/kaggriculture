"""Golden-vector recorder, checker, and live parity sweep for policy backends.

Step 2 of the work plan in ``docs/ocaml_migration_decisions.md``. The fixture
format is the one specified in ``docs/policy_encoding.md``: each vector is one
``(observation, previous_policy_state) -> (expected_action,
expected_next_policy_state)`` pair, recorded from real oracle episodes with the
hand-written ``MonocropReorder`` as the recording side. States are written in
the family-register vocabulary of ``family.json`` — the backend-neutral
artifact — so a future OCaml or C++ backend can consume the file without
knowing anything about Python ``PolicyState``.

Comparison discipline (the register audit in ``docs/policy_dsl.md``):
*decision* registers and the action are compared strictly and fail the run;
*telemetry* registers are compared loosely — every divergence is counted and
printed, but only fails under ``--strict-telemetry``. Loose is not ignored:
a backend that drifts in telemetry will say so on every check.

Subcommands:

    record   run seeded episodes, select vectors by signature coverage, and
             write the fixture file (checked in, unlike traces)
    check    replay the fixture file through a backend (``dsl`` or ``hand``)
    sweep    live turn-by-turn parity, hand-written vs DSL, over many seeds
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from experiments.policies.common.observations import current_tile, own_farm
from experiments.policies.monocrop_reorder.dsl_policy import load_interpreter
from experiments.policies.monocrop_reorder.policy import (
    CANDIDATE_PATH,
    POLICY_ID,
    SCHEMA_VERSION,
    MonocropReorder,
    PolicyState,
    load_parameters,
)
from reference.oracle import run_game
from submission.dsl.family import Family

FORMAT = "golden-vectors-v1"
GOLDEN_PATH = (
    Path(__file__).resolve().parent
    / "policies"
    / "monocrop_reorder"
    / "golden"
    / "vectors.json"
)
LOCK_PATH = Path(__file__).resolve().parents[1] / "reference" / "upstream.lock.json"

# Seed counts are chosen, not picked. Recording seeds 0..N-1 in order, the last
# seed to contribute a new vector signature is the saturation point; a 120-seed
# probe (2026-08-21) saturated at seed 44 and stayed quiet for the remaining 75
# seeds. The defaults keep a 2x margin over that point — the same margin the
# warning in ``record`` enforces. Re-probe with ``record --seeds 120`` after a
# behavioural change to confirm saturation has not moved.
DEFAULT_RECORD_SEEDS = 90
DEFAULT_SWEEP_SEEDS = 90
DEFAULT_PER_SIGNATURE = 8


def _family() -> Family:
    from experiments.policies.monocrop_reorder.dsl_policy import load_family

    return load_family()


def _exact_int(name: str, value: float) -> int:
    """Money-derived state is float-typed upstream but integral by construction."""
    number = float(value)
    if not number.is_integer():
        raise ValueError(f"{name} is fractional: {value!r}")
    return int(number)


def registers_from_hand_state(
    state: PolicyState, order: tuple[str, ...]
) -> dict[str, Any]:
    """Project the hand-written PolicyState onto the family-register vocabulary.

    The mapping is the one recorded in the fidelity notes of docs/policy_dsl.md:
    ``peak_wheat_price`` -> ``peak_price``, ``previous_money is None`` ->
    ``money_seen``, and ``last_farmer_action`` is dropped (telemetry, tuple-
    shaped, deliberately not a DSL register).
    """
    values: dict[str, Any] = {
        "mode": state.mode,
        "last_step": state.last_step,
        "requested_plant_actions": state.requested_plant_actions,
        "mode_entered_step": state.mode_entered_step,
        "money_seen": state.previous_money is not None,
        "previous_money": _exact_int("previous_money", state.previous_money or 0),
        "last_money_delta": _exact_int("last_money_delta", state.last_money_delta),
        "peak_price": state.peak_wheat_price,
        "requested_harvest_actions": state.requested_harvest_actions,
        "requested_sell_units": state.requested_sell_units,
    }
    return {name: values[name] for name in order}


def hand_state_from_registers(registers: dict[str, Any]) -> PolicyState:
    """The reverse projection, so the hand-written class can replay fixtures.

    ``last_farmer_action`` is unrecoverable from the register vocabulary; it is
    telemetry that no guard reads, so its init value is a safe stand-in.
    """
    return PolicyState(
        mode=registers["mode"],
        mode_entered_step=registers["mode_entered_step"],
        last_step=registers["last_step"],
        previous_money=(
            float(registers["previous_money"]) if registers["money_seen"] else None
        ),
        last_money_delta=float(registers["last_money_delta"]),
        peak_wheat_price=registers["peak_price"],
        requested_plant_actions=registers["requested_plant_actions"],
        requested_harvest_actions=registers["requested_harvest_actions"],
        requested_sell_units=registers["requested_sell_units"],
    )


def vector_signature(
    observation: dict[str, Any],
    previous: dict[str, Any],
    following: dict[str, Any],
    action: dict[str, Any],
) -> str:
    """A coarse behavioural fingerprint used to select vectors worth keeping.

    Two vectors with the same signature exercise the same mode pair, the same
    farmer verb on the same class of tile, the same set of market orders, and
    the same reset behaviour — the axes along which the cascade branches.
    """
    tile = current_tile(own_farm(observation))
    if isinstance(tile, dict) and tile.get("kind") == "PLANT":
        tile_class = "plant"
    elif tile is None:
        tile_class = "empty"
    else:
        tile_class = "other"
    market = ",".join(sorted(order[0] for order in action["market"])) or "-"
    parts = [
        previous["mode"],
        following["mode"],
        str(action["farmer"][0]),
        market,
        tile_class,
    ]
    if int(observation["step"]) == 0 and previous["last_step"] >= 0:
        parts.append("reset")
    return ":".join(parts)


@dataclass
class _RecordingPolicy:
    """Wraps the hand-written policy and captures one vector per turn."""

    policy: MonocropReorder
    order: tuple[str, ...]
    player: int
    seed: int = 0
    turn: int = 0
    vectors: list[dict[str, Any]] = field(default_factory=list)

    def start_episode(self, seed: int) -> None:
        # The policy instance is deliberately NOT reset: carrying it into the
        # next episode is what makes the step-0 reset path appear in fixtures.
        self.seed = seed
        self.turn = 0
        self.vectors = []

    def act(self, observation: dict[str, Any]) -> dict[str, Any]:
        previous = registers_from_hand_state(self.policy.state, self.order)
        action = self.policy.act(observation)
        following = registers_from_hand_state(self.policy.state, self.order)
        self.vectors.append(
            {
                "seed": self.seed,
                "player": self.player,
                "turn": self.turn,
                "signature": vector_signature(
                    observation, previous, following, action
                ),
                "observation": observation,
                "previous_policy_state": previous,
                "expected_action": action,
                "expected_next_policy_state": following,
            }
        )
        self.turn += 1
        return action


def record(seeds: int, per_signature: int, out: Path) -> int:
    family = _family()
    order = tuple(family.registers)
    recorders = [
        _RecordingPolicy(MonocropReorder(load_parameters()), order, player)
        for player in (0, 1)
    ]

    kept: dict[str, list[dict[str, Any]]] = {}
    first_seen: dict[str, int] = {}
    for seed in range(seeds):
        for recorder in recorders:
            recorder.start_episode(seed)
        run_game(seed, recorders[0].act, recorders[1].act)
        for vector in recorders[0].vectors + recorders[1].vectors:
            signature = vector["signature"]
            first_seen.setdefault(signature, seed)
            bucket = kept.setdefault(signature, [])
            if len(bucket) < per_signature:
                bucket.append(vector)

    saturation = max(first_seen.values())
    vectors = sorted(
        (vector for bucket in kept.values() for vector in bucket),
        key=lambda vector: (vector["seed"], vector["turn"], vector["player"]),
    )

    manifest = {
        "format": FORMAT,
        "policy": {
            "id": POLICY_ID,
            "schema_version": SCHEMA_VERSION,
            "parameters": json.loads(CANDIDATE_PATH.read_text())["parameters"],
        },
        "reference_commit": json.loads(LOCK_PATH.read_text())["commit"],
        "recorder": {
            "policy_module": "experiments.policies.monocrop_reorder.policy",
            "seeds": seeds,
            "per_signature_cap": per_signature,
            "signature_axes": "prev_mode:next_mode:farmer:market:tile[:reset]",
            "last_new_signature_seed": saturation,
        },
        "registers": {
            "decision": list(family.decision_registers()),
            "telemetry": [
                name
                for name in family.registers
                if name not in family.decision_registers()
            ],
        },
    }
    _write_document(out, manifest, vectors)

    print(f"recorded {len(vectors)} vectors across {len(kept)} signatures")
    print(f"seeds 0..{seeds - 1}; last new signature appeared at seed {saturation}")
    width = max(len(signature) for signature in kept)
    for signature in sorted(kept):
        print(
            f"  {signature:<{width}}  kept {len(kept[signature]):>2}  "
            f"first seed {first_seen[signature]}"
        )
    print(f"wrote {out} ({out.stat().st_size / 1e3:.0f} kB)")
    if saturation * 2 > seeds:
        print(
            f"WARNING: saturation at seed {saturation} is past half the range; "
            "record more seeds"
        )
        return 1
    return 0


def _write_document(
    path: Path, manifest: dict[str, Any], vectors: list[dict[str, Any]]
) -> None:
    """Valid JSON with one compact vector per line, so the file diffs by vector."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as out:
        out.write("{\n")
        for key, value in manifest.items():
            out.write(f'"{key}": {json.dumps(value)},\n')
        out.write('"vectors": [\n')
        for index, vector in enumerate(vectors):
            comma = "," if index + 1 < len(vectors) else ""
            out.write(json.dumps(vector, separators=(",", ":")) + comma + "\n")
        out.write("]\n}\n")


@dataclass
class Comparison:
    """One backend's disagreement tally against expectations."""

    turns: int = 0
    action_failures: int = 0
    decision_failures: int = 0
    telemetry_divergences: int = 0

    @property
    def failed(self) -> bool:
        return bool(self.action_failures or self.decision_failures)

    def compare(
        self,
        where: str,
        decision: frozenset[str],
        expected_action: Any,
        action: Any,
        expected_state: dict[str, Any],
        state: dict[str, Any],
        *,
        report: bool = True,
    ) -> None:
        self.turns += 1
        if action != expected_action:
            self.action_failures += 1
            if report:
                print(f"{where}: action {action!r} != expected {expected_action!r}")
        for name, expected in expected_state.items():
            got = state.get(name)
            if got == expected:
                continue
            if name in decision:
                self.decision_failures += 1
                if report:
                    print(f"{where}: decision {name} {got!r} != {expected!r}")
            else:
                self.telemetry_divergences += 1
                if report:
                    print(f"{where}: telemetry {name} {got!r} != {expected!r} (loose)")

    def summary(self, label: str) -> str:
        return (
            f"{label}: {self.turns} vectors, "
            f"{self.action_failures} action failures, "
            f"{self.decision_failures} decision-register failures, "
            f"{self.telemetry_divergences} telemetry divergences (loose)"
        )


def load_fixtures(path: Path = GOLDEN_PATH) -> dict[str, Any]:
    document = json.loads(path.read_text())
    if document.get("format") != FORMAT:
        raise ValueError(f"{path} is not a {FORMAT} file")
    policy = document.get("policy", {})
    if policy.get("id") != POLICY_ID or policy.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{path} was recorded for {policy.get('id')!r} "
            f"schema {policy.get('schema_version')!r}"
        )
    return document


def _dsl_stepper():
    interpreter = load_interpreter()

    def step(
        observation: dict[str, Any], registers: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        return interpreter.step(observation, dict(registers))

    return step


def _hand_stepper():
    parameters = load_parameters()
    order = tuple(_family().registers)

    def step(
        observation: dict[str, Any], registers: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        policy = MonocropReorder(parameters)
        policy.state = hand_state_from_registers(registers)
        action = policy.act(observation)
        return action, registers_from_hand_state(policy.state, order)

    return step


STEPPERS = {"dsl": _dsl_stepper, "hand": _hand_stepper}


def check(path: Path, backend: str, strict_telemetry: bool) -> int:
    document = load_fixtures(path)
    family = _family()
    decision = frozenset(family.decision_registers())
    registers = set(family.registers)
    step = STEPPERS[backend]()

    comparison = Comparison()
    for vector in document["vectors"]:
        if set(vector["previous_policy_state"]) != registers:
            raise ValueError(
                f"vector seed={vector['seed']} turn={vector['turn']} does not "
                "cover the family's registers; re-record the fixtures"
            )
        action, state = step(vector["observation"], vector["previous_policy_state"])
        where = (
            f"seed {vector['seed']} player {vector['player']} turn {vector['turn']}"
        )
        comparison.compare(
            where,
            decision,
            vector["expected_action"],
            action,
            vector["expected_next_policy_state"],
            state,
        )

    print(comparison.summary(f"check[{backend}]"))
    failed = comparison.failed or (
        strict_telemetry and comparison.telemetry_divergences > 0
    )
    return 1 if failed else 0


def sweep(seeds: int) -> int:
    family = _family()
    order = tuple(family.registers)
    decision = frozenset(family.decision_registers())
    hand = [MonocropReorder(load_parameters()) for _ in (0, 1)]
    dsl = [load_interpreter() for _ in (0, 1)]
    banks = [interpreter.initial_registers() for interpreter in dsl]
    comparison = Comparison()

    def side(player: int):
        def policy(observation: dict[str, Any]) -> Any:
            expected = hand[player].act(observation)
            action, banks[player] = dsl[player].step(observation, banks[player])
            comparison.compare(
                f"seed {seed} player {player} step {observation['step']}",
                decision,
                expected,
                action,
                registers_from_hand_state(hand[player].state, order),
                banks[player],
            )
            return expected

        return policy

    # Policy instances persist across seeds so the step-0 reset path is swept too.
    for seed in range(seeds):
        run_game(seed, side(0), side(1))

    print(comparison.summary(f"sweep[{seeds} seeds]"))
    return 1 if comparison.failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    recorder = commands.add_parser("record", help="record fixtures from episodes")
    recorder.add_argument("--seeds", type=int, default=DEFAULT_RECORD_SEEDS)
    recorder.add_argument(
        "--per-signature", type=int, default=DEFAULT_PER_SIGNATURE
    )
    recorder.add_argument("--out", type=Path, default=GOLDEN_PATH)

    checker = commands.add_parser("check", help="replay fixtures on a backend")
    checker.add_argument("--fixtures", type=Path, default=GOLDEN_PATH)
    checker.add_argument("--backend", choices=sorted(STEPPERS), default="dsl")
    checker.add_argument("--strict-telemetry", action="store_true")

    sweeper = commands.add_parser("sweep", help="live hand-vs-DSL parity")
    sweeper.add_argument("--seeds", type=int, default=DEFAULT_SWEEP_SEEDS)

    args = parser.parse_args()
    if args.command == "record":
        code = record(args.seeds, args.per_signature, args.out)
    elif args.command == "check":
        code = check(args.fixtures, args.backend, args.strict_telemetry)
    else:
        code = sweep(args.seeds)
    sys.exit(code)


if __name__ == "__main__":
    main()
