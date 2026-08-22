# OCaml Migration: Decisions and Rationale

Status: adopted decision record. Captures a design discussion held 2026-08-20
about adopting OCaml as the primary development language for this project's
software layers; **adopted 2026-08-21**, when the game plan's fixed
native-language decision was revised from C++20 to OCaml (see the work plan
below for what has landed).

Nothing here overrides [`kaggriculture_gameplan.md`](kaggriculture_gameplan.md).
Two of its fixed decisions were directly in scope:

> - Python is the official behavioral oracle and final-submission language.
> - OCaml is the native simulation and high-throughput evaluation language
>   *(C++20 until 2026-08-21)*.

The first was never in question and stays. The second is the revision this
document argued for, made under the authority rule in [`README.md`](README.md).

Vocabulary: [`glossary.md`](glossary.md). Encoding detail: [`policy_dsl.md`](policy_dsl.md).

## The question

Can OCaml become the main language for simulation, backtesting, policy search,
and parsing/normalization, while Python remains only the interaction layer
between the Kaggle environment and the policy?

Short answer: yes, and the cost is lowest now — the repository is roughly 1,400
lines across all backends and `fast_model/` is a 304-line PASS scaffold that
nothing yet depends on.

## The binding constraint

`kaggriculture_gameplan.md` line 544: *"No submission dependency on local native
code or FPGA hardware."* Native code includes OCaml native binaries, not just
C++. Kaggle receives a self-contained Python file.

So "an OCaml policy running on Kaggle" cannot mean OCaml executing at
competition time. It must mean one of:

- **(a)** OCaml *generates* the Python that runs; or
- **(b)** OCaml *authors data* that a fixed, hand-written Python interpreter runs.

**(b) is preferred; (a) is the fallback for what (b) cannot express.** What
neither is: hand-transcription, which is what gameplan line 430 currently
assumes ("Distill the selected research policy into `submission/main.py`").
Deleting that manual step is the actual argument for this migration.

## Decision 1 — No FFI. Process boundary only.

`pyml` and Jane Street's `pythonlib` both exist and are in the opam repository
(neither installed). **Rejected.**

- The only thing they would buy is running an OCaml policy inside the Python
  oracle's turn loop. That boundary is one JSON object per turn, 719 turns per
  episode — microseconds against an already-interpreted upstream environment.
- The cost is two garbage collectors in one address space, plus the active
  switch is `5.2.0+ox`, a Jane Street compiler variant; pyml on OxCaml is not a
  combination to assume works.
- `policy_encoding.md` already committed to this shape for C++: *"There is no
  runtime Python-to-C++ policy call planned."* The argument is stronger for
  OCaml, not weaker.

**Instead:** research runs use a subprocess shim satisfying the `make_policy()`
protocol that `reference/run_game.py:load_policy` already expects, piping
observation/action JSON to an OCaml binary. Submission uses generated data or
code. These are two distinct mechanisms for two distinct jobs and must not be
conflated.

## Decision 2 — Push family structure into data

The pivotal decision, and the one that determines whether dual implementations
are needed at all.

A policy family is **algorithm + parameter schema + state semantics**. Only the
parameter schema is currently data; the other two are Python code. Hence:

| Structure lives in | Implementations needed | Cost |
| --- | --- | --- |
| Code | one per family, per backend | `families x backends` |
| Data | one interpreter per backend, written once | `backends + families x 0` |

This is not a new burden introduced by OCaml — `policy_encoding.md` **already**
commits to two implementations of every strategy (Python for Kaggle, C++ for
simulation). The question is whether to collapse a cost already accepted.

Mapped onto the rule of thumb in `CLAUDE.md`:

| Change | Today | Under the DSL |
| --- | --- | --- |
| tune an existing decision | candidate parameter — data | data |
| add decisions/state to the same algorithm | family version bump — code x backends | data |
| change how decisions are generated | new family — code x backends | data, *if it fits the evaluation model* |

## Decision 3 — Tiers, chosen per family

1. **Tier 1 — data-defined.** Guard-cascade heuristics. Zero dual implementation.
   The default.
2. **Tier 2 — dual-implemented.** Does not fit the DSL but should ship. Two
   implementations kept honest by golden vectors. Expensive; use deliberately.
3. **Tier 3 — research-only.** Never shipped, so never needs Python.

Most families die in research, so Tier 3 is the common case and the transcription
cost is paid **once per shipped policy, not once per explored policy**.

