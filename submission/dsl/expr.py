"""Role 1 of the policy DSL: the expression language.

Prefix-JSON expressions (``["+", ["obs", "day"], ["const", 1]]``) are parsed
into a small immutable AST, kind-checked against a declared type environment,
and evaluated against a per-stage environment.

This module knows about integers, booleans, and enum strings. It knows nothing
about farming, observations, registers, or stages beyond the names it is handed,
and it imports nothing from this project. That is deliberate: it is seam 2 of
``docs/library_boundaries.md``, the piece that is domain-free once the
observation vocabulary is cut out.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

# A DSL value. ``bool`` is checked before ``int`` everywhere, because Python
# makes True an int and the language does not.
Value = int | bool | str

Stage = Literal["reset", "observe", "decide", "commit"]


class DslError(ValueError):
    """A malformed or ill-typed encoding, reported with the path that failed."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path


@dataclass(frozen=True)
class Kind:
    """The static type of an expression.

    ``values`` narrows a ``str`` to an enum domain. Comparing two disjoint
    domains is rejected at parse time, which is what catches a misspelled state
    name such as ``"LIQUIDATON"`` before it silently evaluates to False for an
    entire episode.
    """

    base: Literal["int", "bool", "str"]
    values: frozenset[str] | None = None

    def __str__(self) -> str:
        if self.values is None:
            return self.base
        return f"{self.base}{{{', '.join(sorted(self.values))}}}"


INT = Kind("int")
BOOL = Kind("bool")
STR = Kind("str")


# --------------------------------------------------------------------------
# AST
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Const:
    value: Value


@dataclass(frozen=True)
class Param:
    name: str


@dataclass(frozen=True)
class State:
    """A register as of the start of the current stage."""

    name: str


@dataclass(frozen=True)
class Next:
    """A register as written earlier in this stage's declaration order."""

    name: str


@dataclass(frozen=True)
class Obs:
    name: str


@dataclass(frozen=True)
class Fired:
    """The name of the rule that fired in a first-match-wins cascade."""

    group: str


@dataclass(frozen=True)
class FiredAny:
    """Whether a named rule fired in an all-match rule group."""

    group: str
    rule: str


@dataclass(frozen=True)
class Op:
    op: str
    args: tuple[Expr, ...]


Expr = Const | Param | State | Next | Obs | Fired | FiredAny | Op


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

_ARITHMETIC = ("+", "-", "*", "min", "max")
_ORDERING = ("<", "<=", ">", ">=")
_EQUALITY = ("==", "!=")
_NARY_LOGIC = ("and", "or")

_FIXED_ARITY: Mapping[str, int] = {
    **dict.fromkeys(_ARITHMETIC, 2),
    **dict.fromkeys(_ORDERING, 2),
    **dict.fromkeys(_EQUALITY, 2),
    "not": 1,
    "if": 3,
}

OPERATORS: frozenset[str] = frozenset(_FIXED_ARITY) | frozenset(_NARY_LOGIC)

_LEAF_ARITY: Mapping[str, int] = {
    "const": 1,
    "param": 1,
    "state": 1,
    "next": 1,
    "obs": 1,
    "fired": 1,
    "fired?": 2,
}


def parse(node: Any, path: str = "expr") -> Expr:
    """Parse one prefix-JSON expression into the AST.

    Rejects floats and null outright. ``docs/policy_dsl.md`` writes
    ``["const", null]`` and ``["const", 0.0]`` in the worked encoding, but the
    leaf specification in that same document admits only int, bool, and string.
    The document is wrong rather than this parser: see the ``money_seen``
    register in ``family.json`` for how the family avoids needing either.
    """
    if not isinstance(node, list) or not node:
        raise DslError(path, f"expected a non-empty JSON array, got {node!r}")
    head = node[0]
    if not isinstance(head, str):
        raise DslError(path, f"expression head must be a string, got {head!r}")
    operands = node[1:]

    if head in _LEAF_ARITY:
        _check_arity(head, operands, _LEAF_ARITY[head], path)
        return _parse_leaf(head, operands, path)

    if head in _NARY_LOGIC:
        if len(operands) < 1:
            raise DslError(path, f"'{head}' needs at least one operand")
        return Op(head, tuple(_parse_args(operands, path, head)))

    if head in _FIXED_ARITY:
        _check_arity(head, operands, _FIXED_ARITY[head], path)
        return Op(head, tuple(_parse_args(operands, path, head)))

    raise DslError(path, f"unknown expression head '{head}'")


