"""The Phase 4 differential runner: oracle half.

Drives the pinned upstream interpreter over generated action tapes and streams each
finished game to ``kag_sim differential``, which replays the same raw tape through the
OCaml engine and compares every post-turn state. The two halves run as a pipeline, so a
thousand-game gate never materializes a bundle on disk.

    python3 -m tools.differential run --games 1000
    python3 -m tools.differential run --games 200 --variety 1.0
    python3 -m tools.differential minimize --index 417

Every game is a pure function of ``(--master-seed, index)``: the game seed, both players'
styles, both tape seeds, and any configuration overrides. A divergence is therefore
replayable by index alone, which is what ``minimize`` consumes.

The trust gate (``docs/kaggriculture_gameplan.md`` Phase 4) wants at least 1,000 full
720-turn games over scripted and fuzz tapes with matching final balances, statuses and
rewards. ``run`` at its defaults is that population; ``--variety`` samples non-default
configurations instead, which are deliberately shorter so the space can be swept.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from reference import oracle
from tools import coverage as coverage_tags
from tools import tapes

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SIM = REPO_ROOT / "_build" / "default" / "fast_model" / "bin" / "kag_sim.exe"
DEFAULT_REPORT_DIR = REPO_ROOT / "experiments" / "results" / "differential"

# The engine's configuration surface; anything else upstream reads is framework timing.
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

RESOLVED_CURVE_KEYS = {
    "base",
    "I0",
    "T",
    "below_func",
    "below_target",
    "above_func",
    "above_target",
}


# ------------------------------------------------------------------ game specification


VARIETY_CHOICES: dict[str, list[Any]] = {
    "episodeSteps": [25, 49, 73, 121, 241],
    "boardSize": [4, 6, 7, 8, 9, 10, 12],
    "startingMoney": [0, 40, 500, 3000, 250_000],
    "maxMarketOrdersPerTurn": [1, 2, 5, 10, 25],
    "turnsPerDay": [1, 2, 3, 8, 24, 60],
    "shedCapacity": [1, 4, 25, 100, 5000],
    "weedSpawnChance": [0.0, 0.005, 0.25, 1.0],
    "townShopUnlockInterval": [1, 2, 3, 9999],
    "townShopSellInterval": [1, 4, 37],
    "townCenterSellInterval": [1, 6, 41],
    "farmHandCostMult": [1, 10, 250],
}

CURVE_SHAPES = ["linear", "sq", "sqrt", "log", "log10", "hinge"]


def _market_overrides(rng: random.Random) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for item in rng.sample(tapes.PRODUCTS, rng.randint(1, 4)):
        entry: dict[str, Any] = {}
        if rng.random() < 0.7:
            entry["below_func"] = rng.choice(CURVE_SHAPES)
            entry["below_target"] = round(rng.uniform(0.5, 30.0), 3)
        if rng.random() < 0.7:
            entry["above_func"] = rng.choice(CURVE_SHAPES)
            entry["above_target"] = round(rng.uniform(0.5, 30.0), 3)
        if rng.random() < 0.4:
            entry["base"] = rng.randint(1, 250)
        if rng.random() < 0.4:
            entry["I0"] = rng.randint(1, 20_000)
        if rng.random() < 0.4:
            entry["T"] = rng.randint(1, 5000)
        if entry:
            overrides[item] = entry

    if rng.random() < 0.5:
        # The stock curves cannot be driven to the price floor, so one product gets a
        # curve engineered to reach it: market inventory starts at I0, and with a linear
        # above-curve, T=1 and above_target=30 a single unit past I0 already prices under
        # the floor. This also covers the "sales at $1 do not add supply" branch.
        # FERTILIZER is the target because neither the town center nor any shop consumes
        # it, so a sale that crosses I0 leaves the price at the floor instead of having
        # town demand pull inventory straight back under it.
        overrides["FERTILIZER"] = {
            **overrides.get("FERTILIZER", {}),
            "I0": rng.randint(0, 4),
            "T": 1,
            "above_func": "linear",
            "above_target": 30.0,
        }
    return overrides


def game_spec(master_seed: int, index: int, *, variety: float) -> dict[str, Any]:
    """Everything about game ``index``, derived from the master seed alone."""
    rng = random.Random(f"kaggriculture-differential:{master_seed}:{index}")
    overrides: dict[str, Any] = {}
    if rng.random() < variety:
        for key, choices in VARIETY_CHOICES.items():
            if rng.random() < 0.45:
                overrides[key] = rng.choice(choices)
        if rng.random() < 0.35:
            market = _market_overrides(rng)
            if market:
                overrides["marketParams"] = market
        # A bankroll large enough for one order to reach upstream's 99,999-iteration
        # runaway guard, which is otherwise unreachable.
        if rng.random() < 0.08:
            overrides["startingMoney"] = 400_000_000
    return {
        "index": index,
        "seed": rng.randrange(1, 2**31 - 1),
        "styles": [rng.choice(tapes.STYLES), rng.choice(tapes.STYLES)],
        "tape_seeds": [rng.randrange(2**31), rng.randrange(2**31)],
        "overrides": overrides,
    }


# --------------------------------------------------------------------------- projection


def _sparse_tiles(farm: dict) -> list:
    """Only tiles that are neither empty nor LOCKED; which of the two an absent tile is
    follows from ``unlocked_quadrants``, which the digest also carries."""
    return [
        [x, y, tile]
        for y, row in enumerate(farm["tiles"])
        for x, tile in enumerate(row)
        if tile is not None and tile != "LOCKED"
    ]


def _market_state(diag: dict) -> dict:
    """``params`` is the resolved configuration echo, not state."""
    return {key: value for key, value in diag["market"].items() if key != "params"}


def digest(diag: dict) -> dict:
    """Every field the engine can mutate, plus the clock — matching
    ``kag_sim.ml``'s ``differential_digest``. Shed and seed dicts are dense upstream and
    dense arrays in the engine, so zero entries are dropped on both sides; farmer
    inventories are sparse in both and are compared including their key sets."""
    return {
        "farms": [
            {
                "money": farm["money"],
                "farmer": farm["farmer"],
                "hands": farm["hands"],
                "hires_today": farm["hires_today"],
                "unlocked_quadrants": farm["unlocked_quadrants"],
                "tiles": _sparse_tiles(farm),
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
        "market": _market_state(diag),
        "town": diag["town"],
        "day": diag["day"],
        "hour": diag["hour"],
    }


# ------------------------------------------------------------------------ oracle replay


class _Game:
    """A live oracle episode driven one turn at a time."""

    def __init__(self, spec: dict[str, Any]):
        self.interpreter = oracle.load_interpreter()
        self.env = oracle.OracleEnvironment({**spec["overrides"], "seed": spec["seed"]})
        self.state = [oracle._initial_agent_state(0), oracle._initial_agent_state(1)]
        self.env.state = self.state
        self.state = self.interpreter.interpreter(self.state, self.env)
        self.env.state = self.state
        self.state[0].observation.step = 0
        self.turn = 0

        self.configuration = {key: self.env.configuration[key] for key in SCALAR_KEYS}
        diag = oracle.diagnostic_state(self.state, self.env)
        resolved = diag["market"].get("params")
        if resolved is not None:
            for item, entry in resolved.items():
                assert set(entry) == RESOLVED_CURVE_KEYS, f"unexpected curve keys for {item}"
            self.configuration["marketParams"] = resolved

    @property
    def done(self) -> bool:
        return bool(self.env.done)

    def diagnostic(self) -> dict:
        return oracle.diagnostic_state(self.state, self.env)

    def apply(self, actions: list) -> dict:
        for player, action in enumerate(actions):
            self.state[player].action = oracle.structify(action)
        self.state = self.interpreter.interpreter(self.state, self.env)
        self.env.state = self.state
        self.state[0].observation.step = 0 if self.env.done else self.turn + 1
        self.turn += 1
        return self.diagnostic()

    def finish(self, tape: list, digests: list) -> dict:
        diag = self.diagnostic()
        return {
            "tape": tape,
            "digests": digests,
            # A tape that stops before the episode does exempts the reproducer from the
            # terminal comparisons, which only mean anything at the end of a game.
            "complete": bool(self.env.done),
            "final_diagnostic": {**diag, "market": _market_state(diag)},
            "final_status": [agent.status for agent in self.state],
            "final_reward": [agent.reward for agent in self.state],
        }


def record_game(spec: dict[str, Any]) -> dict[str, Any]:
    """Generate a tape and record the oracle's answer to it, in one pass."""
    game = _Game(spec)
    rngs = [random.Random(seed) for seed in spec["tape_seeds"]]
    tape: list = []
    digests: list = []
    while not game.done:
        diag = game.diagnostic()
        actions = [
            tapes.action_for(
                rngs[player],
                diag["farms"][player],
                diag["privates"][player],
                diag["market"],
                game.configuration,
                spec["styles"][player],
            )
            for player in (0, 1)
        ]
        digests.append(digest(game.apply(actions)))
        tape.append(actions)
    return {
        "index": spec["index"],
        "seed": spec["seed"],
        "styles": spec["styles"],
        "configuration": game.configuration,
        **game.finish(tape, digests),
    }


