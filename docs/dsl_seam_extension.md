# DSL Seam Extension

Status: **Analysis, with a measured decision gate.** Refines
[`policy_dsl.md`](policy_dsl.md)'s "What this does not cover" section by pricing each of
its limits against the Phase 6 league table, and feeds the sequencing owned by
[`ocaml_migration_decisions.md`](ocaml_migration_decisions.md). It decides nothing the
game plan has not already delegated; where it commits to an ordering, that ordering is
this document's to own and is justified by measurement recorded here.

Measurements: [`evaluation_protocol.md`](evaluation_protocol.md).

## The problem, stated as a number

`monocrop-reorder-v1` — the only policy currently expressible in the DSL, and therefore
the only thing that can ship — ranks seventh of nine in the Phase 6 league. Its action
histogram is the whole diagnosis:

```
monocrop-reorder-v1     0.0% MOVE      93.2% PASS      score 0.319
premium-crop           30.0% MOVE      35.5% PASS      score 0.757
```

It farms the single tile it spawns on and idles for the rest of the episode. This is not
a tuning failure. The vocabulary it is written in has no MOVE, no hands, and no way to
name any tile but the one underfoot, so no candidate search inside that seam can reach
the strategies above it. **Widening the seam is the critical path to a competitive
submission, and parameter search is behind it, not in front of it.**

## What widening costs to build

An accessor is not one edit. The DSL has three backends that must agree, and adding one
observation costs an entry in each of four seam files:

| File | Role |
| --- | --- |
| `submission/vocabulary.py` | the specification; stdlib only, ships to Kaggle |
| `submission/actions.py` | emit signatures and action assembly |
| `authoring/kaggriculture/{vocabulary,actions}.ml` | typed handles for the emitter |
| `interp/kaggriculture/vocabulary.ml` | the JSON reading path |
| `interp/kaggriculture/native_vocabulary.ml` | the native reading path over `Kag_model` |

Plus the equivalence evidence: golden vectors are what say the three implementations
agree, and they have to be re-recorded. `docs/policy_dsl.md`'s "How to add a feature"
already says adding an accessor is the expensive change and adding a rule is free; this
document is about which accessors are worth that price.

## The three levels

A **level** is a category of *extension to the DSL itself* — how much of the system has to
change before a shippable policy can do a given thing. Levels are not a runtime concept
and have nothing to do with policies, families, or candidates; nothing in the codebase is
"at" a level. They exist only to sort the work.

Each level is described on two independent axes, and confusing them is easy:

- **cost** — engineering effort: which files change, and whether the generic library is
  among them.
- **worth** — what it buys in game outcome, measured in the league.

The finding of this document is that the two run *opposite* to each other here.

(Called *levels*, not tiers: [`ocaml_migration_decisions.md`](ocaml_migration_decisions.md)
Decision 3 already uses "Tier 1/2/3" for how a family ships — data-defined,
dual-implemented, or research-only — which is an unrelated axis.)

The limits in `policy_dsl.md` are not equally costly, and conflating them has been
hiding a cheap win behind an expensive one.

### Level A — vocabulary extension: new accessors and emits, no library change

Everything that is a pure function of `(observation, parameters)` returning a scalar, and
every action the existing four-stage pipeline can already shape. Concretely: `MOVE`,
`DIG`, `FERTILIZE`, `HIRE`, `BUY_LAND`, `BUY_ANIMAL`, `BUY_PRODUCT`, and any accessor
over the board, the market, the town, or the shed.

Cost: the four seam files and fresh vectors. Mechanical. No change to `submission/dsl/`,
`interp/lib/`, or `authoring/lib/`.

### Level B — tile addressing

Reading a tile *other* than the one the worker stands on. Accessors are nullary from the
DSL's point of view, and a parameter is fixed for the whole episode, so there is no way
to say "the tile at (x, y)" for a varying x and y. Options are indexed accessors driven
by a cursor register, or a new leaf form — either is a real `dsl_version` bump and a
design decision, not a mechanical edit.

### Level C — multi-worker (hands)

`Policy_dsl.Interpreter.turn` is `{ farmer : firing option; market : firing list }` and
both action builders hard-code `hands = [||]`. A second worker needs a *third cascade* —
alongside the existing `farmer_cascade` and `market_rules` — producing one firing per
hand, inside the *generic* library — which means `submission/dsl/` and
`interp/lib/` and `authoring/lib/family.ml` and the load-time validation in both
`Family.load`s, on top of the seam files. This is the expensive one by a wide margin.

## What the league already measured

Two of the three levels turned out to be already measured, because the baseline population
happens to contain policies that sit exactly on those boundaries. This was not designed
in; it is what made the analysis cheap.

### Level A is worth 0.319 → 0.757

`premium-crop` scores **0.757** against the same population in which
`monocrop-reorder-v1` scores 0.319, and it beats every entrant except the two livestock
policies. Audited against the seam, everything it reads is either the current tile, a
scalar the vocabulary already has, or one of two derived board computations — "which
direction is the highest-priority task" and "how many tiles are planted". Everything it
emits is `PASS/MOVE/PLANT/WATER/HARVEST/DIG/DROP` plus `BUY_SEED/SELL`.