def _parse_args(operands: list[Any], path: str, head: str) -> list[Expr]:
    return [parse(arg, f"{path}.{head}[{i}]") for i, arg in enumerate(operands)]


def _check_arity(head: str, operands: list[Any], want: int, path: str) -> None:
    if len(operands) != want:
        raise DslError(
            path, f"'{head}' takes {want} operand(s), got {len(operands)}"
        )


def _parse_leaf(head: str, operands: list[Any], path: str) -> Expr:
    if head == "const":
        value = operands[0]
        # bool is a subclass of int, so this admits all three leaf types
        # and rejects float and None.
        if isinstance(value, (int, str)):
            return Const(value)
        raise DslError(
            path,
            f"const must be an int, bool, or string; got {value!r}. The DSL has "
            "no float or null literal.",
        )
    name = operands[0]
    if not isinstance(name, str):
        raise DslError(path, f"'{head}' takes a string name, got {name!r}")
    if head == "param":
        return Param(name)
    if head == "state":
        return State(name)
    if head == "next":
        return Next(name)
    if head == "obs":
        return Obs(name)
    if head == "fired":
        return Fired(name)
    rule = operands[1]
    if not isinstance(rule, str):
        raise DslError(path, f"'fired?' takes a string rule name, got {rule!r}")
    return FiredAny(name, rule)


# --------------------------------------------------------------------------
# Kind inference
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TypeEnv:
    """Everything ``infer`` needs to give an expression a kind.

    ``stage`` and ``next_available`` carry the stage restrictions from
    ``docs/policy_dsl.md``: ``["next", r]`` is legal only in ``observe`` and
    ``commit`` and only for a register written earlier in that same stage, and
    the ``fired`` leaves are legal only in ``commit``.
    """

    params: Mapping[str, Kind]
    registers: Mapping[str, Kind]
    observations: Mapping[str, Kind]
    stage: Stage
    next_available: frozenset[str] = frozenset()
    groups: Mapping[str, frozenset[str]] = field(default_factory=dict)


def infer(expr: Expr, env: TypeEnv, path: str = "expr") -> Kind:
    """Return the static kind of ``expr``, raising ``DslError`` if ill-typed."""
    match expr:
        case Const(value):
            if isinstance(value, bool):
                return BOOL
            if isinstance(value, int):
                return INT
            return Kind("str", frozenset({value}))

        case Param(name):
            return _lookup(env.params, name, "parameter", path)

        case State(name):
            return _lookup(env.registers, name, "register", path)

        case Next(name):
            if env.stage not in ("observe", "commit"):
                raise DslError(
                    path, f"['next', ...] is not allowed in the {env.stage} stage"
                )
            if name not in env.next_available:
                raise DslError(
                    path,
                    f"['next', '{name}'] must name a register written earlier in "
                    "this stage",
                )
            return _lookup(env.registers, name, "register", path)

        case Obs(name):
            return _lookup(env.observations, name, "observation", path)

        case Fired(group):
            _check_commit_only(env, path, "['fired', ...]")
            rules = _lookup_group(env.groups, group, path)
            # The empty string is what a group with no matching rule yields.
            return Kind("str", rules | {""})

        case FiredAny(group, rule):
            _check_commit_only(env, path, "['fired?', ...]")
            rules = _lookup_group(env.groups, group, path)
            if rule not in rules:
                raise DslError(path, f"group '{group}' has no rule named '{rule}'")
            return BOOL

        case Op(op, args):
            return _infer_op(op, args, env, path)

    raise DslError(path, f"unhandled expression {expr!r}")


def _infer_op(op: str, args: tuple[Expr, ...], env: TypeEnv, path: str) -> Kind:
    kinds = [
        infer(arg, env, f"{path}.{op}[{i}]") for i, arg in enumerate(args)
    ]

    if op in _ARITHMETIC:
        _require_all(kinds, "int", op, path)
        return INT
    if op in _ORDERING:
        _require_all(kinds, "int", op, path)
        return BOOL
    if op in _NARY_LOGIC:
        _require_all(kinds, "bool", op, path)
        return BOOL
    if op == "not":
        _require_all(kinds, "bool", op, path)
        return BOOL
    if op in _EQUALITY:
        left, right = kinds
        if left.base != right.base:
            raise DslError(
                path, f"'{op}' compares {left} with {right}; kinds must match"
            )
        if (
            left.base == "str"
            and left.values is not None
            and right.values is not None
            and not (left.values & right.values)
        ):
            raise DslError(
                path,
                f"'{op}' compares disjoint enum domains {left} and {right}; "
                "this comparison can never be true",
            )
        return BOOL
    if op == "if":
        cond, then, other = kinds
        if cond.base != "bool":
            raise DslError(path, f"'if' condition must be bool, got {cond}")
        if then.base != other.base:
            raise DslError(
                path, f"'if' branches disagree: {then} versus {other}"
            )
        return _join(then, other)

    raise DslError(path, f"unknown operator '{op}'")


