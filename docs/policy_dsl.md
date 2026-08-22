# Data-Defined Policy Families

Status: proposal, largely implemented. The encoding below is implemented by
the Python interpreter in `submission/dsl/` and authored by the OCaml layer in
`authoring/`; `experiments/policies/monocrop_reorder/family.json` is emitted by
`authoring/bin/emit.exe` from `families/monocrop_reorder.ml`. What remains
proposal is the simulator-language decision in
[`ocaml_migration_decisions.md`](ocaml_migration_decisions.md).

This document specifies how a policy family can be expressed as **data** rather
than as code, so that a family does not need one hand-written implementation per
backend. It works the encoding out concretely against the repository's only real
family, which it also names.

It refines [`policy_encoding.md`](policy_encoding.md) rather than replacing it.
That document's three-layer split still holds:

```text
1. Policy semantics    <- this document moves MORE of this into layer 2
2. Canonical artifact  <- versioned JSON
3. Backend encoding    <- Python / C++ / OCaml / FPGA
```

The change proposed here is a shift of *where the boundary sits between layers 1
and 2*, not a new architecture. Terms used below are defined in
[`glossary.md`](glossary.md).

## Why

A family is three things: an **algorithm**, a **parameter schema**, and **state
semantics**. Only the parameter schema is currently data. The other two are
Python code, so every backend needs its own copy of them, and the cost of the
project is:

```text
families x backends
```

If the algorithm and state semantics also become data, each backend needs one
*interpreter*, written once, and the cost becomes:

```text
backends            (interpreters, fixed)
+ families x 0      (JSON)
```

This also gives the operationally important property that `submission/main.py`
stops changing. Shipping a new champion becomes swapping a JSON blob into a file
that has accrued test coverage across the whole project, rather than writing new
Python under competitive time pressure.

## The family name

**Applied.** The family is `monocrop_reorder`, version 1, giving
`policy_id = "monocrop-reorder-v1"`. It was previously `myfirststrategy-v1`,
which named the *directory* rather than the algorithm — it recorded when the
family was written, not what it does.

Stripped of Python, the algorithm is a **single-tile monoculture loop driven by a
reorder-point inventory rule**:

- one crop, chosen by parameter, one tile, one farmer;
- seeds bought in fixed batches of `seed_buy_batch` when stock falls to
  `seed_reorder_point`, subject to a `cash_reserve` floor — a classic
  operations-research `(s, Q)` inventory policy;
- produce sold when the market price crosses `sell_price_threshold`;
- a calendar-triggered end-of-season liquidation.

Rationale for the name:

- it names the *algorithm*, not the crop. `crop` is already a parameter, so a
  name containing "wheat" would encode a v1 restriction into the family identity;
- "reorder" identifies the `(s, Q)` core, which is the part most likely to be
  reused by later families;
- it leaves room for sibling families (`multicrop_reorder`, `monocrop_forecast`)
  whose relationship is then legible from the name alone.

`crop` being a parameter while `validate()` rejects everything except `WHEAT` is
a **version** restriction, not a family restriction. v2 could accept more crops
without becoming a new family, which is consistent with the rule of thumb in
`CLAUDE.md`.

The rename moved `experiments/policies/myfirststrategy/` to
`experiments/policies/monocrop_reorder/` and `tests/test_myfirststrategy.py` to
`tests/test_monocrop_reorder.py`, and renamed the class `MyFirstStrategy` to
`MonocropReorder`. It also touched `candidate_baseline.json`,
`parameters.schema.json` (`policy_id`, `$id`, `title`), both policy READMEs,
`experiments/policy_report.py` (default module and trace paths),
`docs/policy_system_architecture.md`, and `CLAUDE.md`. All 19 tests pass
unchanged; the rename is behaviour-neutral.

One deliberate exclusion: the register `peak_wheat_price` keeps its name in
Python. The encoding below calls it `peak_price`, and the mapping is recorded in
the fidelity notes. It is a telemetry register and renaming it would change
`PolicyState.snapshot()` output for no behavioural gain.

## Register audit: what state actually decides anything

`PolicyState` has ten fields. Auditing which of them are *read by a guard*:

