"""A small stateful and parameterized wheat-loop research policy."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CANDIDATE_PATH = Path(__file__).with_name("candidate_baseline.json")
PASS_ACTION: dict[str, list[Any]] = {
    "farmer": ["PASS"],
    "hands": [],
    "market": [],
}
WHEAT_SEED_COST = 10


@dataclass(frozen=True)
class PolicyParameters:
    """Immutable values selected by search before an episode starts."""

    crop: str
    cash_reserve: int
    seed_reorder_point: int
    seed_buy_batch: int
    planting_hour_cutoff: int
    harvest_min_age_days: int
    sell_price_threshold: int
    liquidation_start_day: int

    @classmethod
    def from_candidate(cls, candidate: dict[str, Any]) -> PolicyParameters:
        if candidate.get("policy_id") != "myfirststrategy-v1":
            raise ValueError("candidate policy_id must be myfirststrategy-v1")
        if candidate.get("schema_version") != 1:
            raise ValueError("candidate schema_version must be 1")
        parameters = cls(**candidate["parameters"])
        parameters.validate()
        return parameters

    def validate(self) -> None:
        if self.crop != "WHEAT":
            raise ValueError("myfirststrategy-v1 currently supports only WHEAT")
        if self.cash_reserve < 0 or self.seed_reorder_point < 0:
            raise ValueError("cash and seed reserves cannot be negative")
        if self.seed_buy_batch < 1:
            raise ValueError("seed_buy_batch must be positive")
        if not 0 <= self.planting_hour_cutoff <= 22:
            raise ValueError("planting_hour_cutoff must leave time to water")
        if not 2 <= self.harvest_min_age_days <= 5:
            raise ValueError("harvest_min_age_days is outside the supported range")
        if self.sell_price_threshold < 1:
            raise ValueError("sell_price_threshold must be positive")
        if not 0 <= self.liquidation_start_day <= 29:
            raise ValueError("liquidation_start_day must be within the season")


@dataclass
class PolicyState:
    """Mutable memory belonging to one player in one episode."""

    mode: str = "OPENING"
    mode_entered_step: int = 0
    last_step: int = -1
    last_farmer_action: tuple[Any, ...] = ("PASS",)
    previous_money: float | None = None
    last_money_delta: float = 0.0
    peak_wheat_price: int = 0
    requested_plant_actions: int = 0
    requested_harvest_actions: int = 0
    requested_sell_units: int = 0

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-friendly representation for future parity fixtures."""
        result = asdict(self)
        result["last_farmer_action"] = list(self.last_farmer_action)
        return result


def load_parameters(path: Path = CANDIDATE_PATH) -> PolicyParameters:
    candidate = json.loads(path.read_text(encoding="utf-8"))
    return PolicyParameters.from_candidate(candidate)


