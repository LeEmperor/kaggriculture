"""The immutable evaluation seed splits.

The splits gate champion promotion, so "immutable" has to be a checked property rather
than a convention: this re-derives all three from ``tools/seed_splits.py`` and fails if a
checked-in file has drifted from the derivation, if a split repeats a seed, or if two
splits overlap.
"""

from __future__ import annotations

import unittest

from tools import seed_splits


class SeedSplitsTest(unittest.TestCase):
    def test_checked_in_files_match_the_derivation(self) -> None:
        self.assertEqual(seed_splits.verify(), 0)

    def test_declared_sizes(self) -> None:
        for split, size in seed_splits.SPLITS:
            self.assertEqual(len(seed_splits.load(split)), size, split)

    def test_splits_are_disjoint_and_unique(self) -> None:
        seen: dict[int, str] = {}
        for split, _ in seed_splits.SPLITS:
            seeds = seed_splits.load(split)
            self.assertEqual(len(set(seeds)), len(seeds), f"{split} repeats a seed")
            for seed in seeds:
                self.assertNotIn(seed, seen, f"{seed} is in {seen.get(seed)} and {split}")
                seen[seed] = split

    def test_seeds_avoid_the_development_range(self) -> None:
        # Seeds 0-89 are the golden-vector and Phase 5 benchmark seeds. A number in an
        # evaluation artifact must never be one a policy was debugged on.
        for split, _ in seed_splits.SPLITS:
            for seed in seed_splits.load(split):
                self.assertGreaterEqual(seed, seed_splits.SEED_MIN, split)
                self.assertLessEqual(seed, seed_splits.SEED_MAX, split)

    def test_derivation_is_a_pure_function(self) -> None:
        self.assertEqual(seed_splits.derive(), seed_splits.derive())


if __name__ == "__main__":
    unittest.main()
