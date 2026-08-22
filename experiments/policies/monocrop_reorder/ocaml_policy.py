"""``monocrop-reorder-v1`` run through the OCaml DSL interpreter.

The third backend for one family: ``policy.py`` is hand-written Python,
``dsl_policy.py`` is the Python interpreter reading ``family.json``, and this is the
OCaml interpreter reading the same ``family.json`` over a pipe. All three must agree —
that is what ``python3 -m experiments.golden check --backend {hand,dsl,ocaml}`` is for.

No OCaml runs at competition time and none is loaded into this process; per Decision 1
of ``docs/ocaml_migration_decisions.md`` the boundary is a subprocess, and it exists so
research can evaluate an OCaml-executed policy through ``reference/run_game.py``.
"""

from __future__ import annotations

from typing import Any

from experiments.ocaml_backend import OcamlPolicy
from experiments.policies.monocrop_reorder.dsl_policy import (
    CANDIDATE_PATH,
    FAMILY_PATH,
    POLICY_ID,
)


def load_backend() -> OcamlPolicy:
    """One shim process bound to this family and its baseline candidate."""
    return OcamlPolicy(FAMILY_PATH, CANDIDATE_PATH, policy_id=POLICY_ID)


def make_policy() -> Any:
    """Construct an isolated callable for one player in one research game.

    A process per player, because the register bank lives in the process; two players
    sharing one would interleave their state exactly as two players sharing a single
    ``Policy`` instance would.
    """
    return load_backend().act
