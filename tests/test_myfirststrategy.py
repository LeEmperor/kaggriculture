from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from typing import Any

from experiments.policies.myfirststrategy.policy import (
    MyFirstStrategy,
    PolicyParameters,
    load_parameters,
)
from reference.run_game import load_policy


def observation(
    *,
    step: int = 0,
    day: int = 0,
    hour: int = 0,
    money: float = 3000,
    seeds: int = 0,
    shed_wheat: int = 0,
    farmer_wheat: int = 0,
    tile: Any = None,
    wheat_price: int = 25,
) -> dict[str, Any]:
    tiles = [["LOCKED" for _ in range(10)] for _ in range(10)]
    for y in range(5):
        for x in range(5):
            tiles[y][x] = None
    tiles[4][4] = tile
    return {
        "player": 0,
        "step": step,
        "day": day,
        "hour": hour,
        "farms": [
            {
                "money": money,
                "tiles": tiles,
                "farmer": [4, 4],
                "hands": [],
                "unlocked_quadrants": ["NW"],
                "hires_today": 0,
            },
            {},
        ],
        "private": {
            "shed": {"WHEAT": shed_wheat},
            "seeds": {"WHEAT": seeds},
            "inventories": [{"WHEAT": farmer_wheat} if farmer_wheat else {}],
        },
        "market": {
            "prices": {"WHEAT": wheat_price},
            "inventory": {"WHEAT": 10000},
        },
        "town": {"unlocked_shops": []},
    }


class MyFirstStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parameters = load_parameters()
        self.policy = MyFirstStrategy(self.parameters)

    def test_parameters_are_immutable(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            self.parameters.cash_reserve = 0  # type: ignore[misc]

    def test_buys_seed_batch_while_preserving_cash(self) -> None:
        action = self.policy.act(observation())
        self.assertEqual(action["farmer"], ["PASS"])
        self.assertEqual(action["market"], [["BUY_SEED", "WHEAT", 4]])

    def test_plants_and_updates_policy_state(self) -> None:
        action = self.policy.act(observation(seeds=1))
        self.assertEqual(action["farmer"], ["PLANT", "WHEAT"])
        self.assertEqual(self.policy.state.requested_plant_actions, 1)

    def test_carried_wheat_is_dropped_before_more_farm_work(self) -> None:
        action = self.policy.act(observation(seeds=1, farmer_wheat=3))
        self.assertEqual(action["farmer"], ["DROP"])

    def test_liquidation_is_an_irreversible_fsm_mode(self) -> None:
        action = self.policy.act(
            observation(
                step=648,
                day=self.parameters.liquidation_start_day,
                shed_wheat=7,
                wheat_price=1,
            )
        )
        self.assertEqual(self.policy.state.mode, "LIQUIDATION")
        self.assertEqual(action["market"], [["SELL", "WHEAT", 7]])
        self.assertEqual(action["farmer"], ["PASS"])

    def test_parameter_changes_behavior_without_changing_algorithm(self) -> None:
        altered = PolicyParameters(
            **{
                **self.parameters.__dict__,
                "sell_price_threshold": 40,
            }
        )
        low_threshold = self.policy.act(observation(shed_wheat=2, wheat_price=25))
        high_threshold = MyFirstStrategy(altered).act(
            observation(shed_wheat=2, wheat_price=25)
        )
        self.assertEqual(low_threshold["market"][0], ["SELL", "WHEAT", 2])
        self.assertNotIn(["SELL", "WHEAT", 2], high_threshold["market"])

    def test_reference_loader_constructs_isolated_instances(self) -> None:
        module = "experiments.policies.myfirststrategy.policy"
        first = load_policy(module)
        second = load_policy(module)
        self.assertIsNot(first.__self__, second.__self__)


if __name__ == "__main__":
    unittest.main()