The discipline that makes this safe: **author in the DSL even for throwaway
exploration.** A Tier-3 family written in arbitrary OCaml must be hand-translated
the day it finally wins — which is the worst possible moment to write careful new
Python. A Tier-3 family written in the DSL is promoted for free, because it is
already data.

## Decision 4 — OCaml as an elaboration layer, not a runtime

The governing analogy, and the reason this is principled rather than aesthetic.

In Hardcaml, OCaml is not the runtime; it is the elaboration layer. A circuit is
built as an OCaml value and Verilog is emitted. No OCaml reaches the FPGA.

Here: a policy is built as an OCaml value and JSON is emitted. No OCaml reaches
Kaggle. `Rtl.create Verilog [circ]` has the analogue `Emit.json family`.

This is also why the DSL's expressiveness ceiling is a feature rather than a
limitation: a policy expressible in the DSL is a policy the FPGA branch could
synthesize.

## Decision 5 — Live redeployment favours the interpreter

Staying in the live rankings during an ongoing competition means being able to
ship a new champion quickly and safely.

| Deploy path | Time to ship | Risk |
| --- | --- | --- |
| Hand-transcribe OCaml to Python | hours | high — new untested code, under time pressure |
| Generate Python from OCaml | minutes | medium — emitter output is new each time |
| **Frozen interpreter + swap JSON blob** | **~1 minute** | **near zero — `main.py` never changes** |

Under the third, `submission/main.py` is written once and accumulates test
coverage across the entire project. This operational property is independently
worth more than the language choice.

## What OCaml is actually good for here

The claim that OCaml suits simulation, backtesting, and parsing holds, with
specifics:

- **Parsing / normalization — the largest and least obvious win.**
  `common/candidates.py` hand-validates the candidate envelope;
  `PolicyParameters.validate()` hand-checks eight ranges; `observations.py` does
  defensive `.get()` traversal because a malformed observation is a `KeyError` at
  turn 400. Sum types plus `ppx_yojson_conv` convert all of this into parse-time
  failure with exhaustive matching.
- **Simulation.** Replaces `fast_model`'s C++. OxCaml unboxed records keep the
  transition loop allocation-free; OCaml 5 domains give cleaner rollout
  parallelism than C++ threads. Expect within 1–2x of C++.
- **Search / backtesting.** The type system prevents evaluating a candidate
  against the wrong family version — exactly what `SCHEMA_VERSION` and
  `expected_policy_id` guard by hand today.
- **Where it is worse: exploratory analysis.** `slow_model/` stays Python.

## What stays Python permanently

- `reference/` (364 lines) — loads the pinned `kaggriculture.py` by file path and
  runs upstream transitions verbatim. This is the trust anchor; reimplementing it
  does not port the oracle, it destroys it.
- `reference/run_game.py` and the subprocess shim — the interaction layer.
- `submission/main.py` — generated or interpreter-driven, never hand-edited. CI
  should assert it is byte-identical to emitter output.
- `slow_model/` — matplotlib/numpy analysis.

## Costs, stated honestly

- **No production OCaml-to-Python transpiler exists.** This is a restricted-DSL
  emitter, not a compiler for arbitrary OCaml. Anything outside the DSL falls back
  to hand-transcription.
- **Interpreter latency at submission time.** A tree-walk over ~50 guards x 719
  turns should be negligible, but gameplan step 413 names submission latency as a
  gate — measure it rather than assuming.
- **Two toolchains** (dune alongside CMake/ctest), though two are already in use.
- **~684 lines of `experiments/` to rewrite.** Days, not weeks, and cheapest now.
- **Two-language debugging.** Golden vectors localize disagreements but do not
  eliminate the friction.

## Known limits of the DSL approach

- **Online lookahead search cannot be expressed.** MCTS and receding-horizon
  planning (gameplan step 413) compute actions by simulating futures, so they need
  a transition function *inside the submission* — which the no-native-code rule
  makes a much larger problem than transcription. Note this is unrelated to
  *offline candidate search*, which is unaffected.
- Multi-worker coordination, multi-crop portfolios, and learned policies are all
  outside DSL v1. See [`policy_dsl.md`](policy_dsl.md).

## Work plan and status

This section is the **single owner of sequencing** for this migration. The
"DSL scope" list in [`policy_dsl.md`](policy_dsl.md) covers only the encoding
work and defers here for the overall order. Update the status markers below as
work lands, so a session starting cold can tell what is done from what is
merely written down.

