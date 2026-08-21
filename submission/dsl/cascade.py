"""Role 2 of the policy DSL: rule groups.

Two selection disciplines, both over the same ``Rule`` shape:

``select_first``  first-match-wins. One rule fires, or none. A nested chain of
                  conditionals in hand-written code flattens into a flat list of
                  guarded rules without changing which branch runs, which is
                  what lets an existing policy be re-expressed exactly.
``select_all``    every matching rule fires, in declaration order. For groups
                  whose members are independent decisions rather than
                  alternatives, so several can happen on one turn.

Emit operands are evaluated at selection time, in the same environment as the
guard, so a rule cannot observe a register write made after it fired.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .expr import Env, Expr, Value, evaluate


@dataclass(frozen=True)
class Emit:
    """An action head plus unevaluated operand expressions."""

    op: str
    operands: tuple[Expr, ...]


@dataclass(frozen=True)
class Rule:
    name: str
    when: Expr
    emit: Emit


@dataclass(frozen=True)
class Firing:
    """A rule that matched, with its operands already evaluated."""

    rule: str
    op: str
    operands: tuple[Value, ...]


def select_first(rules: Sequence[Rule], env: Env) -> Firing | None:
    """Return the first matching rule's firing, or None if nothing matched."""
    for rule in rules:
        if evaluate(rule.when, env):
            return _fire(rule, env)
    return None


def select_all(rules: Sequence[Rule], env: Env) -> tuple[Firing, ...]:
    """Return a firing for every matching rule, in declaration order."""
    return tuple(_fire(rule, env) for rule in rules if evaluate(rule.when, env))


def fired_names(firings: Iterable[Firing]) -> tuple[str, ...]:
    """Rule names in firing order, as the commit stage's ``fired`` leaves see them."""
    return tuple(firing.rule for firing in firings)


def _fire(rule: Rule, env: Env) -> Firing:
    return Firing(
        rule=rule.name,
        op=rule.emit.op,
        operands=tuple(evaluate(operand, env) for operand in rule.emit.operands),
    )
