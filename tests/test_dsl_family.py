"""Loading and validating a family encoding, including the real one."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

from experiments.policies.monocrop_reorder.dsl_policy import (
    CANDIDATE_PATH,
    FAMILY_PATH,
    load_family,
)
from submission.actions import EMITS
from submission.dsl import family as family_module
from submission.dsl.expr import DslError
from submission.vocabulary import VOCABULARY


def document() -> dict[str, Any]:
    return json.loads(FAMILY_PATH.read_text())


def load(doc: dict[str, Any]) -> family_module.Family:
    return family_module.load(doc, observations=VOCABULARY.kinds, emits=EMITS)


class RealFamilyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.family = load_family()

    def test_identity_matches_the_candidate_it_is_parameterised_by(self) -> None:
        candidate = json.loads(CANDIDATE_PATH.read_text())
        self.assertEqual(self.family.policy_id, "monocrop-reorder-v1")
        self.assertEqual(self.family.policy_id, candidate["policy_id"])
        self.assertEqual(self.family.dsl_version, family_module.DSL_VERSION)

    def test_register_audit_is_encoded_not_just_documented(self) -> None:
        # docs/policy_dsl.md: only registers a guard reads enter the
        # cross-backend semantic contract.
        self.assertEqual(
            self.family.decision_registers(),
            ("mode", "last_step", "requested_plant_actions"),
        )

    def test_baseline_candidate_binds(self) -> None:
        candidate = json.loads(CANDIDATE_PATH.read_text())
        bound = self.family.bind(candidate["parameters"])
        self.assertEqual(bound["crop"], "WHEAT")
        self.assertEqual(bound["seed_buy_batch"], 4)

    def test_out_of_range_and_unknown_parameters_are_refused(self) -> None:
        candidate = json.loads(CANDIDATE_PATH.read_text())["parameters"]
        for bad in (
            {**candidate, "harvest_min_age_days": 9},
            {**candidate, "crop": "MELON"},
            {**candidate, "seed_buy_batch": 0},
            {**candidate, "extra": 1},
        ):
            with self.assertRaises(DslError):
                self.family.bind(bad)
        del candidate["cash_reserve"]
        with self.assertRaises(DslError):
            self.family.bind(candidate)


class ValidationTest(unittest.TestCase):
    def test_unknown_observation_is_a_load_error(self) -> None:
        doc = document()
        doc["farmer_cascade"][0]["when"] = ["obs", "opponent_money"]
        with self.assertRaises(DslError) as caught:
            load(doc)
        self.assertIn("opponent_money", str(caught.exception))

    def test_unknown_register_write_is_a_load_error(self) -> None:
        doc = document()
        doc["commit"].append({"reg": "nonexistent", "value": ["const", 1]})
        with self.assertRaises(DslError):
            load(doc)

    def test_a_register_cannot_be_written_twice_in_one_stage(self) -> None:
        doc = document()
        doc["commit"].append({"reg": "last_step", "value": ["const", 0]})
        with self.assertRaises(DslError) as caught:
            load(doc)
        self.assertIn("simultaneous", str(caught.exception))

    def test_next_cannot_read_a_register_written_later(self) -> None:
        doc = document()
        observe = doc["observe"]
        mode = next(i for i, w in enumerate(observe) if w["reg"] == "mode")
        entered = next(
            i for i, w in enumerate(observe) if w["reg"] == "mode_entered_step"
        )
        observe[mode], observe[entered] = observe[entered], observe[mode]
        with self.assertRaises(DslError) as caught:
            load(doc)
        self.assertIn("written earlier", str(caught.exception))

    def test_kind_mismatch_between_write_and_register(self) -> None:
        doc = document()
        doc["commit"][-1]["value"] = ["const", "OPENING"]
        with self.assertRaises(DslError):
            load(doc)

    def test_enum_write_outside_the_declared_domain(self) -> None:
        doc = document()
        mode = next(w for w in doc["observe"] if w["reg"] == "mode")
        mode["value"] = ["const", "HARVESTING"]
        with self.assertRaises(DslError):
            load(doc)

    def test_guard_must_be_boolean(self) -> None:
        doc = document()
        doc["farmer_cascade"][0]["when"] = ["obs", "day"]
        with self.assertRaises(DslError):
            load(doc)

    def test_emit_vocabulary_is_checked(self) -> None:
        doc = document()
        doc["market_rules"][0]["emit"] = ["SEL", ["param", "crop"], ["const", 1]]
        with self.assertRaises(DslError) as caught:
            load(doc)
        self.assertIn("SEL", str(caught.exception))

    def test_emit_arity_is_checked(self) -> None:
        doc = document()
        doc["farmer_cascade"][4]["emit"] = ["PLANT"]
        with self.assertRaises(DslError):
            load(doc)

    def test_fired_must_name_a_declared_rule(self) -> None:
        doc = document()
        doc["commit"][2]["value"] = [
            "+",
            ["state", "requested_sell_units"],
            ["if", ["fired?", "market", "sell_everything"], ["const", 1], ["const", 0]],
        ]
        with self.assertRaises(DslError):
            load(doc)

    def test_dsl_version_must_match_the_interpreter(self) -> None:
        doc = document()
        doc["dsl_version"] = 2
        with self.assertRaises(DslError) as caught:
            load(doc)
        self.assertIn("dsl_version", str(caught.exception))

    def test_unknown_top_level_keys_are_refused(self) -> None:
        doc = document()
        doc["farmer_casacde"] = []
        with self.assertRaises(DslError):
            load(doc)

    def test_deep_copy_of_a_valid_document_still_loads(self) -> None:
        self.assertIsNotNone(load(copy.deepcopy(document())))


if __name__ == "__main__":
    unittest.main()
