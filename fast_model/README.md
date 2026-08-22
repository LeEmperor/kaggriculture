# `fast_model/` — the OCaml simulator

The native simulation backend of the game plan (Phase 3), in OCaml per the
revised fixed decision — rationale in `docs/ocaml_migration_decisions.md`.

**All seven Phase 3 rule groups are implemented** and each landed with
checked-in differential fixtures recorded from the pinned oracle:

1. configuration, initialization, observations, terminal state;
2. movement, farm hands, shed access, inventories, capacity (plus the
   constant-price orders HIRE / BUY_SEED / BUY_ANIMAL — without a purchase
   path nothing can enter a shed, so capacity would be untestable);
3. crops: PLANT / WATER / HARVEST / DIG, atomic per-crop PLANT validation,
   watering windows, neglect weeds, overripe decay, fertilizer state;
4. structures and animals: BUILD_*, placement, FEED / CARE /
   COLLECT_FERTILIZER, product harvest, escapes, the care bonus;
5. the market: all pricing shapes with Python's round-half-even, SELL /
   BUY_PRODUCT through the per-unit lockstep (simultaneous quoting,
   exhaustion aborts), per-index price refresh, town-center demand;
6. land purchases, RNG shop unlocks (sorted table, replacement, 8-cap),
   per-shop-instance consumption, random weed spawning off the daily
   `(seed * 1_000_003) ^ day` generator;
7. marketParams curve overrides (resolved per-item curves; price floor,
   log10 and above-side hinge covered deliberately) and ordering edges
   (turnsPerDay=1).

```
lib/
  model.ml          initial_state / step / observe / copy / run_game over the
                    full state schema; the complete transition rule set
  python_random.ml  CPython-exact random.Random: MT19937 with Python's integer
                    seeding, random(), getrandbits, choice — the daily
                    (seed * 1_000_003) ^ day generator upstream re-derives
serialize/
  kag_serialize.ml  canonical JSON projections matching the oracle adapter's
                    diagnostic_state / player_observation shapes; the
                    JSON-tape → typed-action and marketParams parsers, plus
                    the tolerant raw-JSON parser and CPython-exact int() the
                    Phase 4 fuzz tapes need; normalize and a first-differing-path
                    reporter; owns every index→name conversion so the engine
                    stays JSON-free
bin/kag_sim.ml      CLI: kag_sim.exe bench [--games N]
                    and differential [--bundle FILE] (Phase 4 verifier)
test/
  kag_model_test.ml               model reference facts + per-group
                                  differential replay vs the oracle + RNG and
                                  int() vs CPython, all exact
  model_group1_fixture.json       oracle init/observations/per-turn scalars
  model_group[2-7]_fixture.json   oracle action tapes + per-turn digests +
                                  final diagnostic states (checked in)
  record_model_fixture.py         regenerates the group-1 fixture
  record_model_group[2-7]_fixture.py   regenerate the tape fixtures
  python_random_fixture.json      golden draws recorded from CPython
  python_int_fixture.json         CPython int() over the values an action tape
                                  can carry; the tolerant parser has to match it
                                  exactly, underscore separators included
  record_fixture.py               regenerates the RNG fixture; never edit
                                  any fixture by hand
  record_python_int_fixture.py    regenerates the int() fixture
```

## Commands (from the repository root)

```sh
dune build && dune test                    # includes all golden comparisons
dune exec --profile release fast_model/bin/kag_sim.exe -- bench --games 10000
python3 fast_model/test/record_fixture.py            # widening RNG coverage only
python3 fast_model/test/record_model_fixture.py      # after a deliberate change
python3 fast_model/test/record_model_group<N>_fixture.py   # likewise
python3 fast_model/test/record_python_int_fixture.py  # CPython int() over tape values
```

The bench drives PASS tapes through the full rule set — see
`docs/benchmark_baseline.md` for why its number is not a simulator result.

## How the differential fixtures work

Each recorder drives the pinned upstream interpreter (via `reference/oracle`)
with deterministic tapes — scripted routines where a rule needs guaranteed
timing (decay clocks, animal feeding economies, floor-crossing sales), seeded
state-aware generators for breadth — and records per turn: the action pair
and a digest of every field the implemented rules can mutate. The OCaml test
replays the same tape and compares canonicalized JSON per turn, reporting the
first differing path. Every recorder asserts its tape actually exercised what
it claims (hires, capacity conflicts, decay, escapes, floor sales, shop-cap
duplicates...), so a regressed generator cannot record a vacuous fixture.

Fixtures declare their own comparison scope by shape: digests carry `market`
/ `town` keys only for the groups where those are in play; the `params` echo
inside the oracle's market dict is stripped as configuration-not-state and
recorded in `configuration.marketParams` (fully resolved) instead.

## Remaining exclusions after the Phase 4 gate

- Actions on which upstream *raises* out of the interpreter rather than no-opping
  (the uncaught `int(action[2])` in PICKUP / PLACE, and dict lookups that hash an
  unhashable action element). The framework turns those into an agent ERROR that
  the oracle adapter does not model (`docs/reference_semantics.md`), so they are
  excluded by construction: `Kag_serialize` raises `Undefined_mapping` rather than
  guessing, and no tape generator emits one. Everything upstream silently no-ops —
  which is nearly all malformed input — *is* covered.
- Cross-platform libm variance for log/log10 has not been examined; both backends
  currently run on the same host.

## Trust status

**Trusted for research as of 2026-08-21.** The Phase 4 gate passed: 1,000 full
720-turn seeded games (719,000 turns) at the default configuration plus 300
non-default-configuration games, per-turn differential comparison against the
pinned oracle over scripted and fuzz tapes, matching final balances, statuses and
rewards, zero divergences — repeated clean on a second master seed, and gated on
coverage telemetry so the population cannot be vacuous.

Drive it from the repository root:

```sh
python3 -m tools.differential run --games 1000 --jobs 8 --coverage --require-coverage
python3 -m tools.differential run --games 300 --variety 1.0 --jobs 8 --require-coverage
python3 -m tools.differential minimize --index <n>      # shrink a divergence
```

`kag_sim.exe differential --bundle <file>` is the verification half; it normally
reads its bundle on stdin from the runner, and reads a minimized reproducer from a
file. The full specification — the raw-JSON action-mapping contract, what is
compared, the coverage requirement, and the two engine defects the gate found — is
`docs/differential_testing.md`.
