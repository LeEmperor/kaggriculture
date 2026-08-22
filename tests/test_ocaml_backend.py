"""The OCaml interpreter, driven over the subprocess shim.

The third column of the equivalence gate in ``docs/ocaml_migration_decisions.md``:
``policy.py`` (hand-written), ``dsl_policy.py`` (the Python interpreter), and the OCaml
interpreter reading the same ``family.json``. The vectors are the same ones
``tests/test_golden_vectors.py`` replays through the Python interpreter, which is the
point — one fixture file, three backends, no backend privileged.

Every test here is skipped when the OCaml tree has not been built, so
``python3 -m unittest discover`` stays runnable without dune. The gate that must not be
skipped is ``python3 -m experiments.golden check --backend ocaml``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from experiments.golden import GOLDEN_PATH, Comparison, load_fixtures
from experiments.ocaml_backend import (
    BUILD_HINT,
    OcamlBackendError,
    OcamlPolicy,
    is_available,
)
from experiments.policies.monocrop_reorder.dsl_policy import (
    CANDIDATE_PATH,
    FAMILY_PATH,
    POLICY_ID,
    load_family,
    load_interpreter,
)
from experiments.policies.monocrop_reorder.ocaml_policy import load_backend
from reference.oracle import run_game
from submission.dsl.interpreter import Policy


@unittest.skipUnless(is_available(), f"the OCaml shim is not built; {BUILD_HINT}")
class OcamlBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = load_backend()
        cls.family = load_family()
        cls.document = load_fixtures(GOLDEN_PATH)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.backend.close()

    def test_the_shim_serves_the_expected_family(self) -> None:
        identity = self.backend.ping()
        self.assertEqual(identity["policy_id"], POLICY_ID)
        self.assertEqual(identity["family"], self.family.family)
        self.assertEqual(identity["family_version"], self.family.family_version)
        self.assertEqual(identity["dsl_version"], self.family.dsl_version)

    def test_initial_registers_match_the_family_declaration(self) -> None:
        expected = {
            name: register.init for name, register in self.family.registers.items()
        }
        self.assertEqual(self.backend.ping()["registers"], expected)
        # Declaration order too: the fixture vocabulary is an ordered projection.
        self.assertEqual(
            list(self.backend.ping()["registers"]), list(self.family.registers)
        )

    def test_it_replays_every_golden_vector(self) -> None:
        decision = frozenset(self.family.decision_registers())
        comparison = Comparison()
        for vector in self.document["vectors"]:
            action, state = self.backend.step(
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
        self.assertEqual(comparison.turns, len(self.document["vectors"]))
        self.assertEqual(comparison.action_failures, 0)
        self.assertEqual(comparison.decision_failures, 0)
        # Loose does not mean expected-to-diverge: for this family the OCaml
        # interpreter tracks every telemetry register, so divergence is a regression.
        self.assertEqual(comparison.telemetry_divergences, 0)

    def test_a_full_episode_agrees_turn_by_turn(self) -> None:
        """The stateful path, over whole episodes: ``act`` with the bank in the process.

        The vectors only exercise ``step``, and they are signature-selected rather than
        consecutive, so nothing else here proves the shim carries its own register bank
        correctly from one turn to the next. Both players run so the two processes are
        shown not to interfere.
        """
        pairs = [
            (Policy(load_interpreter()), load_backend().__enter__()) for _ in range(2)
        ]
        try:

            def compare(index: int):  # type: ignore[no-untyped-def]
                python, ocaml = pairs[index]

                def policy(observation: dict[str, Any]) -> Any:
                    expected = python.act(observation)
                    self.assertEqual(expected, ocaml.act(observation))
                    self.assertEqual(python.snapshot(), ocaml.snapshot())
                    return expected

                return policy

            run_game(1234, compare(0), compare(1))
            self.assertEqual(pairs[0][0].registers["last_step"], 718)
        finally:
            for _, ocaml in pairs:
                ocaml.close()

    def test_reset_restores_the_initial_bank(self) -> None:
        with load_backend() as policy:
            policy.act(self.document["vectors"][0]["observation"])
            self.assertNotEqual(policy.snapshot(), self.backend.ping()["registers"])
            policy.reset()
            self.assertEqual(policy.snapshot(), self.backend.ping()["registers"])

    def test_a_malformed_request_is_reported_without_killing_the_process(self) -> None:
        with self.assertRaises(OcamlBackendError):
            registers = self.document["vectors"][0]["previous_policy_state"]
            self.backend.step({"step": 0}, registers)
        # The shim must still be serving: one bad vector reports itself, it does not
        # take down a 134-vector replay.
        self.assertEqual(self.backend.ping()["policy_id"], POLICY_ID)

    def test_a_register_bank_that_does_not_match_the_family_is_refused(self) -> None:
        vector = self.document["vectors"][0]
        short = dict(vector["previous_policy_state"])
        short.pop("mode")
        with self.assertRaises(OcamlBackendError):
            self.backend.step(vector["observation"], short)

    def test_a_candidate_for_another_policy_is_refused_at_startup(self) -> None:
        candidate = json.loads(CANDIDATE_PATH.read_text())
        candidate["policy_id"] = "some-other-policy-v1"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps(candidate))
            with self.assertRaises(OcamlBackendError):
                OcamlPolicy(FAMILY_PATH, path, policy_id=POLICY_ID)


if __name__ == "__main__":
    unittest.main()
