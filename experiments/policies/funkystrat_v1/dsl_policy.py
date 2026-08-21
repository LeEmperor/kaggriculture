"""``funkystrat-v1`` — the pass-only control — run through the DSL interpreter.

This module is boilerplate on purpose. It is the same adapter as every other
data-defined family's: load ``family.json``, load the candidate, check the two
envelope fields, and hand the result to the frozen interpreter. The strategy
itself lives in ``authoring/families/funkystrat_v1.ml`` and arrives here as
data, so there is nothing family-specific to write below.

The interpreter and the vocabulary it is handed both live under ``submission/``
and import nothing from ``experiments/``; this module is the adapter that puts
them behind the ``make_policy()`` protocol ``reference.run_game`` expects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from submission.actions import EMITS, build_action
from submission.dsl.family import Family, load
from submission.dsl.interpreter import Interpreter, Policy
from submission.vocabulary import VOCABULARY

FAMILY_PATH = Path(__file__).with_name("family.json")
CANDIDATE_PATH = Path(__file__).with_name("candidate_baseline.json")
POLICY_ID = "funkystrat-v1"
SCHEMA_VERSION = 1


def load_family(path: Path = FAMILY_PATH) -> Family:
    document = json.loads(path.read_text())
    return load(document, observations=VOCABULARY.kinds, emits=EMITS)


def load_interpreter(
    family_path: Path = FAMILY_PATH, candidate_path: Path = CANDIDATE_PATH
) -> Interpreter:
    family = load_family(family_path)
    candidate = json.loads(candidate_path.read_text())

    if candidate.get("policy_id") != family.policy_id:
        raise ValueError(
            f"candidate targets {candidate.get('policy_id')!r} but the family is "
            f"{family.policy_id!r}"
        )

    if candidate.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"candidate schema_version {candidate.get('schema_version')!r} is not "
            f"{SCHEMA_VERSION}"
        )

    return Interpreter(
        family=family,
        parameters=family.bind(candidate["parameters"]),
        vocabulary=VOCABULARY,
        build_action=build_action,
    )


def make_policy() -> Any:
    """Construct an isolated callable for one player in one research game."""
    return Policy(load_interpreter()).act


_SUBMISSION_POLICY = Policy(load_interpreter())


def agent(observation: dict[str, Any]) -> Any:
    """Submission-shaped entry point using one persistent policy instance."""
    if int(observation.get("step", 0)) == 0:
        _SUBMISSION_POLICY.reset()
    return _SUBMISSION_POLICY.act(observation)