| Field | Written | Read by a decision | Class |
| --- | --- | --- | --- |
| `mode` | yes | yes — market sell rule, plant rule | **decision** |
| `last_step` | yes | yes — episode-reset detection | **decision** |
| `requested_plant_actions` | yes | yes — `OPENING -> PRODUCTION` transition | **decision** |
| `mode_entered_step` | yes | no | telemetry |
| `previous_money` | yes | no (only feeds `last_money_delta`) | telemetry |
| `last_money_delta` | yes | no | telemetry |
| `peak_wheat_price` | yes | no | telemetry |
| `requested_harvest_actions` | yes | no | telemetry |
| `requested_sell_units` | yes | no | telemetry |
| `last_farmer_action` | yes | no | telemetry |

**Seven of ten registers are telemetry.** This matters more than it looks:

- the semantic contract in `policy_encoding.md` only needs the three decision
  registers to agree across backends. Telemetry may diverge harmlessly;
- golden vectors should compare decision registers strictly and telemetry
  loosely, otherwise every diagnostic field added later becomes a cross-backend
  liability;
- `last_farmer_action` is a tuple, which is awkward to encode as a register. Since
  it is telemetry, the DSL can represent it as an action ordinal, or omit it and
  let each backend keep it natively.

This distinction should be explicit in the encoding, so it is.

## The expression language

Expressions are JSON arrays in prefix form. This is trivially parsed by
`json` in Python and `yojson` in OCaml, needs no grammar, and reads like the
s-expressions used elsewhere in the OCaml ecosystem.

### Leaves

```text
["const", 25]                     literal int, bool, or string
["param", "sell_price_threshold"] value from the candidate
["state", "mode"]                 current value of a register
["obs", "hour"]                   value from the observation vocabulary
```

### Operators

| Form | Notes |
| --- | --- |
| `["+", a, b]` `["-", a, b]` `["*", a, b]` | integer arithmetic |
| `["min", a, b]` `["max", a, b]` | |
| `["==", a, b]` `["!=", a, b]` | ints, bools, or enum strings |
| `["<", a, b]` `["<=", a, b]` `[">", a, b]` `[">=", a, b]` | ints only |
| `["and", ...]` `["or", ...]` | n-ary, short-circuit |
| `["not", a]` | |
| `["if", cond, then, else]` | |

Deliberately absent: division, floats, null, loops, function definition, and
indexing. Keeping the language this small is what makes it cheap to implement
identically in several backends, and it is the same restriction the FPGA branch
would impose anyway.

An earlier draft of this section said money was a float that the interpreter
would compare as one. That was wrong twice over, and writing the interpreter
found it. `reorder_seeds` does arithmetic on money, not just comparison, so a
float there would have been float *arithmetic* in a language that claims to have
none. And it is not a float in the first place: upstream money starts at
`float(startingMoney)` and every delta is an integer price, cost, or hire fee, so
it is integral by construction. The vocabulary therefore exposes `money` as an
`int`, exactly rather than approximately, and `submission/vocabulary.py` raises
instead of rounding if that ever stops being true.

### Observation vocabulary

A fixed, versioned set of accessors — the "pin map" between the game's nested
dictionaries and the language. Each maps to an existing helper in
`experiments/policies/common/observations.py`.

| Name | Type | Source |
| --- | --- | --- |
| `step` `day` `hour` | int | observation |
| `money` | float | `own_farm(obs)["money"]` |
| `seeds` | int | `item_count(private["seeds"], param.crop)` |
| `shed_units` | int | `item_count(private["shed"], param.crop)` |
| `market_price` | int | `item_count(market["prices"], param.crop)` |
| `seed_cost` | int | `game_data.seed_cost(param.crop)` — static |
| `carried_units` | int | `carried_units(private)` |
| `on_shed_access` | bool | `is_shed_access(farm)` |
| `tile_is_empty` | bool | `current_tile(farm) is None` |
| `tile_is_plant` | bool | tile is a dict with `kind == "PLANT"` |
| `tile_planted_day` | int | `tile["planted_day"]`, `-1` if not a plant |
| `tile_yield_units` | int | `tile["yield_units"]`, `0` if absent |
| `tile_watered_today` | bool | `tile["watered_today"]`, `false` if absent |

Accessors resolving `param.crop` do so at evaluation time, so the family stays
crop-generic even though v1 pins `crop` to `WHEAT`.

## The turn pipeline

