# Library Boundaries: What Could Be Extracted, and Why Not Yet

Status: **deferred**. This document records a reuse analysis and an explicit
decision **not to act on it yet**. Unlike the documents marked *Proposal*, this
one is not waiting on a game-plan revision to become implementable — it is
waiting on Kaggriculture itself to be finished.

Nothing here overrides [`kaggriculture_gameplan.md`](kaggriculture_gameplan.md),
and nothing here should be implemented from. Its purpose is to *mark seams* so
that the work happening now does not accidentally weld them shut, and to record
the naming discussion so it is not re-litigated later.

Vocabulary: [`glossary.md`](glossary.md). Encoding detail:
[`policy_dsl.md`](policy_dsl.md). Sequencing:
[`ocaml_migration_decisions.md`](ocaml_migration_decisions.md).

## The commitment

**The Kaggriculture-specific application gets fleshed out first. No code is
extracted into a standalone library until that is done.**

This is a deliberate ordering decision, not an oversight:

- the repository is ~1,400 lines across all backends;
- there is **one** policy family (`monocrop_reorder`) and **one** working
  backend (Python);
- the DSL is drafted but not implemented, and has not yet been proven able to
  express even that one family — see the gate in
  [`ocaml_migration_decisions.md`](ocaml_migration_decisions.md), step 3;
- `fast_model/` is a 304-line PASS scaffold that nothing depends on.

Designing a reusable interface against `families = 1`, `backends = 1`, and an
unproven encoding produces an interface shaped by a single example. The seam
costs nothing to mark and a great deal to guess wrong.

**Explicitly still in scope and unaffected by this deferral:** the DSL
interpreter, the golden-vector runner, the equivalence proof, and the OCaml
authoring/emitting side. All of that proceeds on the schedule in
`ocaml_migration_decisions.md` and **stays inside this repository**, as ordinary
in-repo modules. "Later extraction is possible" is not a reason to build any of
it as a package now.

## Two axes of reuse, which are not the same thing

The question "what could be a library?" has two different answers, and
conflating them produces bad boundaries.

| Axis | Meaning | What is actually shared |
| --- | --- | --- |
| **Across backends** | Python / OCaml / C++ / FPGA all run `monocrop-reorder-v1` | A **spec and a conformance suite** — not shared code |
| **Across projects** | Kaggriculture now, a different competition later | **Code**, potentially |

On the backend axis, shared code is impossible by construction:
`submission/main.py` is stdlib-only and self-contained, so the Python
interpreter is a vendored file no matter what, and the OCaml interpreter is a
separate implementation over a typed AST. What binds them is
`family.schema.json` plus golden vectors — the semantic contract already
specified in [`policy_encoding.md`](policy_encoding.md).

**On the backend axis, the conformance suite *is* the library.** Attempting to
share implementation code across backends would defeat the point of having
independent implementations that check each other.

Only the project axis could ever justify a real package split, and that axis is
what this document defers.

## Seam inventory

Ranked by how genuinely domain-independent each piece is. "Generic" here means
*contains no Kaggriculture concept*, not *is currently written to be reusable*.

| # | Seam | Generic? | Currently | Extraction value |
| --- | --- | --- | --- | --- |
| 1 | Staged register machine | fully | drafted in `policy_dsl.md` | highest |
| 2 | Expression AST + evaluator | fully, once vocabulary is cut out | drafted | high |
| 3 | Rule cascade (first-match-wins) | fully | drafted | low — it is small |
| 4 | Candidate / experiment harness | fully | `common/candidates.py`, `experiment.schema.json` | medium |
| 5 | Differential-test runner | fully, given a trace abstraction | not built (Phase 4) | unknown |
| 6 | Emitters | per-target | not built | keep separate regardless |
| 7 | Observation vocabulary | **not at all** | `common/observations.py` | none — per-game by definition |

### 1. The staged register machine

The four-stage pipeline in `policy_dsl.md` — reset, observe, decide, commit —
with **simultaneous commit within a stage**, the `["next", reg]` escape hatch,
and the **decision vs. telemetry register classification**.

None of this knows anything about farming. It is Verilog non-blocking assignment
(`<=`) re-derived as a policy-authoring discipline, and it solves a problem every
stateful-agent project has: two implementations disagreeing about whether a
register had already been updated when another read it. `policy_dsl.md` states
this directly — the largest divergence risk is not the rules but the order in
which registers update relative to decisions.

The decision/telemetry split is the most valuable single idea to preserve.
"Seven of ten registers are telemetry" is a general property of heuristic
agents, not a fact about `monocrop_reorder`, and the split is what prevents
every future diagnostic field from becoming a cross-backend liability.

### 2. The expression AST and evaluator

The language itself — prefix JSON, roughly fifteen operators, no division, no
floats, no loops, no indexing, no function definition — is domain-free. The
**observation vocabulary is entirely Kaggriculture**. That is exactly where the
parameterization boundary lies.

