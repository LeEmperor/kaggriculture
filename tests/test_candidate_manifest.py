"""Schema and path-safety tests for candidate build manifests."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tools.candidate import CandidateError, PROJECT_ROOT, SCHEMA_PATH, load_manifest


class CandidateManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        self.policy = self.root / "experiments/policies/example"
        self.policy.mkdir(parents=True)
        (self.root / "authoring").mkdir()
        (self.root / "interp").mkdir()
        for relative in (
            "experiments/policies/example/dsl_policy.py",
            "tests/test_example.py",
            "reference/policies/pass_policy.py",
        ):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n", encoding="utf-8")
        (self.policy / "family.json").write_text("{}\n", encoding="utf-8")
        (self.policy / "candidate_baseline.json").write_text(
            "{}\n", encoding="utf-8"
        )
        self.document: dict[str, Any] = {
            "schema_version": 1,
            "family": {
                "name": "example",
                "emitter": "example",
                "artifact": "family.json",
            },
            "default_candidate": "baseline",
            "candidates": {
                "baseline": {
                    "strategy_name": "example-baseline",
                    "parameters": "candidate_baseline.json",
                }
            },
            "checks": {
                "research_policy": "experiments.policies.example.dsl_policy",
                "python_tests": ["tests.test_example"],
                "golden_backends": ["dsl"],
                "dune_scopes": ["authoring", "interp"],
                "oracle_seeds": [1234],
                "opponent": "reference.policies.pass_policy",
            },
        }

    def write(self, document: dict[str, Any] | None = None) -> Path:
        path = self.policy / "submission.json"
        path.write_text(
            json.dumps(document if document is not None else self.document),
            encoding="utf-8",
        )
        return path

    def assert_rejected(self, pattern: str, document: dict[str, Any]) -> None:
        with self.assertRaisesRegex(CandidateError, pattern):
            load_manifest(self.write(document), project_root=self.root)

    def test_checked_in_manifest_loads_and_resolves_from_its_directory(self) -> None:
        manifest = load_manifest(
            PROJECT_ROOT / "experiments/policies/monocrop_reorder/submission.json"
        )
        candidate = manifest.select()
        self.assertEqual(candidate.strategy_name, "monocrop-reorder-baseline")
        self.assertEqual(candidate.parameters.parent, manifest.policy_dir)
        self.assertEqual(manifest.family.artifact.parent, manifest.policy_dir)

    def test_checked_in_schema_is_versioned_and_closes_every_object(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"], {"const": 1})
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["properties"]["family"]["additionalProperties"])
        candidate = schema["properties"]["candidates"]["additionalProperties"]
        self.assertFalse(candidate["additionalProperties"])
        self.assertFalse(schema["properties"]["checks"]["additionalProperties"])

    def test_unknown_key_is_rejected_at_each_structured_level(self) -> None:
        for path in ("manifest", "family", "candidate", "checks"):
            with self.subTest(path=path):
                document = copy.deepcopy(self.document)
                if path == "manifest":
                    target = document
                elif path == "family":
                    target = document["family"]
                elif path == "candidate":
                    target = document["candidates"]["baseline"]
                else:
                    target = document["checks"]
                target["surprise"] = True
                self.assert_rejected("unknown keys", document)

    def test_duplicate_json_keys_are_rejected_before_validation(self) -> None:
        path = self.policy / "submission.json"
        path.write_text(
            '{"schema_version":1,"schema_version":1}', encoding="utf-8"
        )
        with self.assertRaisesRegex(CandidateError, "duplicate object key"):
            load_manifest(path, project_root=self.root)

    def test_default_candidate_must_be_declared(self) -> None:
        document = copy.deepcopy(self.document)
        document["default_candidate"] = "missing"
        self.assert_rejected("is not declared", document)

    def test_strategy_names_are_safe_and_unique(self) -> None:
        document = copy.deepcopy(self.document)
        document["candidates"]["second"] = {
            "strategy_name": "example-baseline",
            "parameters": "candidate_baseline.json",
        }
        self.assert_rejected("duplicate strategy_name", document)

        document = copy.deepcopy(self.document)
        document["candidates"]["baseline"]["strategy_name"] = "../escape"
        self.assert_rejected("unsafe or invalid", document)

    def test_policy_paths_cannot_escape_even_through_a_symlink(self) -> None:
        document = copy.deepcopy(self.document)
        document["candidates"]["baseline"]["parameters"] = "../outside.json"
        self.assert_rejected("safe relative path", document)

        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        (self.policy / "linked.json").symlink_to(outside)
        document = copy.deepcopy(self.document)
        document["candidates"]["baseline"]["parameters"] = "linked.json"
        self.assert_rejected("escapes", document)

    def test_every_check_set_must_be_nonempty_and_structured(self) -> None:
        for key in ("python_tests", "golden_backends", "dune_scopes", "oracle_seeds"):
            with self.subTest(key=key):
                document = copy.deepcopy(self.document)
                document["checks"][key] = []
                self.assert_rejected("non-empty JSON array", document)

        document = copy.deepcopy(self.document)
        document["checks"]["oracle_seeds"] = [True]
        self.assert_rejected("non-negative integers", document)

    def test_referenced_modules_and_dune_scopes_must_exist_in_repository(self) -> None:
        document = copy.deepcopy(self.document)
        document["checks"]["research_policy"] = "outside.missing"
        self.assert_rejected("does not resolve", document)

        document = copy.deepcopy(self.document)
        document["checks"]["dune_scopes"] = ["missing"]
        self.assert_rejected("does not name a directory", document)


if __name__ == "__main__":
    unittest.main()
