# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A research platform for the Kaggle "Kaggriculture" competition: a 30-day (720-turn), two-player
turn-based farming/market game. The final deliverable is a self-contained, standard-library-only
`submission/main.py` exposing `agent(observation)`. Everything else in the repo exists to find and
validate the policy that file will implement.

The secondary research thesis (FPGA acceleration of self-play rollouts) is a *gated later branch*,
not current work. See `docs/edge_computing_research.md` and `docs/hardware_feasibility.md`.

**`docs/kaggriculture_gameplan.md` is the source of truth** for goals, phases, and acceptance gates.
`docs/README.md` defines the authority order when documents overlap. Architectural changes require
editing the game plan, not just a subsystem doc.

Current status: Phases 0–4 complete. The simulator is OCaml (`fast_model/`, replacing the
deleted C++ scaffold, 2026-08-21). All seven Phase 3 rule groups are implemented, each landed
with checked-in differential fixtures replayed against the pinned oracle (crops, animals, the
full market with exact pricing curves, town, land, end-of-day RNG, marketParams overrides).
Its `Python_random` module matches CPython's `random.Random` bit-for-bit, and `python_int`
matches CPython's `int()` over raw tape values.

The OCaml DSL interpreter (`interp/`) and the subprocess policy shim landed 2026-08-21, so
`monocrop-reorder-v1` now runs on three backends from one `family.json`.

**The simulator is trusted for research as of 2026-08-21**: the Phase 4 gate passed — 1,000
full 720-turn seeded games (719,000 turns) plus 300 non-default-configuration games, per-turn
differential comparison over scripted and fuzz tapes, zero divergences, repeated clean on a
second master seed. See `docs/differential_testing.md`. Phase 5 (benchmark and multicore
rollouts) is next, and is blocked on `kag_sim play`/`evaluate` — the simulator still cannot
run a policy. Both DSL interpreters (Python and OCaml) are proven equivalent to the
hand-written family (90 seeds, checked-in golden vectors); `submission/main.py` does not
exist yet.

### Adopted direction: data-defined policies / OCaml migration

These documents record a design direction that is now **adopted**: the game plan's fixed
native-language decision was revised to OCaml on 2026-08-21, and steps 1–8 of the work plan have
landed (Python interpreter, golden vectors, equivalence proof, OCaml authoring, the OCaml
simulator and its trust gate, the OCaml interpreter, the subprocess shim). Each document says
which of its parts have landed; the glossary is reference material and carries no status:

- `docs/policy_dsl.md` — encoding a policy family as *data* (an expression language + rule cascades
  + a four-stage register pipeline) so each backend needs one interpreter rather than one
  implementation per family. Contains the full worked encoding of `monocrop-reorder-v1`.
  **The encoding, its Python interpreter (`submission/dsl/`), the OCaml authoring side
  (`authoring/`), and the OCaml interpreter (`interp/`) are all implemented.**
- `docs/ocaml_migration_decisions.md` — the adopted decision record for OCaml as the primary
  development language. **Its "Work plan and status" section is the single owner of sequencing**
  and carries live Done/Next markers. Start there to find the next action.
- `docs/glossary.md` — project vocabulary and acronyms. Note the two distinct meanings of
  "search": offline candidate search (unaffected by any of this) vs. online lookahead (cannot be
  expressed as data, and needs a simulator inside the submission).

The equivalence gate (steps 2–3 of the work plan) has passed: golden vectors are checked in at
`experiments/policies/monocrop_reorder/golden/vectors.json`, recorded/checked/swept by
`python3 -m experiments.golden`. The OCaml authoring side (step 4) is built: `authoring/` emits
`family.json` from typed OCaml family values — that file is generated output now, not hand-edited.
The C++→OCaml replacement (steps 6–7) is done: the game plan is revised, the C++ scaffold is
deleted, and `fast_model/` is OCaml with a CPython-exact RNG, past its Phase 4 trust gate. The
OCaml reading side (steps 5 and 8) is built: `interp/` interprets the same `family.json`, and a
subprocess shim — never FFI — lets `reference/run_game.py` drive it. The submission mechanism is
decided by measurement: the frozen Python DSL interpreter plus a swapped JSON blob (~62 µs/turn,
~45 ms per episode). Step 9, `kag_sim play`/`evaluate`, is what Phase 5 waits on.

## Commands

All commands run from the repository root. Python is invoked as `python3 -m <module>` so that the
`reference`, `experiments`, and `tests` packages resolve.

