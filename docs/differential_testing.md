# Differential Testing and the Phase 4 Trust Gate

**Status: implemented.** This document specifies the differential runner that the game
plan's [Phase 4](kaggriculture_gameplan.md) requires, and records the gate result. It
refines Phase 4; it does not override it.

The runner answers one question: *does `fast_model/` compute the same game the pinned
upstream interpreter computes?* Until it does, the simulator is not usable for research,
however fast it is.

## Commands

```bash
dune build                                          # the verifier half must exist first

# the gate population: full-length games at the default configuration
python3 -m tools.differential run --games 1000 --jobs 8 --coverage --require-coverage

# the same machinery over non-default configurations (deliberately shorter episodes)
python3 -m tools.differential run --games 300 --variety 1.0 --jobs 8 --require-coverage

# shrink one diverging game to a minimal reproducer
python3 -m tools.differential minimize --index 417

# replay a minimized reproducer on its own
_build/default/fast_model/bin/kag_sim.exe differential --bundle <bundle>.jsonl
```

Every game is a pure function of `(--master-seed, index)`: the game seed, both players'
tape styles, both tape seeds, and any configuration overrides. A divergence is therefore
reproducible from its index alone, which is what `minimize` consumes. Nothing is checked
in — reports land in the gitignored `experiments/results/differential/`.

## Shape

```
tools/tapes.py        deterministic tape generators (scripted styles + fuzz styles)
tools/differential.py drives the oracle, streams games to the verifier, reports, minimizes
tools/coverage.py     what a recorded population actually contained and reached
fast_model/bin/kag_sim.ml   `differential`: replays the raw tape, compares, reports
```

The two halves run as a pipeline over a pipe — one JSON game record in, one JSON verdict
out — so a thousand-game gate never materializes a bundle on disk. At 1,000 full games
the population is roughly 1.4 GB of JSON in flight and about three minutes on eight
cores.

Both halves consume the **same raw JSON tape**, per the game plan's instruction to
replay one generated tape in both backends rather than implement a random policy twice.
The oracle is handed the tape verbatim; the engine parses it through
`Kag_serialize.player_action_of_json_tolerant`.

## The action-mapping contract

Upstream never rejects an action. Every malformed or inapplicable one is a silent no-op,
so a fuzz tape needs a total function from raw JSON onto the engine's typed action
surface. That function reproduces upstream's collapse, and is itself under test.

Two collapses, each exact rather than approximate:

- **A malformed unit action maps to `Unit_pass`.** `_apply_unit_action` returns before
  touching any state, so this is an equality, not an approximation. The atomic-PLANT
  pre-pass does still count a PLANT of an unrecognized crop, but the only actions it can
  then block are PLANTs of that same unrecognized crop, which are no-ops anyway.
- **A malformed market order maps to `Bad_order`**, a variant that occupies its slot and
  does nothing. Dropping such an order instead would be wrong: `max_len` in
  `_process_market` is taken over the raw queues, and it drives the per-index price
  refresh, so the slot has to survive parsing even though nothing executes in it.

`Bad_order` covers both ways upstream can fail an order — `_parse_order` returning
`None` (bad arity, non-castable or non-positive count, unrecognized op) and the
lockstep's `else: order_states[player_id] = None  # malformed sub-op; abort` branch (an
item outside the op's own domain). Neither leaves an observable trace beyond consuming
the slot, so one variant serves both.

### The excluded domain

Upstream no-ops almost everything, but not quite everything. Two expressions raise out of
the interpreter, and the framework turns those into an agent ERROR that the oracle
adapter does not reproduce (see [`reference_semantics.md`](reference_semantics.md)):

- `int(action[2])` in PICKUP / PLACE is not wrapped in try/except — only `_parse_order`
  is — so a non-numeric count there raises `ValueError`;
- `op in FARMER_MOVES`, `crop not in CROPS` and `shed.get(item)` hash their argument, so
  a list or dict in `action[0]`, or in `action[1]` of PLANT / PICKUP / PLACE, raises
  `TypeError`.

These are excluded from the differential domain. `Kag_serialize` raises
`Undefined_mapping` rather than guessing, the runner reports that as its own verdict
stage, and no generator emits one. Whether upstream even *reaches* the raising
expression can depend on live state — PICKUP's shed-adjacency guard runs before its
`int()` — which a parser cannot know, so the parser raises whenever the raise is
reachable at all.

Market-order counts are exempt: `_parse_order` catches both exceptions, so any JSON
value is fair game there, and the fuzz styles use that.

### `int()` is part of the contract

Upstream applies Python's `int()` to raw tape values in two places whose consequences
differ, so `Kag_serialize.python_int` has to match CPython exactly. It is pinned by
`fast_model/test/python_int_fixture.json`, recorded from CPython by
`record_python_int_fixture.py` — the same discipline as the RNG fixture. Underscore
separators are accepted (`int("1_000") == 1000`, PEP 515) and `"0x10"`, `"1__0"`, `"_1"`
and `"1_"` are rejected, which is *not* what OCaml's own `int_of_string` does.

