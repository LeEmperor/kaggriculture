# Candidate Build Infrastructure

## Status and Scope

Status: **Planned specification**.

This document specifies the micro-build system that turns a policy family and
candidate parameter set into a named, upload-ready Kaggle artifact and verifies
that artifact across the Python and OCaml policy implementations. It refines
the submission packaging portion of Phase 8 in
[`kaggriculture_gameplan.md`](kaggriculture_gameplan.md); it does not change the
simulator, evaluation, multicore, or hardware architecture.

The lower-level deterministic packer already exists in
`tools/build_submission.py`. It validates one family/candidate pair and emits a
self-contained, standard-library-only `main.py`. The work specified here adds a
policy-local interface, manifests, named build outputs, and coordinated checks
around that packer.

## Desired Interface

From a policy directory:

```bash
cd experiments/policies/monocrop_reorder

make candidate
make creck
```

From the repository root:

```bash
make -C experiments/policies/monocrop_reorder candidate
make -C experiments/policies/monocrop_reorder check
```

An optional candidate selector chooses among several parameter sets in one
family:

```bash
make candidate CANDIDATE=baseline
make check CANDIDATE=baseline
```

`make candidate` must print the exact file to select in Kaggle's browser upload
dialog. `make check` must be non-mutating and end with an unambiguous
`READY TO SUBMIT` or failure report.

## Tooling Decision

Use a hybrid system:

| Tool | Role | Decision |
| --- | --- | --- |
| Python | Manifest validation, subprocess coordination, hashing, provenance, and artifact generation | Central orchestration engine |
| Make | Uniform policy-local `candidate` and `check` targets | Thin user-facing facade |
| Dune | OCaml compilation, family emission, and OCaml tests | Retain its existing scope |
| Shell | At most trivial wrappers | Do not put build semantics here |
| Bazel | Would introduce a second repository-wide build model | Do not adopt |

A pure Dune solution makes browser-facing output placement and cross-language
test orchestration awkward. A pure Make solution pushes structured JSON
validation and provenance into brittle recipes. Bazel would be a disproportionate
migration for two policy-local commands. Python already owns the deterministic
submission packer, while Make supplies the concise interface without duplicating
logic in every policy directory.

## Architecture

Each policy directory declares what to build and check; shared infrastructure
owns how:

```text
experiments/policies/<family>/Makefile
                   |
                   v
         tools/candidate.mk
                   |
                   v
         tools/candidate.py
          |        |       \
          |        |        +--> Python, golden, and oracle checks
          |        +-----------> Dune emitter and OCaml checks
          +--------------------> tools/build_submission.py
                                      |
                                      v
                    submissions/<strategy-name>/main.py
```

Responsibilities remain separate:

- `tools/build_submission.py` is a pure lower-level packer for one explicit
  family/candidate pair;
- `tools/candidate.py` is the micro-build orchestrator with `build` and `check`
  subcommands;
- `tools/candidate.mk` maps stable Make targets onto that orchestrator;
- each policy-local `Makefile` only includes the shared Make fragment; and
- each policy-local `submission.json` declares candidates and verification
  gates without containing arbitrary shell commands.

The orchestrator must execute subprocess argument arrays without `shell=True`.
All manifest paths are relative to the manifest, not the caller's current
directory.

## Policy Directory Contract

A participating directory has this shape:

```text
experiments/policies/monocrop_reorder/
  Makefile
  submission.json
  family.json
  candidate_baseline.json
  dsl_policy.py
  ocaml_policy.py
  policy.py
  golden/
```

The Makefile is deliberately tiny. Conceptually it only establishes the policy
directory and includes `tools/candidate.mk`; it must not grow family-specific
build recipes.

### Manifest

Initial `submission.json` shape:

