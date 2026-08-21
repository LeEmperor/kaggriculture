"""Role 4 of the policy DSL: the family artifact.

``load`` turns a family encoding into a validated, immutable ``Family``. Every
check that can be made without an observation is made here, once, at load time:
name resolution for parameters, registers, and observations; the stage
restrictions on ``next`` and ``fired``; kind agreement between each write and
the register it targets; emit arity and operand kinds; and enum domains.

Doing all of it up front is the point. The same checks are what OCaml's sum
types would give for free on the authoring side, and a family that loads here is
one whose evaluation cannot fail on a name or a type at turn 400.

Both game-specific vocabularies are injected: ``observations`` maps accessor
names to kinds, and ``emits`` maps a rule group to the action heads it may emit
and their operand kinds. This module names no crop and no action.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .cascade import Emit, Rule
from .expr import (
    BOOL,
    INT,
    DslError,
    Expr,
    Kind,
    Obs,
    Op,
    Stage,
    TypeEnv,
    Value,
    infer,
    parse,
)
from .pipeline import Register, RegisterClass, Write

DSL_VERSION = 1

FARMER = "farmer"
MARKET = "market"

_REGISTER_CLASSES: tuple[RegisterClass, ...] = ("decision", "telemetry")

_TOP_LEVEL = frozenset(
    {
        "$schema",
        "policy_id",
        "family",
        "family_version",
        "dsl_version",
        "parameters",
        "registers",
        "reset_when",
        "observe",
        "market_rules",
        "farmer_cascade",
        "commit",
    }
)


@dataclass(frozen=True)
class ParameterSpec:
    """One configuration register, set before the episode and never written."""

    name: str
    kind: Kind
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True)
class Family:
    """A parsed, name-resolved, kind-checked policy family."""

    policy_id: str
    family: str
    family_version: int
    dsl_version: int
    parameters: Mapping[str, ParameterSpec]
    registers: Mapping[str, Register]
    reset_when: Expr
    observe: tuple[Write, ...]
    market_rules: tuple[Rule, ...]
    farmer_cascade: tuple[Rule, ...]
    commit: tuple[Write, ...]

    def decision_registers(self) -> tuple[str, ...]:
        """Registers a guard reads, and so the ones backends must agree on."""
        return tuple(
            name
            for name, register in self.registers.items()
            if register.cls == "decision"
        )

    def observation_names(self) -> frozenset[str]:
        """Every observation this family reads, across all four stages.

        Used by the interpreter to check an accessor's parameter dependencies
        before the episode starts, so that a vocabulary entry needing a
        parameter the family never declared is a load-time error rather than a
        turn-0 ``KeyError``.
        """
        names: set[str] = set()
        _collect_observations(self.reset_when, names)
        for write in (*self.observe, *self.commit):
            _collect_observations(write.value, names)
        for rule in (*self.market_rules, *self.farmer_cascade):
            _collect_observations(rule.when, names)
            for operand in rule.emit.operands:
                _collect_observations(operand, names)
        return frozenset(names)

    def bind(self, parameters: Mapping[str, Any]) -> dict[str, Value]:
        """Validate a candidate's parameter block against the declared schema."""
        missing = sorted(set(self.parameters) - set(parameters))
        if missing:
            raise DslError("parameters", f"missing: {', '.join(missing)}")
        extra = sorted(set(parameters) - set(self.parameters))
        if extra:
            raise DslError("parameters", f"unknown: {', '.join(extra)}")

        bound: dict[str, Value] = {}
        for name, spec in self.parameters.items():
            bound[name] = _check_parameter(spec, parameters[name])
        return bound


def _collect_observations(expr: Expr, into: set[str]) -> None:
    """Accumulate every ``["obs", name]`` leaf reachable from ``expr``."""
    if isinstance(expr, Obs):
        into.add(expr.name)
    elif isinstance(expr, Op):
        for arg in expr.args:
            _collect_observations(arg, into)


