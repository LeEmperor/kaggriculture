# `authoring/` — policy families as typed OCaml values

Step 4 of the work plan in `docs/ocaml_migration_decisions.md`: the elaboration
layer of Decision 4. A policy family is built as an OCaml value and its JSON
encoding is emitted; no OCaml runs at research or competition time. The frozen
Python interpreter under `submission/dsl/` executes the emitted artifact, and
the golden vectors in `experiments/policies/monocrop_reorder/golden/` are what
prove the two sides agree.

```
lib/         policy_family — the game-agnostic core
  expr.ml      GADT-kinded expression language; ill-kinded families don't compile
  family.ml    Family.create (residual validation) + Family.to_json (the emitter)
kaggriculture/ the per-game seam, mirroring submission/vocabulary.py + actions.py
  vocabulary.ml  typed observation handles, one per KINDS entry
  actions.ml     typed emit constructors carrying the WORKER/MARKET_EMITS signatures
families/    one module per authored family
  monocrop_reorder.ml
bin/emit.ml  serializes a family to stdout
test/        validation_test — what Family.create must refuse
```

## Commands

Run from this directory, on the `5.2.0+ox` opam switch (already the shell
default; `dune` resolves from `~/.opam/5.2.0+ox/bin`).

```sh
dune build          # type-check everything; an ill-kinded family fails here
dune test           # validation negative tests + the real family's positive case
dune exec bin/emit.exe > ../experiments/policies/monocrop_reorder/family.json
```

## Where each class of mistake is caught

| Mistake | Caught by |
| --- | --- |
| wrong operator arity or operand kind, malformed emit | the OCaml compiler (GADT) |
| `next`/`fired` in the wrong stage, `next` before its write | `Family.create` |
| enum write outside a register's domain, double write, name errors | `Family.create` |
| anything the above miss | `submission/dsl/family.py` at load — the final gate |

`family.json` is generated output: change the family here, re-emit, and run the
Python suite (`python3 -m unittest discover -s tests -p 'test_*.py'` and
`python3 -m experiments.golden check`) from the repo root. A behavioural change
additionally lands in `policy.py` and re-records the golden vectors — see the
family's own README.