Shape it would take in OCaml, recorded so the in-repo modules can be written
along the same lines without being packaged:

```ocaml
module type VOCABULARY = sig
  type observation
  val accessors : (string * (observation -> Value.t)) list
end

module type ACTIONS = sig
  type t
  val emit : string -> Value.t list -> t   (* ["SELL", crop, units] *)
end

module Make (V : VOCABULARY) (A : ACTIONS) : sig
  type family
  val of_json   : Yojson.Safe.t -> (family, Error.t) result
  val step      : family -> Params.t -> Regs.t -> V.observation -> A.t * Regs.t
  val emit_json : family -> Yojson.Safe.t
end
```

In Python the same seam is an injected dispatch dict, which is how
`policy_dsl.md` already describes the interpreter. **Writing it that way is
required for the submission anyway** — the interpreter must be vendorable into
`submission/main.py` without dragging game tables along behind it. This is the
one seam that must be respected *now*, and it is a file-layout matter, not a
packaging one.

### 4. The candidate and experiment harness

Envelope validation (`policy_id` + `schema_version` + `parameters`), provenance
records (`project_commit`, `upstream_commit`, `seeds`, `player_positions`,
`machine`), seed splits, and champion promotion. Every offline-search-against-a-
simulator project needs these and most reimplement them badly.

Important structural note: this layer should depend on the DSL only through an
**opaque artifact type**. A harness that understands the contents of a family
encoding has been coupled to the DSL for no benefit.

### 6. Emitters

`Emit.json`, Python codegen, the packed binary ABI from `policy_encoding.md`,
and any Hardcaml path. These have very different dependencies from one another
and from the interpreter. Keep them as separate modules regardless of whether
anything is ever extracted — a submission-shaped interpreter must not
transitively depend on an RTL emitter.

## What must never be extracted

- **`reference/`.** The pinned upstream oracle is a *trust anchor*. Generalizing
  it does not port it, it destroys the property that makes it valuable.
  `ocaml_migration_decisions.md` already states this as permanent.
- **`game_data.py` (`SEED_COSTS`) and `observations.py`.** Game contract, by
  definition per-game. The existing docstring — "stable *game-contract* helpers
  only" — is doing real work and should stay.
- **The action encoding** in `actions.py`. Kaggriculture's list form.
- **The simulator transition function**, whichever language it ends up in.

## Naming

Recorded so the discussion is not repeated. Three names were floated:
`research-caml`, `market-caml`, `stratcaml`.

| Candidate | Verdict | Reason |
| --- | --- | --- |
| `market-caml` for the DSL | **rejected** | The DSL contains no market concept. Markets live in the *vocabulary*, the one layer that is per-game. Naming the language after a domain it does not own is what makes a library un-reusable later. |
| `research-caml` | **rejected** | "Research" is not a subsystem, so it cannot define a boundary. It would become a junk drawer. |
| `stratcaml` | **kept, for the harness** | Right idea, wrong layer: it fits candidates/seeds/experiments/champions (seam 4), not the expression language. |

Two further constraints on any eventual naming:

- **The spec must have a language-neutral name.** A Python implementation is a
  first-class citizen, so a `-caml` suffix on `family.schema.json` or on the
  golden-vector format would be a lie about what the artifact is.
- **The `-caml` suffix is acceptable for a project or repository name, not for
  a dune library.** The OCaml ecosystem ships `hardcaml`, `bonsai`, `core`;
  `hardcaml` earned its suffix by being the exception, not the pattern.

### There is no "kernel" — there are four roles

"Kernel" is used in the seam inventory above as a *reuse category*: everything
left after removing game knowledge. It is not a module name, and treating it as
one is why no name fit. What it covers is four separable things:

| # | Role | Shape | Depends on |
| --- | --- | --- | --- |
| 1 | Expression language — AST + `eval_expr` | pure: `expr x env -> value` | nothing |
| 2 | Rule cascade — first-match-wins selection | pure: `rules x env -> (name, action)` | 1 |
| 3 | Staged register pipeline — reset/observe/decide/commit, simultaneous commit, `["next", reg]` | *semantics*, not a function | 1, 2 |
| 4 | Family container — parameters + registers + stages, versioned | a type / artifact | 1-3 |

These are numbered by dependency order, unrelated to the seam ranking above.

Each has an obvious name individually; their union does not. That is a signal to
name the roles, not the union.

**`mealy` is rejected.** It names the policy *contract* —
`(action_t, state_t+1) = F(observation_t, state_t; parameters)` — which
`MonocropReorder` already satisfies today with no DSL at all. A name shared by
the thing being replaced and the thing replacing it cannot distinguish them. The
actual discriminator is role 3: staged, simultaneous commit, which is precisely
what the hand-written Python lacks, its ordering being implicit in the body of
`act()`.

The governing analogy supports splitting rather than unifying. Hardcaml has no
"kernel"; it names each role:

| Hardcaml | Role | Here |
| --- | --- | --- |
| `Signal` | combinational expression graph | role 1 |
| `Always` | staged / non-blocking assignment DSL | role 3 |
| `Circuit` | the elaborated artifact | role 4 |
| `Cyclesim` | interpreter that runs it | `step` |
| `Rtl` | emitter | `Emit.json`, Python codegen |

`Always` exists in Hardcaml for exactly the reason role 3 exists here:
assignment ordering inside a clocked block needs its own discipline.

One practical constraint follows. `hardcaml_networking/` sits alongside this
repository, so `rtl`, `datapath`, `regfile`, and `signal` already carry a
different meaning in this author's working vocabulary. The register metaphor in
`policy_system_architecture.md` prose is good; those identifiers would collide.

### Module names, which are the only naming decision that binds now

There is no package and no dune library to name until step 4 of the migration
plan, which is deferred. The names that must be chosen this month are modules
inside this repository, and they fall out of the roles directly:

```text
expr            role 1 - AST + eval. Zero project imports.
cascade         role 2 - rule selection
pipeline        role 3 - the four stages and commit semantics
family          role 4 - parse and validate the encoding, hold the artifact
vocabulary      the per-game seam; the only Kaggriculture-aware file
interpreter     binds the above to a vocabulary; this is what main.py vendors
```

None of these forecloses anything, and none of them is an umbrella.

### Umbrella names, only if extraction ever happens

| Layer | Name | Contents |
| --- | --- | --- |
| Spec (neutral) | `policyspec` | `family.schema.json`, golden-vector format, conformance suite |
| Roles 1-4 | `policy_family` | Named after the artifact it defines, runs, and emits |
| Vocabulary | `kaggriculture_vocab` | Per-game. An *instance*, never a library |
| Harness | `stratcaml` | Candidates, seeds, experiments, differential runner |
| Emitters | `emit` | json / python / packed / hardcaml |

The general rule, which is what actually settles this: **name a layer by what it
holds or produces, not by what shape it is.** Shape names (`mealy`,
`market-caml`) describe properties that many things share, so they locate
nothing. Artifact names locate exactly one thing. `mealy` stays in
[`glossary.md`](glossary.md), where it correctly describes the contract and
nothing else.

## What to do now instead

Mark the seams; do not cut them.

1. Write the DSL interpreter as a **self-contained unit with zero project
   imports**, taking its vocabulary as an injected table. Required for
   `submission/main.py` regardless of any library question.
2. Keep the vocabulary table in its own module, so the two are separable by
   deletion rather than by refactor.
3. Keep emitters out of the interpreter's dependency set.
4. Keep the harness depending on family encodings only as opaque artifacts.
5. Leave everything in this repository, in the layout the game plan already
   describes. No new packages, no new repositories, no dune library split beyond
   what the OCaml work itself needs.

None of the above costs anything relative to writing the code without regard for
reuse. All of it preserves the option.

## When to revisit

Reasons that would justify reopening this document. Any *one* is sufficient; none
has occurred:

- a **second policy family** exists and the interface of roles 1-4 survived it
  unchanged;
- a **second game or competition** is being started in earnest, with real work
  committed to it rather than an idea;
- the **OCaml and Python interpreters both pass** the golden-vector conformance
  suite, proving the spec is genuinely implementation-independent;
- a third party asks to use one of these pieces.

The first two are the ones that would actually shape the interface. Until then,
the interface would be shaped by `monocrop_reorder` alone.

## Note on a second competition

The prompt for this analysis was whether this machinery could serve a different
competition later. Recorded so the constraints are known before that is
attempted, **not** as a plan:

- Seams 1 and 4 (register machine, harness) would port. Seam 7 (vocabulary)
  would not, by design.
- A quoting or market-making policy *is* a Mealy machine over market
  observations, so the shape fits well.
- Two DSL v1 restrictions would bite immediately: **no float or fixed-point
  arithmetic**, and **no rolling-window accessors** (moving averages, realized
  volatility, imbalance over N ticks). Both are deliberate omissions that keep
  the FPGA branch synthesizable, per `policy_dsl.md`.
- The likely resolutions, if it ever comes up: fixed-point parameters in the
  style `policy_encoding.md` already proposes (`market_scarcity_weight_q8_8`),
  and windowed accessors added to the *vocabulary* rather than as operators in
  the language. Adding statefulness to accessors is a real design question and
  is not answered here.

None of this should influence DSL v1. A language bent toward a hypothetical
second application before the first one works serves neither.

## Open questions

- Does the harness (seam 4) belong with roles 1-4 or apart from them? It has no
  dependency on the DSL, which argues apart, but they are always used together.
- Does the conformance suite live with the spec or with each implementation?
  With the spec is the honest answer, but that only becomes a real question once
  a second implementation exists.
- Do `cascade` and `pipeline` survive as separate modules, or does the
  interpreter collapse them once written? The four-role split is a claim about
  the design, not yet an observation about code.
