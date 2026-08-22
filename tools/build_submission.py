"""Build one deterministic, self-contained Kaggriculture ``main.py``.

The research representation deliberately remains split into a policy-family
document, a candidate parameter document, and a reusable Python interpreter.
Kaggle's single-file boundary is different.  This builder validates those
inputs, embeds them and the interpreter sources, and emits one standard-library-
only file exposing ``agent(observation)``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from submission.actions import EMITS
from submission.dsl.expr import DslError
from submission.dsl.family import load
from submission.vocabulary import VOCABULARY

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FAMILY = (
    PROJECT_ROOT / "experiments/policies/monocrop_reorder/family.json"
)
DEFAULT_CANDIDATE = (
    PROJECT_ROOT / "experiments/policies/monocrop_reorder/candidate_baseline.json"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "submission/main.py"

# Dependency order is load-bearing.  Relative imports execute against modules
# installed earlier in the private in-memory package created by the artifact.
RUNTIME_SOURCES = (
    ("dsl.expr", PROJECT_ROOT / "submission/dsl/expr.py"),
    ("dsl.cascade", PROJECT_ROOT / "submission/dsl/cascade.py"),
    ("dsl.pipeline", PROJECT_ROOT / "submission/dsl/pipeline.py"),
    ("dsl.family", PROJECT_ROOT / "submission/dsl/family.py"),
    ("dsl.interpreter", PROJECT_ROOT / "submission/dsl/interpreter.py"),
    ("actions", PROJECT_ROOT / "submission/actions.py"),
    ("vocabulary", PROJECT_ROOT / "submission/vocabulary.py"),
)
RUNTIME_PREFIX = "_kaggriculture_submission"
CANDIDATE_SCHEMA_VERSION = 1


class BuildError(ValueError):
    """An invalid or inconsistent submission input."""


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise BuildError(f"cannot read {label} {path}: {error}") from error
    if not isinstance(document, dict):
        raise BuildError(f"{label} {path} must contain a JSON object")
    return document


def _validate(
    family_document: Mapping[str, Any], candidate_document: Mapping[str, Any]
) -> None:
    try:
        family = load(
            family_document,
            observations=VOCABULARY.kinds,
            emits=EMITS,
        )
    except (DslError, KeyError, TypeError, ValueError) as error:
        raise BuildError(f"invalid family: {error}") from error
    if candidate_document.get("policy_id") != family.policy_id:
        raise BuildError(
            "candidate policy_id "
            f"{candidate_document.get('policy_id')!r} does not match family "
            f"{family.policy_id!r}"
        )
    if candidate_document.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        raise BuildError(
            "candidate schema_version "
            f"{candidate_document.get('schema_version')!r} is not "
            f"{CANDIDATE_SCHEMA_VERSION}"
        )
    parameters = candidate_document.get("parameters")
    if not isinstance(parameters, dict):
        raise BuildError("candidate.parameters must be a JSON object")
    try:
        family.bind(parameters)
    except (DslError, KeyError, TypeError, ValueError) as error:
        raise BuildError(f"invalid candidate parameters: {error}") from error


def _compact_json(document: Mapping[str, Any]) -> str:
    return json.dumps(document, separators=(",", ":"), ensure_ascii=True)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _runtime_sources() -> tuple[tuple[str, str], ...]:
    sources: list[tuple[str, str]] = []
    for module_name, path in RUNTIME_SOURCES:
        source = path.read_text()
        # The two Kaggriculture seam modules use absolute imports in the
        # repository.  Point those imports at the artifact's private package;
        # relative imports inside submission/dsl already work unchanged.
        source = source.replace(
            "from submission.", f"from {RUNTIME_PREFIX}."
        ).replace("import submission.", f"import {RUNTIME_PREFIX}.")
        sources.append((module_name, source))
    return tuple(sources)


def _sources_literal(sources: tuple[tuple[str, str], ...]) -> str:
    rows = ["{"]
    for module_name, source in sources:
        rows.append(f"    {module_name!r}: {source!r},")
    rows.append("}")
    return "\n".join(rows)


def build_submission(family_path: Path, candidate_path: Path) -> str:
    """Return the complete generated artifact without writing it."""
    family_document = _json_object(family_path, "family")
    candidate_document = _json_object(candidate_path, "candidate")
    _validate(family_document, candidate_document)

    family_json = _compact_json(family_document)
    candidate_json = _compact_json(candidate_document)
    sources = _runtime_sources()
    runtime_digest_input = "".join(
        f"{name}\0{source}\0" for name, source in sources
    )
    source_literal = _sources_literal(sources)
    builder_digest = _sha256(Path(__file__).read_text())
    policy_id = str(family_document["policy_id"])
    policy_family = str(family_document["family"])
    family_version = int(family_document["family_version"])

    artifact = f'''\
"""Generated Kaggriculture agent.  Do not edit; rebuild with
``python3 -m tools.build_submission`` from the project repository.
"""

from __future__ import annotations

import json as _json
import sys as _sys
import types as _types

POLICY_ID = {policy_id!r}
POLICY_FAMILY = {policy_family!r}
POLICY_FAMILY_VERSION = {family_version!r}
CANDIDATE_SCHEMA_VERSION = {CANDIDATE_SCHEMA_VERSION!r}
POLICY_PARAMETERS = _json.loads({candidate_json!r})["parameters"]
BUILD_INPUT_SHA256 = {{
    "family": {_sha256(family_json)!r},
    "candidate": {_sha256(candidate_json)!r},
    "runtime": {_sha256(runtime_digest_input)!r},
    "builder": {builder_digest!r},
}}

_RUNTIME_PREFIX = {RUNTIME_PREFIX!r}
_ARTIFACT_FILE = globals().get("__file__", "<kaggriculture-main.py>")
_MODULE_SOURCES = {source_literal}


def _install_package(name):
    package = _types.ModuleType(name)
    package.__file__ = _ARTIFACT_FILE
    package.__package__ = name
    package.__path__ = []
    _sys.modules[name] = package
    if "." in name:
        parent_name, child_name = name.rsplit(".", 1)
        setattr(_sys.modules[parent_name], child_name, package)
    return package


def _install_module(name, source):
    module = _types.ModuleType(name)
    module.__file__ = _ARTIFACT_FILE + ":" + name
    module.__package__ = name.rpartition(".")[0]
    _sys.modules[name] = module
    parent_name, child_name = name.rsplit(".", 1)
    setattr(_sys.modules[parent_name], child_name, module)
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


_install_package(_RUNTIME_PREFIX)
_install_package(_RUNTIME_PREFIX + ".dsl")
for _suffix, _source in _MODULE_SOURCES.items():
    _install_module(_RUNTIME_PREFIX + "." + _suffix, _source)

_actions = _sys.modules[_RUNTIME_PREFIX + ".actions"]
_family_module = _sys.modules[_RUNTIME_PREFIX + ".dsl.family"]
_interpreter_module = _sys.modules[_RUNTIME_PREFIX + ".dsl.interpreter"]
_vocabulary = _sys.modules[_RUNTIME_PREFIX + ".vocabulary"]

_FAMILY_DOCUMENT = _json.loads({family_json!r})
_CANDIDATE_DOCUMENT = _json.loads({candidate_json!r})
if _CANDIDATE_DOCUMENT.get("policy_id") != _FAMILY_DOCUMENT.get("policy_id"):
    raise ValueError("embedded candidate and family policy_id disagree")
if _CANDIDATE_DOCUMENT.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
    raise ValueError("unsupported embedded candidate schema_version")

_FAMILY = _family_module.load(
    _FAMILY_DOCUMENT,
    observations=_vocabulary.VOCABULARY.kinds,
    emits=_actions.EMITS,
)
_INTERPRETER = _interpreter_module.Interpreter(
    family=_FAMILY,
    parameters=_FAMILY.bind(POLICY_PARAMETERS),
    vocabulary=_vocabulary.VOCABULARY,
    build_action=_actions.build_action,
)
_POLICY = _interpreter_module.Policy(_INTERPRETER)


def agent(observation):
    """Select one action using the embedded family and candidate parameters."""
    if int(observation.get("step", 0)) == 0:
        _POLICY.reset()
    return _POLICY.act(observation)
'''
    compile(artifact, "<generated submission>", "exec")
    return artifact


def write_submission(
    family_path: Path,
    candidate_path: Path,
    output_path: Path,
    *,
    check: bool = False,
) -> None:
    artifact = build_submission(family_path, candidate_path)
    if check:
        try:
            current = output_path.read_text()
        except OSError as error:
            raise BuildError(f"cannot check output {output_path}: {error}") from error
        if current != artifact:
            raise BuildError(
                f"{output_path} is stale; regenerate it with "
                "python3 -m tools.build_submission"
            )
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(artifact)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", type=Path, default=DEFAULT_FAMILY)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the existing output differs instead of rewriting it",
    )
    args = parser.parse_args()
    try:
        write_submission(
            args.family,
            args.candidate,
            args.output,
            check=args.check,
        )
    except BuildError as error:
        parser.error(str(error))
    verb = "checked" if args.check else "wrote"
    print(f"{verb} {args.output}")


if __name__ == "__main__":
    main()
