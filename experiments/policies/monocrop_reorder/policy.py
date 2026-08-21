"""Single-tile monoculture loop driven by a reorder-point inventory rule.

v1 pins ``crop`` to WHEAT; the algorithm itself is crop-generic.

Exists as a reference model for what old python-only policies looked like.

Tags: DO_NOT_DELETE
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# grab in common items -> functions that add some little abstraction
from experiments.policies.common.actions import (
    MarketOrder,
    PolicyAction,
    WorkerAction,
    buy_seed,
    complete_action,
    drop,
    harvest,
    pass_worker,
    plant,
    sell,
    water,
)

# strategy-level parameter loaders
from experiments.policies.common.candidates import (
    candidate_parameters,
    load_candidate,
)

# seed catalog
from experiments.policies.common.game_data import seed_cost

# helpers for looking at current state, such as which tile I'm on, what's in my inventory, or which quadrant im in
from experiments.policies.common.observations import (
    carried_units,
    current_tile,
    is_shed_access,
    item_count,
    own_farm,
)

# globals
CANDIDATE_PATH = Path(__file__).with_name("candidate_baseline.json")
POLICY_ID = "monocrop-reorder-v1"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PolicyParameters:
    """Immutable values selected by search before an episode starts."""
    """
      Set of things we are ingesting as the strategies params
      we get to pick these ourself, so this may or may not expand
      not sure if we need an automateable way to do it
    """

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
        values = candidate_parameters(
            candidate,
            expected_policy_id=POLICY_ID,
            expected_schema_version=SCHEMA_VERSION,
        )
        parameters = cls(**values)
        parameters.validate()
        return parameters

    def validate(self) -> None:
        if self.crop != "WHEAT":
            raise ValueError("monocrop-reorder-v1 currently supports only WHEAT")
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


# This is the internal state by which the policy cares about
# aka we control which registers and things we actually care to remember in between states or turns or any granularity of time
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


# load the model's params
def load_parameters(path: Path = CANDIDATE_PATH) -> PolicyParameters:
    return PolicyParameters.from_candidate(load_candidate(path))


# Strategy class
class MonocropReorder:
    """One-tile monoculture FSM demonstrating parameters and per-game state."""

    def __init__(self, parameters: PolicyParameters):
        self.parameters = parameters
        self.state = PolicyState()

    def reset(self) -> None:
        self.state = PolicyState()

    def act(self, observation: dict[str, Any]) -> PolicyAction:
        step = int(observation["step"])
        if step == 0 and self.state.last_step >= 0:
            self.reset()

        farm = own_farm(observation)
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
        return complete_action(farmer=farmer_action, market=market_actions)

    # "get the inputs from the pins"
    def _observe(
        self, step: int, day: int, money: float, market: dict[str, Any]
    ) -> None: # void

        # $ today vs $ yesterday
        if self.state.previous_money is None:
            self.state.last_money_delta = 0.0
        else:
            self.state.last_money_delta = money - self.state.previous_money
        self.state.previous_money = money

        # get current wheat price
        wheat_price = item_count(market.get("prices"), self.parameters.crop)

        # store market stats
        self.state.peak_wheat_price = max(self.state.peak_wheat_price, wheat_price)

        # liquidation check on inputs
        if day >= self.parameters.liquidation_start_day:
            self._enter_mode("LIQUIDATION", step)
        elif self.state.requested_plant_actions > 0:
            self._enter_mode("PRODUCTION", step)
            # this exists as the main branching logic for moving the FSM of the agent


    # state transition helper
    def _enter_mode(self, mode: str, step: int) -> None:
        if self.state.mode != mode:
            self.state.mode = mode
            self.state.mode_entered_step = step


    # helper for market-interactions
    # output encodings for any market interactions
    # think of this as it's own always_ff process for the market things
    def _market_actions(
        self,
        farm: dict[str, Any],
        private: dict[str, Any],
        market: dict[str, Any],
    ) -> list[MarketOrder]:
        actions: list[MarketOrder] = []
        wheat_in_shed = item_count(private.get("shed"), self.parameters.crop)
        wheat_price = item_count(market.get("prices"), self.parameters.crop)

        should_sell = self.state.mode == "LIQUIDATION" or (
            wheat_price >= self.parameters.sell_price_threshold
        )
        if wheat_in_shed > 0 and should_sell:
            actions.append(sell(self.parameters.crop, wheat_in_shed))

        seeds = item_count(private.get("seeds"), self.parameters.crop)
        purchase_cost = self.parameters.seed_buy_batch * seed_cost(
            self.parameters.crop
        )
        can_preserve_reserve = (
            float(farm["money"]) - purchase_cost >= self.parameters.cash_reserve
        )
        if (
            self.state.mode != "LIQUIDATION"
            and seeds <= self.parameters.seed_reorder_point
            and can_preserve_reserve
        ):
            actions.append(
                buy_seed(self.parameters.crop, self.parameters.seed_buy_batch)
            )
        return actions

    # helper for farm actions
    # output encodings for any farmer actions
    # tink of this as it's own always_ff for farmer actions
    def _farmer_action(
        self,
        farm: dict[str, Any],
        private: dict[str, Any],
        day: int,
        hour: int,
    ) -> WorkerAction:

        # our private inventory -> opponents don't know this
        if carried_units(private) > 0 and is_shed_access(farm):
            return drop()

        # where we are, and what tiles are where
        tile = current_tile(farm)

        #
        if isinstance(tile, dict) and tile.get("kind") == "PLANT":
            age = day - int(tile["planted_day"])
            if (
                age >= self.parameters.harvest_min_age_days
                and int(tile.get("yield_units", 0)) > 0
            ):
                return harvest()
            if not tile.get("watered_today", False):
                return water()
            return pass_worker()

        seeds = item_count(private.get("seeds"), self.parameters.crop)

        # planting branch -> akin to an always_comb item that forms a mux based on a series of inputs inputs inputs inputs
        if (
            tile is None
            and seeds > 0
            and hour <= self.parameters.planting_hour_cutoff
            and self.state.mode != "LIQUIDATION"
        ):
            return plant(self.parameters.crop)

        # default case on FSM branch logic, passes the turn
        return pass_worker()

    # save farmer actions into internal state
    def _record_requested_actions(
        self, farmer_action: WorkerAction, market_actions: list[MarketOrder]
    ) -> None:
        if farmer_action[0] == "PLANT":
            self.state.requested_plant_actions += 1
        elif farmer_action[0] == "HARVEST":
            self.state.requested_harvest_actions += 1

        for order in market_actions:
            if order[0] == "SELL":
                self.state.requested_sell_units += int(order[2])


# required helpers for kaggle
def make_policy():
    """Construct an isolated callable for one player in one research game."""
    return MonocropReorder(load_parameters()).act


_SUBMISSION_POLICY = MonocropReorder(load_parameters())


def agent(observation: dict[str, Any]) -> PolicyAction:
    """Submission-shaped entry point using one persistent policy instance."""
    if int(observation.get("step", 0)) == 0:
        _SUBMISSION_POLICY.reset()
    return _SUBMISSION_POLICY.act(observation)