```bash
# One-time: fetch the pinned upstream Kaggle environment (gitignored, required by most tests)
python3 reference/bootstrap.py

# Python tests (87 tests; the OCaml-backend ones skip unless `dune build` has run)
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest tests.test_monocrop_reorder                                   # one module
python3 -m unittest tests.test_monocrop_reorder.MonocropReorderTest.test_name     # one test

# OCaml build and test (one dune workspace at the root: authoring/ + fast_model/ + interp/)
dune build && dune test          # authoring + interpreter validator tests, model/RNG goldens

# Optimized benchmark (PASS tapes through the full rule set — see docs/benchmark_baseline.md)
dune exec --profile release fast_model/bin/kag_sim.exe -- bench --games 100000

# Phase 4 differential gate (oracle vs OCaml, per turn; see docs/differential_testing.md)
python3 -m tools.differential run --games 1000 --jobs 8 --coverage --require-coverage
python3 -m tools.differential run --games 300 --variety 1.0 --jobs 8 --require-coverage
python3 -m tools.differential minimize --index <n>    # shrink a divergence to a reproducer

# Lint / typecheck — ruff and pyrefly are installed only in slow_model/.venv
slow_model/.venv/bin/ruff check .        # from root: ruff defaults, no config file exists there
(cd slow_model && .venv/bin/ruff check . && .venv/bin/pyrefly check)   # uses pyproject.toml
```

There is no root `pyproject.toml`, so linting from the root uses ruff's built-in defaults. The
`E,F,I,B,UP` selection and line length 88 are configured in `slow_model/pyproject.toml` and apply
only under that directory.

### Running a policy

```bash
# Full JSONL trace of one episode
python3 -m reference.run_game --seed 1234 \
  --policy-a experiments.policies.monocrop_reorder.policy \
  --policy-b reference.policies.pass_policy \
  --trace /tmp/run.jsonl

# The same family, run through the DSL interpreter instead of the hand-written class
python3 -m reference.run_game --seed 1234 \
  --policy-a experiments.policies.monocrop_reorder.dsl_policy \
  --policy-b reference.policies.pass_policy \
  --trace /tmp/run-dsl.jsonl

# Compact iteration report (margin, action counts, market units, final policy state)
python3 -m experiments.policy_report --seed 1234

# Golden vectors (checked-in fixtures in experiments/policies/monocrop_reorder/golden/)
python3 -m experiments.golden check      # replay fixtures; --backend dsl|hand|ocaml, --strict-telemetry
python3 -m experiments.golden sweep      # live hand-vs-backend parity; --backend, 90 seeds, ~75 s
python3 -m experiments.golden record     # re-record fixtures (only after a deliberate behaviour change)

# The same family through the OCaml interpreter (needs `dune build` first)
python3 -m reference.run_game --seed 1234 \
  --policy-a experiments.policies.monocrop_reorder.ocaml_policy \
  --policy-b reference.policies.pass_policy

# OCaml authoring layer (dune resolves from the 5.2.0+ox opam switch)
dune exec authoring/bin/emit.exe \
  > experiments/policies/monocrop_reorder/family.json        # regenerate the family artifact
```

`--policy-a/--policy-b` take *module paths*. `reference.run_game.load_policy` prefers a
`make_policy()` factory (fresh instance per player) and falls back to a module-level `agent`.
Research policies should export both; `agent` is the submission-shaped entry point.

## Architecture

### Three backends, one strategy

```
Official pinned Python interpreter   (reference/)   — the behavioral oracle, never reimplemented
            |  deterministic JSONL traces
            v
OCaml simulator                      (fast_model/)  — only trusted after differential equivalence
            |  millions of rollouts
            v
Policy search / champion selection   (experiments/)
            |  families authored in OCaml (authoring/), emitted as JSON,
            |  run by either interpreter (interp/ or submission/dsl/)
            v
submission/main.py                                  — stdlib only, self-contained
```

There is **one strategy with several implementations**, not several trained models — and
under the DSL most of them are one interpreter each, not one per family. No runtime
Python↔OCaml bridge (FFI) exists or is planned: research drives OCaml over a subprocess
pipe (`experiments/ocaml_backend.py`), and the submission never loads native code. The shared
artifact between backends is versioned JSON — the candidate (`policy_id` + `schema_version` +
`parameters`, `docs/policy_encoding.md`) and the family encoding (`docs/policy_dsl.md`).

### `reference/` — the oracle

`reference/oracle.py` deliberately does **not** import `kaggle_environments` (its top-level import
loads every bundled environment). Instead it injects a stub `kaggle_environments` module with just
`resolve_episode_seed`, then loads the pinned `kaggriculture.py` by file path. Game transitions run
verbatim upstream; the adapter only reproduces seed resolution, shared/private observation
projection, and the framework's step assignment.

Consequences worth knowing:
- The adapter does **not** reproduce framework schema validation, timeouts, or invalid-agent status
  transitions. Malformed-action differential tests are not yet authoritative (`docs/reference_semantics.md`).
- The upstream checkout (`reference/upstream/`) is gitignored; the pin lives in
  `reference/upstream.lock.json`. Never adopt a newer upstream commit silently.
