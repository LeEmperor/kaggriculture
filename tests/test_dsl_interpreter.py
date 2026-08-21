"""The turn pipeline, and its agreement with the hand-written family."""

from __future__ import annotations

import unittest
from typing import Any

from experiments.policies.monocrop_reorder.dsl_policy import (
    load_interpreter,
    make_policy,
)
from experiments.policies.monocrop_reorder.policy import (
    MonocropReorder,
    load_parameters,
)
from reference.oracle import run_game
from submission.dsl.interpreter import Policy
from tests.test_monocrop_reorder import observation

# The three registers docs/policy_dsl.md classes as decision registers, and so
# the only ones two backends are required to agree on.
DECISION = ("mode", "last_step", "requested_plant_actions")


class TurnPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = Policy(load_interpreter())

    def test_initial_registers_match_the_declared_inits(self) -> None:
        self.assertEqual(self.policy.registers["mode"], "OPENING")
        self.assertEqual(self.policy.registers["last_step"], -1)
        self.assertIs(self.policy.registers["money_seen"], False)

    def test_observe_runs_before_decisions(self) -> None:
        # Liquidation is entered in the observe stage, so the market rule that
        # reads `mode` in the same turn must already see it.
        action = self.policy.act(
            observation(step=648, day=27, shed_wheat=7, wheat_price=1)
        )
        self.assertEqual(self.policy.registers["mode"], "LIQUIDATION")
        self.assertEqual(action["market"], [["SELL", "WHEAT", 7]])

    def test_commit_runs_after_decisions(self) -> None:
        # requested_plant_actions is what promotes OPENING to PRODUCTION, and it
        # must reflect turns strictly before this one.
        self.policy.act(observation(seeds=1))
        self.assertEqual(self.policy.registers["requested_plant_actions"], 1)
        self.assertEqual(self.policy.registers["mode"], "OPENING")
        self.policy.act(observation(step=1, seeds=1, tile=None))
        self.assertEqual(self.policy.registers["mode"], "PRODUCTION")

    def test_mode_entered_step_uses_the_in_stage_mode_write(self) -> None:
        self.policy.act(observation(step=5, seeds=1))
        self.policy.act(observation(step=6, day=27))
        self.assertEqual(self.policy.registers["mode"], "LIQUIDATION")
        self.assertEqual(self.policy.registers["mode_entered_step"], 6)

    def test_step_zero_after_a_played_turn_restores_every_init(self) -> None:
        self.policy.act(observation(step=5, seeds=1, shed_wheat=3, wheat_price=40))
        self.assertNotEqual(self.policy.registers["requested_plant_actions"], 0)
        self.policy.act(observation(step=0))
        self.assertEqual(self.policy.registers["requested_plant_actions"], 0)
        self.assertEqual(self.policy.registers["peak_price"], 25)

    def test_snapshots_separate_decision_from_telemetry(self) -> None:
        self.policy.act(observation())
        self.assertEqual(tuple(self.policy.decision_snapshot()), DECISION)
        self.assertIn("peak_price", self.policy.snapshot())

    def test_make_policy_returns_isolated_instances(self) -> None:
        first, second = make_policy(), make_policy()
        first(observation(seeds=1))
        self.assertEqual(second(observation(seeds=1))["farmer"], ["PLANT", "WHEAT"])


class HandWrittenParityTest(unittest.TestCase):
    """The gate from docs/ocaml_migration_decisions.md, at unit scale.

    The full version — many seeds, recorded fixtures, loose telemetry
    comparison — is step 2 of that work plan. This is the fast subset that keeps
    the two implementations from drifting in the meantime.
    """

    def setUp(self) -> None:
        self.hand = MonocropReorder(load_parameters())
        self.dsl = Policy(load_interpreter())

    def assert_same(self, obs: dict[str, Any]) -> None:
        self.assertEqual(self.hand.act(obs), self.dsl.act(obs))
        for name in DECISION:
            self.assertEqual(
                getattr(self.hand.state, name), self.dsl.registers[name], name
            )

    def test_each_cascade_branch_agrees(self) -> None:
        plant = {"kind": "PLANT", "planted_day": 0, "yield_units": 0}
        ripe = {"kind": "PLANT", "planted_day": 0, "yield_units": 5}
        for obs in (
            observation(),
            observation(step=1, seeds=1),
            observation(step=2, seeds=1, farmer_wheat=3),
            observation(step=3, tile=plant),
            observation(step=4, tile={**plant, "watered_today": True}),
            observation(step=5, day=4, tile=ripe),
            observation(step=6, shed_wheat=9, wheat_price=40),
            observation(step=7, shed_wheat=9, wheat_price=5),
            observation(step=8, day=27, shed_wheat=9, wheat_price=1),
            observation(step=9, day=27, seeds=4),
            observation(step=10, hour=23, seeds=4),
        ):
            with self.subTest(step=obs["step"]):
                self.assert_same(obs)

    def test_a_full_episode_agrees_turn_by_turn(self) -> None:
        pairs = [
            (MonocropReorder(load_parameters()), Policy(load_interpreter()))
            for _ in range(2)
        ]

        def compare(index: int):  # type: ignore[no-untyped-def]
            hand, dsl = pairs[index]

            def policy(obs: dict[str, Any]) -> Any:
                expected = hand.act(obs)
                self.assertEqual(expected, dsl.act(obs))
                for name in DECISION:
                    self.assertEqual(
                        getattr(hand.state, name), dsl.registers[name], name
                    )
                return expected

            return policy

        run_game(1234, compare(0), compare(1))
        self.assertEqual(pairs[0][0].state.last_step, 718)


if __name__ == "__main__":
    unittest.main()
