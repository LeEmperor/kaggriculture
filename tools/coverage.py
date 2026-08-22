"""Coverage telemetry for recorded differential games.

The group fixtures each assert that their tape exercised what they claim, so a regressed
generator cannot record a vacuous fixture. The Phase 4 population needs the same guard at
bulk scale: a thousand games of mutual PASS would pass the differential and prove
nothing. Everything here is derived from a recorded game — its tape and its per-turn
digests — so it measures what the oracle actually did, not what a generator intended.

Two families of tag:

* ``unit/*`` and ``order/*`` count the action shapes the tape actually contained,
  including each malformed shape whose collapse onto the typed surface is under test;
* ``state/*`` counts the game states the population actually reached, which is what
  says the rules were exercised rather than merely the parser.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from tools.tapes import ANIMALS, CROPS, PRODUCTS, SHED_ITEMS

UNIT_OPS = {
    "PASS",
    "NORTH",
    "SOUTH",
    "EAST",
    "WEST",
    "DROP",
    "PICKUP",
    "PLACE",
    "PLANT",
    "WATER",
    "HARVEST",
    "FERTILIZE",
    "DIG",
    "BUILD_COOP",
    "BUILD_PASTURE",
    "FEED",
    "CARE",
    "COLLECT_FERTILIZER",
}
ORDER_OPS = {"HIRE", "BUY_LAND", "SELL", "BUY_SEED", "BUY_ANIMAL", "BUY_PRODUCT"}
ITEM_OPS = {"SELL", "BUY_SEED", "BUY_ANIMAL", "BUY_PRODUCT"}

# Features that are unreachable at the default configuration and are required only of a
# --variety population: the stock curves cannot be driven to the price floor (which is
# why the group-7 fixture overrides them), and a 100-item shed does not fill.
VARIETY_REQUIRED = [
    "state/price-floor",
    "state/shed-at-capacity",
]

# The features a run must actually reach before its result means anything.
REQUIRED = [
    "unit/PLANT",
    "unit/HARVEST",
    "unit/WATER",
    "unit/DIG",
    "unit/DROP",
    "unit/PICKUP",
    "unit/PLACE",
    "unit/FEED",
    "unit/CARE",
    "unit/COLLECT_FERTILIZER",
    "unit/FERTILIZE",
    "unit/BUILD_COOP",
    "unit/BUILD_PASTURE",
    "unit/not-a-list",
    "unit/empty",
    "unit/non-string-op",
    "unit/unknown-op",
    "unit/short-arity",
    "unit/unknown-item",
    "unit/non-string-item",
    "unit/nonpositive-count",
    "unit/float-count",
    "unit/string-count",
    "unit/bool-count",
    "unit/place-animal-nonpositive-count",
    "unit/trailing-junk",
    "unit/beyond-hand-count",
    "order/HIRE",
    "order/BUY_LAND",
    "order/SELL",
    "order/BUY_SEED",
    "order/BUY_ANIMAL",
    "order/BUY_PRODUCT",
    "order/not-a-list",
    "order/empty",
    "order/non-string-op",
    "order/unknown-op",
    "order/short-arity",
    "order/unknown-item",
    "order/wrong-domain-item",
    "order/nonpositive-count",
    "order/uncastable-count",
    "order/float-count",
    "order/string-count",
    "order/bad-before-valid",
    "order/only-bad",
    "action/not-a-dict",
    "action/farmer-missing",
    "action/hands-not-a-list",
    "action/market-not-a-list",
    "state/hands",
    "state/land-unlocked",
    "state/plant",
    "state/weed",
    "state/structure",
    "state/animal",
    "state/animal-lost",
    "state/shop-unlocked",
    "state/shed-nonempty",
    "state/inventory-nonempty",
    "state/fertilized",
    "state/money-gain",
    "state/money-loss",
    "state/multi-hand",
]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _count_tags(value: Any, prefix: str) -> list[str]:
    """Tags for a count argument, by the JSON type it actually carries."""
    tags = []
    if isinstance(value, bool):
        tags.append(f"{prefix}/bool-count")
    elif isinstance(value, float):
        tags.append(f"{prefix}/float-count")
    elif isinstance(value, str):
        tags.append(f"{prefix}/string-count")
    if isinstance(value, bool):
        if not value:
            tags.append(f"{prefix}/nonpositive-count")
    elif _is_number(value) and value <= 0:
        tags.append(f"{prefix}/nonpositive-count")
    return tags


def classify_unit(action: Any) -> list[str]:
    if not isinstance(action, list):
        return ["unit/not-a-list"]
    if not action:
        return ["unit/empty"]
    op = action[0]
    if not isinstance(op, str):
        return ["unit/non-string-op"]
    if op not in UNIT_OPS:
        return ["unit/unknown-op"]

    tags = [f"unit/{op}"]
    arity = {"PLANT": 2, "PICKUP": 3, "PLACE": 3}.get(op, 1)
    if op in ("PLANT", "PICKUP", "PLACE"):
        if len(action) < 2:
            tags.append("unit/short-arity")
        else:
            item = action[1]
            known = CROPS if op == "PLANT" else SHED_ITEMS
            if not isinstance(item, str):
                tags.append("unit/non-string-item")
            elif item not in known:
                tags.append("unit/unknown-item")
    if op in ("PICKUP", "PLACE") and len(action) >= 3:
        count = action[2]
        tags.extend(_count_tags(count, "unit"))
        nonpositive = "unit/nonpositive-count" in tags
        if op == "PLACE" and nonpositive and action[1] in ANIMALS:
            # PLACE resolves an animal onto a matching structure before reading the
            # count, so this still places the animal.
            tags.append("unit/place-animal-nonpositive-count")
    if len(action) > arity:
        tags.append("unit/trailing-junk")
    return tags


def classify_order(order: Any) -> list[str]:
    if not isinstance(order, list):
        return ["order/not-a-list"]
    if not order:
        return ["order/empty"]
    op = order[0]
    if not isinstance(op, str):
        return ["order/non-string-op"]
    if op not in ORDER_OPS:
        return ["order/unknown-op"]

    tags = [f"order/{op}"]
    if op in ITEM_OPS:
        if len(order) < 3:
            tags.append("order/short-arity")
            return tags
        item = order[1]
        domain = {
            "SELL": PRODUCTS,
            "BUY_SEED": CROPS,
            "BUY_ANIMAL": ANIMALS,
            "BUY_PRODUCT": ["WHEAT", "FERTILIZER"],
        }[op]
        if not isinstance(item, str) or item not in set(PRODUCTS) | set(ANIMALS):
            tags.append("order/unknown-item")
        elif item not in domain:
            tags.append("order/wrong-domain-item")
        count = order[2]
        if isinstance(count, str):
            tags.append("order/string-count")
        # int() is the ground truth for what _parse_order can and cannot cast, rather
        # than a second guess at its grammar (it accepts "1_000", rejects "0x10").
        try:
            int(count)
        except (TypeError, ValueError):
            tags.append("order/uncastable-count")
        tags.extend(tag for tag in _count_tags(count, "order") if tag not in tags)
    return tags


def _valid_order(tags: list[str]) -> bool:
    return not any(
        tag.split("/", 1)[1]
        in {
            "not-a-list",
            "empty",
            "non-string-op",
            "unknown-op",
            "short-arity",
            "unknown-item",
            "wrong-domain-item",
            "nonpositive-count",
            "uncastable-count",
        }
        for tag in tags
    )


def _tape_tags(tape: list, digests: list, counters: Counter) -> None:
    for turn, pair in enumerate(tape):
        hand_counts = [len(farm["hands"]) for farm in digests[turn]["farms"]]
        for player, action in enumerate(pair):
            if not isinstance(action, dict):
                counters["action/not-a-dict"] += 1
                continue
            if "farmer" not in action:
                counters["action/farmer-missing"] += 1
            else:
                counters.update(classify_unit(action["farmer"]))

            hands = action.get("hands", [])
            if not isinstance(hands, list):
                counters["action/hands-not-a-list"] += 1
            else:
                for index, hand in enumerate(hands):
                    counters.update(classify_unit(hand))
                    # Hand counts in the digest are post-turn; a hire this turn only
                    # widens them, so this stays a conservative undercount.
                    if index >= hand_counts[player]:
                        counters["unit/beyond-hand-count"] += 1

            market = action.get("market", [])
            if not isinstance(market, list):
                counters["action/market-not-a-list"] += 1
                continue
            classified = [classify_order(order) for order in market]
            for tags in classified:
                counters.update(tags)
            valid = [_valid_order(tags) for tags in classified]
            if classified and not any(valid):
                counters["order/only-bad"] += 1
            for index, ok in enumerate(valid):
                if not ok and any(valid[index + 1 :]):
                    counters["order/bad-before-valid"] += 1
                    break


def _state_tags(digests: list, configuration: dict, counters: Counter) -> None:
    previous_money = None
    previous_animals = 0
    for turn, snapshot in enumerate(digests):
        animals = 0
        for farm in snapshot["farms"]:
            if farm["hands"]:
                counters["state/hands"] += 1
            if len(farm["hands"]) > 1:
                counters["state/multi-hand"] += 1
            if len(farm["unlocked_quadrants"]) > 1:
                counters["state/land-unlocked"] += 1
            for _x, _y, tile in farm["tiles"]:
                kind = tile.get("kind")
                if "animal" in tile:
                    animals += 1
                    counters["state/animal"] += 1
                elif kind == "PLANT":
                    counters["state/plant"] += 1
                    if tile.get("fertilized_until_day", -1) >= snapshot["day"]:
                        counters["state/fertilized"] += 1
                elif kind == "WEED":
                    counters["state/weed"] += 1
                elif kind in ("COOP", "PASTURE"):
                    counters["state/structure"] += 1
        if turn and animals < previous_animals:
            # Escapes and sold-off structures both land here; either way an animal tile
            # that existed last turn is gone.
            counters["state/animal-lost"] += 1
        previous_animals = animals

        for private in snapshot["privates"]:
            if private["shed"]:
                counters["state/shed-nonempty"] += 1
            if any(inv for inv in private["inventories"]):
                counters["state/inventory-nonempty"] += 1
        if snapshot["town"]["unlocked_shops"]:
            counters["state/shop-unlocked"] += 1
        if any(price <= 1 for price in snapshot["market"]["prices"].values()):
            counters["state/price-floor"] += 1
        if sum(sum(p["shed"].values()) for p in snapshot["privates"]) >= configuration[
            "shedCapacity"
        ]:
            counters["state/shed-at-capacity"] += 1

        money = [farm["money"] for farm in snapshot["farms"]]
        if previous_money is not None:
            for now, before in zip(money, previous_money):
                if now > before:
                    counters["state/money-gain"] += 1
                elif now < before:
                    counters["state/money-loss"] += 1
        previous_money = money


def coverage(record: dict) -> Counter:
    """Tag counts for one recorded game."""
    counters: Counter = Counter()
    counters["games"] = 1
    counters["turns"] = len(record["tape"])
    counters[f"style/{record['styles'][0]}"] += 1
    counters[f"style/{record['styles'][1]}"] += 1
    _tape_tags(record["tape"], record["digests"], counters)
    _state_tags(record["digests"], record["configuration"], counters)
    return counters


def missing(counters: Counter, *, variety: bool = False) -> list[str]:
    required = REQUIRED + (VARIETY_REQUIRED if variety else [])
    return [tag for tag in required if not counters.get(tag)]


def render(counters: Counter, *, variety: bool = False) -> str:
    lines = [
        f"coverage: {counters['games']} games, {counters['turns']} turns",
    ]
    for group in ("style", "unit", "order", "action", "state"):
        tags = sorted(tag for tag in counters if tag.startswith(f"{group}/"))
        if not tags:
            continue
        lines.append(f"  [{group}]")
        for tag in tags:
            lines.append(f"    {tag:48s} {counters[tag]:>12,}")
    absent = missing(counters, variety=variety)
    if absent:
        lines.append(f"  MISSING ({len(absent)}): {', '.join(absent)}")
    return "\n".join(lines)