```json
{
  "schema_version": 1,
  "family": {
    "name": "monocrop_reorder",
    "emitter": "monocrop_reorder",
    "artifact": "family.json"
  },
  "default_candidate": "baseline",
  "candidates": {
    "baseline": {
      "strategy_name": "monocrop-reorder-baseline",
      "parameters": "candidate_baseline.json"
    }
  },
  "checks": {
    "research_policy": "experiments.policies.monocrop_reorder.dsl_policy",
    "python_tests": [
      "tests.test_monocrop_reorder",
      "tests.test_dsl_interpreter",
      "tests.test_golden_vectors",
      "tests.test_submission_boundary"
    ],
    "golden_backends": ["hand", "dsl", "ocaml"],
    "dune_scopes": ["authoring", "interp"],
    "oracle_seeds": [1234],
    "opponent": "reference.policies.pass_policy"
  }
}
```

The checked-in schema must reject unknown keys, duplicate strategy names,
unsafe strategy names or paths, a missing default candidate, empty check sets,
and references that escape the policy or repository boundaries. Check
configuration is structured data interpreted by the orchestrator; manifests
must not embed shell fragments.

## Build Output Contract

Keep reusable runtime source and generated results distinct:

```text
submission/                         reusable Python runtime and DSL seams
submissions/                        candidate build results
  monocrop-reorder-baseline/
    main.py                         file selected in the browser
    build.json                      provenance for these exact bytes
```

Kaggle requires the uploaded single file to be named `main.py`. A strategy-named
directory gives artifacts stable, descriptive identities without producing an
incorrect upload basename such as `monocrop_reorder.py`.

`build.json` records at least:

```json
{
  "schema_version": 1,
  "strategy_name": "monocrop-reorder-baseline",
  "policy_id": "monocrop-reorder-v1",
  "family": "monocrop_reorder",
  "family_version": 1,
  "candidate": "baseline",
  "project_commit": "...",
  "artifact_sha256": "...",
  "family_sha256": "...",
  "candidate_sha256": "...",
  "runtime_sha256": "...",
  "builder_sha256": "..."
}
```

The generated `main.py` remains deterministic. Time stamps, machine names, and
other volatile build metadata belong in `build.json`, never in `main.py`.
Generation uses a candidate-specific temporary file followed by an atomic
replace. Concurrent builds of different strategy names must not share an output
path.

During migration, the existing `submission/main.py` may remain as a
compatibility artifact, but `submissions/<strategy-name>/main.py` becomes the
canonical browser-upload result. The compatibility path must not silently point
at an unknown candidate.

## `candidate` Target

`make candidate` performs these steps in order:

1. Load and validate `submission.json`.
2. Resolve `CANDIDATE`, or use `default_candidate`.
3. Build the relevant OCaml authoring target.
4. Run the manifest's named family emitter and capture stdout.
5. Atomically update `family.json` only after valid JSON is emitted.
6. Validate the candidate ID, schema, and parameters against that family.
7. Invoke `tools/build_submission.py` as a library to create the named
   `submissions/<strategy-name>/main.py`.
8. Compile and import the artifact in an isolated Python process.
9. Write `build.json`.
10. Print the strategy identity, artifact hash, and absolute browser-upload
    path.

Expected summary:

```text
Built monocrop-reorder-baseline
Upload:
  /home/wayne/devel/shaw/kaggriculture/submissions/monocrop-reorder-baseline/main.py
SHA-256:
  0123456789abcdef...
```

Build must fail before replacing a previously valid artifact when emission,
validation, generation, compilation, or isolated import fails.

## `check` Target

`make check` is non-mutating and fails fast in this order:

1. Validate the manifest and selected candidate.
2. Confirm fresh OCaml emitter output exactly matches checked-in `family.json`.
3. Confirm the candidate binds to the emitted family.
4. Rebuild `main.py` in memory and confirm the named artifact is byte-current.
5. Run the manifest's scoped Dune tests.
6. Run its declared Python tests.
7. Run golden-vector checks for the declared hand, DSL, and OCaml backends.
8. Compare the generated agent with the research DSL policy turn-by-turn.
9. Run full oracle games for the declared seeds, opponents, and both player
   positions.
