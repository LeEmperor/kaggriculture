"""The checked-in golden vectors, and the DSL interpreter's agreement with them.

This is the recorded half of the equivalence gate in
``docs/ocaml_migration_decisions.md``; the live half (hand-vs-DSL over full
episodes) is ``python3 -m experiments.golden sweep`` and the episode test in
``tests/test_dsl_interpreter.py``. Here the oracle never runs: the fixtures on
disk are the contract, which is exactly what a future OCaml or C++ backend will
be checked against.
"""

from __future__ import annotations

import json
import unittest

from experiments.golden import (
    GOLDEN_PATH,
    LOCK_PATH,
    STEPPERS,
    Comparison,
    load_fixtures,
)
from experiments.policies.monocrop_reorder.dsl_policy import load_family


class GoldenVectorsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load_fixtures(GOLDEN_PATH)
        cls.family = load_family()

    def test_fixtures_are_checked_in_and_non_trivial(self) -> None:
        vectors = self.document["vectors"]
        self.assertGreater(len(vectors), 50)
        # Every vector carries the full register bank in the family vocabulary.
        registers = set(self.family.registers)
        for vector in vectors:
            self.assertEqual(set(vector["previous_policy_state"]), registers)
            self.assertEqual(set(vector["expected_next_policy_state"]), registers)

    def test_register_classification_matches_the_family(self) -> None:
        decision = tuple(self.document["registers"]["decision"])
        self.assertEqual(decision, self.family.decision_registers())
        telemetry = set(self.document["registers"]["telemetry"])
        self.assertEqual(
            telemetry, set(self.family.registers) - set(decision)
        )

    def test_fixtures_were_recorded_against_the_pinned_upstream(self) -> None:
        lock = json.loads(LOCK_PATH.read_text())
        self.assertEqual(self.document["reference_commit"], lock["commit"])

    def test_the_reset_path_is_covered(self) -> None:
        # run_game gives each player a fresh policy, so this coverage only
        # exists because the recorder reuses instances across episodes.
        self.assertTrue(
            any(
                vector["observation"]["step"] == 0
                and vector["previous_policy_state"]["last_step"] >= 0
                for vector in self.document["vectors"]
            )
        )

    def test_every_farmer_verb_and_market_order_appears(self) -> None:
        farmer = {
            vector["expected_action"]["farmer"][0]
            for vector in self.document["vectors"]
        }
        market = {
            order[0]
            for vector in self.document["vectors"]
            for order in vector["expected_action"]["market"]
        }
        self.assertEqual(farmer, {"DROP", "HARVEST", "WATER", "PASS", "PLANT"})
        self.assertEqual(market, {"SELL", "BUY_SEED"})

    def test_the_dsl_interpreter_replays_every_vector(self) -> None:
        step = STEPPERS["dsl"]()
        decision = frozenset(self.family.decision_registers())
        comparison = Comparison()
        for vector in self.document["vectors"]:
            action, state = step(
                vector["observation"], vector["previous_policy_state"]
            )
            comparison.compare(
                f"seed {vector['seed']} turn {vector['turn']}",
                decision,
                vector["expected_action"],
                action,
                vector["expected_next_policy_state"],
                state,
                report=False,
            )
        self.assertEqual(comparison.action_failures, 0)
        self.assertEqual(comparison.decision_failures, 0)
        # Loose does not mean expected-to-diverge: for this family the DSL
        # tracks every telemetry register, so divergence is a regression.
        self.assertEqual(comparison.telemetry_divergences, 0)


if __name__ == "__main__":
    unittest.main()