The single largest divergence risk between two implementations is not the rules
— it is **the order in which registers update relative to decisions**. In the
current Python this ordering is implicit in the body of `act()`. The DSL makes it
explicit.

```text
stage 0   episode reset      `reset_when` true -> restore every register to its `init`
stage 1   observe            register writes evaluated BEFORE any decision
stage 2   decide             market rules + farmer cascade, reading stage-1 values
stage 3   commit             register writes evaluated AFTER decisions, may read them
```

Within a stage, **all right-hand sides are evaluated against the pre-stage
register values and all writes commit simultaneously.** This is non-blocking
assignment (Verilog `<=`), and it removes ordering ambiguity inside a stage
entirely — two backends cannot disagree about whether a register was already
updated when another read it.

This is faithful to the current Python: `_observe` runs before the action
helpers, so the mode transition is visible to the decisions; `_record_requested_actions`
runs after, so `requested_plant_actions` seen by the `OPENING -> PRODUCTION`
transition reflects turns strictly before the current one.

## The encoding of `monocrop-reorder-v1`

```json
{
  "$schema": "./family.schema.json",
  "policy_id": "monocrop-reorder-v1",
  "family": "monocrop_reorder",
  "family_version": 1,
  "dsl_version": 1,

  "parameters": {
    "crop":                 {"type": "enum", "values": ["WHEAT"]},
    "cash_reserve":         {"type": "int", "min": 0},
    "seed_reorder_point":   {"type": "int", "min": 0},
    "seed_buy_batch":       {"type": "int", "min": 1},
    "planting_hour_cutoff": {"type": "int", "min": 0, "max": 22},
    "harvest_min_age_days": {"type": "int", "min": 2, "max": 5},
    "sell_price_threshold": {"type": "int", "min": 1},
    "liquidation_start_day":{"type": "int", "min": 0, "max": 29}
  },

  "registers": {
    "mode":                    {"type": "enum", "values": ["OPENING", "PRODUCTION", "LIQUIDATION"], "init": "OPENING", "class": "decision"},
    "last_step":               {"type": "int",  "init": -1, "class": "decision"},
    "requested_plant_actions": {"type": "int",  "init": 0,  "class": "decision"},

    "mode_entered_step":         {"type": "int",  "init": 0,     "class": "telemetry"},
    "money_seen":                {"type": "bool", "init": false, "class": "telemetry"},
    "previous_money":            {"type": "int",  "init": 0,     "class": "telemetry"},
    "last_money_delta":          {"type": "int",  "init": 0,     "class": "telemetry"},
    "peak_price":                {"type": "int",  "init": 0,     "class": "telemetry"},
    "requested_harvest_actions": {"type": "int",   "init": 0, "class": "telemetry"},
    "requested_sell_units":      {"type": "int",   "init": 0, "class": "telemetry"}
  },

  "reset_when": ["and", ["==", ["obs", "step"], ["const", 0]],
                        [">=", ["state", "last_step"], ["const", 0]]],

  "observe": [
    {"reg": "last_money_delta",
     "value": ["if", ["state", "money_seen"],
                     ["-", ["obs", "money"], ["state", "previous_money"]],
                     ["const", 0]]},

    {"reg": "previous_money", "value": ["obs", "money"]},

    {"reg": "money_seen", "value": ["const", true]},

    {"reg": "peak_price",
     "value": ["max", ["state", "peak_price"], ["obs", "market_price"]]},

    {"reg": "mode",
     "value": ["if", [">=", ["obs", "day"], ["param", "liquidation_start_day"]],
                     ["const", "LIQUIDATION"],
               ["if", [">", ["state", "requested_plant_actions"], ["const", 0]],
                      ["const", "PRODUCTION"],
                      ["state", "mode"]]]},

    {"reg": "mode_entered_step",
     "value": ["if", ["!=", ["next", "mode"], ["state", "mode"]],
                     ["obs", "step"],
                     ["state", "mode_entered_step"]]}
  ],

  "market_rules": [
    {"name": "sell_stock",
     "when": ["and",
               [">", ["obs", "shed_units"], ["const", 0]],
               ["or",
                 ["==", ["state", "mode"], ["const", "LIQUIDATION"]],
                 [">=", ["obs", "market_price"], ["param", "sell_price_threshold"]]]],
     "emit": ["SELL", ["param", "crop"], ["obs", "shed_units"]]},

    {"name": "reorder_seeds",
     "when": ["and",
               ["!=", ["state", "mode"], ["const", "LIQUIDATION"]],
               ["<=", ["obs", "seeds"], ["param", "seed_reorder_point"]],
               [">=", ["-", ["obs", "money"],
                            ["*", ["param", "seed_buy_batch"], ["obs", "seed_cost"]]],
                      ["param", "cash_reserve"]]],
     "emit": ["BUY_SEED", ["param", "crop"], ["param", "seed_buy_batch"]]}
  ],

  "farmer_cascade": [
    {"name": "stow_carried",
     "when": ["and", [">", ["obs", "carried_units"], ["const", 0]],
                     ["obs", "on_shed_access"]],
     "emit": ["DROP"]},

    {"name": "harvest_ready",
     "when": ["and", ["obs", "tile_is_plant"],
                     [">=", ["-", ["obs", "day"], ["obs", "tile_planted_day"]],
                            ["param", "harvest_min_age_days"]],
                     [">", ["obs", "tile_yield_units"], ["const", 0]]],
     "emit": ["HARVEST"]},

    {"name": "water_crop",
     "when": ["and", ["obs", "tile_is_plant"],
                     ["not", ["obs", "tile_watered_today"]]],
     "emit": ["WATER"]},

    {"name": "tend_wait",
     "when": ["obs", "tile_is_plant"],
     "emit": ["PASS"]},

    {"name": "plant_seed",
     "when": ["and", ["obs", "tile_is_empty"],
                     [">", ["obs", "seeds"], ["const", 0]],
                     ["<=", ["obs", "hour"], ["param", "planting_hour_cutoff"]],
                     ["!=", ["state", "mode"], ["const", "LIQUIDATION"]]],
     "emit": ["PLANT", ["param", "crop"]]},

    {"name": "idle",
     "when": ["const", true],
     "emit": ["PASS"]}
  ],

  "commit": [
    {"reg": "requested_plant_actions",
     "value": ["+", ["state", "requested_plant_actions"],
                    ["if", ["==", ["fired", "farmer"], ["const", "plant_seed"]],
                           ["const", 1], ["const", 0]]]},

    {"reg": "requested_harvest_actions",
     "value": ["+", ["state", "requested_harvest_actions"],
                    ["if", ["==", ["fired", "farmer"], ["const", "harvest_ready"]],
                           ["const", 1], ["const", 0]]]},

    {"reg": "requested_sell_units",
     "value": ["+", ["state", "requested_sell_units"],
                    ["if", ["fired?", "market", "sell_stock"],
                           ["obs", "shed_units"], ["const", 0]]]},

    {"reg": "last_step", "value": ["obs", "step"]}
  ]
}
```