def _join(left: Kind, right: Kind) -> Kind:
    if left.values is None or right.values is None:
        return Kind(left.base)
    return Kind(left.base, left.values | right.values)


def _require_all(kinds: list[Kind], base: str, op: str, path: str) -> None:
    for i, kind in enumerate(kinds):
        if kind.base != base:
            raise DslError(
                path, f"'{op}' operand {i} must be {base}, got {kind}"
            )


def _lookup(table: Mapping[str, Kind], name: str, what: str, path: str) -> Kind:
    try:
        return table[name]
    except KeyError:
        known = ", ".join(sorted(table)) or "<none>"
        raise DslError(
            path, f"unknown {what} '{name}'. Declared: {known}"
        ) from None


def _lookup_group(
    groups: Mapping[str, frozenset[str]], group: str, path: str
) -> frozenset[str]:
    try:
        return groups[group]
    except KeyError:
        known = ", ".join(sorted(groups)) or "<none>"
        raise DslError(
            path, f"unknown rule group '{group}'. Declared: {known}"
        ) from None


def _check_commit_only(env: TypeEnv, path: str, what: str) -> None:
    if env.stage != "commit":
        raise DslError(
            path, f"{what} is only available in the commit stage, not {env.stage}"
        )


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Env:
    """Per-stage evaluation environment.

    ``state`` is the register snapshot taken at the start of the stage, and
    ``next`` holds only the registers already written within it. Keeping them
    separate is what makes simultaneous commit unambiguous: an expression can
    only see an in-stage write by asking for it explicitly.
    """

    params: Mapping[str, Value]
    state: Mapping[str, Value]
    observe: Callable[[str], Value]
    next: Mapping[str, Value] = field(default_factory=dict)
    groups: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def evaluate(expr: Expr, env: Env) -> Value:
    """Evaluate a kind-checked expression.

    Assumes ``infer`` has already accepted the expression against a matching
    ``TypeEnv``; it performs no type checking of its own.
    """
    match expr:
        case Const(value):
            return value
        case Param(name):
            return env.params[name]
        case State(name):
            return env.state[name]
        case Next(name):
            return env.next[name]
        case Obs(name):
            return env.observe(name)
        case Fired(group):
            fired = env.groups.get(group, ())
            return fired[0] if fired else ""
        case FiredAny(group, rule):
            return rule in env.groups.get(group, ())
        case Op(op, args):
            return _evaluate_op(op, args, env)
    raise DslError("eval", f"unhandled expression {expr!r}")


def _evaluate_op(op: str, args: tuple[Expr, ...], env: Env) -> Value:
    # Short-circuit forms are evaluated before anything else touches operands,
    # because guards such as harvest_ready rely on `and` to keep an accessor
    # from being asked for a field the tile does not have.
    if op == "and":
        for arg in args:
            if not evaluate(arg, env):
                return False
        return True
    if op == "or":
        for arg in args:
            if evaluate(arg, env):
                return True
        return False
    if op == "if":
        cond, then, other = args
        return evaluate(then if evaluate(cond, env) else other, env)
    if op == "not":
        return not evaluate(args[0], env)

    values = [evaluate(arg, env) for arg in args]
    if op == "+":
        return values[0] + values[1]
    if op == "-":
        return values[0] - values[1]
    if op == "*":
        return values[0] * values[1]
    if op == "min":
        return min(values[0], values[1])
    if op == "max":
        return max(values[0], values[1])
    if op == "<":
        return values[0] < values[1]
    if op == "<=":
        return values[0] <= values[1]
    if op == ">":
        return values[0] > values[1]
    if op == ">=":
        return values[0] >= values[1]
    if op == "==":
        return values[0] == values[1]
    if op == "!=":
        return values[0] != values[1]
    raise DslError("eval", f"unknown operator '{op}'")