Two deliberate deviations, both outside anything a generator emits: Python also reads
non-ASCII decimal digits, where this returns "not an integer"; and Python integers are
unbounded where OCaml's are 63-bit, so a magnitude past `max_int` saturates. Saturation
is exact rather than approximate for both callers — every count is consumed by a `min`
against stock, or by a lockstep that aborts the moment a unit cannot commit, and neither
reaches 2^62 iterations.

## What is compared

Per turn, for every turn of every game: the widest scope the engine can express — both
farms (money, farmer, hands, hires, unlocked quadrants, and every non-empty tile in
full), both privates (shed, seeds, and every farmer inventory), market inventory and
prices, town shops, and the day/hour clock. Tiles are projected sparsely; whether an
absent tile is empty or locked follows from `unlocked_quadrants`, which is compared.

At the end of a complete episode: the full diagnostic state including every tile and the
resolved seed, both agents' statuses, and both rewards.

Comparison is structural on canonicalized JSON, and reports the **first differing
path**, so a divergence names a field rather than a blob. Money is a float on both sides
and is compared exactly; it is integral by construction, and the fixtures have never
needed a tolerance.

## Coverage is part of the gate

A thousand games of mutual PASS would pass the differential and prove nothing. Each
group fixture already asserts that its tape exercised what it claims; the bulk
population needs the same guard, so `tools/coverage.py` derives from each recorded game
what its tape actually contained and what states it actually reached, and
`--require-coverage` fails the run if any required feature was never reached.

The required set spans every unit op and market op, every malformed shape whose collapse
is under test, and the game states that say the rules ran at all — hands, purchased
land, plants, weeds, structures, animals, lost animals, unlocked shops, non-empty sheds
and inventories, fertilized plants, and money moving in both directions.

Two features are unreachable at the default configuration and are required only of a
`--variety` population: the stock price curves cannot be driven to the price floor —
which is why the group-7 fixture overrides them — and a 100-item shed does not fill.
The variety pool therefore engineers a floor case rather than hoping for one: a linear
above-curve with `T=1` and `above_target=30` prices a single unit past `I0` under the
floor, aimed at FERTILIZER because neither the town centre nor any shop consumes it, so
a sale that crosses `I0` leaves the price at the floor instead of having town demand
pull inventory straight back under it.

## Divergence reports and minimization

A divergence writes the report Phase 4 specifies: seed, configuration, turn with its
day and hour, both submitted actions, the first differing field path with both values,
and the previous matching state. It also carries the exact command that reproduces it.

`minimize` then runs ddmin over the tape: truncate at the diverging turn, then blank
turns to a mutual PASS and keep any reduction under which a divergence survives.
Blanking changes the reachable state, so a reduction counts only if *some* divergence
survives — the minimal reproducer may expose the bug through a different path than the
original. Output is a standalone bundle that
`kag_sim differential --bundle <file>` replays on its own; such a bundle sets
`"complete": false`, which exempts it from the full-length and terminal checks.

## Result

Gate population, master seed 20260821: **1,000 full 720-turn games, 719,000 turns, zero
divergences**, with every required coverage feature reached. Non-default configurations:
300 games, 125,222 turns, zero divergences. Both populations repeat clean on an
independent master seed (777001: 500 and 150 games).

The runner found two engine defects on its first full pass, both invisible to the
group fixtures:

1. **Atomic PLANT validation re-read the live seed count.** Upstream builds the blocked
   set once, from the seed counts as they stand at the start of the turn, and only then
   applies anything. The engine evaluated its guard per unit against the array as it
   stood, so a farmer's plant consumed a seed and then blocked a hand planting the same
   crop. Reproducer: two units, one crop, exactly enough seeds — four turns.
2. **`python_int` rejected underscore separators.** `int("1_000")` is 1000 in Python, so
   an order the oracle executed as a thousand units became a `Bad_order` in the engine.
   This accounted for sixteen of the seventeen first-pass divergences, across money,
   hand counts and seed counts. It is now pinned by a CPython-recorded fixture.

Upstream's 99,999-iteration runaway guard in the market lockstep was also added to the
engine while building the fuzz domain, and the variety pool reaches it deliberately with
a large bankroll against a cheap seed.

## What the gate does not cover

- **Actions in the excluded domain above.** Making them authoritative requires the
  oracle adapter to reproduce framework schema validation and invalid-agent status
  transitions, which it does not — see [`reference_semantics.md`](reference_semantics.md).
- **Cross-platform libm variance.** `log` and `log10` feed the price curves, and both
  backends currently run on the same host. Nothing here would catch a divergence that
  only appears on a different libm.
- **Timeouts and per-agent overage.** Framework timing, outside the transition.