Three extra leaf forms appear above and are stage-restricted:

- `["next", reg]` — valid only in `observe`/`commit`, reads a value written
  earlier in the same stage's declaration order. This is the one deliberate
  exception to simultaneous commit, needed because `mode_entered_step` depends on
  the mode transition. It is restricted to registers declared above it.
- `["fired", "farmer"]` — the `name` of the cascade rule that matched. `commit` only.
- `["fired?", "market", name]` — whether a named market rule emitted. `commit` only.

### Fidelity notes

- Rules 2–4 of the cascade are a flattening of the nested `if tile is PLANT`
  block in `_farmer_action`. First-match-wins makes this exact.
- `peak_wheat_price` is renamed `peak_price`, since it tracks `param.crop`.
- `requested_sell_units` accumulates `shed_units` because the sell rule always
  sells the entire shed. A future rule emitting a partial quantity would need the
  emitted operand, not the observation.
- `money_seen` is a register the first draft did not have. It replaces
  `["const", null]`, which the leaf specification above never admitted: the
  language has int, bool, and enum literals and no option type. A boolean flag
  costs one telemetry register and keeps every backend, including hardware, free
  of a null case.
- The encoding declares **ten** registers, not the nine of the first draft and
  not the ten of the Python: `money_seen` is added and `last_farmer_action` is
  dropped rather than modelled, since it is a tuple that no guard reads.
  Backends may keep it natively for diagnostics.
- The current Python resets by constructing a fresh `PolicyState`; stage 0
  reproduces this by restoring every `init`.

## What the backends become