All of it lands **inside this repository**. Extraction of any part into a
standalone library is deferred until the Kaggriculture application is finished;
see [`library_boundaries.md`](library_boundaries.md) for the seam analysis, the
naming discussion, and the conditions that would reopen the question. The one
constraint that binds the work below is a file-layout one, needed for the
submission regardless: the interpreter takes its observation vocabulary as an
injected table rather than importing it.

### Done

- **Family renamed.** `myfirststrategy` -> `monocrop_reorder`; `policy_id` is now
  `monocrop-reorder-v1`; class `MyFirstStrategy` -> `MonocropReorder`; tests moved
  to `tests/test_monocrop_reorder.py`. Behaviour-neutral, 19/19 tests unchanged,
  no new lint findings.
- **DSL v1 drafted** against the existing family — see
  [`policy_dsl.md`](policy_dsl.md). Structurally cross-checked: parameters match
  `candidate_baseline.json` exactly, every `obs`/`state`/`param` reference
  resolves, and the decision/telemetry classification of all nine registers was
  verified mechanically. Not yet proven behaviourally.

- **Step 1 — the Python DSL interpreter is written.** `submission/dsl/` holds the
  four roles named in [`library_boundaries.md`](library_boundaries.md) — `expr`,
  `cascade`, `pipeline`, `family` — plus `interpreter`, which binds them to an
  injected vocabulary. It imports only the standard library and names no crop and
  no action; `tests/test_submission_boundary.py` enforces both by parsing the
  package rather than by convention. The Kaggriculture seam is the two files
  `submission/vocabulary.py` and `submission/actions.py`.

  `monocrop-reorder-v1` is encoded as
  `experiments/policies/monocrop_reorder/family.json` and run through
  `experiments/policies/monocrop_reorder/dsl_policy.py`, which satisfies the
  `make_policy()` protocol so `reference.run_game` can drive it unchanged.

  Two things the draft encoding got wrong turned up while implementing it, and
  both are fixed in `policy_dsl.md`: `["const", null]` and `["const", 0.0]` were
  never in the leaf specification, and money is an integer rather than a float.
  See that document's fidelity notes.

  Validation is a load-time pass, not a runtime one: name resolution, the stage
  restrictions on `next` and `fired`, kind agreement between each write and its
  register, emit arity and operand kinds, and enum domains. This is the same work
  OCaml sum types would do on the authoring side, which is the point — a family
  that loads cannot fail on a name or a type at turn 400.

- **Step 2 — the golden-vector runner exists.** `experiments/golden.py`, a CLI
  rather than a unittest. `record` runs seeded oracle episodes with the
  hand-written policy and writes fixtures in the
  [`policy_encoding.md`](policy_encoding.md) format; `check` replays them
  through a backend (`dsl` or `hand`); `sweep` is the live turn-by-turn parity
  run. Fixture states are written in the family-register vocabulary of
  `family.json` — the projection from `PolicyState` (`peak_wheat_price` ->
  `peak_price`, `previous_money is None` -> `money_seen`, `last_farmer_action`
  dropped) lives in the runner, so the fixture file is consumable by a backend
  that has never heard of Python. Per the register audit in
  [`policy_dsl.md`](policy_dsl.md): a mismatched action or decision register
  fails the run; telemetry registers are compared loosely — every divergence
  counted and printed, fatal only under `--strict-telemetry`.

- **Step 3 — equivalence proven, evidence on disk.**
  *Fixtures:* 134 vectors covering 19 behavioural signatures (mode pair, farmer
  verb, tile class, market orders, reset) are checked in at
  `experiments/policies/monocrop_reorder/golden/vectors.json` and replayed by
  `tests/test_golden_vectors.py` on every test run, no oracle required.
  *Seed count:* chosen by signature saturation rather than picked — a 120-seed
  probe found the last new signature at seed 44, so recording and sweeping
  default to 90 seeds, a 2x margin the recorder warns about if it erodes.
  *Live parity:* `sweep` over 90 seeds (129,420 turns) found zero action
  failures, zero decision-register failures, and zero loose telemetry
  divergences. The step-0 reset path is covered because the recorder and the
  sweep reuse policy instances across episodes, which a single `run_game`
  never exercises.

Steps 1-3 were pure Python and paid off independently of the migration; the
gate they form has now passed.

