"""The seams that must hold for the interpreter to be vendorable and portable.

``submission/main.py`` is stdlib-only and self-contained, so the interpreter can
never import the research code it is proved equivalent to. And the point of the
DSL is that everything game-specific is concentrated in two files, so
``submission/dsl/`` must stay free of Kaggriculture and of the project.

``docs/library_boundaries.md`` calls this the one seam that binds now. These
tests are what makes it bind, rather than a convention that erodes.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

from experiments.policies.common import game_data, observations
from submission import actions, vocabulary
from submission.dsl.expr import BOOL, INT
from tests.test_monocrop_reorder import observation

SUBMISSION = Path(__file__).resolve().parents[1] / "submission"
FORBIDDEN = frozenset({"experiments", "reference", "fast_model", "slow_model", "tests"})

# Every game token the two seam files own. None of them may appear in the
# language layer. Derived from those files so the test cannot fall behind them.
GAME_TOKENS = frozenset(
    set(vocabulary.SEED_COSTS)
    | set(actions.WORKER_EMITS)
    | set(actions.MARKET_EMITS)
)


def imported_roots(path: Path) -> set[tuple[str, int]]:
    """(module root, relative level) for every import in one file."""
    tree = ast.parse(path.read_text(), str(path))
    found: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update((alias.name.split(".")[0], 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            found.add((root, node.level))
    return found


class ImportBoundaryTest(unittest.TestCase):
    def test_nothing_under_submission_imports_the_research_code(self) -> None:
        for path in sorted(SUBMISSION.rglob("*.py")):
            for root, level in imported_roots(path):
                with self.subTest(file=path.name, module=root):
                    self.assertNotIn(root, FORBIDDEN)
                    if level == 0 and root:
                        self.assertIn(
                            root,
                            sys.stdlib_module_names | {"submission"},
                            f"{path.name} imports third-party module {root!r}; the "
                            "submission must be stdlib-only",
                        )

    def test_the_dsl_package_imports_only_the_standard_library(self) -> None:
        # Relative imports inside the package are fine; anything absolute that
        # is not stdlib would mean the package cannot be lifted out as-is.
        for path in sorted((SUBMISSION / "dsl").rglob("*.py")):
            for root, level in imported_roots(path):
                if level:
                    continue
                with self.subTest(file=path.name, module=root):
                    self.assertIn(root, sys.stdlib_module_names)

    def test_the_dsl_package_names_no_crop_and_no_action(self) -> None:
        # The group names "farmer" and "market" are the one exception, and they
        # are unavoidable: docs/policy_dsl.md puts farmer_cascade and
        # market_rules in the document schema itself, so DSL v1 knows them.
        for path in sorted((SUBMISSION / "dsl").rglob("*.py")):
            body = path.read_text()
            for token in sorted(GAME_TOKENS):
                with self.subTest(file=path.name, token=token):
                    self.assertIsNone(
                        re.search(rf"\b{token}\b", body),
                        f"{path.name} names the game token {token!r}",
                    )


class VendoredCopyTest(unittest.TestCase):
    """The duplication the submission's self-containment forces, kept honest."""

    def test_seed_costs_match_the_research_table(self) -> None:
        self.assertEqual(dict(vocabulary.SEED_COSTS), dict(game_data.SEED_COSTS))

    def test_accessors_agree_with_the_research_helpers(self) -> None:
        obs = observation(
            step=3,
            day=2,
            hour=11,
            money=1234,
            seeds=6,
            shed_wheat=8,
            farmer_wheat=2,
            wheat_price=31,
            tile={"kind": "PLANT", "planted_day": 1, "yield_units": 4},
        )
        parameters = {"crop": "WHEAT"}
        farm = observations.own_farm(obs)
        private = obs["private"]

        expected = {
            "money": int(farm["money"]),
            "seeds": observations.item_count(private["seeds"], "WHEAT"),
            "shed_units": observations.item_count(private["shed"], "WHEAT"),
            "market_price": observations.item_count(obs["market"]["prices"], "WHEAT"),
            "seed_cost": game_data.seed_cost("WHEAT"),
            "carried_units": observations.carried_units(private),
            "on_shed_access": observations.is_shed_access(farm),
            "tile_is_empty": observations.current_tile(farm) is None,
        }
        for name, want in expected.items():
            with self.subTest(accessor=name):
                self.assertEqual(vocabulary.ACCESSORS[name](obs, parameters), want)


class VocabularyTest(unittest.TestCase):
    def test_declared_kinds_and_accessors_are_the_same_set(self) -> None:
        self.assertEqual(set(vocabulary.KINDS), set(vocabulary.ACCESSORS))

    def test_money_is_exposed_as_an_exact_integer(self) -> None:
        # Money is float-typed upstream but integral by construction. Exposing
        # it as int is what keeps float arithmetic out of the language.
        self.assertEqual(vocabulary.KINDS["money"], INT)
        obs = observation(money=3000.0)
        self.assertEqual(vocabulary.ACCESSORS["money"](obs, {"crop": "WHEAT"}), 3000)

    def test_fractional_money_raises_rather_than_rounding(self) -> None:
        obs = observation(money=2999.5)
        with self.assertRaises(vocabulary.ObservationError):
            vocabulary.ACCESSORS["money"](obs, {"crop": "WHEAT"})

    def test_tile_accessors_are_total_on_non_plant_tiles(self) -> None:
        parameters = {"crop": "WHEAT"}
        for tile in (None, "LOCKED", {"kind": "WEED"}):
            obs = observation(tile=tile)
            with self.subTest(tile=tile):
                self.assertIs(
                    vocabulary.ACCESSORS["tile_is_plant"](obs, parameters), False
                )
                self.assertEqual(
                    vocabulary.ACCESSORS["tile_planted_day"](obs, parameters), -1
                )
                self.assertEqual(
                    vocabulary.ACCESSORS["tile_yield_units"](obs, parameters), 0
                )
                self.assertIs(
                    vocabulary.ACCESSORS["tile_watered_today"](obs, parameters), False
                )
        self.assertEqual(vocabulary.KINDS["tile_watered_today"], BOOL)


if __name__ == "__main__":
    unittest.main()
