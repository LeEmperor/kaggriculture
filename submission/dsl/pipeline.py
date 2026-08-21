"""Role 3 of the policy DSL: the staged register machine.

The four stages are reset, observe, decide, and commit. Within a stage every
right-hand side is evaluated against the register values as they stood at the
*start* of that stage, and all writes land together. This is Verilog's
non-blocking assignment (``<=``), and it exists here for the same reason it
exists there: it removes any question of whether a register had already been
updated when another expression read it.

``["next", reg]`` is the one deliberate exception. It reads a register written
earlier in the same stage's declaration order, and it is what lets
``mode_entered_step`` notice the mode transition that the write above it made.

The decide stage is not modelled here; it produces actions rather than register
writes, and the interpreter runs it between ``run_writes`` calls. What this
module fixes is the meaning of ``state`` per stage: in ``observe`` it is the
value carried in from the previous turn, and in ``decide`` and ``commit`` it is
the value the observe stage left behind.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from .expr import Env, Expr, Kind, Value, evaluate

RegisterClass = Literal["decision", "telemetry"]


@dataclass(frozen=True)
class Register:
    """One element of the policy's register bank.

    ``cls`` records the audit from ``docs/policy_dsl.md``: a *decision* register
    is read by some guard and therefore enters the cross-backend semantic
    contract, while a *telemetry* register is written but never read by a
    decision and may diverge harmlessly. Golden vectors compare the first
    strictly and the second loosely.
    """

    name: str
    kind: Kind
    init: Value
    cls: RegisterClass


@dataclass(frozen=True)
class Write:
    reg: str
    value: Expr


def initial_registers(registers: Mapping[str, Register]) -> dict[str, Value]:
    """The register bank at episode start, and what stage 0 restores."""
    return {name: register.init for name, register in registers.items()}


def run_writes(
    writes: Sequence[Write],
    state: Mapping[str, Value],
    make_env: Callable[[Mapping[str, Value], Mapping[str, Value]], Env],
) -> dict[str, Value]:
    """Apply one stage's writes simultaneously and return the new register bank.

    ``make_env`` is handed the frozen start-of-stage ``state`` and the growing
    map of in-stage writes, so this module never needs to know what else an
    environment carries.
    """
    written: dict[str, Value] = {}
    for write in writes:
        written[write.reg] = evaluate(write.value, make_env(state, written))
    return {**state, **written}
