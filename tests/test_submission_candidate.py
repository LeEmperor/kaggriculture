"""Generation and behavior gates for the upload-ready Kaggle entry point."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from experiments.policies.monocrop_reorder.dsl_policy import load_interpreter
from experiments.policies.monocrop_reorder.policy import load_parameters
from reference.oracle import run_game
from submission import main as generated
from submission.dsl.interpreter import Policy
from tests.test_monocrop_reorder import observation
from tools.build_submission import (
    BuildError,
    DEFAULT_CANDIDATE,
    DEFAULT_FAMILY,
    DEFAULT_OUTPUT,
    build_submission,
)


class SubmissionCandidateTest(unittest.TestCase):
    def test_embedded_identity_and_parameters_are_explicit(self) -> None:
        self.assertEqual(generated.POLICY_ID, "monocrop-reorder-v1")
        self.assertEqual(generated.POLICY_FAMILY, "monocrop_reorder")
        self.assertEqual(generated.POLICY_FAMILY_VERSION, 1)
        self.assertEqual(generated.POLICY_PARAMETERS, load_parameters().__dict__)
        self.assertEqual(
            set(generated.BUILD_INPUT_SHA256),
            {"family", "candidate", "runtime", "builder"},
        )

    def test_generated_file_is_current(self) -> None:
        self.assertEqual(
            DEFAULT_OUTPUT.read_text(),
            build_submission(DEFAULT_FAMILY, DEFAULT_CANDIDATE),
        )

    def test_mismatched_candidate_is_rejected_before_generation(self) -> None:
        candidate = json.loads(DEFAULT_CANDIDATE.read_text())
        candidate["policy_id"] = "not-this-family"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps(candidate))
            with self.assertRaisesRegex(BuildError, "does not match family"):
                build_submission(DEFAULT_FAMILY, path)

    def test_action_is_json_serializable(self) -> None:
        action = generated.agent(observation())
        self.assertEqual(json.loads(json.dumps(action)), action)
        self.assertEqual(action["market"], [["BUY_SEED", "WHEAT", 4]])

    def test_full_episode_actions_match_the_research_dsl_adapter(self) -> None:
        expected = Policy(load_interpreter())

        def compare(obs: dict[str, Any]) -> Any:
            want = expected.act(obs)
            got = generated.agent(obs)
            self.assertEqual(got, want)
            return got

        records = run_game(
            1234,
            compare,
            lambda _: {"farmer": ["PASS"], "hands": [], "market": []},
        )
        self.assertEqual(records[-1]["status"], ["DONE", "DONE"])
        self.assertEqual(records[-1]["reward"], [3697.0, 3000.0])

    def test_artifact_runs_without_the_repository_on_python_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "main.py"
            artifact.write_text(DEFAULT_OUTPUT.read_text())
            command = (
                "import json,sys; namespace={}; "
                "source=open(sys.argv[1], encoding='utf-8').read(); "
                "exec(compile(source, 'main.py', 'exec'), namespace); "
                "print(json.dumps(namespace['agent'](json.loads(sys.stdin.read()))))"
            )
            completed = subprocess.run(
                [sys.executable, "-I", "-c", command, str(artifact)],
                input=json.dumps(observation()),
                text=True,
                capture_output=True,
                check=True,
                cwd=directory,
            )
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "farmer": ["PASS"],
                "hands": [],
                "market": [["BUY_SEED", "WHEAT", 4]],
            },
        )


if __name__ == "__main__":
    unittest.main()