- Trace records are the differential-testing contract: `reference_commit`, `seed`, `turn`,
  `actions`, `observations`, `diagnostic_state`, `status`, `reward`. `diagnostic_state` holds both
  players' private state for testing; `player_observation` is what policies actually receive.
- The default episode has **719** action transitions after the initial observation, despite the
  prose calling it "720 turns". Both players are marked DONE while processing step `episodeSteps-2`.

### `experiments/policies/` — policy families

Vocabulary is used precisely (see `docs/policy_system_architecture.md`):
- **Policy family** = one directory = a versioned algorithm + parameter schema + state semantics.
- **Candidate** = one immutable JSON parameter assignment (`candidate_baseline.json`).
- **Policy** = family instantiated with a candidate.

The split is deliberately hardware-flavored: parameters are configuration registers set before the
episode; `PolicyState` is the register bank mutated during it; the algorithm is the combinational
logic. `(action_t, state_t+1) = F(observation_t, state_t; parameters)` — a Mealy machine.

Rule of thumb for where a change goes: tuning an existing decision → candidate parameter; adding
decisions/state to the same recognizable algorithm → family version bump; changing how decisions
are generated or coordinated → new family.

`experiments/policies/common/` holds stable *game-contract* helpers only — action constructors,
observation accessors, fixed game data (`SEED_COSTS`), candidate-envelope validation. Strategy
decisions, state transitions, and parameter validation stay inside each family.

`PolicyState.snapshot()` is optional but `experiments/policy_report.py` picks it up automatically
(via `policy.__self__.state.snapshot`) to print final policy state — implement it on new families.

`monocrop_reorder` now has **three** implementations, deliberately: `policy.py`
(hand-written), `dsl_policy.py` (the Python interpreter running `family.json`), and
`ocaml_policy.py` (the OCaml interpreter running the same file over a pipe). The first two
are kept in step by `tests/test_dsl_interpreter.py`, all three by
`python3 -m experiments.golden check --backend {hand,dsl,ocaml}` and by
`tests/test_ocaml_backend.py`. A behavioural change to the family must land in the
hand-written one and in `family.json`, or the parity tests fail — which is the point, until
the hand-written one is retired. Only `family.json` is shared by the two interpreters, so
they cannot drift without the artifact drifting.

### `submission/` — the DSL interpreter

```
submission/
  dsl/            stdlib only, no game concepts, no project imports
    expr.py       AST, static kind inference, short-circuit evaluation
    cascade.py    first-match-wins selection (farmer) and all-match (market)
    pipeline.py   staged register writes; simultaneous commit; ["next", reg]
    family.py     parse + fully validate a family encoding at load time
    interpreter.py  binds the above to an injected vocabulary; the turn loop
  vocabulary.py   the ONLY game-aware observation code; accessor table
  actions.py      the ONLY game-aware action code; emit signatures + assembly
```

The seam is enforced, not assumed: `tests/test_submission_boundary.py` parses every file under
`submission/` and fails on any import outside the standard library, any import of `experiments/`
or `reference/`, and any crop or action name appearing inside `dsl/`.

Three consequences worth knowing before editing:

- **Validation happens at load, not at runtime.** `family.load` resolves every name, enforces the
  stage restrictions on `next`/`fired`, checks kind agreement between each write and its register,
  and checks emit arity and enum domains. A family that loads cannot fail on a name or a type
  mid-episode. Runtime type checking happens in exactly one place — values crossing in from the
  game, in `interpreter._check_observed`.
- **Adding an observation accessor is the expensive change**, not adding a rule. Rules are data.
  An accessor costs one entry in `KINDS` and one in `ACCESSORS` per backend. `docs/policy_dsl.md`
  has the full "how to add a feature" test.
- **`submission/vocabulary.py` duplicates `SEED_COSTS` and the `observations.py` helpers on
  purpose** — the submission is stdlib-only and self-contained, so it cannot import them.
  `tests/test_submission_boundary.py` asserts the copies still agree.

Money is exposed to the DSL as an `int`. It is float-typed upstream but integral by construction
(`float(startingMoney)` plus integer deltas), so this is exact rather than a rounding; the
accessor raises rather than rounds if that ever stops holding.

### `authoring/` — the OCaml elaboration layer

A dune project (opam switch `5.2.0+ox`) that authors policy families as typed OCaml values and
emits their JSON encoding — the Hardcaml relationship: OCaml elaborates, JSON is the artifact, no
OCaml runs at research or competition time. `lib/` (`policy_family`: `expr.ml`, `family.ml`) is
game-agnostic — a GADT makes arity/kind errors uncompilable, `Family.create` enforces the stage
and domain rules the types can't. `kaggriculture/` mirrors `submission/vocabulary.py` and
`actions.py` as typed handles. `families/monocrop_reorder.ml` is the authoring source of
`family.json`; `dune test` runs the validator's negative suite. Changing a family's data form
means editing the `.ml`, re-emitting, and re-running the Python suite — the Python loader stays
the final gate on the emitted artifact.