So `premium-crop` **is a Level-A policy**, and it is the answer to "how much does Level A
buy": third place instead of seventh, without touching `policy_dsl` at all. The delta
from today's vocabulary is small and enumerable:

- emits: `MOVE(direction)`, `DIG`
- accessor: the direction toward the highest-priority task, over `{N, S, E, W, HERE}`
- accessor: the direction toward the nearest shed-access tile (for the endgame dump)
- accessor: the number of tiles currently planted with the family's crop

### Level C is worth 1.6%

`animal-solo` was written for this question: the `animal-focused` engine with its crew
removed — one worker keeping the same three cows, buying every unit of feed at market
price instead of growing it with hired hands. Head to head over 2048 games:

| | score | mean final money | head-to-head margin |
| --- | ---: | ---: | ---: |
| `animal-focused` (3 hands) | 0.944 | $30,053 | +$484 mean, +$106 median |
| `animal-solo` (1 worker) | 0.924 | $29,746 | — |

The crew wins 55.3% of their games by a mean margin of **1.6% of bankroll**, and the
median margin is $106. That is the return on the most expensive extension available.
**Level C is not on the critical path**, and the result is worth stating plainly because
it is counter-intuitive: hiring is nearly free in this game, so one would expect more
workers to dominate. They do not. `expansion` shows extra workers mostly buy extra
walking, and `animal-solo` shows one worker can run a full livestock operation alone.

### Level B's worth is not measured, and is the real open question

No existing baseline sits on the Level-B boundary, because every native baseline can
address tiles freely — that is the one capability the natives have that a Level-A DSL
policy would not.

## The design question Level A raises

`premium-crop`'s task ranking — water outranks harvest outranks plant outranks dig, with
an endgame reversal and a shared sowing budget — lives inside `best_owned`. Under Level A
that logic would move into `submission/vocabulary.py`, because that is the only place an
accessor can compute it.

That is strategy in the vocabulary, not strategy in the data, and it costs exactly the
property [`ocaml_migration_decisions.md`](ocaml_migration_decisions.md) Decision 5 was
adopted for. A champion ships as a frozen interpreter plus a swapped JSON blob, in about
a minute and at near-zero risk. Anything baked into an accessor is *not* in the blob: it
is code in `main.py`, and changing it means shipping new interpreter code under live-ladder
time pressure, which is the failure mode the whole design avoids.

So Level A has a knob, and the knob is how fat the accessors are:

| | Where the ranking lives | Redeploy cost | Reachable score |
| --- | --- | --- | --- |
| **Fat accessor** | `vocabulary.py`, frozen | changing it means new `main.py` | ≈ 0.857, known |
| **Thin accessor + Level B** | the family JSON, swappable | swap the blob | unknown |

The fat form is cheap and reaches a known-good score today. The thin form preserves the
operational property the migration was justified by. They are not exclusive — a fat
accessor can be added now and thinned later — but adding one is a commitment to keeping
it working across every backend, so the choice should be deliberate.

## Recommended order

1. **Level A, fat, now.** Add the four accessors and two emits above and re-author
   `monocrop-reorder` (or a successor family) to walk a plot. Target: match
   `premium-crop`'s 0.857 in the league. This is the largest measured gain available and
   the cheapest tier.
2. **Then the parameterized heuristic and candidate search** (game plan Phase 6/7) inside
   the widened seam. Search was always going to be gated on this; the league is what
   proved it rather than assumed it.
3. **Then Level B** (tile addressing), justified by whichever of two things the search shows: candidates
   that want per-tile logic the fat accessor cannot express, or the redeployment cost of
   the frozen ranking becoming real.
4. **Level C last, if ever.** Worth 1.6% of bankroll for the largest engineering cost
   on the list — a third cascade inside `policy_dsl`, in both interpreters, plus both
   `Family.load` validations. Revisit only if a searched candidate is demonstrably
   bottlenecked on worker-hours rather than on decisions.

CPU layout, SIMD, and hardware work remain gated on profile evidence and are not
reopened by any of this.

## The probe protocol, for reuse

The method that measured Levels A and C generalizes and is cheap, so it should be the
default before paying for a seam extension:

> Write a native baseline in `baselines/` that is deliberately handicapped to exactly the
> capabilities the extension would grant, run it in the league, and compare. It is a
> measuring instrument, not a candidate: it never becomes a DSL family and never ships.

Two properties make the result trustworthy and one makes it approximate:

- The handicapped baseline plays the same seeds, the same opponents, and both seats as
  everything else, so the comparison is controlled.
- The coverage gate proves it actually exercised the capability rather than declaring it.
- Scores are relative to the population, so a probe changes the table it is added to:
  introducing `animal-solo` moved `premium-crop` from 0.857 to 0.757 without either
  policy changing. Compare within one artifact, never across two.
- It is an **upper bound**, not an estimate. A native policy restricted to the same
  information and actions can still compute things the expression language cannot — it
  has arbitrary arithmetic, loops, and no four-stage pipeline. If the upper bound is
  unimpressive, the extension is not worth buying; if it is impressive, the extension is
  worth *attempting*.
