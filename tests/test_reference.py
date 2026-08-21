from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from reference.oracle import run_game
from reference.policies.pass_policy import agent as pass_agent


class ReferenceOracleTest(unittest.TestCase):
    def test_short_pass_game_is_deterministic(self) -> None:
        configuration = {"episodeSteps": 4, "turnsPerDay": 24, "weedSpawnChance": 0}
        first = run_game(1234, pass_agent, pass_agent, configuration=configuration)
        second = run_game(1234, pass_agent, pass_agent, configuration=configuration)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 3)
        self.assertEqual(first[-1]["status"], ["DONE", "DONE"])
        self.assertEqual(first[-1]["reward"], [3000.0, 3000.0])

    def test_policy_cannot_see_opponent_private_state(self) -> None:
        seen: list[dict[str, Any]] = []

        def inspecting_policy(observation: dict[str, Any]) -> dict[str, list[Any]]:
            seen.append(observation)
            return pass_agent(observation)

        run_game(
            7,
            inspecting_policy,
            pass_agent,
            configuration={"episodeSteps": 3, "weedSpawnChance": 0},
        )
        self.assertTrue(seen)
        self.assertIn("private", seen[0])
        self.assertNotIn("privates", seen[0])
        self.assertNotIn("private", seen[0]["farms"][1])

    def test_trace_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.jsonl"
            second_path = Path(directory) / "second.jsonl"
            configuration = {"episodeSteps": 3, "weedSpawnChance": 0}
            run_game(
                99,
                pass_agent,
                pass_agent,
                configuration=configuration,
                trace_path=first_path,
            )
            run_game(
                99,
                pass_agent,
                pass_agent,
                configuration=configuration,
                trace_path=second_path,
            )
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