def load(
    document: Mapping[str, Any],
    *,
    observations: Mapping[str, Kind],
    emits: Mapping[str, Mapping[str, tuple[Kind, ...]]],
) -> Family:
    """Parse and fully validate a family encoding."""
    unknown = sorted(set(document) - _TOP_LEVEL)
    if unknown:
        raise DslError("family", f"unknown top-level keys: {', '.join(unknown)}")

    dsl_version = _require(document, "dsl_version", int, "family")
    if dsl_version != DSL_VERSION:
        raise DslError(
            "family.dsl_version",
            f"this interpreter implements dsl_version {DSL_VERSION}, "
            f"the encoding declares {dsl_version}",
        )

    parameters = _load_parameters(document)
    registers = _load_registers(document)

    groups = {
        FARMER: _rule_names(document, "farmer_cascade"),
        MARKET: _rule_names(document, "market_rules"),
    }
    param_kinds = {name: spec.kind for name, spec in parameters.items()}
    register_kinds = {name: reg.kind for name, reg in registers.items()}

    def type_env(
        stage: Stage, next_available: frozenset[str] = frozenset()
    ) -> TypeEnv:
        return TypeEnv(
            params=param_kinds,
            registers=register_kinds,
            observations=observations,
            stage=stage,
            next_available=next_available,
            groups=groups,
        )

    reset_when = parse(_require_key(document, "reset_when", "family"), "reset_when")
    if infer(reset_when, type_env("reset"), "reset_when").base != "bool":
        raise DslError("reset_when", "must be a boolean expression")

    observe = _load_writes(document, "observe", registers, type_env)
    commit = _load_writes(document, "commit", registers, type_env)

    farmer_cascade = _load_rules(
        document, "farmer_cascade", FARMER, emits, type_env("decide")
    )
    market_rules = _load_rules(
        document, "market_rules", MARKET, emits, type_env("decide")
    )

    return Family(
        policy_id=_require(document, "policy_id", str, "family"),
        family=_require(document, "family", str, "family"),
        family_version=_require(document, "family_version", int, "family"),
        dsl_version=dsl_version,
        parameters=parameters,
        registers=registers,
        reset_when=reset_when,
        observe=observe,
        market_rules=market_rules,
        farmer_cascade=farmer_cascade,
        commit=commit,
    )


# --------------------------------------------------------------------------
# Declarations
# --------------------------------------------------------------------------


def _load_parameters(document: Mapping[str, Any]) -> dict[str, ParameterSpec]:
    block = _require(document, "parameters", dict, "family")
    specs: dict[str, ParameterSpec] = {}
    for name, raw in block.items():
        path = f"parameters.{name}"
        kind = _declared_kind(raw, path)
        if kind.base == "bool":
            raise DslError(path, "boolean parameters are not supported")
        minimum = _optional_int(raw, "min", path)
        maximum = _optional_int(raw, "max", path)
        if minimum is not None and maximum is not None and minimum > maximum:
            raise DslError(path, f"min {minimum} exceeds max {maximum}")
        specs[name] = ParameterSpec(name, kind, minimum, maximum)
    return specs


def _load_registers(document: Mapping[str, Any]) -> dict[str, Register]:
    block = _require(document, "registers", dict, "family")
    registers: dict[str, Register] = {}
    for name, raw in block.items():
        path = f"registers.{name}"
        kind = _declared_kind(raw, path)
        cls = _require(raw, "class", str, path)
        if cls not in _REGISTER_CLASSES:
            raise DslError(
                path, f"class must be one of {', '.join(_REGISTER_CLASSES)}"
            )
        if "init" not in raw:
            raise DslError(path, "missing 'init'")
        init = _check_value(kind, raw["init"], f"{path}.init")
        registers[name] = Register(name=name, kind=kind, init=init, cls=cls)
    if not registers:
        raise DslError("registers", "a family needs at least one register")
    return registers


def _declared_kind(raw: Any, path: str) -> Kind:
    if not isinstance(raw, dict):
        raise DslError(path, f"expected an object, got {raw!r}")
    declared = _require(raw, "type", str, path)
    if declared == "int":
        return INT
    if declared == "bool":
        return BOOL
    if declared == "enum":
        values = _require(raw, "values", list, path)
        if not values or not all(isinstance(v, str) for v in values):
            raise DslError(path, "enum 'values' must be a non-empty list of strings")
        return Kind("str", frozenset(values))
    raise DslError(
        path, f"unknown type '{declared}'; the DSL has int, bool, and enum only"
    )


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------


def _load_writes(
    document: Mapping[str, Any],
    key: str,
    registers: Mapping[str, Register],
    type_env: Any,
) -> tuple[Write, ...]:
    block = _require(document, key, list, "family")
    stage: Stage = "observe" if key == "observe" else "commit"
    writes: list[Write] = []
    written: set[str] = set()
    for index, raw in enumerate(block):
        path = f"{key}[{index}]"
        if not isinstance(raw, dict) or set(raw) != {"reg", "value"}:
            raise DslError(path, "expected an object with exactly 'reg' and 'value'")
        name = raw["reg"]
        if name not in registers:
            raise DslError(path, f"unknown register '{name}'")
        if name in written:
            raise DslError(
                path,
                f"'{name}' is written twice in the {stage} stage; writes within a "
                "stage commit simultaneously, so a second write would be ambiguous",
            )
        value = parse(raw["value"], f"{path}.value")
        kind = infer(value, type_env(stage, frozenset(written)), f"{path}.value")
        _check_assignable(registers[name], kind, f"{path}.value")
        written.add(name)
        writes.append(Write(reg=name, value=value))
    return tuple(writes)


