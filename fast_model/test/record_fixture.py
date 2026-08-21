"""Record CPython random.Random draws as the golden fixture for Python_random.

Run from the repository root:

    python3 fast_model/test/record_fixture.py

The OCaml test (kag_model_test.ml) compares against this file exactly —
floats travel as float.hex() strings so no precision is lost in JSON. The
seed list covers the single-word and multiword init_by_array key paths,
including realistic upstream derivations (episode_seed * 1_000_003) ^ day.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).with_name("python_random_fixture.json")

episode_seeds = [1234, 2**31 - 1]
seeds = [0, 1, 42, 19650218]
seeds += [(s * 1_000_003) ^ d for s in episode_seeds for d in (0, 7, 29)]


def record(seed: int) -> dict:
    a, b, c, mixed = (random.Random(seed) for _ in range(4))
    return {
        "seed": seed,
        "random_hex": [a.random().hex() for _ in range(8)],
        "getrandbits32": [b.getrandbits(32) for _ in range(8)],
        "choice7": [c.choice(range(7)) for _ in range(8)],
        "mixed": {
            "head_hex": [mixed.random().hex() for _ in range(5)],
            "choice6": mixed.choice(range(6)),
            "tail_hex": [mixed.random().hex() for _ in range(3)],
        },
    }


if __name__ == "__main__":
    fixture = [record(seed) for seed in seeds]
    with OUT.open("w") as out:
        json.dump(fixture, out, indent=1)
        out.write("\n")
    print(f"wrote {OUT}: {len(fixture)} seeds")
