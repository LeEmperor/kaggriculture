from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.policies.common.actions import (
    buy_seed,
    complete_action,
    sell,
)
from experiments.policies.common.candidates import (
    candidate_parameters,
    load_candidate,
)
from experiments.policies.common.game_data import SEED_COSTS, seed_cost
from experiments.policies.common.observations import (
    carried_units,
    current_tile,
    is_shed_access,
    item_count,
    own_farm,
    worker_inventory,
)


class ActionHelpersTest(unittest.TestCase):
    def test_complete_actions_do_not_share_mutable_defaults(self) -> None:
        first = complete_action()
        second = complete_action()

        first["farmer"].append("unexpected")
        first["market"].append(sell("WHEAT", 1))

        self.assertEqual(second, {"farmer": ["PASS"], "hands": [], "market": []})

    def test_market_orders_require_positive_units(self) -> None:
        self.assertEqual(buy_seed("WHEAT", 2), ["BUY_SEED", "WHEAT", 2])
        with self.assertRaises(ValueError):
            sell("WHEAT", 0)


class ObservationHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.farm = {
            "farmer": [4, 4],
            "tiles": [[f"{x},{y}" for x in range(10)] for y in range(10)],
        }
        self.private = {
            "inventories": [{"WHEAT": 2, "CARROT": 1}, {"WHEAT": 4}]
        }

    def test_selects_own_farm_and_current_tile(self) -> None:
        opponent = {"farmer": [0, 0], "tiles": [[None]]}
        observation = {"player": 1, "farms": [opponent, self.farm]}

        self.assertIs(own_farm(observation), self.farm)
        self.assertEqual(current_tile(self.farm), "4,4")
        self.assertEqual(current_tile(self.farm, (2, 3)), "2,3")

    def test_reads_inventory_counts(self) -> None:
        self.assertEqual(item_count({"WHEAT": 3}, "WHEAT"), 3)
        self.assertEqual(item_count({"WHEAT": -1}, "WHEAT"), 0)
        self.assertEqual(carried_units(self.private), 3)
        self.assertEqual(worker_inventory(self.private, 1), {"WHEAT": 4})
        self.assertEqual(worker_inventory(self.private, 2), {})

    def test_recognizes_the_four_central_shed_access_tiles(self) -> None:
        for position in ((4, 4), (5, 4), (4, 5), (5, 5)):
            with self.subTest(position=position):
                self.assertTrue(is_shed_access(self.farm, position))
        self.assertFalse(is_shed_access(self.farm, (3, 4)))


class CandidateHelpersTest(unittest.TestCase):
    def test_loads_and_validates_candidate_envelope(self) -> None:
        candidate = {
            "policy_id": "test-v1",
            "schema_version": 1,
            "parameters": {"threshold": 7},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps(candidate), encoding="utf-8")
            loaded = load_candidate(path)

        self.assertEqual(
            candidate_parameters(
                loaded,
                expected_policy_id="test-v1",
                expected_schema_version=1,
            ),
            {"threshold": 7},
        )

    def test_rejects_the_wrong_policy_family(self) -> None:
        with self.assertRaisesRegex(ValueError, "policy_id"):
            candidate_parameters(
                {
                    "policy_id": "other-v1",
                    "schema_version": 1,
                    "parameters": {},
                },
                expected_policy_id="test-v1",
                expected_schema_version=1,
            )

    def test_rejects_a_boolean_schema_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "schema_version"):
            candidate_parameters(
                {
                    "policy_id": "test-v1",
                    "schema_version": True,
                    "parameters": {},
                },
                expected_policy_id="test-v1",
                expected_schema_version=1,
            )


class GameDataTest(unittest.TestCase):
    def test_seed_costs_are_shared_immutable_game_data(self) -> None:
        self.assertEqual(seed_cost("WHEAT"), 10)
        self.assertEqual(seed_cost("MELON"), 80)
        with self.assertRaises(TypeError):
            SEED_COSTS["WHEAT"] = 999  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "unknown crop"):
            seed_cost("POTATO")


if __name__ == "__main__":
    unittest.main()
