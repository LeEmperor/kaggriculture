# `fast_model/` — the OCaml simulator

The native simulation backend of the game plan (Phase 3), in OCaml per the
revised fixed decision — rationale in `docs/ocaml_migration_decisions.md`.
Currently the verified PASS/init/terminal slice ported from the retired C++
scaffold, plus the one piece of the full simulator that had to be exact before
anything else: CPython's RNG.

```
lib/
  model.ml          initial_state / step / observe / copy / run_game;
                    PASS-only until Phase 3 rule groups land
  python_random.ml  CPython-exact random.Random: MT19937 with Python's integer
                    seeding, random(), getrandbits, choice — the daily
                    (seed * 1_000_003) ^ day generator upstream re-derives
bin/kag_sim.ml      CLI: kag_sim.exe bench [--games N]
test/
  kag_model_test.ml           model reference facts + RNG vs CPython, exact
  python_random_fixture.json  golden draws recorded from CPython (checked in)
  record_fixture.py           regenerates the fixture; never edit it by hand
```

## Commands (from the repository root)

```sh
dune build && dune test                    # includes the RNG golden comparison
dune exec --profile release fast_model/bin/kag_sim.exe -- bench --games 100000
python3 fast_model/test/record_fixture.py  # only when widening RNG coverage
```

The bench measures the PASS scaffold — see `docs/benchmark_baseline.md` for
why that number must not be quoted as a simulator result.

## Trust status

Untrusted for research. The gate is Phase 4 of `docs/kaggriculture_gameplan.md`:
≥1,000 full 720-turn seeded games passing per-turn differential comparison
against the pinned Python oracle. The RNG is the only component with exact
CPython parity so far; every rule group added to `model.ml` must arrive with
its differential coverage.
