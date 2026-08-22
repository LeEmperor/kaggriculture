"""Run the native evaluation layer and record the result as a provenanced artifact.

``kag_sim league`` and ``kag_sim evaluate`` produce the measurement; this adds
everything needed to know later whether the measurement can be believed — the project
and upstream commits, the host, the toolchain, and a digest of every input file
including the seed split. The split matters most: a promotion decision may cite only a
result measured on ``validation``, and the artifact is what says which split was used.

Commands::

    python3 -m tools.league run       --split training     # the population against itself
    python3 -m tools.league evaluate  --split validation --family F --candidate C
    python3 -m tools.league promote   --challenger A.json --incumbent B.json

``promote`` applies the rule in docs/evaluation_protocol.md to two artifacts. The rule
is executable on purpose: a predeclared confidence rule that lives only in prose is one
a tired human applies loosely at the moment it matters most.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools import seed_splits

ROOT = Path(__file__).resolve().parents[1]
NATIVE_EXE = ROOT / "_build/default/fast_model/bin/kag_sim.exe"
SCHEMA = ROOT / "experiments/evaluation.schema.json"
RESULTS = ROOT / "experiments/results/leagues"
DEFAULT_ENTRANTS = ROOT / "experiments/leagues/baseline_v1/entrants.json"

# --- the promotion rule, as numbers -----------------------------------------------
# See docs/evaluation_protocol.md for what each threshold is for and why it is here.
PROMOTION = {
    "split": "validation",
    "min_score_rate": 0.55,
    "min_win_rate_ci_low": 0.50,
    "min_margin_ci_low": 0.0,
    "max_opponent_score_regression": 0.10,
    "min_worst_matchup_score": 0.20,
    "max_catastrophic_loss_rate": 0.10,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(
            command, cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _git_head() -> str:
    return _run(["git", "rev-parse", "HEAD"])


def _worktree_dirty() -> bool:
    return bool(_run(["git", "status", "--porcelain"]) not in ("", "unavailable"))


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.partition(":")[2].strip()
    return platform.processor() or "unknown"


def _seed_file(args: argparse.Namespace) -> tuple[Path, str]:
    """The seed file and the name of the split it came from.

    An explicit ``--seeds`` outside the immutable splits is labelled ``ad-hoc``; the
    promotion check refuses such an artifact, which is the point of labelling it.
    """
    if args.seeds:
        path = Path(args.seeds).resolve()
        for split, _ in seed_splits.SPLITS:
            if path == seed_splits.path_for(split).resolve():
                return path, split
        return path, "ad-hoc"
    return seed_splits.path_for(args.split).resolve(), args.split


def _provenance(kind: str, seed_file: Path, split: str, inputs: dict[str, Path]) -> dict[str, Any]:
    lock = json.loads((ROOT / "reference/upstream.lock.json").read_text())
    head = _git_head()
    dirty = _worktree_dirty()
    digests = {name: _sha256(path) for name, path in inputs.items() if path.is_file()}
    digests["seeds"] = _sha256(seed_file)
    return {
        "kind": kind,
        "schema_version": 1,
        "project_commit": "WORKTREE" if dirty else head,
        "upstream_commit": lock["commit"],
        "simulator": "ocaml-native",
        "build": {
            "compiler": f"OCaml {_run(['ocamlc', '-version'])}",
            "flags": ["dune", "--profile", "release"],
        },
        "machine": {
            "platform": platform.platform(),
            "cpu": _cpu_model(),
            "logical_cpus": os.cpu_count(),
        },
        "recorded_at": datetime.now(UTC).isoformat(),
        "player_positions": ["a-first", "b-first"],
        "seed_split": {
            "name": split,
            "version": seed_splits.VERSION,
            "file": _relative(seed_file),
            "sha256": digests["seeds"],
            "seeds": len(seed_splits.load(split)) if split != "ad-hoc" else _count(seed_file),
        },
        "inputs": {
            **{name: _relative(path) for name, path in inputs.items()},
            "sha256": digests,
        },
    }


def _relative(path: Path) -> str:
    """Repo-relative when it can be — an ad-hoc seed file may live anywhere."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _count(path: Path) -> int:
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def _native(args: list[str]) -> dict[str, Any]:
    if not NATIVE_EXE.is_file():
        raise SystemExit(
            f"{NATIVE_EXE} is missing; run `dune build --profile release` first"
        )
    completed = subprocess.run(
        [str(NATIVE_EXE), *args], cwd=ROOT, check=False, capture_output=True, text=True
    )
    sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        raise SystemExit(f"kag_sim exited {completed.returncode}")
    return json.loads(completed.stdout)


