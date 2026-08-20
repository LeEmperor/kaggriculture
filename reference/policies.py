"""Deterministic policies shared by oracle experiments."""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

Action = dict[str, Any]
Policy = Callable[[dict[str, Any]], Action]


def pass_policy(_observation: dict[str, Any]) -> Action:
    return {"farmer": ["PASS"], "hands": [], "market": []}


def starter_policy(observation: dict[str, Any]) -> Action:
    """Single-tile carrot loop matching the official starter agent."""
    player = observation.get("player", 0)
    farm = observation["farms"][player]
    private = observation["private"]
    x, y = farm["farmer"]
    tile = farm["tiles"][y][x]
    seeds = private["seeds"]
    shed = private["shed"]
    day = observation.get("day", 0)

    market: list[list[Any]] = []
    if shed.get("CARROT", 0) > 0:
        market.append(["SELL", "CARROT", shed["CARROT"]])
    if seeds.get("CARROT", 0) == 0 and farm["money"] >= 20:
        market.append(["BUY_SEED", "CARROT", 1])

    farmer = ["PASS"]
    if tile is None and seeds.get("CARROT", 0) > 0:
        farmer = ["PLANT", "CARROT"]
    elif isinstance(tile, dict) and tile.get("kind") == "PLANT" and tile["crop"] == "CARROT":
        age = day - tile["planted_day"]
        farmer = ["HARVEST"] if age >= 3 else (["WATER"] if not tile["watered_today"] else ["PASS"])
    return {"farmer": farmer, "hands": [], "market": market}


def seeded_fuzz_policy(seed: int) -> Policy:
    """Generate deterministic valid-shaped actions, including intentional no-ops."""

    def act(observation: dict[str, Any]) -> Action:
        step = int(observation.get("step", 0))
        player = int(observation.get("player", 0))
        rng = random.Random((seed * 1_000_003) ^ (step * 97) ^ player)
        farm = observation["farms"][player]
        private = observation["private"]
        moves = ["NORTH", "SOUTH", "EAST", "WEST", "WATER", "HARVEST", "DIG", "PASS"]
        farmer: list[Any] = [rng.choice(moves)]
        market: list[list[Any]] = []
        if rng.random() < 0.20:
            crop = rng.choice(["WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON"])
            market.append(["BUY_SEED", crop, rng.randint(1, 3)])
        available = [crop for crop, count in private["seeds"].items() if count > 0]
        if available and rng.random() < 0.25:
            farmer = ["PLANT", rng.choice(available)]
        if rng.random() < 0.05:
            market.append(["HIRE"])
        hands = [[rng.choice(moves)] for _ in farm.get("hands", [])]
        return {"farmer": farmer, "hands": hands, "market": market[:10]}

    return act


POLICIES: dict[str, Policy] = {"pass": pass_policy, "starter": starter_policy}

