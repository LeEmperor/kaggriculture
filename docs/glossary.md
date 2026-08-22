# Glossary and Acronyms

Short definitions for terms and acronyms used across this repository's documents,
code comments, and commit messages. Where a term has a precise project-specific
meaning that differs from common usage, the project meaning is authoritative and
the difference is called out.

Authority note: this document defines vocabulary only. It does not set policy.
Where it summarizes a rule, [`kaggriculture_gameplan.md`](kaggriculture_gameplan.md)
governs.

## Project vocabulary

| Term | Meaning |
| --- | --- |
| **Policy family** | One directory under `experiments/policies/`. A versioned *algorithm* + *parameter schema* + *state semantics*. Not just "the knobs" — the knobs are one of its three parts. |
| **Candidate** | One immutable JSON parameter assignment for a family, e.g. `candidate_baseline.json`. Carries `policy_id` + `schema_version` + `parameters`. |
| **Policy** | A family instantiated with a candidate. This is the thing that plays a game. |
| **Oracle** | `reference/` — the pinned upstream Kaggle interpreter, run verbatim. The behavioral source of truth. Never reimplemented. |
| **Trust gate** | The Phase 4 bar the C++/native simulator must clear before it may be used for research: >=1,000 full 720-turn seeded games passing per-turn differential comparison. |
| **Champion** | The research policy currently selected for distillation into the submission. |
| **Decision register** | A `PolicyState` field that some guard actually reads. Must agree bit-for-bit across backends. |
| **Telemetry register** | A `PolicyState` field that is written but never read by any decision. Useful for diagnostics; divergence across backends is harmless. |
| **Golden vector** | A fixture pinning `(observation, previous_policy_state) -> (action, next_policy_state)`. The mechanism that proves two backends implement the same policy. |
| **Tier 1 / 2 / 3** | How a family is implemented. T1 data-defined (DSL, zero dual implementation). T2 dual-implemented in two languages. T3 research-only, never shipped. See [`ocaml_migration_decisions.md`](ocaml_migration_decisions.md). |
| **Interaction layer** | The thin Python that connects a policy to the outside world: `reference/run_game.py`, the subprocess shim, `submission/main.py`. Deliberately kept small and boring. |

## Two things called "search"

These are routinely confused and mean very different things here.

| Term | When | Where | Ships to Kaggle? |
| --- | --- | --- | --- |
| **Offline candidate search** | During research | Your machine; millions of rollouts | No — only the winning JSON |
| **Online lookahead search** | Mid-episode, at decision time | Inside the agent | Yes — and it needs a simulator shipped with it |

A guard-cascade policy can be expressed as data. An online-lookahead policy cannot,
because it needs the game's transition function inside the submission.

## Technical acronyms

| Acronym | Expansion | Note in this project |
| --- | --- | --- |
| **AST** | Abstract Syntax Tree | The tree form of a DSL expression before evaluation. |
| **ASan / UBSan** | Address Sanitizer / Undefined Behavior Sanitizer | Enabled in the CMake `dev` preset. Never benchmark this build. |
| **DSL** | Domain-Specific Language | Here: the restricted expression + rule language that encodes a policy family as data. See [`policy_dsl.md`](policy_dsl.md). |
| **FFI** | Foreign Function Interface | Calling between languages in-process. Explicitly *not* used here. |
| **FSM** | Finite State Machine | The mode machine inside a policy family. |
| **JSON** | JavaScript Object Notation | Candidates, schemas, golden vectors. Tracked in git. |
| **JSONL** | JSON Lines — one JSON object per line | Episode traces. Gitignored (`*.jsonl`); reproducible from a seed. |
| **Mealy machine** | FSM whose outputs depend on state *and* current input | The policy contract: `(action_t, state_t+1) = F(observation_t, state_t; parameters)`. |
| **Moore machine** | FSM whose outputs depend on state alone | Contrast to Mealy; not the shape used here. |
| **MCTS** | Monte Carlo Tree Search | The canonical online-lookahead method. Gated behind gameplan step 413. |
| **OR / (s, Q)** | Operations Research / reorder-point inventory policy | Order `Q` units when stock falls to reorder point `s`. The core of the `monocrop_reorder` family. |
| **OxCaml** | Jane Street's OCaml variant (switch `5.2.0+ox`) | Adds unboxed types, modes, stack allocation. |
| **POD** | Plain Old Data | Structs with no heap, strings, or virtual dispatch — the `fast_model` transition-loop discipline. |
| **PPX** | PreProcessor eXtension (OCaml) | Derives serializers/comparators, e.g. `ppx_yojson_conv`. |
| **RNG** | Random Number Generator | Environment randomness. Policy randomness must be seeded separately and independently. |
| **opam / dune** | OCaml package manager / build system | Analogous to pip / CMake. |
| **pyml / pythonlib** | OCaml<->CPython bindings | Evaluated and deliberately rejected; see [`ocaml_migration_decisions.md`](ocaml_migration_decisions.md). |

## Game vocabulary

Defined fully in [`kaggle_supplied_instructions.md`](kaggle_supplied_instructions.md);
listed here only as a reading aid.

| Term | Meaning |
| --- | --- |
| **Season** | One full episode. 30 days x 24 turns/day. |
| **Turn / step** | One transition. The default episode has **719** action transitions after the initial observation, despite the prose saying "720 turns". |
| **Shed** | Private per-player storage. Not visible to the opponent. |
| **Shed access** | The four central tiles from which `DROP` / `PICKUP` reach the shed. |
| **One-time yield** | Crops producing a single harvest (wheat, carrot, melon). |
| **Ongoing yield** | Crops/animals producing on a schedule (tomato, strawberry, animals). |
| **Liquidation** | End-of-season conversion of held goods into coins. Only coins score. |