- **Step 4 — the OCaml authoring and emitting side is built.** `authoring/`, a
  dune project on the `5.2.0+ox` switch. The game-agnostic core is the
  `policy_family` library (the `expr` and `family` roles from
  [`library_boundaries.md`](library_boundaries.md)): a GADT-kinded expression
  language, so operator arity and kind agreement are compile-time facts, plus
  `Family.create`, which enforces the residual rules the types cannot carry —
  the stage restrictions on `next`/`fired`, in-stage write uniqueness, enum
  write domains, name registration — with a negative-test suite (`dune test`)
  asserting each refusal. The per-game seam is `authoring/kaggriculture/`
  (`vocabulary.ml`, `actions.ml`), mirroring the two Python seam files:
  observation handles carry the `KINDS` kinds, emit constructors carry the
  `EMITS` signatures.

  `monocrop-reorder-v1` is authored as `families/monocrop_reorder.ml`, and
  `Emit.json` exists as `bin/emit.exe` (Decision 4, discharged). Its output was
  proven structurally identical to the hand-written `family.json` before
  replacing it; **the checked-in `family.json` is now generated output**, and
  the full Python suite plus a `--strict-telemetry` golden check pass against
  it unchanged. The Python loader remains the final gate on the artifact.

- **Step 7 — the game plan is revised (2026-08-21).** The fixed native-language
  decision now names OCaml; the Phase 3 heading, API sketch, and representation
  guidance are OCaml; Phase 8's "distill" step now specifies the frozen
  interpreter + swapped data blob; `policy_encoding.md`'s backend references
  and layer-3 list follow. This document is therefore an **adopted decision
  record**, no longer a proposal, except where marked below.

