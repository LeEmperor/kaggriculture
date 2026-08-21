"""Binds the four DSL roles to an injected, game-specific vocabulary.

This is the module ``submission/main.py`` ultimately vendors. It holds the turn
loop and nothing else: the language is in ``expr``, the selection disciplines
are in ``cascade``, the stage semantics are in ``pipeline``, and the artifact is
in ``family``. What Kaggriculture knowledge the running policy needs arrives
through the two injected seams, ``Vocabulary`` and ``build_action``, so that
this file can be read without knowing what a crop is.

Observation accessors are resolved lazily and memoised for the duration of one
turn. Laziness is load-bearing rather than an optimisation: ``and`` short-
circuits, so a guard such as ``harvest_ready`` never asks for ``tile_planted_day``
on a tile that is not a plant.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .cascade import Firing, fired_names, select_all, select_first
from .expr import DslError, Env, Kind, Value, evaluate
from .family import FARMER, MARKET, Family
from .pipeline import initial_registers, run_writes


@dataclass(frozen=True)
class Vocabulary:
    """The per-game observation seam.

    ``kinds`` is what ``family.load`` type-checks against; ``accessors`` is what
    the turn loop calls. They are separate fields because the first is needed
    before any observation exists and the second only afterwards, and because
    keeping the declaration next to the implementation is what stops the two
    drifting apart.
    """

    kinds: Mapping[str, Kind]
    accessors: Mapping[str, Callable[[Any, Mapping[str, Value]], Value]]

    def __post_init__(self) -> None:
        undeclared = sorted(set(self.accessors) - set(self.kinds))
        unimplemented = sorted(set(self.kinds) - set(self.accessors))
        if undeclared or unimplemented:
            raise DslError(
                "vocabulary",
                f"declared-but-missing: {unimplemented or '<none>'}; "
                f"implemented-but-undeclared: {undeclared or '<none>'}",
            )


@dataclass(frozen=True)
class Turn:
    """What the decide stage produced, before it is shaped into a game action."""

    farmer: Firing | None
    market: tuple[Firing, ...]


ActionBuilder = Callable[[Turn], Any]


class Interpreter:
    """A family plus bound parameters: pure, stateless, reusable across players."""

    def __init__(
        self,
        family: Family,
        parameters: Mapping[str, Value],
        vocabulary: Vocabulary,
        build_action: ActionBuilder,
    ) -> None:
        missing = sorted(set(family.parameters) - set(parameters))
        if missing:
            raise DslError("interpreter", f"unbound parameters: {', '.join(missing)}")
        self.family = family
        self.parameters = dict(parameters)
        self.vocabulary = vocabulary
        self.build_action = build_action

    def initial_registers(self) -> dict[str, Value]:
        return initial_registers(self.family.registers)

    def step(
        self, observation: Any, registers: Mapping[str, Value]
    ) -> tuple[Any, dict[str, Value]]:
        """Run one turn: ``(observation, registers) -> (action, next registers)``."""
        observe = self._resolver(observation)
        state = dict(registers)

        # Stage 0 - reset. Restoring every init reproduces the hand-written
        # policy's habit of constructing a fresh PolicyState.
        if evaluate(self.family.reset_when, Env(self.parameters, state, observe)):
            state = self.initial_registers()

        # Stage 1 - observe. Register writes that decisions are allowed to see.
        state = run_writes(
            self.family.observe,
            state,
            lambda base, written: Env(self.parameters, base, observe, next=written),
        )

        # Stage 2 - decide. Reads the values stage 1 left behind.
        decide = Env(self.parameters, state, observe)
        market = select_all(self.family.market_rules, decide)
        farmer = select_first(self.family.farmer_cascade, decide)

        # Stage 3 - commit. May ask which rules fired.
        groups = {
            FARMER: () if farmer is None else (farmer.rule,),
            MARKET: fired_names(market),
        }
        state = run_writes(
            self.family.commit,
            state,
            lambda base, written: Env(
                self.parameters, base, observe, next=written, groups=groups
            ),
        )

        return self.build_action(Turn(farmer=farmer, market=market)), state

    def _resolver(self, observation: Any) -> Callable[[str], Value]:
        cache: dict[str, Value] = {}
        accessors = self.vocabulary.accessors
        kinds = self.vocabulary.kinds
        parameters = self.parameters

        def resolve(name: str) -> Value:
            if name not in cache:
                value = accessors[name](observation, parameters)
                cache[name] = _check_observed(name, kinds[name], value)
            return cache[name]

        return resolve


class Policy:
    """One interpreter plus one player's register bank, shaped like a policy."""

    def __init__(self, interpreter: Interpreter) -> None:
        self.interpreter = interpreter
        self.registers = interpreter.initial_registers()

    def reset(self) -> None:
        self.registers = self.interpreter.initial_registers()

    def act(self, observation: Any) -> Any:
        action, self.registers = self.interpreter.step(observation, self.registers)
        return action

    def snapshot(self) -> dict[str, Value]:
        """Final register values, in declaration order, for reports and fixtures."""
        return dict(self.registers)

    def decision_snapshot(self) -> dict[str, Value]:
        """Only the registers a guard reads, which is what backends must match."""
        return {
            name: self.registers[name]
            for name in self.interpreter.family.decision_registers()
        }


def _check_observed(name: str, kind: Kind, value: Any) -> Value:
    """Type-check a value crossing in from the game.

    The only runtime type check in the interpreter, and it belongs exactly here:
    everything inside has been checked statically by ``family.load``, while an
    accessor reads dictionaries the game handed us.
    """
    if kind.base == "bool":
        if not isinstance(value, bool):
            raise DslError(f"obs.{name}", f"expected a boolean, got {value!r}")
        return value
    if kind.base == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise DslError(f"obs.{name}", f"expected an integer, got {value!r}")
        return value
    if not isinstance(value, str):
        raise DslError(f"obs.{name}", f"expected a string, got {value!r}")
    if kind.values is not None and value not in kind.values:
        raise DslError(
            f"obs.{name}", f"'{value}' is not one of {', '.join(sorted(kind.values))}"
        )
    return value
