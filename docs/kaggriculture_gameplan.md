# Kaggriculture Strategy Research Game Plan

## Status and Purpose

This document is the source of truth for implementing the Kaggriculture research platform and final competition agent.

Current status: **Phases 0–5 complete. The native simulator is trusted: the
Phase 4 trust gate below passed on 2026-08-21 — 1,000 full 720-turn seeded games
(719,000 turns) plus 300 non-default-configuration games, per-turn differential
comparison against the pinned oracle, scripted and fuzz tapes, zero divergences,
repeated clean on a second master seed. The Phase 5 same-policy workload measured
135.573x scalar native speedup and a fixed worker pool plateaued near 2x at four
to eight workers on the 4-core host.** Phase 6 (strategy platform and baseline opponent
population) is next.

The primary objective is to build the strongest practical Kaggle agent. Hardware acceleration is a secondary research path and must be justified by measurements. The first major deliverable is a trusted native simulator, not an FPGA demonstration or an early submission bot.

## Source Documents

This plan consolidates the following repository documents:

- `docs/kaggle_supplied_instructions.md` — supplied game rules and agent interface.
- `docs/kaggriculture_acceleration_codex_brief.md` — simulator, search, and acceleration proposal.
- `docs/kaggriculture_hardcaml_architecture.md` — proposed Hardcaml repository boundaries.

The supplied instructions are a guide, but the official environment implementation must resolve any ambiguity. During planning, the latest inspected upstream revision was:

```text
Repository:  https://github.com/Kaggle/kaggle-environments.git
Commit:      28b6d8af3ce73926b3d0fda1410c1ddd8384ab8c
Date:        2026-08-14
Environment: kaggriculture 0.1.0
```

The implementing agent must confirm that this remains the intended competition revision before pinning it. A newer upstream commit must not be adopted silently.

## Architectural Decisions

The platform will use this progression:

```text
Official Python environment
          |
          v
Deterministic reference traces
          |
          v
Differential-tested OCaml simulator
          |
          v
Multicore strategy evaluation and self-play
          |
          v
Policy search and champion selection
          |
          v
Self-contained Kaggle main.py
```

GPU and FPGA work are optional later branches:

```text
Profiled research workload
          |
          +-- CPU layout/SIMD improvements
          +-- GPU feasibility experiment
          +-- FPGA feasibility experiment
```

The acceleration project brief is the main architectural backbone. The Hardcaml brief must be interpreted as a later, gated acceleration track. Its current dataset-to-features-to-prediction framing does not match Kaggriculture's interactive control problem and should not drive the initial implementation.

The following decisions are fixed unless this document is deliberately revised:

- Python is the official behavioral oracle and final-submission language.
- OCaml (the `5.2.0+ox` OxCaml switch) is the native simulation and
  high-throughput evaluation language. *Revised 2026-08-21 from C++20; the
  rationale and decision record is
  [`ocaml_migration_decisions.md`](ocaml_migration_decisions.md). The C++
  scaffold and its CMake toolchain were removed in the same change.*
- Correctness takes priority over speed until differential equivalence is established.
- A native simulator is not trustworthy merely because aggregate scores look plausible.
- Strategy strength is evaluated against an opponent population, not one opponent.
- Hardcaml begins only after the CPU platform is correct, benchmarked, and profiled.
- Hardware must own a coarse unit of work, preferably complete rollout lanes; tiny host/device arithmetic calls are out of scope.
- No generic `hardcaml_emulation` or `hardcaml_platform` repository is created initially.

## Phase 0 — Repository and Reproducibility Foundation

### Deliverables

- Record the pinned upstream repository, commit, environment version, and retrieval date in a machine-readable file.
- Add a bootstrap command that obtains the pinned reference without committing a large upstream checkout.
- Establish separate development and optimized benchmark builds.
- Create one top-level README that points contributors to this plan and documents the basic commands.
- Define an experiment artifact format containing:
  - project commit;
  - upstream environment commit;
  - simulator backend;
  - compiler and flags;
  - policy identity and parameters;
  - opponent population;
  - seed sets and player positions;
  - machine information;
  - date and results.

### Repository shape

Use the existing directories rather than creating a separate repository prematurely:

