# Kaggriculture Documentation

This directory contains the project's planning and game-reference documents.
Use the authority order below when documents overlap.

## Authority Order

1. [`kaggriculture_gameplan.md`](kaggriculture_gameplan.md) is the source of
   truth for project goals, architecture, implementation phases, testing gates,
   and milestone exit criteria.
2. [`kaggle_supplied_instructions.md`](kaggle_supplied_instructions.md) is the
   supplied reference for game rules, observations, actions, and configuration.
   During implementation, the pinned official Kaggle environment source must
   resolve any ambiguity or discrepancy in these prose rules.
3. Focused subsystem specifications created during implementation may refine
   their own subsystem, but they must not silently override the game plan.
   Architectural changes require an explicit update to the game plan.
4. Documents marked **Proposal** record decisions that are not yet adopted.
   They must not be implemented from until the game plan is updated to match.
   A proposal may be marked *partially implemented* when a self-contained part
   of it has landed under a licence granted elsewhere — `policy_dsl.md`'s Python
   interpreter is the standing example, sanctioned by the work plan in
   `ocaml_migration_decisions.md` and by `library_boundaries.md`. The rest of
   such a document is still a proposal and rule 4 still applies to it.
5. Documents marked **Deferred** record analysis whose action has been
   explicitly postponed. They must not be implemented from at all until their
   own stated reopening conditions are met.

## Current Documents

| Document | Status | Purpose |
| --- | --- | --- |
| [`kaggriculture_gameplan.md`](kaggriculture_gameplan.md) | Authoritative | Overall implementation and research roadmap. |
| [`kaggle_supplied_instructions.md`](kaggle_supplied_instructions.md) | Reference | Supplied game rules and agent interface. |
| [`hardware_feasibility.md`](hardware_feasibility.md) | Planned specification | Alveo U50 acceleration contract, architecture, verification, and benchmark plan. |
| [`edge_computing_research.md`](edge_computing_research.md) | Background research | Survey of hardware acceleration for market simulation and agent-based workloads. Motivates the gated acceleration track; not an implementation spec. |
| [`reference_semantics.md`](reference_semantics.md) | In progress | Behaviors verified directly against the pinned upstream interpreter. |
| [`differential_testing.md`](differential_testing.md) | Implemented | The Phase 4 differential runner and trust-gate result: the raw-JSON action-mapping contract and its excluded domain, what is compared per turn, the coverage requirement, and divergence reporting and minimization. Refines the game plan's Phase 4. |
| [`dsl_seam_extension.md`](dsl_seam_extension.md) | Analysis with a measured gate | Why the DSL's game seam is the critical path to a competitive submission, the three levels of extension and what each costs across the seam files, what the Phase 6 league measured each to be worth (vocabulary extension: 0.319→0.757 score; multi-worker: 1.6% of bankroll), the frozen-vocabulary tradeoff, and the reusable probe protocol. Refines `policy_dsl.md`'s limits section. |
| [`evaluation_protocol.md`](evaluation_protocol.md) | Implemented | The Phase 6 evaluation layer: immutable train/validation/holdout seed splits, the native baseline opponent population and its non-vacuity coverage gate, the reported statistics, the evaluation artifact, and the executable champion-promotion rule. Refines the game plan's Phases 6 and 7. |
| [`benchmark_baseline.md`](benchmark_baseline.md) | Implemented | Phase 5 same-policy Python-oracle/subprocess versus native benchmark, retained raw-run protocol, fixed worker-pool scaling, and separately labeled historical PASS-tape measurements. |
| [`kaggriculture_market_game.md`](kaggriculture_market_game.md) | Explanatory | Economic and operational overview of the game. |
| [`policy_system_architecture.md`](policy_system_architecture.md) | Initial architecture | Policy algorithm, candidate parameters, per-game state, and champion flow. |
| [`new_strategy_bringup.md`](new_strategy_bringup.md) | Implementation guide | Minimal OCaml-to-policy-report setup for a newly authored strategy family. |
| [`policy_encoding.md`](policy_encoding.md) | Initial architecture | JSON, C++, and FPGA policy encoding boundaries. |
| [`policy_dsl.md`](policy_dsl.md) | Proposal, largely implemented | Encoding a policy family as data so each backend needs one interpreter rather than one implementation per family. Refines `policy_encoding.md`. The encoding, the Python interpreter (`submission/dsl/`), the OCaml authoring/emitting side (`authoring/`), and the OCaml interpreter (`interp/`) are all built. |
| [`submission_format.md`](submission_format.md) | Implementation reference | Kaggle artifact, entry-point, action-shape, validation, upload, and live-ladder contract. |
| [`candidate_build_infrastructure.md`](candidate_build_infrastructure.md) | Planned specification | Policy-local micro-build system for named candidate artifacts, cross-language verification, and reproducible submission provenance. |
| [`ocaml_migration_decisions.md`](ocaml_migration_decisions.md) | Adopted decision record | OCaml as the primary development language. Steps 1–11 have landed: the trusted native simulator, native DSL evaluation, the Phase 5 benchmark and worker pool, and the Phase 6 evaluation layer with its baseline opponent population. Candidate search is next. |
| [`library_boundaries.md`](library_boundaries.md) | Deferred | Reuse analysis of the DSL, register machine, and research harness, plus an explicit decision not to extract any of them until the Kaggriculture application is finished. |
| [`glossary.md`](glossary.md) | Reference | Acronyms and project vocabulary. Defines terms only; sets no policy. |

Two historical briefs (`kaggriculture_acceleration_codex_brief.md` and
`kaggriculture_hardcaml_architecture.md`) were previously listed here as
Superseded. They are no longer present on disk and their rows have been removed.
Their accepted decisions were consolidated into the game plan, which describes
them as historical inputs; git history preserves their original wording.

## Expected Future Documents

Create focused documents only when implementation produces enough stable detail
to justify a separate specification. Likely examples are:

- `native_simulator_design.md` for the trusted simulator's state and APIs.

`evaluation_protocol.md` was one of these and now exists.

Avoid duplicating requirements across files or creating a document for every
small feature. Each concern should have one clearly identified source of truth.