class MyFirstStrategy:
    """One-tile wheat FSM demonstrating parameters and per-game state."""

    def __init__(self, parameters: PolicyParameters):
        self.parameters = parameters
        self.state = PolicyState()

    def reset(self) -> None:
        self.state = PolicyState()

    def act(self, observation: dict[str, Any]) -> dict[str, list[Any]]:
        step = int(observation["step"])
        if step == 0 and self.state.last_step >= 0:
            self.reset()

        player = int(observation["player"])
        farm = observation["farms"][player]
        private = observation["private"]
        market = observation["market"]
        day = int(observation["day"])
        hour = int(observation["hour"])
        money = float(farm["money"])

        self._observe(step, day, money, market)
        market_actions = self._market_actions(farm, private, market)
        farmer_action = self._farmer_action(farm, private, day, hour)
        self._record_requested_actions(farmer_action, market_actions)

        self.state.last_step = step
        self.state.last_farmer_action = tuple(farmer_action)
        return {"farmer": farmer_action, "hands": [], "market": market_actions}

    def _observe(
        self, step: int, day: int, money: float, market: dict[str, Any]
    ) -> None:
        if self.state.previous_money is None:
            self.state.last_money_delta = 0.0
        else:
            self.state.last_money_delta = money - self.state.previous_money
        self.state.previous_money = money

        wheat_price = int(market.get("prices", {}).get("WHEAT", 0))
        self.state.peak_wheat_price = max(self.state.peak_wheat_price, wheat_price)

        if day >= self.parameters.liquidation_start_day:
            self._enter_mode("LIQUIDATION", step)
        elif self.state.requested_plant_actions > 0:
            self._enter_mode("PRODUCTION", step)

    def _enter_mode(self, mode: str, step: int) -> None:
        if self.state.mode != mode:
            self.state.mode = mode
            self.state.mode_entered_step = step

    def _market_actions(
        self,
        farm: dict[str, Any],
        private: dict[str, Any],
        market: dict[str, Any],
    ) -> list[list[Any]]:
        actions: list[list[Any]] = []
        shed = private.get("shed", {})
        wheat_in_shed = max(0, int(shed.get(self.parameters.crop, 0)))
        wheat_price = int(market.get("prices", {}).get(self.parameters.crop, 0))

        should_sell = self.state.mode == "LIQUIDATION" or (
            wheat_price >= self.parameters.sell_price_threshold
        )
        if wheat_in_shed > 0 and should_sell:
            actions.append(["SELL", self.parameters.crop, wheat_in_shed])

        seeds = max(0, int(private.get("seeds", {}).get(self.parameters.crop, 0)))
        purchase_cost = self.parameters.seed_buy_batch * WHEAT_SEED_COST
        can_preserve_reserve = (
            float(farm["money"]) - purchase_cost >= self.parameters.cash_reserve
        )
        if (
            self.state.mode != "LIQUIDATION"
            and seeds <= self.parameters.seed_reorder_point
            and can_preserve_reserve
        ):
            actions.append(
                ["BUY_SEED", self.parameters.crop, self.parameters.seed_buy_batch]
            )
        return actions

    def _farmer_action(
        self,
        farm: dict[str, Any],
        private: dict[str, Any],
        day: int,
        hour: int,
    ) -> list[Any]:
        farmer_inventory = (private.get("inventories") or [{}])[0]
        if sum(
            max(0, int(count)) for count in farmer_inventory.values()
        ) > 0 and self._is_shed_access(farm):
            return ["DROP"]

        x, y = farm["farmer"]
        tile = farm["tiles"][y][x]
        if isinstance(tile, dict) and tile.get("kind") == "PLANT":
            age = day - int(tile["planted_day"])
            if (
                age >= self.parameters.harvest_min_age_days
                and int(tile.get("yield_units", 0)) > 0
            ):
                return ["HARVEST"]
            if not tile.get("watered_today", False):
                return ["WATER"]
            return ["PASS"]

        seeds = max(0, int(private.get("seeds", {}).get(self.parameters.crop, 0)))
        if (
            tile is None
            and seeds > 0
            and hour <= self.parameters.planting_hour_cutoff
            and self.state.mode != "LIQUIDATION"
        ):
            return ["PLANT", self.parameters.crop]
        return ["PASS"]

    @staticmethod
    def _is_shed_access(farm: dict[str, Any]) -> bool:
        board_size = len(farm["tiles"])
        half = board_size // 2
        return tuple(farm["farmer"]) in {
            (half - 1, half - 1),
            (half, half - 1),
            (half - 1, half),
            (half, half),
        }

    def _record_requested_actions(
        self, farmer_action: list[Any], market_actions: list[list[Any]]
    ) -> None:
        if farmer_action[0] == "PLANT":
            self.state.requested_plant_actions += 1
        elif farmer_action[0] == "HARVEST":
            self.state.requested_harvest_actions += 1

        for order in market_actions:
            if order[0] == "SELL":
                self.state.requested_sell_units += int(order[2])


def make_policy():
    """Construct an isolated callable for one player in one research game."""
    return MyFirstStrategy(load_parameters()).act


_SUBMISSION_POLICY = MyFirstStrategy(load_parameters())


def agent(observation: dict[str, Any]) -> dict[str, list[Any]]:
    """Submission-shaped entry point using one persistent policy instance."""
    if int(observation.get("step", 0)) == 0:
        _SUBMISSION_POLICY.reset()
    return _SUBMISSION_POLICY.act(observation)