**Python** — one interpreter over this JSON, stdlib only, roughly 250–350 lines:
a recursive `eval_expr`, the observation vocabulary as a dispatch dict, and a
four-stage `act()`. Written once, frozen, then only ever gains tests.

**OCaml** — two things, pointing in opposite directions, and worth not confusing.
*Authoring* (`authoring/`) builds a family as a typed OCaml value and emits this
JSON: the Hardcaml relationship, where OCaml is the elaboration layer and
`Rtl.create Verilog [circ]` has the analogue `Emit.json family`. A GADT makes
arity and kind agreement compile-time facts, so a malformed family cannot be
written down. *Interpreting* (`interp/`) reads this JSON back, and gets no such
help: the encoding arrives already written, so every kind is re-derived by a
load-time pass, exactly as the Python loader does it. Same rules, checked by a
pass rather than by the compiler.

The interpreter's one structural departure from the Python original is that the
observation type is a parameter rather than `Any`, so a single interpreter serves
a JSON observation replayed from a golden vector and a native simulator
observation read straight out of the engine.

**C++ / FPGA** — layer 3 of `policy_encoding.md` is unchanged. A fixed rule count
with fixed-width operands packs naturally into the block described there.

## How to add a feature

The question this section answers: when extending a family, what is data and
what forces code changes in every backend?

### It is not "memory versus decision"

A natural first instinct is to classify a new feature as either a memory feature
(a new register) or a decision feature (new logic), and to assume the first is
data while the second is code. **Both are data.** A decision is a guard
expression plus an emit, living in `farmer_cascade` or `market_rules` exactly as
a register write lives in `observe` or `commit`. If decisions were code, this
document would have no point.

What actually costs a dual implementation is a much narrower set:

| What you are adding | Data or code? | Backends touched |
| --- | --- | --- |
| A register whose value is an expression over existing leaves | **data** | none |
| A guard, rule, or emit over existing leaves | **data** | none |
| A parameter, or a different value for one | **data** (candidate) | none |
| **A new observation accessor** — `opponent_money`, `tile_growth_stage` | **code** | all, one line each |
| **A new operator** — division, `abs`, `clamp` | **code** | all, plus FPGA cost |
| A new stage, or changed commit semantics | **code** | all; `dsl_version` bump |
| Loops, indexing, online lookahead | **not expressible** | Tier 2, two implementations |

Only the last four rows cost anything, and rows 4 and 5 are the ones met in
practice. **The observation vocabulary is where the dual-implementation cost
actually lives** — not the rules. This is why the interpreter must take its
vocabulary as an injected table rather than importing one; see
[`library_boundaries.md`](library_boundaries.md).

### Registers and decisions are coupled

Memory and decisions are not alternatives, and the interesting case is where
they meet. Consider adding *"do not sell below today's peak price"*:

```json
[">=", ["obs", "market_price"], ["state", "peak_price"]]
```

That is zero code. But `peak_price` is declared `"class": "telemetry"`, and a
guard now reads it. **It has become a decision register**, so it enters the
semantic contract of [`policy_encoding.md`](policy_encoding.md) and must be
compared strictly in golden vectors rather than loosely.

Adding a register is free. Adding a *reader* to a register is what promotes it
into the cross-backend contract. Re-run the register audit above whenever a
guard gains a `["state", ...]` reference, and update the `class` field in the
same change.

### History costs one register per lag

The language has no indexing, so `price[day - 3]` cannot be written. A shift
register can:

```json
{"reg": "price_d1", "value": ["obs", "market_price"]},
{"reg": "price_d2", "value": ["state", "price_d1"]},
{"reg": "price_d3", "value": ["state", "price_d2"]}
```

Because commit is simultaneous, all three right-hand sides read pre-stage values
and the shift is correct with no ordering hazard — this is the job non-blocking
assignment exists to do, and the reason the pipeline was specified that way.

A moving *sum* over those lags is then plain integer addition. A moving
*average* is where the wall is, since division is deliberately absent.

The rule: **history costs one register per lag; ratios cost an operator.** The
first is free and scales badly. The second is not free and has consequences for
every backend including hardware. Reach for registers before operators.

### The test, in order

1. Expressible with existing `obs` / `state` / `param` leaves and existing
   operators? **JSON only.** A family version bump per the rule of thumb in
   `CLAUDE.md`, but the bump is a new file rather than new code.