10. Copy the generated file into an isolated temporary directory and run it
    with no repository on `PYTHONPATH`.
11. Confirm every returned action is JSON serializable and the generated file
    imports only the standard library.
12. Confirm `build.json` matches the artifact and its current inputs.

Expected summary:

```text
candidate:       monocrop-reorder-baseline
family emission: PASS
candidate bind:  PASS
dune tests:      PASS
python tests:    PASS
golden hand:     PASS
golden dsl:      PASS
golden ocaml:    PASS
full episode:    PASS
isolated import: PASS
artifact stale:  NO

READY TO SUBMIT
```

The detailed failing command and its captured output must remain visible on
failure. A summarized green report must not discard evidence needed to diagnose
a red gate.

## Generated-Artifact Policy

Candidate manifests, parameter files, schemas, Makefiles, builders, and tests
are source and must be checked in. `submissions/` is a build-results directory;
the implementation must make an explicit repository decision before adding it
to `.gitignore`:

- ignoring it keeps the repository small and relies on deterministic rebuilds
  plus recorded hashes; or
- retaining selected artifacts preserves the exact uploaded bytes for audit.

Whichever choice is adopted, leaderboard or experiment records must retain the
artifact SHA-256 and project commit. Do not accumulate unlabeled `main.py`
copies whose family and parameters cannot be recovered.

## Implementation Sequence

1. Define and test the `submission.json` schema and path-safety rules.
2. Add `tools/candidate.py` with manifest loading and `build`/`check`
   subcommands.
3. Add `tools/candidate.mk` and a tiny policy-local Makefile.
4. Migrate `monocrop_reorder` first and generate its named baseline artifact.
5. Generalize the existing Monocrop-specific submission tests into
   manifest-driven artifact checks.
6. Add the configured Dune, Python, golden, full-episode, and isolation gates.
7. Migrate `funkystrat_v1` as the second family to prove that the convention is
   reusable rather than Monocrop-specific.
8. Update [`submission_format.md`](submission_format.md) so its normal workflow
   uses policy-local Make targets and named artifacts.
9. Decide and document the generated-artifact retention policy.

## Acceptance Criteria

- A contributor can enter either migrated policy directory and run only
  `make candidate` followed by `make check`.
- No policy directory duplicates emitter, packer, validation, hashing, or test
  orchestration logic.
- Changing a family, candidate, runtime seam, or builder makes `make check`
  report the artifact stale.
- A failed build never destroys the last valid candidate artifact.
- The artifact runs from a temporary directory with no project import path.
- Generated behavior agrees with the configured research policy and OCaml
  backend over the declared gates.
- The final output names the exact `main.py` to upload and the hash of its bytes.
- The infrastructure does not depend on `fast_model/`, multicore evaluation,
  FPGA tooling, or hardware availability.

## Next-Session Handoff

Copy and paste this into a fresh coding session:

```text
Implement the first slice of docs/candidate_build_infrastructure.md.

Context:
- tools/build_submission.py already deterministically builds one self-contained
  main.py from an explicit family.json and candidate JSON.
- submission/main.py is currently the generated monocrop-reorder-v1 baseline.
- Do not modify the simulator/HPC work; candidate packaging is independent.

Start with:
1. Add and test a versioned submission.json schema plus a safe manifest loader.
2. Add tools/candidate.py with a manifest-driven `build` subcommand.
3. Add tools/candidate.mk and a tiny Makefile under
   experiments/policies/monocrop_reorder/ so `make candidate` works there.
4. Emit the named artifact to
   submissions/monocrop-reorder-baseline/main.py and write build.json with
   provenance hashes.
5. Preserve tools/build_submission.py as the lower-level packer rather than
   duplicating it.

Then add the non-mutating `make check` gates from the specification. Keep paths
relative to the manifest, use subprocess argument arrays without shell=True,
write outputs atomically, preserve unrelated working-tree changes, and run the
full Python suite when done.
```