```text
reference/        Python oracle, trace generation, and reference policies
fast_model/       OCaml simulator, policies, tests, benchmarks, and search tools
slow_model/       Python analysis and final-agent experimentation
tests/            Cross-backend and end-to-end tests
experiments/      Checked-in experiment definitions; generated results ignored
docs/             Semantics, benchmark reports, and architectural decisions
submission/       Final self-contained Kaggle agent and packaging checks
```

## Phase 1 — Freeze the Official Behavior

### Required investigation

Read the pinned upstream interpreter and document the exact behavior of:

- initialization and seed resolution;
- public versus private observation fields;
- worker action validation and execution order;
- simultaneous planting when seeds are insufficient;
- movement, locked tiles, and shed-access tiles;
- every crop and animal lifecycle transition;
- inventory capacity and overflow on every deposit path;
- market order parsing, limits, quoting, interleaving, and price rounding;
- hiring, worker spawn order, and daily reset;
- land purchase ordering and costs;
- town-center and shop consumption timing;
- weed and shop random-number consumption order;
- day refresh and plant decay order;
- invalid-action behavior;
- terminal-step convention, rewards, and ties;
- every supported configuration override.

Write the findings into `docs/reference_semantics.md`. Include a section for differences between the prose instructions and upstream code. Do not resolve discrepancies by guesswork.

### Important behaviors already identified

The implementing agent should verify these directly rather than rediscovering them late:

- Town-center consumption runs on interpreter step zero.
- The two players' unit market orders quote against the same pre-commit market state and then commit in player order.
- BUY_PRODUCT quotes the post-buy inventory; SELL quotes the pre-sell inventory.
- All PLANT requests for a crop are suppressed when that turn's combined worker demand exceeds available seeds.
- End-of-day randomness uses a fresh Python RNG seeded from the episode seed and day, scans player 0's farm before player 1's, and draws the shop unlock after weed rolls.
- The interpreter's terminal condition and the surrounding Kaggle framework's published step convention must both be reproduced.

## Phase 2 — Python Reference Oracle

### Interface

Provide a command equivalent to:

```bash
python -m reference.run_game \
  --seed 1234 \
  --policy-a reference.policies.pass_policy \
  --policy-b reference.policies.starter_policy \
  --trace output.jsonl
```

The harness must execute the exact pinned interpreter. It may use the installed Kaggle package or a lightweight adapter around the pinned source, but it must not reimplement game transitions.

Each JSONL turn record must include:

```json
{
  "reference_commit": "...",
  "seed": 1234,
  "turn": 42,
  "actions": [{}, {}],
  "observations": [{}, {}],
  "diagnostic_state": {},
  "status": ["ACTIVE", "ACTIVE"],
  "reward": [null, null]
}
```

`diagnostic_state` may contain both private states for testing. Policies must still receive only the information exposed to their player.

### Reference policies

Implement small deterministic policies whose purpose is coverage rather than strength:

- pass-only;
- boundary movement and shed operations;
- complete one-time crop lifecycle;
- complete ongoing crop lifecycle;
- livestock construction, placement, feeding, care, harvesting, and fertilizer collection;
- land purchase and multi-worker behavior;
- market buy/sell stress;
- seeded valid-action fuzzing;
- deliberately malformed and illegal actions.

Policy randomness must be independent of environment randomness and explicitly seeded.

### Acceptance gate

- Repeated runs with the same revision, configuration, seed, and policies produce byte-identical canonical traces.
- The harness records enough state to identify the first semantic disagreement with another backend.
- Tests demonstrate that one player's policy cannot see the opponent's private state.

## Phase 3 — Correct Native (OCaml) Simulator

### Core API

Keep the transition engine independent of command-line parsing, files, threads, and strategy search.

```ocaml
type config      type state      type observation
type player_action      type joint_action      type result

val initial_state : config -> state
val step : state -> joint_action -> unit
val observe : state -> player:int -> observation
val copy : state -> state              (* for hypothetical rollouts *)
val run_game : config -> policy -> policy -> result
```

Expose explicit state copying for future hypothetical rollouts. Do not hide mutable global state inside the engine.

### Representation

