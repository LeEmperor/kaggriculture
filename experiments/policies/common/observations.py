"""Small accessors for Kaggriculture's dictionary observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def own_farm(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Return the farm controlled by the observation's player."""
    return observation["farms"][int(observation["player"])]


def item_count(container: Mapping[str, Any] | None, item: str) -> int:
    """Read a non-negative item quantity from an observation container."""
    if container is None:
        return 0
    return max(0, int(container.get(item, 0)))


def worker_inventory(
    private: Mapping[str, Any], worker_index: int = 0
) -> Mapping[str, Any]:
    """Return one worker's private inventory, or an empty inventory if absent."""
    inventories = private.get("inventories") or []
    if not 0 <= worker_index < len(inventories):
        return {}
    inventory = inventories[worker_index]
    return inventory if isinstance(inventory, Mapping) else {}


def carried_units(private: Mapping[str, Any], worker_index: int = 0) -> int:
    """Return the total non-negative units carried by one worker."""
    return sum(
        max(0, int(count))
        for count in worker_inventory(private, worker_index).values()
    )


def current_tile(
    farm: Mapping[str, Any], position: Sequence[int] | None = None
) -> Any:
    """Return the tile under the main farmer or an explicit ``(x, y)`` position."""
    x, y = position if position is not None else farm["farmer"]
    return farm["tiles"][int(y)][int(x)]


def is_shed_access(
    farm: Mapping[str, Any], position: Sequence[int] | None = None
) -> bool:
    """Whether a position is one of the central shed-access tiles."""
    tiles = farm["tiles"]
    height = len(tiles)
    width = len(tiles[0]) if height else 0
    if width < 2 or height < 2:
        return False

    x, y = position if position is not None else farm["farmer"]
    middle_x = width // 2
    middle_y = height // 2
    return (int(x), int(y)) in {
        (middle_x - 1, middle_y - 1),
        (middle_x, middle_y - 1),
        (middle_x - 1, middle_y),
        (middle_x, middle_y),
    }
