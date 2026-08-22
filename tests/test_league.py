"""The Phase 6 evaluation layer: the native league, and the promotion rule.

Two separable claims. The native half is checked by running a real (tiny) league and
asserting the artifact shape the promotion rule reads. The rule itself is pure and is
checked against synthetic artifacts, because the cases that matter — a regression against
one opponent, an ad-hoc seed file, a challenger measured on the wrong split — are ones a
real run should never produce.
"""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tools import league, seed_splits

ROOT = Path(__file__).resolve().parents[1]
KAG_SIM = ROOT / "_build/default/fast_model/bin/kag_sim.exe"
ENTRANTS = ROOT / "experiments/leagues/baseline_v1/entrants.json"

SUMMARY_FIELDS = (
    "games",
    "wins",
    "draws",
    "losses",
    "win_rate",
    "score_rate",
    "win_rate_ci95",
    "margin_mean",
    "margin_median",
    "margin_mean_ci95",
    "margin_p5",
    "margin_p95",
    "catastrophic_loss_rate",
)


def _summary(**overrides: Any) -> dict[str, Any]:
    summary = {
        "games": 100,
        "turns": 71900,
        "wins": 70,
        "draws": 0,
        "losses": 30,
        "win_rate": 0.70,
        "score_rate": 0.70,
        "win_rate_ci95": [0.60, 0.78],
        "margin_mean": 5000.0,
        "margin_median": 4800.0,
        "margin_variance": 1.0,
        "margin_stddev": 1.0,
        "margin_stderr": 100.0,
        "margin_mean_ci95": [4800.0, 5200.0],
        "margin_p5": -2000.0,
        "margin_p95": 12000.0,
        "margin_min": -9000.0,
        "margin_max": 20000.0,
        "money_mean": 9000.0,
        "opponent_money_mean": 4000.0,
        "catastrophic_losses": 2,
        "catastrophic_loss_rate": 0.02,
    }
    summary.update(overrides)
    return summary


def _artifact(**overrides: Any) -> dict[str, Any]:
    document = {
        "kind": "evaluation",
        "schema_version": 1,
        "seed_split": {"name": "validation", "sha256": "a" * 64},
        "inputs": {"sha256": {"opponents_file": "b" * 64}},
        "native": {
            "overall": _summary(),
            "by_opponent": {"crop-greedy": _summary(), "pass": _summary()},
            "worst_matchup": {"opponent": "crop-greedy", "score_rate": 0.70},
        },
    }
    document.update(overrides)
    return document


class PromotionRuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.incumbent = _artifact()
        self.incumbent["native"]["overall"] = _summary(
            win_rate=0.55, score_rate=0.55, margin_mean=2000.0
        )
        self.challenger = _artifact()

    def test_a_clear_improvement_is_promoted(self) -> None:
        promote, reasons = league.promotion_verdict(self.challenger, self.incumbent)
        self.assertTrue(promote, reasons)

    def test_thresholds_and_prose_agree(self) -> None:
        protocol = (ROOT / "docs/evaluation_protocol.md").read_text(encoding="utf-8")
        for value in ("0.55", "0.50", "0.10", "0.20"):
            self.assertIn(value, protocol)
        self.assertEqual(league.PROMOTION["split"], "validation")

    def test_ad_hoc_seeds_cannot_promote(self) -> None:
        self.challenger["seed_split"]["name"] = "ad-hoc"
        promote, reasons = league.promotion_verdict(self.challenger, self.incumbent)
        self.assertFalse(promote)
        self.assertTrue(any("ad-hoc" in reason for reason in reasons), reasons)

    def test_training_split_cannot_promote(self) -> None:
        self.challenger["seed_split"]["name"] = "training"
        promote, _ = league.promotion_verdict(self.challenger, self.incumbent)
        self.assertFalse(promote)

    def test_different_seed_files_cannot_be_compared(self) -> None:
        self.challenger["seed_split"]["sha256"] = "c" * 64
        promote, reasons = league.promotion_verdict(self.challenger, self.incumbent)
        self.assertFalse(promote)
        self.assertTrue(any("different seed files" in reason for reason in reasons))

    def test_severe_single_opponent_regression_blocks_promotion(self) -> None:
        # Better overall, much worse against one opponent: the failure mode the
        # per-opponent limit exists for.
        self.incumbent["native"]["by_opponent"]["crop-greedy"] = _summary(score_rate=0.90)
        self.challenger["native"]["by_opponent"]["crop-greedy"] = _summary(score_rate=0.60)
        promote, reasons = league.promotion_verdict(self.challenger, self.incumbent)
        self.assertFalse(promote)
        self.assertTrue(any("crop-greedy" in reason for reason in reasons), reasons)

    def test_high_variance_challenger_blocks_promotion(self) -> None:
        self.challenger["native"]["overall"] = _summary(catastrophic_loss_rate=0.40)
        promote, reasons = league.promotion_verdict(self.challenger, self.incumbent)
        self.assertFalse(promote)
        self.assertTrue(any("catastrophic" in reason for reason in reasons), reasons)

    def test_a_collapsed_worst_matchup_blocks_promotion(self) -> None:
        self.challenger["native"]["worst_matchup"] = {
            "opponent": "animal-focused",
            "score_rate": 0.05,
        }
        promote, reasons = league.promotion_verdict(self.challenger, self.incumbent)
        self.assertFalse(promote)
        self.assertTrue(any("animal-focused" in reason for reason in reasons), reasons)

    def test_no_improvement_over_the_incumbent_blocks_promotion(self) -> None:
        incumbent = copy.deepcopy(self.challenger)
        incumbent["native"]["overall"]["margin_mean"] = 9000.0
        promote, reasons = league.promotion_verdict(self.challenger, incumbent)
        self.assertFalse(promote)
        self.assertTrue(any("incumbent" in reason for reason in reasons), reasons)


@unittest.skipUnless(KAG_SIM.is_file(), "OCaml executables have not been built")
class NativeLeagueTest(unittest.TestCase):
    def test_league_reports_every_declared_statistic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            seeds = Path(directory) / "seeds.txt"
            seeds.write_text("\n".join(str(s) for s in seed_splits.load("training")[:6]))
            completed = subprocess.run(
                [
                    str(KAG_SIM), "league",
                    "--entrants", str(ENTRANTS),
                    "--seeds", str(seeds),
                    "--threads", "4",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
        document = json.loads(completed.stdout)
        entrants = json.loads(ENTRANTS.read_text())
        self.assertEqual(len(document["table"]), len(entrants))
        # Every unordered pair, both seat orders, both seeds.
        pairs = len(entrants) * (len(entrants) - 1) // 2
        self.assertEqual(document["games"], pairs * 2 * 6)
        for row in document["table"]:
            for field in SUMMARY_FIELDS:
                self.assertIn(field, row["overall"], f"{row['id']} is missing {field}")
            self.assertEqual(
                row["coverage"]["declared_shapes_missing"],
                [],
                f"{row['id']} did not emit every declared action shape",
            )
            self.assertEqual(
                sorted(row["by_position"]), ["player_0", "player_1"], row["id"]
            )
            self.assertEqual(len(row["by_opponent"]), len(entrants) - 1, row["id"])

    def test_evaluate_accepts_a_baseline_and_gates_on_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            seeds = Path(directory) / "seeds.txt"
            seeds.write_text("\n".join(str(s) for s in seed_splits.load("training")[:6]))
            opponents = Path(directory) / "opponents.json"
            opponents.write_text(json.dumps(["baseline:pass"]))
            completed = subprocess.run(
                [
                    str(KAG_SIM), "evaluate",
                    "--baseline", "crop-greedy",
                    "--opponents", str(opponents),
                    "--seeds", str(seeds),
                    "--coverage",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
        document = json.loads(completed.stdout)
        self.assertEqual(document["policy"], {"id": "crop-greedy", "kind": "baseline"})
        self.assertEqual(document["games"], 12)
        self.assertEqual(document["coverage"]["declared_shapes_missing"], [])
        self.assertGreater(document["overall"]["win_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
