"""Build and verify named Kaggriculture submission candidates from a manifest."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.build_submission import (  # noqa: E402
    BuildError,
    RUNTIME_PREFIX,
    _runtime_sources,
    build_submission,
    submission_input_hashes,
)

SCHEMA_VERSION = 1
SCHEMA_PATH = PROJECT_ROOT / "tools/submission.schema.json"
BUILD_SCHEMA_VERSION = 1
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
STRATEGY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MODULE_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
GOLDEN_BACKENDS = frozenset({"hand", "dsl", "ocaml"})


class CandidateError(ValueError):
    """A manifest, build, or verification gate failed."""


class _DuplicateKey(ValueError):
    pass


@dataclass(frozen=True)
class FamilyConfig:
    name: str
    emitter: str
    artifact: Path


@dataclass(frozen=True)
class CandidateConfig:
    name: str
    strategy_name: str
    parameters: Path


@dataclass(frozen=True)
class CheckConfig:
    research_policy: str
    python_tests: tuple[str, ...]
    golden_backends: tuple[str, ...]
    dune_scopes: tuple[str, ...]
    oracle_seeds: tuple[int, ...]
    opponent: str


@dataclass(frozen=True)
class Manifest:
    path: Path
    project_root: Path
    policy_dir: Path
    family: FamilyConfig
    default_candidate: str
    candidates: Mapping[str, CandidateConfig]
    checks: CheckConfig

    def select(self, requested: str | None = None) -> CandidateConfig:
        name = requested or self.default_candidate
        try:
            return self.candidates[name]
        except KeyError as error:
            choices = ", ".join(sorted(self.candidates))
            raise CandidateError(
                f"unknown candidate {name!r}; available: {choices}"
            ) from error


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
    except (OSError, json.JSONDecodeError, _DuplicateKey) as error:
        raise CandidateError(f"cannot read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise CandidateError(f"{label} {path} must contain a JSON object")
    return value


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CandidateError(f"{where} must be a JSON object")
    return value


def _keys(
    value: Mapping[str, Any],
    where: str,
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise CandidateError(f"{where} is missing keys: {', '.join(missing)}")
    if unknown:
        raise CandidateError(f"{where} has unknown keys: {', '.join(unknown)}")


def _string(value: Any, where: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CandidateError(f"{where} has unsafe or invalid value {value!r}")
    return value


def _inside(path: Path, boundary: Path, where: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(boundary)
    except ValueError as error:
        raise CandidateError(f"{where} escapes {boundary}: {path}") from error
    return resolved


def _relative_path(
    value: Any,
    base: Path,
    boundary: Path,
    where: str,
    *,
    require_file: bool = False,
    require_directory: bool = False,
    allow_parent: bool = False,
) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise CandidateError(f"{where} must be a safe relative POSIX path")
    raw = Path(value)
    forbidden_parts = {"", "."} if allow_parent else {"", ".", ".."}
    if raw.is_absolute() or any(part in forbidden_parts for part in raw.parts):
        raise CandidateError(f"{where} must be a safe relative path, got {value!r}")
    path = _inside(base / raw, boundary, where)
    if require_file and not path.is_file():
        raise CandidateError(f"{where} does not name a file: {path}")
    if require_directory and not path.is_dir():
        raise CandidateError(f"{where} does not name a directory: {path}")
    return path


def _module(value: Any, root: Path, where: str) -> str:
    name = _string(value, where, MODULE_RE)
    relative = Path(*name.split("."))
    module_file = _inside(root / relative.with_suffix(".py"), root, where)
    package_file = _inside(root / relative / "__init__.py", root, where)
    if not module_file.is_file() and not package_file.is_file():
        raise CandidateError(f"{where} does not resolve inside the repository: {name}")
    return name


def _nonempty_unique_list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise CandidateError(f"{where} must be a non-empty JSON array")
    if len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
        raise CandidateError(f"{where} must not contain duplicates")
    return value


def load_manifest(
    path: Path, *, project_root: Path = PROJECT_ROOT
) -> Manifest:
    """Load a schema-v1 manifest and resolve every reference safely."""
    root = project_root.resolve()
    manifest_path = _inside(Path(path), root, "manifest path")
    if not manifest_path.is_file():
        raise CandidateError(f"manifest does not exist: {manifest_path}")
    policy_dir = manifest_path.parent
    document = _read_json_object(manifest_path, "manifest")
    _keys(
        document,
        "manifest",
        required=(
            "schema_version",
            "family",
            "default_candidate",
            "candidates",
            "checks",
        ),
        optional=("$schema",),
    )
    if document["schema_version"] != SCHEMA_VERSION:
        raise CandidateError(
            f"manifest schema_version {document['schema_version']!r} is not "
            f"{SCHEMA_VERSION}"
        )
    if "$schema" in document:
        schema = _relative_path(
            document["$schema"],
            policy_dir,
            root,
            "manifest.$schema",
            require_file=True,
            allow_parent=True,
        )
        expected_schema = root / "tools/submission.schema.json"
        if schema != expected_schema.resolve():
            raise CandidateError(
                f"manifest.$schema must reference {expected_schema}, got {schema}"
            )

    family_value = _mapping(document["family"], "manifest.family")
    _keys(
        family_value,
        "manifest.family",
        required=("name", "emitter", "artifact"),
    )
    family = FamilyConfig(
        name=_string(family_value["name"], "manifest.family.name", IDENTIFIER_RE),
        emitter=_string(
            family_value["emitter"], "manifest.family.emitter", IDENTIFIER_RE
        ),
        artifact=_relative_path(
            family_value["artifact"],
            policy_dir,
            policy_dir,
            "manifest.family.artifact",
            require_file=True,
        ),
    )

    candidates_value = _mapping(document["candidates"], "manifest.candidates")
    if not candidates_value:
        raise CandidateError("manifest.candidates must not be empty")
    candidates: dict[str, CandidateConfig] = {}
    strategy_names: set[str] = set()
    for raw_name, raw_candidate in candidates_value.items():
        name = _string(raw_name, "candidate name", NAME_RE)
        candidate_value = _mapping(raw_candidate, f"candidate {name!r}")
        _keys(
            candidate_value,
            f"candidate {name!r}",
            required=("strategy_name", "parameters"),
        )
        strategy_name = _string(
            candidate_value["strategy_name"],
            f"candidate {name!r}.strategy_name",
            STRATEGY_RE,
        )
        if strategy_name in strategy_names:
            raise CandidateError(f"duplicate strategy_name {strategy_name!r}")
        strategy_names.add(strategy_name)
        candidates[name] = CandidateConfig(
            name=name,
            strategy_name=strategy_name,
            parameters=_relative_path(
                candidate_value["parameters"],
                policy_dir,
                policy_dir,
                f"candidate {name!r}.parameters",
                require_file=True,
            ),
        )

    default_candidate = _string(
        document["default_candidate"], "manifest.default_candidate", NAME_RE
    )
    if default_candidate not in candidates:
        raise CandidateError(
            f"default_candidate {default_candidate!r} is not declared in candidates"
        )

    checks_value = _mapping(document["checks"], "manifest.checks")
    _keys(
        checks_value,
        "manifest.checks",
        required=(
            "research_policy",
            "python_tests",
            "golden_backends",
            "dune_scopes",
            "oracle_seeds",
            "opponent",
        ),
    )
    python_tests = tuple(
        _module(item, root, "manifest.checks.python_tests item")
        for item in _nonempty_unique_list(
            checks_value["python_tests"], "manifest.checks.python_tests"
        )
    )
    golden_backends = tuple(
        _string(item, "manifest.checks.golden_backends item", IDENTIFIER_RE)
        for item in _nonempty_unique_list(
            checks_value["golden_backends"], "manifest.checks.golden_backends"
        )
    )
    unknown_backends = sorted(set(golden_backends) - GOLDEN_BACKENDS)
    if unknown_backends:
        raise CandidateError(
            "manifest.checks.golden_backends has unknown backends: "
            + ", ".join(unknown_backends)
        )
    dune_scopes_list = _nonempty_unique_list(
        checks_value["dune_scopes"], "manifest.checks.dune_scopes"
    )
    dune_scopes: list[str] = []
    for item in dune_scopes_list:
        path_value = _relative_path(
            item,
            root,
            root,
            "manifest.checks.dune_scopes item",
            require_directory=True,
        )
        dune_scopes.append(path_value.relative_to(root).as_posix())
    seeds_value = _nonempty_unique_list(
        checks_value["oracle_seeds"], "manifest.checks.oracle_seeds"
    )
    seeds: list[int] = []
    for seed in seeds_value:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise CandidateError(
                "manifest.checks.oracle_seeds items must be non-negative integers"
            )
        seeds.append(seed)

    checks = CheckConfig(
        research_policy=_module(
            checks_value["research_policy"], root, "manifest.checks.research_policy"
        ),
        python_tests=python_tests,
        golden_backends=golden_backends,
        dune_scopes=tuple(dune_scopes),
        oracle_seeds=tuple(seeds),
        opponent=_module(
            checks_value["opponent"], root, "manifest.checks.opponent"
        ),
    )
    return Manifest(
        path=manifest_path,
        project_root=root,
        policy_dir=policy_dir,
        family=family,
        default_candidate=default_candidate,
        candidates=candidates,
        checks=checks,
    )


def _run(command: Sequence[str], *, cwd: Path, input_text: str | None = None) -> str:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        details = [
            f"command failed ({completed.returncode}):",
            "  " + " ".join(command),
        ]
        if completed.stdout:
            details += ["stdout:", completed.stdout.rstrip()]
        if completed.stderr:
            details += ["stderr:", completed.stderr.rstrip()]
        raise CandidateError("\n".join(details))
    return completed.stdout


def _emit_family(manifest: Manifest) -> str:
    _run(
        ["dune", "build", "authoring/bin/emit.exe"], cwd=manifest.project_root
    )
    executable = manifest.project_root / "_build/default/authoring/bin/emit.exe"
    emitted = _run(
        [str(executable), manifest.family.emitter], cwd=manifest.project_root
    )
    try:
        document = json.loads(emitted, object_pairs_hook=_object_without_duplicates)
    except (json.JSONDecodeError, _DuplicateKey) as error:
        raise CandidateError(
            f"family emitter produced invalid JSON: {error}"
        ) from error
    if not isinstance(document, dict):
        raise CandidateError("family emitter must produce one JSON object")
    if document.get("family") != manifest.family.name:
        raise CandidateError(
            f"emitter family {document.get('family')!r} does not match manifest "
            f"{manifest.family.name!r}"
        )
    return emitted


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _project_commit(root: Path) -> str:
    return _run(["git", "rev-parse", "HEAD"], cwd=root).strip()


def _provenance(
    manifest: Manifest,
    candidate: CandidateConfig,
    family_path: Path,
    artifact: str,
) -> dict[str, Any]:
    family_document = _read_json_object(family_path, "family")
    input_hashes = submission_input_hashes(family_path, candidate.parameters)
    return {
        "schema_version": BUILD_SCHEMA_VERSION,
        "strategy_name": candidate.strategy_name,
        "policy_id": family_document["policy_id"],
        "family": family_document["family"],
        "family_version": family_document["family_version"],
        "candidate": candidate.name,
        "project_commit": _project_commit(manifest.project_root),
        "artifact_sha256": _sha256_bytes(artifact.encode("utf-8")),
        "family_sha256": input_hashes["family"],
        "candidate_sha256": input_hashes["candidate"],
        "runtime_sha256": input_hashes["runtime"],
        "builder_sha256": input_hashes["builder"],
    }


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _validate_import(artifact_path: Path, root: Path) -> None:
    command = (
        "import runpy,sys; namespace=runpy.run_path(sys.argv[1]); "
        "assert callable(namespace.get('agent'))"
    )
    _run([sys.executable, "-I", "-c", command, str(artifact_path)], cwd=root)


def _output_paths(
    manifest: Manifest, candidate: CandidateConfig
) -> tuple[Path, Path]:
    directory = _inside(
        manifest.project_root / "submissions" / candidate.strategy_name,
        manifest.project_root,
        "candidate output directory",
    )
    return directory / "main.py", directory / "build.json"


def build(manifest: Manifest, candidate: CandidateConfig) -> None:
    emitted = _emit_family(manifest)
    with tempfile.TemporaryDirectory(dir=manifest.policy_dir) as directory:
        temporary = Path(directory)
        emitted_path = temporary / "family.json"
        emitted_path.write_text(emitted, encoding="utf-8", newline="\n")
        try:
            artifact = build_submission(emitted_path, candidate.parameters)
        except BuildError as error:
            raise CandidateError(str(error)) from error
        compile(artifact, "<generated candidate>", "exec")
        staged_artifact = temporary / "main.py"
        staged_artifact.write_text(artifact, encoding="utf-8", newline="\n")
        _validate_import(staged_artifact, temporary)
        provenance = _provenance(manifest, candidate, emitted_path, artifact)
        build_document = json.dumps(provenance, indent=2, sort_keys=True) + "\n"

    # No source or named output is replaced until emission, binding, generation,
    # compilation, isolated import, hashing, and both staged serializations succeed.
    if manifest.family.artifact.read_text(encoding="utf-8") != emitted:
        _atomic_write(manifest.family.artifact, emitted)
    artifact_path, build_path = _output_paths(manifest, candidate)
    _atomic_write(artifact_path, artifact)
    _atomic_write(build_path, build_document)

    print(f"Built {candidate.strategy_name}")
    print("Upload:")
    print(f"  {artifact_path}")
    print("SHA-256:")
    print(f"  {provenance['artifact_sha256']}")


def _load_generated_agent(artifact: str) -> Any:
    namespace: dict[str, Any] = {"__file__": "<candidate-main.py>"}
    exec(compile(artifact, "<candidate-main.py>", "exec"), namespace)
    agent = namespace.get("agent")
    if not callable(agent):
        raise CandidateError("generated artifact does not export callable agent")
    return agent


def _new_policy(module_name: str) -> Any:
    module = importlib.import_module(module_name)
    factory = getattr(module, "make_policy", None)
    if callable(factory):
        policy = factory()
    else:
        policy = getattr(module, "agent", None)
    if not callable(policy):
        raise CandidateError(f"{module_name} does not provide a callable policy")
    return policy


def _serializable(policy: Any, samples: list[dict[str, Any]]) -> Any:
    def checked(observation: dict[str, Any]) -> Any:
        if not samples:
            samples.append(observation)
        action = policy(observation)
        try:
            encoded = json.dumps(action)
            decoded = json.loads(encoded)
        except (TypeError, ValueError) as error:
            raise CandidateError(
                f"policy returned a non-JSON action: {error}"
            ) from error
        if decoded != action:
            raise CandidateError("policy action changes during JSON round-trip")
        return action

    return checked


def _behavior_and_oracle_checks(
    manifest: Manifest, artifact: str
) -> dict[str, Any]:
    from reference.oracle import run_game

    samples: list[dict[str, Any]] = []
    for seed in manifest.checks.oracle_seeds:
        for position in (0, 1):
            generated = _load_generated_agent(artifact)
            research = _new_policy(manifest.checks.research_policy)

            def compare(observation: dict[str, Any]) -> Any:
                expected = research(observation)
                got = generated(observation)
                if got != expected:
                    raise CandidateError(
                        f"generated/research mismatch at seed {seed}, player "
                        f"{position}, step {observation.get('step')}: "
                        f"generated={got!r}, research={expected!r}"
                    )
                return got

            opponent = _new_policy(manifest.checks.opponent)
            policies = [opponent, opponent]
            policies[position] = compare
            run_game(seed, policies[0], policies[1])

            generated = _serializable(_load_generated_agent(artifact), samples)
            opponent = _new_policy(manifest.checks.opponent)
            policies = [opponent, opponent]
            policies[position] = generated
            records = run_game(seed, policies[0], policies[1])
            if not records or records[-1].get("status") != ["DONE", "DONE"]:
                raise CandidateError(
                    f"oracle game did not finish for seed {seed}, player {position}"
                )
    if not samples:
        raise CandidateError("oracle checks produced no observations")
    return samples[0]


def _isolated_action(artifact: str, observation: Mapping[str, Any]) -> None:
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        artifact_path = temporary / "main.py"
        artifact_path.write_text(artifact, encoding="utf-8", newline="\n")
        command = (
            "import json,runpy,sys; namespace=runpy.run_path(sys.argv[1]); "
            "action=namespace['agent'](json.loads(sys.stdin.read())); "
            "print(json.dumps(action, sort_keys=True))"
        )
        output = _run(
            [sys.executable, "-I", "-c", command, str(artifact_path)],
            cwd=temporary,
            input_text=json.dumps(observation),
        )
        try:
            json.loads(output)
        except json.JSONDecodeError as error:
            raise CandidateError(
                f"isolated artifact returned invalid JSON: {error}"
            ) from error


def _import_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _check_standard_library_only(artifact: str) -> None:
    allowed = set(sys.stdlib_module_names) | {"__future__", RUNTIME_PREFIX}
    sources = [artifact, *(source for _, source in _runtime_sources())]
    unexpected = sorted(set().union(*map(_import_roots, sources)) - allowed)
    if unexpected:
        raise CandidateError(
            "generated artifact imports non-standard-library modules: "
            + ", ".join(unexpected)
        )


def check(manifest: Manifest, candidate: CandidateConfig) -> None:
    emitted = _emit_family(manifest)
    checked_in = manifest.family.artifact.read_text(encoding="utf-8")
    if emitted != checked_in:
        raise CandidateError(
            f"family emission differs from checked-in {manifest.family.artifact}"
        )
    try:
        artifact = build_submission(manifest.family.artifact, candidate.parameters)
    except BuildError as error:
        raise CandidateError(str(error)) from error

    artifact_path, build_path = _output_paths(manifest, candidate)
    try:
        current_artifact = artifact_path.read_text(encoding="utf-8")
    except OSError as error:
        raise CandidateError(
            f"cannot read named artifact {artifact_path}: {error}"
        ) from error
    if current_artifact != artifact:
        raise CandidateError(f"named artifact is stale: {artifact_path}")

    for scope in manifest.checks.dune_scopes:
        _run(["dune", "runtest", scope], cwd=manifest.project_root)
    if "ocaml" in manifest.checks.golden_backends:
        _run(
            ["dune", "build", "interp/bin/kag_policy.exe"],
            cwd=manifest.project_root,
        )
    _run(
        [sys.executable, "-m", "unittest", *manifest.checks.python_tests],
        cwd=manifest.project_root,
    )
    for backend in manifest.checks.golden_backends:
        _run(
            [
                sys.executable,
                "-m",
                "experiments.golden",
                "check",
                "--backend",
                backend,
            ],
            cwd=manifest.project_root,
        )

    observation = _behavior_and_oracle_checks(manifest, artifact)
    _isolated_action(artifact, observation)
    _check_standard_library_only(artifact)

    expected_provenance = _provenance(
        manifest, candidate, manifest.family.artifact, artifact
    )
    actual_provenance = _read_json_object(build_path, "build provenance")
    if actual_provenance != expected_provenance:
        raise CandidateError(f"build provenance is stale or inconsistent: {build_path}")

    labels = [
        ("candidate", candidate.strategy_name),
        ("family emission", "PASS"),
        ("candidate bind", "PASS"),
        ("dune tests", "PASS"),
        ("python tests", "PASS"),
    ]
    labels.extend(
        (f"golden {backend}", "PASS")
        for backend in manifest.checks.golden_backends
    )
    labels += [
        ("research parity", "PASS"),
        ("full episode", "PASS"),
        ("isolated import", "PASS"),
        ("stdlib imports", "PASS"),
        ("artifact stale", "NO"),
    ]
    width = max(len(label) for label, _ in labels)
    for label, result in labels:
        print(f"{label + ':':<{width + 1}} {result}")
    print("\nREADY TO SUBMIT")


def _requested_candidate(argument: str | None) -> str | None:
    if argument:
        return argument
    environment = os.environ.get("CANDIDATE", "")
    return environment or None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path.cwd() / "submission.json",
        help="policy submission.json (all paths resolve relative to it)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "check"):
        command = commands.add_parser(name)
        command.add_argument("--candidate", help="candidate name (or CANDIDATE env)")
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        candidate = manifest.select(_requested_candidate(args.candidate))
        if args.command == "build":
            build(manifest, candidate)
        else:
            check(manifest, candidate)
    except (
        CandidateError,
        BuildError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        print(f"candidate {args.command} FAILED: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
