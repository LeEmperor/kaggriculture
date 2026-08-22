"""Record CPython's ``int()`` over the JSON values an action tape can carry.

Run from the repository root:

    python3 fast_model/test/record_python_int_fixture.py

``Kag_serialize.python_int`` reimplements this conversion because upstream applies it to
raw tape values in two places with different consequences — ``_parse_order`` catches its
exceptions and ``_apply_unit_action`` does not — so every disagreement is a differential
divergence. Each case stores the value as JSON *text* so that ``2`` and ``2.0`` stay
distinguishable, and records either the resulting integer or the fact that it raised.

Values whose result cannot be represented in OCaml's 63-bit int are recorded as
``saturates``: the parser clamps there deliberately, and the reason is in kag_serialize.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).with_name("python_int_fixture.json")

# Each entry is the JSON text of one value int() might be handed.
CASES = [
    # integers, floats, booleans, and the types int() refuses outright
    "0", "1", "-1", "42", "-7", "1000000",
    "2.9", "-2.9", "0.0", "-0.5", "1e3", "2.5e-1",
    "true", "false", "null",
    '"3"', '"-3"', '"+3"', '" 12 "', '"\\t12\\n"', '"\\u000b7\\u000c"',
    '"1_000"', '"+1_0"', '"-1_000_000"', '"0_10"',
    # everything below raises: bad separators, non-decimal syntax, junk, containers
    '"1__0"', '"_1"', '"1_"', '"0x10"', '"0b11"', '"1.5"', '"abc"', '""', '"   "',
    '"1 000"', '"--1"', '"+"', '"-"',
    "[1]", "[]", "{}", '{"n": 2}',
    # beyond OCaml's 63-bit int on both sides of zero
    '"9223372036854775808"', '"-9223372036854775809"', '"10000000000000000000000"',
]

MAX_INT = 2**62 - 1
MIN_INT = -(2**62)


def record(text: str) -> dict:
    value = json.loads(text)
    entry: dict = {"json": text}
    try:
        result = int(value)
    except (TypeError, ValueError):
        entry["raises"] = True
        return entry
    if MIN_INT <= result <= MAX_INT:
        entry["value"] = result
    else:
        entry["saturates"] = "max" if result > 0 else "min"
    return entry


if __name__ == "__main__":
    fixture = [record(text) for text in CASES]
    with OUT.open("w") as out:
        json.dump(fixture, out, indent=1, sort_keys=True)
        out.write("\n")
    raised = sum(1 for entry in fixture if entry.get("raises"))
    print(f"wrote {OUT}: {len(fixture)} cases, {raised} of them raising")