def _validate(document: dict[str, Any]) -> None:
    """Validate against experiments/evaluation.schema.json when jsonschema is available.

    The dependency is optional on purpose — the repository's Python side is
    standard-library only outside slow_model/ — so a missing validator degrades to a
    required-key check rather than skipping silently.
    """
    schema = json.loads(SCHEMA.read_text())
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        missing = [key for key in schema["required"] if key not in document]
        if missing:
            raise SystemExit(f"artifact is missing required keys: {missing}") from None
        print("note: jsonschema not installed; checked required keys only", file=sys.stderr)
        return
    jsonschema.validate(document, schema)


def _write(document: dict[str, Any], label: str, out: str | None) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    path = Path(out) if out else RESULTS / f"{label}-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=False) + "\n")
    return path


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    header = (
        "| Entrant | Score | Win rate | 95% CI | Margin mean | Margin median "
        "| p5 | p95 | Catastrophic | Worst matchup |\n"
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
    )
    lines = [header]
    for row in rows:
        overall = row["overall"]
        low, high = overall["win_rate_ci95"]
        worst = row.get("worst_matchup") or {}
        lines.append(
            "| `{id}` | {score:.3f} | {win:.3f} | {low:.3f}–{high:.3f} | {mean:,.0f} "
            "| {median:,.0f} | {p5:,.0f} | {p95:,.0f} | {cat:.1%} | {worst} |".format(
                id=row["id"],
                score=overall["score_rate"],
                win=overall["win_rate"],
                low=low,
                high=high,
                mean=overall["margin_mean"],
                median=overall["margin_median"],
                p5=overall["margin_p5"],
                p95=overall["margin_p95"],
                cat=overall["catastrophic_loss_rate"],
                worst=(
                    f"`{worst['opponent']}` ({worst['score_rate']:.3f})" if worst else "—"
                ),
            )
        )
    return "\n".join(lines)


def command_run(args: argparse.Namespace) -> int:
    seed_file, split = _seed_file(args)
    entrants = Path(args.entrants).resolve()
    label = args.label or f"baseline-league-{split}"
    native = _native(
        [
            "league",
            "--entrants", str(entrants),
            "--seeds", str(seed_file),
            "--threads", str(args.threads),
            "--label", label,
        ]
    )
    document = _provenance(
        "league",
        seed_file,
        split,
        {"entrants_file": entrants, "executable": NATIVE_EXE},
    )
    document["native"] = native
    _validate(document)
    path = _write(document, label, args.out)
    print(_markdown_table(native["table"]))
    print(f"\nartifact: {_relative(path)}", file=sys.stderr)
    return 0


def command_evaluate(args: argparse.Namespace) -> int:
    seed_file, split = _seed_file(args)
    opponents = Path(args.opponents).resolve()
    inputs: dict[str, Path] = {"opponents_file": opponents, "executable": NATIVE_EXE}
    native_args = [
        "evaluate",
        "--opponents", str(opponents),
        "--seeds", str(seed_file),
        "--threads", str(args.threads),
        "--coverage",
    ]
    if args.baseline:
        label = args.label or f"{args.baseline}-{split}"
        native_args += ["--baseline", args.baseline]
    else:
        family = Path(args.family).resolve()
        candidate = Path(args.candidate).resolve()
        inputs["family"] = family
        inputs["candidate"] = candidate
        label = args.label or f"{candidate.stem}-{split}"
        native_args += ["--family", str(family), "--candidate", str(candidate)]
    native_args += ["--label", label]
    native = _native(native_args)
    document = _provenance("evaluation", seed_file, split, inputs)
    document["native"] = native
    _validate(document)
    path = _write(document, label, args.out)
    overall = native["overall"]
    print(
        f"{native['policy']['id']}: score {overall['score_rate']:.3f}, "
        f"win {overall['win_rate']:.3f} "
        f"(95% CI {overall['win_rate_ci95'][0]:.3f}-{overall['win_rate_ci95'][1]:.3f}), "
        f"margin mean {overall['margin_mean']:,.0f}, "
        f"catastrophic {overall['catastrophic_loss_rate']:.1%}"
    )
    print(f"artifact: {_relative(path)}", file=sys.stderr)
    return 0