### `interp/` — the OCaml interpreter

The reading counterpart of `submission/dsl/`, and pointed the opposite way from `authoring/`:
authoring builds a family that is well-kinded by construction and emits JSON, while this reads
JSON that arrives already written and must re-derive every kind at load time. Same rules,
checked by a pass instead of by the compiler — which is why `authoring/lib/expr.ml` (a GADT)
and `interp/lib/expr.ml` (an untyped AST plus an inference pass) are both correct and neither
is a duplicate of the other.

```
interp/
  lib/            the `policy_dsl` library — game agnostic, no crop and no action named
    expr.ml         AST, parse-from-JSON, kind inference, short-circuit evaluation
    cascade.ml      first-match-wins (farmer) and all-match (market)
    pipeline.ml     staged register writes; simultaneous commit; ["next", reg]
    family.ml       parse + fully validate a family encoding at load time; candidate binding
    interpreter.ml  binds the above to an injected vocabulary; the turn loop
  kaggriculture/  the per-game seam: vocabulary.ml + actions.ml over JSON observations
  bin/            kag_policy.exe — the subprocess shim (line protocol in its header)
  test/           the load-time refusals; 42 cases the golden vectors cannot reach
```

The observation type is a **parameter**, not fixed: a vocabulary is `'obs vocabulary`. That is
the one deliberate departure from the Python original, and it is what will let `kag_sim
play`/`evaluate` bind the same interpreter to `Kag_model.observe` without a conversion layer —
a second `interp/kaggriculture/` vocabulary, not a change to `policy_dsl`.

`interp/kaggriculture/` must agree with `submission/vocabulary.py` and `submission/actions.py`,
which are its specification; the golden vectors are what say so, and nothing else enforces it.

### `fast_model/` — the OCaml simulator

`lib/model.ml` fixes the intended API shape: `initial_state`, `step`, `observe`, `copy`,
`run_game` over flat mutable records with fixed-length arrays, no strings or allocation in the
transition loop. Keep the transition engine free of CLI parsing, files, threads, and search.
`lib/python_random.ml` is a CPython-exact `random.Random` (MT19937 + Python's integer seeding,
`random()`, `getrandbits`, `choice`) proven against `test/python_random_fixture.json`, draws
recorded from CPython by `test/record_fixture.py` — upstream re-derives
`random.Random((seed * 1_000_003) ^ day)` each day, so this is the whole RNG surface.

The `kag_sim bench` number (~1.3 µs/transition with the full rule set, PASS tapes) is not a
head-to-head result. Do not report a Python/native speedup until both backends run the same
full transition workload.

**The Phase 4 trust gate has passed** (2026-08-21), so the simulator is usable for research.
`tools/differential.py` drives the oracle and streams each game to `kag_sim differential`,
which replays the same raw JSON tape through the engine and compares every post-turn state.
Both halves consume one tape; the engine parses it through a *tolerant* parser
(`Kag_serialize.player_action_of_json_tolerant`) that reproduces upstream's collapse of
malformed input — a malformed unit action becomes `Unit_pass`, a malformed market order
becomes `Bad_order`, which occupies its slot rather than being dropped because `max_len`
drives the per-index price refresh. The narrow domain where upstream *raises* instead of
no-opping is excluded and raises `Undefined_mapping` rather than being guessed at. Coverage
telemetry (`tools/coverage.py`) gates a run on having actually reached every rule and every
malformed shape, so a population cannot be vacuous. Full specification and result:
`docs/differential_testing.md`.

## Conventions

- Python is 3.12 here but targets 3.11+; every module uses `from __future__ import annotations`,
  PEP 604 unions, and dataclasses. Match that style; `slow_model`'s ruff selection (`E,F,I,B,UP`,
  line length 88) is the de facto house style even where it is not enforced.
- Policy randomness must be explicitly seeded and independent of environment randomness.
- Generated artifacts are gitignored: `build/`, `reference/upstream/`, `experiments/results/`,
  and **all `*.jsonl`** — traces are reproducible from a seed, not checked in.
- Experiment result artifacts must validate against `experiments/experiment.schema.json`
  (see `experiments/example.experiment.json`).
- New docs: only when implementation produces stable detail worth a separate spec, and add a row to
  the table in `docs/README.md`. Documents marked "Superseded" there must not be implemented from.
- `slow_model/` is a scratch analysis area (matplotlib/numpy in its own `.venv`), not part of the
  oracle/simulator/policy pipeline. `test_dir/` is scratch.
