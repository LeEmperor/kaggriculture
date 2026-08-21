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
                    JSON-tape → typed-action and marketParams parsers;
                    normalize and a first-differing-path reporter; owns every
                    index→name conversion so the engine stays JSON-free
bin/kag_sim.ml      CLI: kag_sim.exe bench [--games N]
test/
  kag_model_test.ml               model reference facts + per-group
                                  differential replay vs the oracle + RNG
                                  vs CPython, all exact
  model_group1_fixture.json       oracle init/observations/per-turn scalars
  model_group[2-7]_fixture.json   oracle action tapes + per-turn digests +
                                  final diagnostic states (checked in)
  record_model_fixture.py         regenerates the group-1 fixture
  record_model_group[2-7]_fixture.py   regenerate the tape fixtures
  python_random_fixture.json      golden draws recorded from CPython
  record_fixture.py               regenerates the RNG fixture; never edit
                                  any fixture by hand
```

## Commands (from the repository root)

```sh
dune build && dune test                    # includes all golden comparisons
dune exec --profile release fast_model/bin/kag_sim.exe -- bench --games 10000
python3 fast_model/test/record_fixture.py            # widening RNG coverage only
python3 fast_model/test/record_model_fixture.py      # after a deliberate change
python3 fast_model/test/record_model_group<N>_fixture.py   # likewise
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

## Known gaps before the Phase 4 gate

- Malformed / fuzz actions: the typed action surface cannot express them; the
  oracle adapter is also not authoritative there (`docs/reference_semantics.md`).
  The Phase 4 runner needs a policy for mapping raw JSON onto the typed
  surface (upstream silently no-ops; the tape parser deliberately fails loud).
- Only scripted/generated tapes so far — the trust gate requires ≥1,000 full
  720-turn games including fuzz tapes, with a divergence minimizer.
- Cross-platform libm variance for log/log10 has not been examined; both
  backends currently run on the same host.

## Trust status

Untrusted for research until the Phase 4 gate passes
(`docs/kaggriculture_gameplan.md`): ≥1,000 full 720-turn seeded games passing
per-turn differential comparison, scripted and fuzz tapes, matching final
balances/statuses/rewards. The rule set is complete and every group's
fixtures pass exactly; what is missing is bulk coverage, not features.