def promotion_verdict(challenger: dict[str, Any], incumbent: dict[str, Any]) -> tuple[bool, list[str]]:
    """Apply docs/evaluation_protocol.md's rule. Returns (promote?, reasons)."""
    reasons: list[str] = []

    for name, artifact in (("challenger", challenger), ("incumbent", incumbent)):
        split = artifact["seed_split"]["name"]
        if split != PROMOTION["split"]:
            reasons.append(
                f"{name} was measured on the {split!r} split, not {PROMOTION['split']!r}"
            )
    if challenger["seed_split"].get("sha256") != incumbent["seed_split"].get("sha256"):
        reasons.append("the two artifacts used different seed files")
    if challenger["inputs"]["sha256"].get("opponents_file") != incumbent["inputs"][
        "sha256"
    ].get("opponents_file"):
        reasons.append("the two artifacts used different opponent populations")

    new = challenger["native"]["overall"]
    old = incumbent["native"]["overall"]
    if new["score_rate"] < PROMOTION["min_score_rate"]:
        reasons.append(
            f"score rate {new['score_rate']:.3f} is below {PROMOTION['min_score_rate']}"
        )
    if new["win_rate_ci95"][0] < PROMOTION["min_win_rate_ci_low"]:
        reasons.append(
            f"win-rate 95% CI lower bound {new['win_rate_ci95'][0]:.3f} is below "
            f"{PROMOTION['min_win_rate_ci_low']}"
        )
    if new["margin_mean_ci95"][0] <= PROMOTION["min_margin_ci_low"]:
        reasons.append(
            f"margin 95% CI lower bound {new['margin_mean_ci95'][0]:,.0f} does not clear "
            f"{PROMOTION['min_margin_ci_low']}"
        )
    if new["margin_mean_ci95"][0] <= old["margin_mean"]:
        reasons.append(
            "the challenger's margin CI does not clear the incumbent's mean margin "
            f"({new['margin_mean_ci95'][0]:,.0f} vs {old['margin_mean']:,.0f})"
        )
    if new["catastrophic_loss_rate"] > PROMOTION["max_catastrophic_loss_rate"]:
        reasons.append(
            f"catastrophic-loss rate {new['catastrophic_loss_rate']:.1%} exceeds "
            f"{PROMOTION['max_catastrophic_loss_rate']:.0%}"
        )

    worst = challenger["native"].get("worst_matchup") or {}
    if worst and worst["score_rate"] < PROMOTION["min_worst_matchup_score"]:
        reasons.append(
            f"worst matchup {worst['opponent']} at {worst['score_rate']:.3f} is below "
            f"{PROMOTION['min_worst_matchup_score']}"
        )

    for opponent, new_summary in challenger["native"]["by_opponent"].items():
        old_summary = incumbent["native"]["by_opponent"].get(opponent)
        if old_summary is None:
            reasons.append(f"the incumbent has no result against {opponent}")
            continue
        drop = old_summary["score_rate"] - new_summary["score_rate"]
        if drop > PROMOTION["max_opponent_score_regression"]:
            reasons.append(
                f"severe regression against {opponent}: score rate fell "
                f"{drop:.3f} (limit {PROMOTION['max_opponent_score_regression']})"
            )
    return not reasons, reasons


def command_promote(args: argparse.Namespace) -> int:
    challenger = json.loads(Path(args.challenger).read_text())
    incumbent = json.loads(Path(args.incumbent).read_text())
    promote, reasons = promotion_verdict(challenger, incumbent)
    if promote:
        print("PROMOTE: every criterion in docs/evaluation_protocol.md is met")
        return 0
    print("HOLD: the challenger does not meet the promotion rule")
    for reason in reasons:
        print(f"  - {reason}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def seeds_arguments(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--split",
            choices=[name for name, _ in seed_splits.SPLITS],
            default="training",
        )
        target.add_argument("--seeds", help="an explicit seed file; labelled ad-hoc")
        target.add_argument("--threads", type=int, default=os.cpu_count() or 1)
        target.add_argument("--label")
        target.add_argument("--out")

    run = sub.add_parser("run", help="round-robin over an entrant population")
    run.add_argument("--entrants", default=str(DEFAULT_ENTRANTS))
    seeds_arguments(run)
    run.set_defaults(handler=command_run)

    evaluate = sub.add_parser("evaluate", help="one policy against an opponent population")
    evaluate.add_argument("--family")
    evaluate.add_argument("--candidate")
    evaluate.add_argument("--baseline")
    evaluate.add_argument("--opponents", default=str(DEFAULT_ENTRANTS))
    seeds_arguments(evaluate)
    evaluate.set_defaults(handler=command_evaluate)

    promote = sub.add_parser("promote", help="apply the champion-promotion rule")
    promote.add_argument("--challenger", required=True)
    promote.add_argument("--incumbent", required=True)
    promote.set_defaults(handler=command_promote)

    args = parser.parse_args()
    if args.command == "evaluate" and bool(args.baseline) == bool(args.candidate):
        parser.error("evaluate needs exactly one of --baseline or --candidate")
    if args.command == "evaluate" and args.candidate and not args.family:
        parser.error("--candidate requires --family")
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
