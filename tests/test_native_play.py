"""End-to-end gate for the native simulator policy vocabulary.

Golden vectors cannot reach this path: they carry JSON observations and therefore test
``interp/kaggriculture/vocabulary.ml``. This test runs the same seed through the OCaml
subprocess policy on the pinned oracle and through ``kag_sim play``'s native vocabulary,
then compares every action and both final balances.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from experiments.ocaml_backend import is_available

ROOT = Path(__file__).resolve().parents[1]
KAG_SIM = ROOT / "_build/default/fast_model/bin/kag_sim.exe"
FAMILY = ROOT / "experiments/policies/monocrop_reorder/family.json"
CANDIDATE = ROOT / "experiments/policies/monocrop_reorder/candidate_baseline.json"


@unittest.skipUnless(
    KAG_SIM.is_file() and is_available(), "OCaml executables have not been built"
)
class NativePlayTest(unittest.TestCase):
    def test_native_actions_and_final_money_match_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            reference_trace = temporary / "reference.jsonl"
            native_trace = temporary / "native.jsonl"

            reference = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "reference.run_game",
                    "--seed",
                    "1234",
                    "--policy-a",
                    "experiments.policies.monocrop_reorder.ocaml_policy",
                    "--policy-b",
                    "reference.policies.pass_policy",
                    "--trace",
                    str(reference_trace),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            native = subprocess.run(
                [
                    str(KAG_SIM),
                    "play",
                    "--seed",
                    "1234",
                    "--family",
                    str(FAMILY),
                    "--policy-a",
                    str(CANDIDATE),
                    "--trace",
                    str(native_trace),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            reference_records = [
                json.loads(line) for line in reference_trace.read_text().splitlines()
            ]
            native_records = [
                json.loads(line) for line in native_trace.read_text().splitlines()
            ]
            self.assertEqual(
                [record["actions"] for record in reference_records],
                [record["actions"] for record in native_records],
            )
            self.assertEqual(len(reference_records), 719)
            self.assertEqual(
                json.loads(reference.stdout)["reward"],
                json.loads(native.stdout)["final_money"],
            )

    def test_evaluate_runs_both_seats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            opponents = temporary / "opponents.json"
            seeds = temporary / "seeds.txt"
            opponents.write_text('["pass"]\n', encoding="utf-8")
            seeds.write_text("1234\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    str(KAG_SIM),
                    "evaluate",
                    "--family",
                    str(FAMILY),
                    "--candidate",
                    str(CANDIDATE),
                    "--opponents",
                    str(opponents),
                    "--seeds",
                    str(seeds),
                    "--threads",
                    "2",
                    "--copies",
                    "2",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            report = json.loads(completed.stdout)
            self.assertEqual(report["games"], 4)
            self.assertEqual(report["turns"], 2876)
            self.assertEqual(report["threads"], 2)
            self.assertEqual(report["workload_copies"], 2)
            self.assertEqual(report["wins"] + report["draws"] + report["losses"], 4)
            for metric in (
                "wall_seconds",
                "games_per_second",
                "turns_per_second",
                "nanoseconds_per_turn",
            ):
                self.assertGreater(report[metric], 0)


if __name__ == "__main__":
    unittest.main()