- Use variants and fixed-length arrays for products, crop types, animals, shops, market fields, and the default board.
- Use native ints with explicit 32-bit masking wherever Python's integer semantics matter (the RNG above all).
- Keep numeric behavior identical to Python before considering fixed-point alternatives.
- Avoid strings, hashtables, allocation, and closures inside the measured transition loop where practical; OxCaml unboxed records where they help.
- Keep diagnostic serialization separate from public observation construction.
- Implement a Python-compatible RNG path for exact reference replay (CPython's MT19937 with its exact seeding, `random()`, and `getrandbits`-based `choice`).
- If a different high-throughput RNG is later useful, expose it as an explicitly named statistical mode that cannot run differential tests.

### Correctness order

Implement and test coherent rule groups in this order:

1. configuration, initialization, observations, and terminal state;
2. movement, workers, shed access, inventories, and capacity;
3. crops, watering, fertilizer, harvest, and decay;
4. structures, animals, feed, care, products, and fertilizer;
5. market orders, pricing curves, simultaneous quoting, and money;
6. land, hiring, town demand, weeds, shops, and end-of-day RNG;
7. full transition ordering and configuration overrides.

Do not begin performance-specific state layouts until the scalar representation passes differential testing.

## Phase 4 — Differential and Property Testing

### Differential runner

For identical configuration, seed, and joint action tape:

1. run the official Python oracle;
2. run the native simulator;
3. canonicalize both diagnostic states;
4. compare initialization and every post-turn state;
5. stop at the first difference.

A divergence report must include:

```text
seed
configuration
turn and day/hour
both submitted actions
first differing field path
reference value
native value
the previous matching state or its trace location
```

Prefer replaying one generated action tape in both backends over independently implementing a random policy twice.

Implemented exactly that way: both halves consume the same raw JSON tape, the
oracle verbatim and the engine through a tolerant parser that reproduces
upstream's collapse of malformed input. That collapse is itself part of what the
fuzz tapes test. See `docs/differential_testing.md`.

A population also has to be shown non-vacuous: a thousand games of mutual PASS
would pass the comparison and prove nothing. Coverage telemetry derived from each
recorded game gates the run on having actually reached every rule and every
malformed action shape the gate claims to cover.

### Test categories

- Focused unit tests for every rule listed in Phase 1.
- Market boundary and rounding vectors for every price shape.
- Inventory-capacity tests for pickup, drop, place, purchase, and nightly drop.
- Simultaneous-action conflicts and ordered market interactions.
- RNG vectors, weed locations, duplicate shop unlocks, and non-default seeds.
- Full games with scripted policies.
- Valid and invalid action fuzzing.
- Non-default configuration testing, especially short days and games.
- Development builds under AddressSanitizer and UndefinedBehaviorSanitizer.

### Trust gate

**Passed 2026-08-21.** The runner is specified and its result recorded in
`docs/differential_testing.md`; drive it with `python3 -m tools.differential`.
Each condition below is met, and the excluded behaviour named in the fourth is
enumerated there: actions on which upstream raises out of the interpreter rather
than no-opping, and cross-platform libm variance in the price curves.

The first major milestone is complete only when all of the following hold:

- at least 1,000 full 720-turn seeded games pass per-turn differential comparison;
- the set covers scripted and fuzz-generated action tapes;
- final bank balances, statuses, and rewards match;
- any excluded configuration or behavior is explicitly documented;
- a deterministic failing trace can be minimized and replayed locally.

Aggregate win-rate similarity is not a substitute for this gate.

## Phase 5 — Benchmark and Multicore Rollouts

**Completed 2026-08-21.** The reproducible policy workload, raw repetitions,
same-workload scalar comparison, fixed OCaml worker pool, and 1/2/4/8-worker
scaling result are recorded in [`benchmark_baseline.md`](benchmark_baseline.md).
The 135.573x result is scoped to the exact oracle/subprocess-versus-native
workload recorded there. Hardware-counter profiling was blocked by host policy;
therefore none of the deferred state-layout, batching, SIMD, or specialized
dispatch changes below was started.

### Commands

Target a stable CLI along these lines:

```bash
kag-sim play --seed 123 --policy-a a.json --policy-b b.json
kag-sim bench --games 100000
kag-sim evaluate --family family.json --candidate a.json \
  --opponents opponents.json --seeds seeds.txt --threads 16 --copies 50
kag-test differential --seeds seeds.txt
```

### Metrics

Measure both the Python oracle and native backend:

- games per second;
- turns per second;
- nanoseconds per turn;
- CPU utilization;
- peak and steady-state memory;
- thread scaling;
- cache misses and memory bandwidth where available.

Use a fixed worker pool for independent games. Avoid one thread per game. Use thread-local state and result accumulators, and inspect false sharing before adding more complicated scheduling.

After scalar multicore benchmarking, profile before attempting:

- structure-of-arrays batching;
- reduced state copying;
- SIMD across independent environments;
- specialized policy dispatch;
- faster statistical RNG mode.

Record correctness and benchmark build configurations separately. Never compare a sanitized build to an optimized throughput target.

## Phase 6 — Strategy Platform

### Policy layers

Organize the initial agent around four cooperating responsibilities:

1. **Daily obligations** — watering, feeding, time-sensitive harvests, and survival.
2. **Routing and labor** — movement, tile assignments, hiring, shed logistics, and congestion-free schedules.
3. **Capital allocation** — seeds, animals, structures, land, fertilizer, and cash reserves.
4. **Market behavior** — selling schedules, scarcity, town demand, opponent production, and endgame liquidation.

Keep policy decisions separate from simulator transitions. A policy receives only its legal observation and emits one action per active worker plus the bounded ordered market list.

### Baseline opponent population

Build interpretable baselines before search:

- random valid actions;
- crop-greedy short-cycle farming;
- premium-crop delayed return;
- animal-focused production;
- aggressive worker hiring and expansion;
- inventory holding and bundled selling;
- scarcity-responsive trading;
- previous champion snapshots;
- randomized variants of each baseline.

Do not optimize only against the current best policy.

### Parameterized heuristic

Use a versioned, serializable parameter schema. Candidate parameters may include:

- crop and animal allocation preferences;
- expected remaining-season value;
- mandatory-work safety margin;
- hiring limit and marginal labor threshold;
- land-purchase timing;
- cash and wheat-feed reserves;
- fertilizer use and animal-care policy;
- product-specific selling thresholds;
- expected future town demand;
- opponent production and inventory-pressure estimates;
- endgame liquidation horizon.

Do not freeze this schema before baseline play reveals which decisions materially affect results.

## Phase 7 — Evaluation, Search, and Self-Play

### Dataset splits

Create immutable, versioned sets for:

- training seeds used by search;
- validation seeds used for champion promotion;
- untouched holdout seeds used only for milestone reports.

Evaluate both player positions and use common random numbers: compare candidates on the same seeds and opponent assignments to reduce evaluation noise.

### Reported statistics

Win rate is the primary selection metric. Also record:

- mean and median money differential;
- variance and confidence interval;
- 5th and 95th percentiles;
- ties;
- worst matchup;
- per-opponent and per-position results;
- catastrophic-loss rate;
- compute time and number of games.

### Search sequence

1. Manual and grid search for a small number of high-impact thresholds.
2. Random search across the first stable parameter schema.
3. Evolutionary search or CMA-ES for continuous parameter refinement.
4. Champion archive and population self-play.
5. Monte Carlo lookahead or receding-horizon planning only if rollout throughput and final submission latency justify it.

Bayesian optimization and neural policies are not initial requirements. Add them only in response to measured limitations of the heuristic search.

### Champion promotion

A candidate becomes champion only when it:

- beats the incumbent on the validation suite with a predeclared confidence rule;
- does not introduce a severe opponent-specific regression;
- remains within final-agent runtime constraints;
- has a recorded parameter artifact and reproducible evaluation record.

Previous champions remain in the opponent population to reduce cyclic forgetting.

## Phase 8 — Final Kaggle Agent

`submission/main.py` is the frozen DSL interpreter (`submission/dsl/` plus the
vocabulary and action seams) with the champion's family and candidate JSON
inlined as data. Shipping a new champion changes only that data blob; the
interpreter code is written once and accumulates test coverage across the whole
project. A champion that cannot be expressed in the DSL is instead
dual-implemented and proven with golden vectors (Tier 2 of
[`ocaml_migration_decisions.md`](ocaml_migration_decisions.md)) — hand
transcription without that harness is not an accepted path. *Revised 2026-08-21;
this step previously read "distill the selected research policy", a manual
step the DSL exists to delete.*

The submission must:

- be self-contained and use only competition-available dependencies;
- depend on neither the native simulator nor hardware;
- accept the official observation and emit the official action shape;
- never use diagnostic/private opponent data from the research harness;
- stay within per-turn and total runtime limits;
- provide deterministic safe behavior for malformed observations, planning timeout, or an infeasible planned action;
- liquidate inventory deliberately before the terminal step because unsold goods have no value.

Validate the Python submission wrapper on the same holdout seeds, opponent population, and player-position swaps as the research policy. Where the research policy is also implemented natively, compare its selected actions with the distilled Python version over recorded observations.

## Phase 9 — Hardware Feasibility Gate

The detailed Alveo U50 execution contract, proposed microarchitecture,
verification ladder, and benchmark protocol are specified in
[`hardware_feasibility.md`](hardware_feasibility.md). This section remains the
authoritative gate for deciding when hardware work may begin.

Do not begin RTL merely because the project is Hardcaml-oriented. First publish a profiling report that divides runtime among:

- game-state transition;
- policy evaluation;
- RNG;
- state cloning;
- memory traffic;
- cross-game scheduling;
- result aggregation.

For CPU SIMD, GPU, and FPGA options, estimate:

- end-to-end games per second, not isolated kernel operations;
- state bytes per rollout lane;
- branching and synchronization costs;
- transfer and launch overhead;
- expected parallel lanes or batch width;
- engineering cost and impact on strategy iteration speed.

Hardcaml implementation begins only if a coarse-grained accelerator promises a material end-to-end research-throughput improvement over optimized multicore CPU execution.

### If the gate passes

- The FPGA should receive large commands such as policy parameters, opponent identifiers, and seed ranges.
- Each lane should own game state, RNG state, and a hardware-compatible policy for many turns or a complete game.
- The device should return aggregate wins, losses, ties, reward sums, and squared reward sums rather than per-turn host traffic.
- Cyclesim must match the trusted native simulator before synthesis performance matters.
- Board integration remains outside generic numerical or ML circuits.

Repository ownership follows this rule:

- Kaggriculture-specific state machines, features, policies, replay, scoring, and board tops belong in `hardcaml_kaggriculture`.
- A circuit moves to `hardcaml_ml` only after it has a competition-independent interface and a demonstrated reuse case.
- Ethernet, UDP, packet FIFOs, and generic network transports belong in `hardcaml_networking`.
- Simulation helpers stay local until at least two independent projects demonstrate the same abstraction.

## Milestones and Exit Criteria

### Milestone 1 — Trusted simulator

- Pinned and documented official behavior.
- Deterministic Python trace oracle.
- Complete scalar OCaml transition engine.
- Focused rule tests and replayable divergence reporting.
- At least 1,000 full per-turn differential games.
- Single-thread and multicore benchmark report.

### Milestone 2 — Strategy research engine

- Swappable policy API and baseline opponent population.
- Parameterized heuristic with versioned serialization.
- Immutable training, validation, and holdout seeds.
- Reproducible evaluation artifacts.
- Random search plus evolutionary search or CMA-ES.
- Champion archive and per-opponent reporting.

### Milestone 3 — Competitive submission

- Promoted champion validated in both player positions.
- Self-contained `submission/main.py`.
- Runtime, legality, fallback, and endgame-liquidation tests.
- Holdout report comparing submission and research implementations.

### Milestone 4 — Acceleration decision

- Profile of the mature research workload.
- Measured CPU layout/SIMD results.
- Quantitative GPU and FPGA feasibility estimates.
- Explicit decision to stop at CPU or begin a named coarse-grained accelerator.

## Working Rules for Implementing Agents

For every substantial change:

1. Inspect the pinned upstream behavior before encoding a rule.
2. Implement the smallest coherent component.
3. Add focused tests and, where applicable, differential coverage.
4. Run tests before moving to the next component.
5. Record semantic assumptions and discovered discrepancies.
6. Keep unrelated user changes intact.
7. Do not optimize an untrusted transition path.
8. Do not use opponent-private diagnostic data in a policy.
9. Do not present hardware throughput without end-to-end comparison.
10. Update this document when an architectural decision changes; do not let implementation silently supersede it.

## Explicit Non-Goals for the Initial Build

- No early FPGA or GPU simulator.
- No tiny PCIe arithmetic kernels.
- No generic Hardcaml simulation/platform repository.
- No assumption that a supervised dataset already exists.
- No neural network requirement.
- No optimization against a single opponent or tiny fixed seed set.
- No submission dependency on local native code or FPGA hardware.
- No claim of simulator correctness based only on final rewards.