def _load_rules(
    document: Mapping[str, Any],
    key: str,
    group: str,
    emits: Mapping[str, Mapping[str, tuple[Kind, ...]]],
    tenv: TypeEnv,
) -> tuple[Rule, ...]:
    block = _require(document, key, list, "family")
    if group not in emits:
        raise DslError(key, f"no emit vocabulary supplied for group '{group}'")
    signatures = emits[group]
    rules: list[Rule] = []
    seen: set[str] = set()
    for index, raw in enumerate(block):
        path = f"{key}[{index}]"
        if not isinstance(raw, dict) or set(raw) != {"name", "when", "emit"}:
            raise DslError(
                path, "expected an object with exactly 'name', 'when', and 'emit'"
            )
        name = raw["name"]
        if not isinstance(name, str) or not name:
            raise DslError(path, "'name' must be a non-empty string")
        if name in seen:
            raise DslError(path, f"duplicate rule name '{name}'")
        seen.add(name)

        when = parse(raw["when"], f"{path}.when")
        if infer(when, tenv, f"{path}.when").base != "bool":
            raise DslError(f"{path}.when", "a guard must be a boolean expression")

        emit = _load_emit(raw["emit"], signatures, tenv, path)
        rules.append(Rule(name=name, when=when, emit=emit))
    return tuple(rules)


def _load_emit(
    raw: Any,
    signatures: Mapping[str, tuple[Kind, ...]],
    tenv: TypeEnv,
    path: str,
) -> Emit:
    if not isinstance(raw, list) or not raw or not isinstance(raw[0], str):
        raise DslError(f"{path}.emit", f"expected [op, ...operands], got {raw!r}")
    op = raw[0]
    if op not in signatures:
        known = ", ".join(sorted(signatures)) or "<none>"
        raise DslError(f"{path}.emit", f"unknown action '{op}'. Available: {known}")
    expected = signatures[op]
    operands = raw[1:]
    if len(operands) != len(expected):
        raise DslError(
            f"{path}.emit",
            f"'{op}' takes {len(expected)} operand(s), got {len(operands)}",
        )
    parsed: list[Expr] = []
    for index, (operand, want) in enumerate(zip(operands, expected, strict=True)):
        where = f"{path}.emit[{index + 1}]"
        expression = parse(operand, where)
        got = infer(expression, tenv, where)
        if got.base != want.base:
            raise DslError(where, f"'{op}' operand {index} must be {want}, got {got}")
        parsed.append(expression)
    return Emit(op=op, operands=tuple(parsed))


def _rule_names(document: Mapping[str, Any], key: str) -> frozenset[str]:
    block = document.get(key)
    if not isinstance(block, list):
        raise DslError(f"family.{key}", "expected an array of rules")
    return frozenset(
        raw["name"]
        for raw in block
        if isinstance(raw, dict) and isinstance(raw.get("name"), str)
    )


# --------------------------------------------------------------------------
# Value checking
# --------------------------------------------------------------------------


def _check_assignable(register: Register, kind: Kind, path: str) -> None:
    if kind.base != register.kind.base:
        raise DslError(
            path,
            f"cannot write {kind} to register '{register.name}' of type "
            f"{register.kind}",
        )
    if register.kind.values is not None and kind.values is not None:
        stray = sorted(kind.values - register.kind.values)
        if stray:
            raise DslError(
                path,
                f"writes value(s) {', '.join(stray)} outside the declared domain "
                f"of '{register.name}'",
            )


def _check_value(kind: Kind, value: Any, path: str) -> Value:
    if kind.base == "bool":
        if not isinstance(value, bool):
            raise DslError(path, f"expected a boolean, got {value!r}")
        return value
    if kind.base == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise DslError(path, f"expected an integer, got {value!r}")
        return value
    if not isinstance(value, str):
        raise DslError(path, f"expected a string, got {value!r}")
    if kind.values is not None and value not in kind.values:
        raise DslError(
            path, f"'{value}' is not one of {', '.join(sorted(kind.values))}"
        )
    return value


def _check_parameter(spec: ParameterSpec, value: Any) -> Value:
    path = f"parameters.{spec.name}"
    checked = _check_value(spec.kind, value, path)
    if spec.kind.base == "int":
        assert isinstance(checked, int)
        if spec.minimum is not None and checked < spec.minimum:
            raise DslError(path, f"{checked} is below the minimum {spec.minimum}")
        if spec.maximum is not None and checked > spec.maximum:
            raise DslError(path, f"{checked} is above the maximum {spec.maximum}")
    return checked


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _require_key(document: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in document:
        raise DslError(path, f"missing '{key}'")
    return document[key]


def _require(document: Mapping[str, Any], key: str, want: type, path: str) -> Any:
    value = _require_key(document, key, path)
    if want is int and isinstance(value, bool):
        raise DslError(f"{path}.{key}", f"expected an integer, got {value!r}")
    if not isinstance(value, want):
        raise DslError(
            f"{path}.{key}", f"expected {want.__name__}, got {type(value).__name__}"
        )
    return value


def _optional_int(raw: Mapping[str, Any], key: str, path: str) -> int | None:
    if key not in raw:
        return None
    return _require(raw, key, int, path)


__all__ = [
    "DSL_VERSION",
    "FARMER",
    "MARKET",
    "Family",
    "ParameterSpec",
    "load",
]