2. Needs a game fact nothing exposes? **Add one accessor per backend**, then
   return to step 1.
3. Needs arithmetic the language lacks? Try registers first. If genuinely
   unavoidable it is an evaluator change everywhere plus a `dsl_version` bump.
   Resist it.
4. Needs to simulate futures, iterate, or index? Outside this language. Tier 2,
   dual-implemented, deliberately — see the section below.

Steps 1 and 2 should cover nearly everything a guard-cascade family wants.
Frequent arrival at step 3 means the vocabulary is too thin: push the
computation into an accessor rather than growing the language, because accessors
are per-game and operators are permanent.

## What this does not cover

Honest limits, so this is not adopted for the wrong job:

- **Online lookahead.** MCTS and receding-horizon planning (gameplan step 413)
  compute actions by simulating futures, not by evaluating guards. They need a
  transition function inside the agent and are outside this language entirely.
- **Multi-worker coordination.** The cascade selects one action for one farmer.
  `hands` is currently always empty. Hiring farm hands means either a cascade per
  worker plus an assignment rule, or a new family shape.
- **Cross-crop portfolio logic.** Every accessor resolving `param.crop` assumes
  one crop. Multi-crop families need indexed accessors — a real DSL version bump.
- **Learned policies.** No arithmetic depth for a network evaluation, by design.

The first family that genuinely needs one of these should be built as Tier 2
(dual-implemented) rather than by growing the DSL to fit, until it is clear the
shape recurs.

**These limits are now priced.** The Phase 6 league measured what each costs in
score, and the answers are not proportional to their engineering cost:
multi-worker coordination — the most expensive to add, since it needs a new
third cascade inside this library rather than an entry in a seam file — is worth 1.6% of
final bankroll, while movement and board-derived accessors, which need no change
to this library at all, take a policy from 0.319 to 0.757. See [`dsl_seam_extension.md`](dsl_seam_extension.md) for the tiers, the
measurements, and the recommended order.

## Adoption order (DSL scope)

Sequencing for the wider migration lives in
[`ocaml_migration_decisions.md`](ocaml_migration_decisions.md), which is the
single owner of that order and carries live status markers. This list covers
only the encoding work.

1. **Done** — freeze `dsl_version: 1` against `monocrop-reorder-v1` as written
   above. Structurally cross-checked: parameters match `candidate_baseline.json`
   exactly, every `obs`/`state`/`param` reference resolves to something declared,
   and the decision/telemetry class of all nine registers was verified
   mechanically against the guards, mode transition, and `reset_when`.
2. **Done** — `submission/dsl/`, stdlib only, vocabulary injected; and the
   golden-vector runner, `experiments/golden.py` (record / check / sweep).
3. **Done** — interpreter-driven `monocrop-reorder-v1` selected the same action
   and the same next decision-register values as today's `MonocropReorder`
   across 90 seeds and 129,420 turns, with zero loose telemetry divergences.
   134 fixtures spanning 19 behavioural signatures are checked in at
   `experiments/policies/monocrop_reorder/golden/vectors.json` and replayed by
   `tests/test_golden_vectors.py`. Two encoding defects were found and fixed
   here in the process, which is what this step is for.
4. **Done** — the OCaml authoring/emitting side, `authoring/`. The encoding
   above is now emitted by `families/monocrop_reorder.ml` through
   `bin/emit.exe`; the checked-in `family.json` is generated output, proven
   structurally identical to the hand-written original before replacing it.
5. **Done** — family renamed from `myfirststrategy` to `monocrop_reorder`.
6. **Done** — the OCaml reading side, `interp/`: the `policy_dsl` library
   (`expr`, `cascade`, `pipeline`, `family`, `interpreter`) plus the per-game
   seam `interp/kaggriculture/`, and the subprocess shim that lets it be
   evaluated through `reference/run_game.py`. `monocrop-reorder-v1` runs through
   it from the same `family.json`, and `experiments/golden.py` gained an `ocaml`
   backend as a third column: 134 vectors clean under `--strict-telemetry`, and
   a live 90-seed sweep clean over the same 129,420 turns as step 3.

Steps 2-3 were pure Python and worth doing whether or not the OCaml migration
proceeds; both have passed, so downstream work is unblocked.