- **Step 6 — in progress.** The C++ scaffold and its CMake/ctest toolchain are
  deleted. `fast_model/` is now OCaml inside the repo-root dune workspace:
  the PASS/init/terminal slice ported behaviour-for-behaviour (`lib/model.ml`),
  the `kag_sim` bench CLI (baseline re-measured in
  [`benchmark_baseline.md`](benchmark_baseline.md)), and — the de-risking
  choice — `lib/python_random.ml`, a CPython-exact `random.Random` (MT19937
  with Python's integer seeding, `random()`, `getrandbits`, `choice`), proven
  bit-for-bit against draws recorded from CPython
  (`fast_model/test/python_random_fixture.json`). The upstream environment
  re-derives `random.Random((seed * 1_000_003) ^ day)` each day and draws only
  `random()` and `choice`, so exact differential replay is now unblocked.

  **Rule group 1 landed (2026-08-21):** configuration (every scalar knob in the
  upstream spec; `marketParams` overrides deliberately unrepresentable until
  the market groups), full initialization (farms, privates, market, town),
  the observation projection, and terminal bookkeeping. Differential coverage
  is checked in: `fast_model/serialize/` renders the oracle adapter's
  `diagnostic_state` / `player_observation` JSON shapes from the OCaml state,
  and `fast_model/test/model_group1_fixture.json` (recorded by
  `record_model_fixture.py`) holds exact oracle init states plus per-turn
  day/hour/step/status/reward over full PASS episodes for default and
  non-default configurations. The comparison reports the first differing JSON
  path — the seed of the Phase 4 divergence report.

  **Rule groups 2–7 landed (2026-08-21) — the Phase 3 rule set is complete.**
  Each group landed with its own oracle-recorded tape fixture
  (`fast_model/test/model_group<N>_fixture.json`, regenerated by the sibling
  `record_model_group<N>_fixture.py`): the OCaml test replays the recorded
  action tape and compares a canonicalized per-turn digest of every field the
  group's rules can mutate, then the final diagnostic state, reporting the
  first differing JSON path. Recorders assert their tapes exercised what they
  claim, so a regressed generator cannot record a vacuous fixture. Highlights
  and deliberate scope calls, in group order:

  - *Group 2* (movement/hands/shed/inventories/capacity) pulls the three
    constant-price orders HIRE / BUY_SEED / BUY_ANIMAL forward — without a
    purchase path nothing can enter a shed, so capacity would be untestable.
    Inventory insertion order is modeled exactly: Python dict order decides
    which items survive a capacity-limited drop.
  - *Group 3* (crops) covers the atomic PLANT validation, watering windows,
    neglect weeds, and the overripe-decay clock via a scripted stakeout;
    FERTILIZE's coverage waited for a fertilizer source.
  - *Group 4* (structures/animals) scripts a goose ranch timed against the
    escape clock (feed grown on the spawn tile) and covers escapes, the care
    bonus, DIG semantics, and collected-fertilizer FERTILIZE.
  - *Group 5* (market) implements the pricing shapes with CPython's
    round-half-even, the two-phase per-unit lockstep (simultaneous quotes
    from pre-commit inventory, player-order commits), and pulls the
    deterministic town-center tick forward — market inventory cannot match
    the oracle without it. A BUY_PRODUCT-fed dairy script closes group 4's
    COW/SHEEP production gap.
  - *Group 6* (land/town/weeds) connects the daily
    `(seed * 1_000_003) ^ day` generator: weed draws consumed per empty tile
    (even at chance 0) feeding the sorted-table shop choice, drawn with
    replacement to the 8-instance cap; BUY_LAND unlocks quadrants in fixed
    order.
  - *Group 7* (overrides/ordering) resolves sparse marketParams into
    per-item curves (the `params` echo in the market dict is treated as
    configuration, not state), deliberately reaching the price floor, log10,
    and an above-side hinge; a turnsPerDay=1 case stresses the end-of-day
    cadence.

  What remains before the simulator is usable is Phase 4, not features: the
  bulk differential runner and its ≥1,000-game trust gate, including a policy
  for malformed/fuzz actions (the typed action surface cannot express them,
  and the tape parser deliberately fails loud rather than no-opping).

- **Step 6 — the Phase 4 differential runner is built and the trust gate passes.**
  `tools/differential.py` drives the pinned oracle over generated tapes and streams each
  game to `kag_sim differential`, which replays the same raw tape through the engine and
  compares every post-turn state; the two halves run as a pipeline, so no bundle is ever
  written. 1,000 full 720-turn games (719,000 turns) at the default configuration and
  300 non-default-configuration games pass with zero divergences, and both repeat clean
  on a second master seed. Full specification and result in
  [`differential_testing.md`](differential_testing.md).

  The open question this step carried — how raw, possibly malformed JSON actions map
  onto the typed OCaml action surface — is decided and implemented. A malformed unit
  action collapses to `Unit_pass`, which is an equality rather than an approximation
  because `_apply_unit_action` returns before touching state; a malformed market order
  collapses to a new `Bad_order` variant that occupies its slot and does nothing,
  because `max_len` is taken over the raw queues and drives the per-index price refresh.
  The narrow domain where upstream *raises* instead of no-opping is excluded explicitly
  and raises `Undefined_mapping` rather than being guessed at.

  The runner found two engine defects the group fixtures could not: atomic PLANT
  validation re-read the live seed count instead of fixing the blocked set before any
  unit acted, and `python_int` rejected the underscore separators CPython's `int()`
  accepts. Both are fixed; the second is now pinned by a CPython-recorded fixture.

- **Step 8 — the OCaml DSL interpreter is written (2026-08-21).** The reading
  counterpart of `submission/dsl/`, and deliberately *not* a second use of
  `authoring/lib/`: those two point in opposite directions. The authoring GADT
  builds a family that is well-kinded by construction and emits JSON; the
  interpreter reads JSON that arrives already written and must re-derive every
  kind at load time. The same rules, checked by a pass instead of by the
  compiler.

  `interp/lib/` is the `policy_dsl` library — `expr`, `cascade`, `pipeline`,
  `family`, `interpreter`, the same five roles as the Python package, naming no
  crop and no action. The one structural difference from the Python original is
  that the observation type is a parameter rather than `Any`: a vocabulary is
  `'obs vocabulary`, so the same interpreter serves a JSON observation replayed
  from a golden vector and, later, a native `Kag_model` observation with no
  conversion layer between them. `interp/kaggriculture/` is the per-game seam
  (`vocabulary.ml`, `actions.ml`), the port of `submission/vocabulary.py` and
  `submission/actions.py`; those two files are the specification it must agree
  with, and the vectors are what say so.

  Validation is a load-time pass here as it is there, and `interp/test/`
  asserts the refusals — 42 cases covering the stage restrictions, kind
  disagreement, enum domains, emit arity, candidate bounds, the vocabulary's
  self-consistency, and the parameter dependencies of an accessor. Those are
  claims about encodings that are *rejected*, which no accepted family can
  exercise, so the golden vectors cannot reach them.

- **Step 5 — the subprocess policy shim is built (2026-08-21).** `interp/bin/`
  is `kag_policy.exe`: one JSON request per line in, one response per line out,
  no FFI anywhere (Decision 1). `experiments/ocaml_backend.py` is the Python
  half. The shim serves two request forms because it has two jobs — with a
  register bank supplied the step is stateless, which is what replaying a golden
  vector needs; without one the process keeps a bank of its own, which is what
  driving a live episode through `reference/run_game.py` needs.

  This step and step 8 landed together and each justifies the other: the shim
  had nothing to carry until a family executed in OCaml, and the interpreter had
  no way to reach the existing evidence until the shim existed.

  **The equivalence gate passes on the third backend.** `experiments/golden.py`
  now takes `--backend ocaml` alongside `dsl` and `hand`, and `sweep` takes a
  backend too rather than being hard-wired to the Python interpreter:

  - 134 checked-in vectors replayed, zero action failures, zero decision-register
    failures, zero telemetry divergences under `--strict-telemetry`;
  - live turn-by-turn parity against the hand-written policy over 90 seeds and
    129,420 turns, zero divergences — the same evidence base step 3 used;
  - full 719-turn episode traces byte-identical to the Python interpreter's on
    seeds 1234, 7, and 42.

  `experiments/policies/monocrop_reorder/ocaml_policy.py` exposes `make_policy()`,
  so `--policy-a experiments.policies.monocrop_reorder.ocaml_policy` works
  through `reference/run_game.py` unchanged.

- **Step 9 — `kag_sim play` / `evaluate` are built (2026-08-21).** The native
  policy path binds the unchanged generic interpreter to
  `Kag_model.Model.observation` through
  `interp/kaggriculture/native_vocabulary.ml`, and its action builder emits
  `Model.player_action` directly. No JSON enters the policy/transition turn
  loop. `Model.run_game` now takes two policies and an optional action callback;
  `play` writes a comparable JSONL action trace, while `evaluate` runs a
  candidate against an opponent file over a seed file in both player seats.

  Both feasibility gaps are closed at their intended boundaries:

  - `Model.observation` carries `obs_board_size`, one integer added to its
    existing zero-copy view, so row-major tile access and shed adjacency are
    computable without reaching back into the state/configuration.
  - The upstream product/animal/shed tables are restated in the native
    vocabulary. The transition engine remains string- and JSON-free; the
    serializer and policy vocabulary independently own their boundary mappings.

  The native seam has evidence the JSON vectors cannot provide. An OCaml test
  compares all fifteen native accessors against the proven JSON vocabulary on
  empty and planted 8x8 states and compares the two action builders' external
  shape. The end-to-end gate runs seed 1234 through
  `reference.run_game --policy-a ...ocaml_policy` and `kag_sim play`: all 719
  two-player action pairs agree and both finish at `[3697.0, 3000.0]`. That
  comparison is a Python regression test, not a one-off result.

- **Step 10 — Phase 5 scalar benchmarking and multicore rollouts are built
  (2026-08-21).** `tools/benchmark_policy.py` owns one manifest-defined workload
  and drives both the pinned Python oracle with the OCaml policy subprocess and
  native `kag_sim evaluate`. Inputs are hashed, every seed runs in both seats,
  setup is outside the timers, warmups and repeated alternating-order runs are
  retained, and no speedup is calculated until result aggregates agree.

  On the checked-in `monocrop-reorder-v1` baseline-versus-PASS workload (ten
  seeds, both seats), all repetitions agreed on 20 games / 14,380 turns and
  final-money totals. Median scalar throughput was 1,373.646 turns/s on the
  oracle/subprocess path and 186,228.750 turns/s native: 135.573x for this exact
  end-to-end workload, not a language ratio. The historical PASS tape remains
  explicitly separate in [`benchmark_baseline.md`](benchmark_baseline.md).

  `kag_sim evaluate --threads N` now uses a fixed pool of OCaml domains with
  worker-local game state, policy state, and accumulators. On 1,000-game native
  repetitions it scaled 1.575x at two workers and plateaued near 2x at four and
  eight workers on the 4-core/8-thread host. Raw repetitions
  live under the gitignored `experiments/results/benchmarks/`; the benchmark doc
  records the exact artifact. `perf stat` was blocked by the host's
  `perf_event_paranoid=4`, so no state-layout, batching, SIMD, or specialized
  dispatch change was attempted without hardware-counter evidence.

- **Step 11 — the Phase 6 evaluation layer and baseline population are built
  (2026-08-22).** Immutable `training`/`validation`/`holdout` seed splits
  (1024/512/512, disjoint, re-derivable and verified on every test run), the
  evaluation artifact and its schema, the executable champion-promotion rule,
  eight baseline opponents, and `kag_sim league`. Full specification and the
  measured league table: [`evaluation_protocol.md`](evaluation_protocol.md).

  **The baselines are native OCaml, not DSL families, and that is a decision this
  document has to own.** Four of the six opponents the game plan names —
  animal-focused, expansion, market-focused, random-valid — cannot be written
  inside the DSL's game seam at all: the vocabulary is fifteen accessors about
  the tile the farmer stands on, the action seam is `PASS/DROP/HARVEST/WATER/PLANT`
  plus `BUY_SEED/SELL`, `hands` is hard-coded empty, and there is no randomness
  operator. Decision 3's Tier 3 covers this exactly — research-only, never
  shipped, so never needing Python — and the discipline that document states
  ("author in the DSL even for throwaway exploration") is about *candidates*,
  which may one day win and have to ship. A measuring stick is not a candidate.
  The alternative considered and rejected was widening the seam first, which
  would have meant guessing which accessors matter before anything had been
  measured; the population is what will tell us.

  Non-vacuity is a gate rather than a claim: each baseline declares the action
  shapes it exists to produce, `kag_sim` tallies what was actually emitted, and a
  declared shape that never appeared exits non-zero. Two declared absences are
  findings in their own right — a uniformly random agent essentially never
  reaches HARVEST, because keeping a plant alive to first yield needs two
  deliberate waterings of the same tile; and the endgame DROP correctly does
  nothing for baselines whose last crop cycle finishes before the final day.

  `evaluate` grew heterogeneous entrants (a baseline id or a family/candidate
  pair, each carrying its own family) and the game plan's full Phase 7 statistic
  list, while keeping the flat field block the Phase 5 benchmark's correctness
  check reads — verified against the checked-in workload, which still reports 20
  games, 14,380 turns, and the same money totals.

### Next

12. **Widen the DSL game seam — Level A — before authoring the parameterized
    heuristic.** The league inverted the order this step was written in. Search
    inside the current seam has a hard ceiling: `monocrop-reorder-v1` emits 0.0%
    MOVE and 93.2% PASS and ranks seventh of nine, because the vocabulary cannot
    name a tile it is not standing on. No parameter search fixes that.

    The levels and their measured prices are in
    [`dsl_seam_extension.md`](dsl_seam_extension.md). The short form: Level A —
    new accessors and emits in the seam files, no change to `policy_dsl` — takes
    a policy from 0.319 to 0.757, because `premium-crop` turns out to *be* a
    Level-A policy. Level C, hands, is the most expensive extension on the list
    (a third cascade inside the generic library, in both interpreters) and is
    worth 1.6% of bankroll: `animal-solo`, the livestock engine with its crew
    removed and all feed bought at market price, wins 44.7% of its games against
    `animal-focused` and finishes $307 behind it on average. Tier 2, tile
    addressing, is unpriced and is the real open question.

    That document also records the tradeoff Level A forces and Decision 5 pays
    for: a fat accessor puts the task ranking in `submission/vocabulary.py`,
    where it is frozen code rather than a swappable blob.

13. **Then Phase 6's parameterized heuristic and Phase 7 search**, inside the
    widened seam. The game plan defers freezing the parameter schema until
    "baseline play reveals which decisions materially affect results", and it now
    has. Run search on the `training` split and promote through
    `tools.league promote` on `validation`. Do not begin CPU layout/SIMD or
    hardware work without reopening it from profile evidence.

## Resolved questions

- **Does OCaml replace C++ in `fast_model/`, or join it?** Replaced, 2026-08-21.
  Keeping both would have meant transcribing every champion twice.
- **Interpreter-as-submission or generated Python?** Interpreter + JSON blob,
  decided by the measurement the earlier draft asked for: the Python DSL
  interpreter steps `monocrop-reorder-v1` at ~62 µs/turn (~45 ms per full
  719-turn episode) — three orders of magnitude inside the per-turn budget.
  Recorded in the game plan's Phase 8.

## Open questions

- Is the FPGA branch still credible with OCaml as the simulator language? Likely
  yes, and possibly more so, given the shared Hardcaml toolchain.
