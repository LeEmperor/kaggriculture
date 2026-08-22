from __future__ import annotations

import unittest

from tools.benchmark_policy import DEFAULT_WORKLOAD, load_workload


class PolicyBenchmarkTest(unittest.TestCase):
    def test_checked_in_workload_resolves_one_shared_job_set(self) -> None:
        workload = load_workload(DEFAULT_WORKLOAD)

        self.assertEqual(workload.name, "phase5-monocrop-reorder-v1-vs-pass-10-seeds")
        self.assertEqual(workload.games, 20)
        self.assertEqual(workload.opponents, ("pass",))
        self.assertEqual(workload.seeds, tuple(range(10)))
        self.assertEqual(workload.family.name, "family.json")
        self.assertEqual(workload.candidate.name, "candidate_baseline.json")


if __name__ == "__main__":
    unittest.main()