def replay_tape(spec: dict[str, Any], tape: list) -> dict[str, Any]:
    """Record the oracle's answer to a tape someone else built — the minimizer's path."""
    game = _Game(spec)
    digests: list = []
    used: list = []
    for actions in tape:
        if game.done:
            break
        digests.append(digest(game.apply(actions)))
        used.append(actions)
    return {
        "index": spec["index"],
        "seed": spec["seed"],
        "styles": spec["styles"],
        "configuration": game.configuration,
        **game.finish(used, digests),
    }


# ------------------------------------------------------------------------- the pipeline


class Verifier:
    """A ``kag_sim differential`` child, fed one game at a time."""

    def __init__(self, sim: Path, *, quiet: bool = False):
        self.process = subprocess.Popen(
            [str(sim), "differential", "--bundle", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL if quiet else None,
            text=True,
            encoding="utf-8",
        )

    def check(self, record: dict[str, Any]) -> dict[str, Any]:
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(json.dumps(record, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("kag_sim differential exited before answering")
        return json.loads(line)

    def close(self) -> None:
        assert self.process.stdin
        self.process.stdin.close()
        self.process.wait()


def _truncate(value: Any, limit: int = 400) -> Any:
    text = json.dumps(value, separators=(",", ":"))
    return value if len(text) <= limit else text[:limit] + "..."


def divergence_report(
    spec: dict[str, Any], record: dict[str, Any], verdict: dict[str, Any]
) -> dict[str, Any]:
    """The report shape Phase 4 specifies, plus enough to replay it."""
    turn = verdict.get("turn", -1)
    reference = record["digests"][turn] if 0 <= turn < len(record["digests"]) else None
    previous = (
        record["digests"][turn - 1]
        if turn > 0
        else "initialization (no earlier turn matched)"
    )
    return {
        "index": spec["index"],
        "seed": record["seed"],
        "styles": record["styles"],
        "configuration": record["configuration"],
        "stage": verdict.get("stage"),
        "turn": turn,
        "day": reference["day"] if isinstance(reference, dict) else None,
        "hour": reference["hour"] if isinstance(reference, dict) else None,
        "actions": record["tape"][turn] if 0 <= turn < len(record["tape"]) else None,
        "first_difference": verdict.get("path"),
        "reference_state": reference,
        "native_state": verdict.get("native"),
        "previous_matching_state": previous,
        "replay": f"python3 -m tools.differential minimize --index {spec['index']}",
    }


def _run_chunk(
    master_seed: int, indices: list[int], variety: float, sim: str
) -> tuple[int, list[dict[str, Any]], Counter]:
    verifier = Verifier(Path(sim))
    failures: list[dict[str, Any]] = []
    counters: Counter = Counter()
    try:
        for index in indices:
            spec = game_spec(master_seed, index, variety=variety)
            record = record_game(spec)
            counters.update(coverage_tags.coverage(record))
            verdict = verifier.check(record)
            if not verdict.get("ok"):
                failures.append(divergence_report(spec, record, verdict))
    finally:
        verifier.close()
    return len(indices), failures, counters


# ------------------------------------------------------------------------------ minimize


def _diverges(spec: dict[str, Any], tape: list, sim: Path) -> dict[str, Any] | None:
    record = replay_tape(spec, tape)
    if not record["tape"]:
        return None
    verifier = Verifier(sim, quiet=True)
    try:
        verdict = verifier.check(record)
    finally:
        verifier.close()
    return None if verdict.get("ok") else verdict


def minimize(spec: dict[str, Any], tape: list, sim: Path) -> tuple[list, dict[str, Any]]:
    """ddmin over turns: keep a turn's actions, or blank it to a mutual PASS. Blanking
    changes the reachable state, so a reduction counts only if some divergence survives."""
    blank = {"farmer": ["PASS"], "hands": [], "market": []}
    verdict = _diverges(spec, tape, sim)
    if verdict is None:
        raise SystemExit("the full tape does not diverge; nothing to minimize")

    # Everything after the first divergence is dead weight.
    tape = tape[: verdict["turn"] + 1]
    verdict = _diverges(spec, tape, sim) or verdict

    granularity = 2
    while granularity <= len(tape):
        size = max(1, len(tape) // granularity)
        reduced = False
        start = 0
        while start < len(tape):
            candidate = list(tape)
            for i in range(start, min(start + size, len(tape))):
                candidate[i] = [blank, blank]
            if candidate != tape:
                trial = _diverges(spec, candidate, sim)
                if trial is not None:
                    tape, verdict = candidate, trial
                    tape = tape[: verdict["turn"] + 1]
                    verdict = _diverges(spec, tape, sim) or verdict
                    reduced = True
                    granularity = max(granularity - 1, 2)
                    break
            start += size
        if not reduced:
            if granularity >= len(tape):
                break
            granularity = min(granularity * 2, len(tape))
    return tape, verdict


# ----------------------------------------------------------------------------- commands


def command_run(args: argparse.Namespace) -> int:
    sim = Path(args.sim)
    if not sim.exists():
        print(f"no simulator at {sim}; run `dune build` first", file=sys.stderr)
        return 2

    indices = (
        [int(part) for part in args.only.split(",")]
        if args.only
        else list(range(args.games))
    )
    jobs = max(1, args.jobs)
    chunks = [indices[i::jobs] for i in range(jobs)] if jobs > 1 else [indices]
    chunks = [chunk for chunk in chunks if chunk]

    done = 0
    failures: list[dict[str, Any]] = []
    counters: Counter = Counter()
    if len(chunks) == 1:
        verifier = Verifier(sim)
        try:
            for index in chunks[0]:
                spec = game_spec(args.master_seed, index, variety=args.variety)
                record = record_game(spec)
                counters.update(coverage_tags.coverage(record))
                verdict = verifier.check(record)
                done += 1
                if not verdict.get("ok"):
                    failures.append(divergence_report(spec, record, verdict))
                    print(
                        f"DIVERGED game {index} seed {record['seed']} "
                        f"turn {verdict.get('turn')} [{verdict.get('stage')}] "
                        f"{verdict.get('path')}",
                        file=sys.stderr,
                    )
                    if args.stop_on_first:
                        break
                if done % args.progress_every == 0:
                    print(f"  {done}/{len(indices)} games", file=sys.stderr, flush=True)
        finally:
            verifier.close()
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = [
                pool.submit(_run_chunk, args.master_seed, chunk, args.variety, str(sim))
                for chunk in chunks
            ]
            for future in futures:
                count, chunk_failures, chunk_counters = future.result()
                done += count
                failures.extend(chunk_failures)
                counters.update(chunk_counters)
                print(f"  {done}/{len(indices)} games", file=sys.stderr, flush=True)

    turns_note = "full-length" if not args.variety else f"variety={args.variety}"
    print(
        f"differential: {done} games ({turns_note}), {len(failures)} divergent",
        file=sys.stderr,
    )
    if args.coverage:
        print(coverage_tags.render(counters, variety=bool(args.variety)), file=sys.stderr)
    absent = coverage_tags.missing(counters, variety=bool(args.variety))
    if absent and args.require_coverage:
        # A population that never reached a rule proves nothing about that rule, so an
        # all-green differential over it is not a gate result.
        print(
            f"coverage: {len(absent)} required features never reached: "
            f"{', '.join(absent)}",
            file=sys.stderr,
        )
        return 1
    if failures:
        report_dir = Path(args.report)
        report_dir.mkdir(parents=True, exist_ok=True)
        for failure in failures:
            tag = "variety" if args.variety else "full"
            path = (
                report_dir
                / f"divergence-{tag}-{args.master_seed}-{failure['index']}.json"
            )
            path.write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n")
            print(f"  wrote {path}", file=sys.stderr)
        for failure in failures[:5]:
            print(
                f"  game {failure['index']} seed {failure['seed']} "
                f"styles {failure['styles']} turn {failure['turn']} "
                f"(day {failure['day']} hour {failure['hour']}): "
                f"{failure['first_difference']}",
                file=sys.stderr,
            )
        return 1
    return 0


def command_minimize(args: argparse.Namespace) -> int:
    sim = Path(args.sim)
    if not sim.exists():
        print(f"no simulator at {sim}; run `dune build` first", file=sys.stderr)
        return 2
    spec = game_spec(args.master_seed, args.index, variety=args.variety)
    record = record_game(spec)
    tape, verdict = minimize(spec, record["tape"], sim)
    minimal = replay_tape(spec, tape)
    report = divergence_report(spec, minimal, verdict)
    report["minimized_turns"] = len(tape)
    report["original_turns"] = len(record["tape"])

    report_dir = Path(args.report)
    report_dir.mkdir(parents=True, exist_ok=True)
    tag = "variety" if args.variety else "full"
    report_path = report_dir / f"minimized-{tag}-{args.master_seed}-{args.index}.json"
    bundle_path = (
        report_dir / f"minimized-{tag}-{args.master_seed}-{args.index}.bundle.jsonl"
    )
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    bundle_path.write_text(json.dumps(minimal, separators=(",", ":")) + "\n")
    print(
        f"minimized game {args.index} from {len(record['tape'])} to {len(tape)} turns\n"
        f"  report {report_path}\n"
        f"  replay {sim} differential --bundle {bundle_path}",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    # Shared options are attached to both levels so either ordering parses.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--sim", default=str(DEFAULT_SIM))
    common.add_argument("--master-seed", type=int, default=20260821)
    common.add_argument("--variety", type=float, default=0.0)
    common.add_argument("--report", default=str(DEFAULT_REPORT_DIR))

    parser = argparse.ArgumentParser(
        prog="python3 -m tools.differential", parents=[common]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser(
        "run", parents=[common], help="record and verify a population of games"
    )
    run.add_argument("--games", type=int, default=1000)
    run.add_argument("--only", help="comma-separated game indices instead of a range")
    run.add_argument("--jobs", type=int, default=1)
    run.add_argument("--progress-every", type=int, default=25)
    run.add_argument("--stop-on-first", action="store_true")
    run.add_argument("--coverage", action="store_true", help="print the coverage table")
    run.add_argument(
        "--require-coverage",
        action="store_true",
        help="fail unless the population reached every feature the gate requires",
    )
    run.set_defaults(handler=command_run)

    shrink = sub.add_parser(
        "minimize", parents=[common], help="shrink one diverging game to a reproducer"
    )
    shrink.add_argument("--index", type=int, required=True)
    shrink.set_defaults(handler=command_minimize)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
