"""Benchmark one policy workload on the official and native backends.

The workload manifest is the single source for the family, candidate, opponent
population, and seeds. Both backends run every matchup in both player seats. The
official backend executes the pinned Python interpreter and calls the OCaml DSL policy
over its subprocess protocol; the native backend executes ``kag_sim evaluate``.

Run an optimized build first:

    dune build --profile release
    python3 -m tools.benchmark_policy run

Raw repetitions and their metadata are retained under the gitignored
``experiments/results/benchmarks`` directory by default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from experiments.ocaml_backend import OcamlPolicy
from reference.oracle import evaluate_game, load_interpreter
from reference.policies.pass_policy import agent as pass_policy

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKLOAD = ROOT / "experiments/benchmarks/phase5_policy/workload.json"
DEFAULT_NATIVE_EXE = ROOT / "_build/default/fast_model/bin/kag_sim.exe"
DEFAULT_POLICY_EXE = ROOT / "_build/default/interp/bin/kag_policy.exe"
CORRECTNESS_FIELDS = (
    "games",
    "turns",
    "wins",
    "draws",
    "losses",
    "candidate_money_total",
    "opponent_money_total",
)
METRIC_FIELDS = (
    "wall_seconds",
    "games_per_second",
    "turns_per_second",
    "nanoseconds_per_turn",
)


@dataclass(frozen=True)
class Workload:
    manifest: Path
    name: str
    family: Path
    candidate: Path
    opponents_file: Path
    opponents: tuple[str | Path, ...]
    seeds_file: Path
    seeds: tuple[int, ...]

    @property
    def games(self) -> int:
        return len(self.opponents) * len(self.seeds) * 2


def _resolve(base: Path, raw: object, field: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"workload {field} must be a non-empty path string")
    path = Path(raw)
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _read_lines(path: Path) -> list[str]:
    return [
        line
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    ]


def load_workload(path: Path) -> Workload:
    manifest = path.resolve()
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("workload schema_version must be 1")
    base = manifest.parent
    family = _resolve(base, document.get("family"), "family")
    candidate = _resolve(base, document.get("candidate"), "candidate")
    opponents_file = _resolve(base, document.get("opponents"), "opponents")
    seeds_file = _resolve(base, document.get("seeds"), "seeds")
    for required in (family, candidate, opponents_file, seeds_file):
        if not required.is_file():
            raise FileNotFoundError(required)

    opponent_document = json.loads(opponents_file.read_text(encoding="utf-8"))
    if not isinstance(opponent_document, list) or not opponent_document:
        raise ValueError("opponents must be a non-empty JSON array")
    opponents: list[str | Path] = []
    for raw in opponent_document:
        if raw == "pass":
            opponents.append("pass")
        elif isinstance(raw, str):
            opponent = Path(raw)
            if not opponent.is_absolute():
                opponent = (opponents_file.parent / opponent).resolve()
            if not opponent.is_file():
                raise FileNotFoundError(opponent)
            opponents.append(opponent)
        else:
            raise ValueError("each opponent must be a candidate path or 'pass'")

    try:
        seeds = tuple(int(raw) for raw in _read_lines(seeds_file))
    except ValueError as error:
        raise ValueError(f"invalid integer in {seeds_file}") from error
    if not seeds:
        raise ValueError("seeds must not be empty")
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be unique")

    name = document.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("workload name must be a non-empty string")
    return Workload(
        manifest=manifest,
        name=name,
        family=family,
        candidate=candidate,
        opponents_file=opponents_file,
        opponents=tuple(opponents),
        seeds_file=seeds_file,
        seeds=seeds,
    )


def _record_result(totals: dict[str, Any], candidate: float, opponent: float) -> None:
    totals["games"] += 1
    totals["candidate_money_total"] += candidate
    totals["opponent_money_total"] += opponent
    if candidate > opponent:
        totals["wins"] += 1
    elif candidate < opponent:
        totals["losses"] += 1
    else:
        totals["draws"] += 1


def run_python_backend(workload: Workload, policy_exe: Path) -> dict[str, Any]:
    """Run the compact official-oracle side once and return one raw measurement."""

    # Interpreter loading and policy-process startup are setup, just as family/candidate
    # loading occurs before kag_sim's timer. Warmup repetitions handle filesystem caches.
    load_interpreter()
    candidate = OcamlPolicy(
        workload.family,
        workload.candidate,
        executable=policy_exe,
    )
    opponent_processes: list[OcamlPolicy | None] = []
    try:
        for opponent in workload.opponents:
            opponent_processes.append(
                None
                if opponent == "pass"
                else OcamlPolicy(workload.family, opponent, executable=policy_exe)
            )

        totals: dict[str, Any] = {
            "backend": "official-python-ocaml-subprocess",
            "threads": 1,
            "games": 0,
            "turns": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "candidate_money_total": 0.0,
            "opponent_money_total": 0.0,
        }
        started = time.perf_counter()
        for opponent_process in opponent_processes:
            opponent_policy = (
                pass_policy if opponent_process is None else opponent_process.act
            )
            for seed in workload.seeds:
                as_a = evaluate_game(seed, candidate.act, opponent_policy)
                if as_a.status != ("DONE", "DONE"):
                    raise RuntimeError(f"seed {seed} did not terminate: {as_a.status}")
                totals["turns"] += as_a.turns
                _record_result(totals, as_a.final_money[0], as_a.final_money[1])

                as_b = evaluate_game(seed, opponent_policy, candidate.act)
                if as_b.status != ("DONE", "DONE"):
                    raise RuntimeError(f"seed {seed} did not terminate: {as_b.status}")
                totals["turns"] += as_b.turns
                _record_result(totals, as_b.final_money[1], as_b.final_money[0])
        seconds = time.perf_counter() - started
    finally:
        candidate.close()
        for process in opponent_processes:
            if process is not None:
                process.close()

    totals.update(
        {
            "wall_seconds": seconds,
            "games_per_second": totals["games"] / seconds,
            "turns_per_second": totals["turns"] / seconds,
            "nanoseconds_per_turn": seconds * 1.0e9 / totals["turns"],
        }
    )
    games = totals["games"]
    totals["mean_candidate_money"] = totals["candidate_money_total"] / games
    totals["mean_opponent_money"] = totals["opponent_money_total"] / games
    totals["mean_margin"] = (
        totals["candidate_money_total"] - totals["opponent_money_total"]
    ) / games
    return totals


def _native_command(
    workload: Workload, native_exe: Path, threads: int, copies: int = 1
) -> list[str]:
    return [
        str(native_exe),
        "evaluate",
        "--family",
        str(workload.family),
        "--candidate",
        str(workload.candidate),
        "--opponents",
        str(workload.opponents_file),
        "--seeds",
        str(workload.seeds_file),
        "--threads",
        str(threads),
        "--copies",
        str(copies),
    ]


def _run_command(command: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    controller_seconds = time.perf_counter() - started
    measurement = json.loads(completed.stdout)
    measurement["controller_wall_seconds"] = controller_seconds
    return measurement


def _python_command(workload: Workload, policy_exe: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "tools.benchmark_policy",
        "_python",
        "--workload",
        str(workload.manifest),
        "--policy-exe",
        str(policy_exe),
    ]


def _correctness_signature(measurement: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(measurement[field] for field in CORRECTNESS_FIELDS)


def _summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        field: {
            "median": statistics.median(float(run[field]) for run in runs),
            "min": min(float(run[field]) for run in runs),
            "max": max(float(run[field]) for run in runs),
        }
        for field in METRIC_FIELDS
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _version(command: list[str]) -> str:
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _git_head() -> str:
    return _version(["git", "rev-parse", "HEAD"])


def _worktree_dirty() -> bool:
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return True
    return bool(completed.stdout.strip())


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.partition(":")[2].strip()
    return platform.processor() or "unknown"


def _policy_metadata(path: Path) -> dict[str, Any]:
    candidate = json.loads(path.read_text(encoding="utf-8"))
    return {
        "id": str(candidate.get("policy_id", "unknown")),
        "parameters": candidate.get("parameters", {}),
    }


def _artifact(
    workload: Workload,
    protocol: dict[str, Any],
    runs: dict[str, list[dict[str, Any]]],
    summaries: dict[str, dict[str, Any]],
    native_exe: Path,
    policy_exe: Path,
) -> dict[str, Any]:
    lock = json.loads((ROOT / "reference/upstream.lock.json").read_text())
    source_head = _git_head()
    worktree_dirty = _worktree_dirty()
    opponent_labels = [str(value) for value in workload.opponents]
    scalar_python = summaries["official-python-ocaml-subprocess"]
    scalar_native = summaries["ocaml-native-scalar-comparison"]
    speedup = (
        scalar_python["wall_seconds"]["median"]
        / scalar_native["wall_seconds"]["median"]
    )
    scaling_base = summaries["ocaml-native-scaling-threads-1"]
    scaling: dict[str, Any] = {}
    for name, summary in summaries.items():
        prefix = "ocaml-native-scaling-threads-"
        if not name.startswith(prefix):
            continue
        threads = int(name.removeprefix(prefix))
        throughput_speedup = (
            summary["turns_per_second"]["median"]
            / scaling_base["turns_per_second"]["median"]
        )
        scaling[str(threads)] = {
            "speedup": throughput_speedup,
            "parallel_efficiency": throughput_speedup / threads,
        }
    return {
        "project_commit": "WORKTREE" if worktree_dirty else source_head,
        "upstream_commit": lock["commit"],
        "simulator": "policy-backend-comparison",
        "build": {
            "compiler": f"CPython {platform.python_version()}; OCaml {_version(['ocamlc', '-version'])}",
            "flags": ["dune", "--profile", "release"],
        },
        "policy_a": _policy_metadata(workload.candidate),
        "policy_b": {"id": "opponent-population", "parameters": {}},
        "opponents": opponent_labels,
        "seeds": list(workload.seeds),
        "player_positions": ["a-first", "b-first"],
        "machine": {
            "platform": platform.platform(),
            "cpu": _cpu_model(),
            "logical_cpus": os.cpu_count(),
        },
        "recorded_at": datetime.now(UTC).isoformat(),
        "results": {
            "schema_version": 1,
            "source_revision": {
                "head": source_head,
                "worktree_dirty": worktree_dirty,
            },
            "workload": {
                "name": workload.name,
                "games_per_repetition": workload.games,
                "family": str(workload.family.relative_to(ROOT)),
                "candidate": str(workload.candidate.relative_to(ROOT)),
                "opponents_file": str(workload.opponents_file.relative_to(ROOT)),
                "seeds_file": str(workload.seeds_file.relative_to(ROOT)),
                "sha256": {
                    "family": _sha256(workload.family),
                    "candidate": _sha256(workload.candidate),
                    "opponents": _sha256(workload.opponents_file),
                    "seeds": _sha256(workload.seeds_file),
                },
            },
            "protocol": protocol,
            "executables": {
                "native": str(native_exe),
                "native_sha256": _sha256(native_exe),
                "policy_subprocess": str(policy_exe),
                "policy_subprocess_sha256": _sha256(policy_exe),
            },
            "correctness": {
                "matched": True,
                "fields": list(CORRECTNESS_FIELDS),
                "signature": list(_correctness_signature(runs[next(iter(runs))][0])),
            },
            "raw_runs": runs,
            "summaries": summaries,
            "scalar_speedup_native_over_python": speedup,
            "native_thread_scaling": scaling,
        },
    }


def run_benchmark(args: argparse.Namespace) -> int:
    workload = load_workload(args.workload)
    native_exe = args.native_exe.resolve()
    policy_exe = args.policy_exe.resolve()
    for executable in (native_exe, policy_exe):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise FileNotFoundError(
                f"{executable} is not executable; run 'dune build --profile release'"
            )
    threads = tuple(dict.fromkeys(args.threads))
    if not threads or any(value <= 0 for value in threads):
        raise ValueError("--threads values must be positive")
    if 1 not in threads:
        threads = (1, *threads)

    python_key = "official-python-ocaml-subprocess"
    scalar_native_key = "ocaml-native-scalar-comparison"
    native_keys = {value: f"ocaml-native-scaling-threads-{value}" for value in threads}
    commands = {
        python_key: _python_command(workload, policy_exe),
        scalar_native_key: _native_command(workload, native_exe, 1),
        **{
            native_keys[value]: _native_command(
                workload, native_exe, value, args.scaling_copies
            )
            for value in threads
        },
    }

    env = os.environ.copy()
    env["KAG_POLICY_EXE"] = str(policy_exe)
    for _ in range(args.warmups):
        for command in commands.values():
            _run_command(command, env)

    runs = {name: [] for name in commands}
    names = list(commands)
    for repetition in range(args.repetitions):
        ordered = names if repetition % 2 == 0 else list(reversed(names))
        for name in ordered:
            measurement = _run_command(commands[name], env)
            measurement["repetition"] = repetition + 1
            runs[name].append(measurement)

    scalar_expected = _correctness_signature(runs[python_key][0])
    for name in (python_key, scalar_native_key):
        backend_runs = runs[name]
        for measurement in backend_runs:
            actual = _correctness_signature(measurement)
            if actual != scalar_expected:
                raise RuntimeError(
                    f"backend results differ for {name}: "
                    f"{actual!r} != {scalar_expected!r}"
                )
    scaling_expected = tuple(
        value * args.scaling_copies for value in scalar_expected
    )
    for name in native_keys.values():
        for measurement in runs[name]:
            actual = _correctness_signature(measurement)
            if actual != scaling_expected:
                raise RuntimeError(
                    f"backend results differ for {name}: "
                    f"{actual!r} != {scaling_expected!r}"
                )

    summaries = {name: _summarize(backend_runs) for name, backend_runs in runs.items()}
    protocol = {
        "warmups": args.warmups,
        "repetitions": args.repetitions,
        "thread_counts": list(threads),
        "scaling_workload_copies": args.scaling_copies,
        "timing_scope": "game loops only; family/candidate/process startup excluded",
        "run_order": "alternating forward/reverse by repetition",
    }
    artifact = _artifact(
        workload,
        protocol,
        runs,
        summaries,
        native_exe,
        policy_exe,
    )
    output = args.output
    if output is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        output = ROOT / "experiments/results/benchmarks" / f"{workload.name}-{stamp}.json"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")

    print(f"raw_results={output}")
    print(f"games_per_repetition={workload.games}")
    for name, summary in summaries.items():
        print(
            f"{name}: median_seconds={summary['wall_seconds']['median']:.6f} "
            f"games_per_second={summary['games_per_second']['median']:.3f} "
            f"turns_per_second={summary['turns_per_second']['median']:.3f} "
            f"nanoseconds_per_turn={summary['nanoseconds_per_turn']['median']:.3f}"
        )
    print(
        "scalar_speedup_native_over_python="
        f"{artifact['results']['scalar_speedup_native_over_python']:.3f}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run warmups and retained repetitions")
    run.add_argument("--workload", type=Path, default=DEFAULT_WORKLOAD)
    run.add_argument("--warmups", type=int, default=1)
    run.add_argument("--repetitions", type=int, default=5)
    run.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4, 8])
    run.add_argument("--scaling-copies", type=int, default=50)
    run.add_argument("--native-exe", type=Path, default=DEFAULT_NATIVE_EXE)
    run.add_argument("--policy-exe", type=Path, default=DEFAULT_POLICY_EXE)
    run.add_argument("--output", type=Path)
    run.set_defaults(handler=run_benchmark)

    python_backend = subparsers.add_parser("_python", help=argparse.SUPPRESS)
    python_backend.add_argument("--workload", type=Path, required=True)
    python_backend.add_argument("--policy-exe", type=Path, required=True)
    python_backend.set_defaults(
        handler=lambda args: print(
            json.dumps(
                run_python_backend(
                    load_workload(args.workload), args.policy_exe.resolve()
                ),
                sort_keys=True,
            )
        )
        or 0
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if getattr(args, "warmups", 0) < 0:
        raise ValueError("--warmups must be non-negative")
    if getattr(args, "repetitions", 1) <= 0:
        raise ValueError("--repetitions must be positive")
    if getattr(args, "scaling_copies", 1) <= 0:
        raise ValueError("--scaling-copies must be positive")
    raise SystemExit(args.handler(args))


if __name__ == "__main__":
    main()
