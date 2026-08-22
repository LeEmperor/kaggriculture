# Bringing Up a New Strategy

## Status and Scope

Status: **Implementation guide**.

This is the shortest path from a new typed OCaml policy family to a runnable
policy report. It covers authoring, emission, the baseline candidate, and the
shared Python DSL adapter. Submission packaging is a later step described in
[`candidate_build_infrastructure.md`](candidate_build_infrastructure.md).

## 1. Choose the Routing Name

Use one routing name consistently for the OCaml filename, emitter key, policy
directory, and Python module path. For example, `monocrop_field_v1` maps to:

```text
authoring/families/monocrop_field_v1.ml
experiments/policies/monocrop_field_v1/
experiments.policies.monocrop_field_v1.dsl_policy
```

The `Family.create ~policy_id` is embedded policy identity, not the routing
name used by Emacs. It must match `candidate_baseline.json` exactly. The
`Family.create ~family` value is emitted family metadata.

## 2. Author and Register the Family

Copy `authoring/families/template_v1.ml`, rename it to the routing name, and
replace the template parameters, registers, rules, identity, and version.
Then register it in `authoring/bin/emit.ml`:

```ocaml
; ("monocrop_field_v1", fun () -> Families.Monocrop_field_v1.family)
```

`dune build` compiles every module, but the emitter cannot select a family
until this registry entry exists.

## 3. Add the Runnable Policy Directory

Create:

```text
experiments/policies/monocrop_field_v1/
  __init__.py
  candidate_baseline.json
  dsl_policy.py
```

The candidate envelope binds values for every declared family parameter:

```json
{
  "policy_id": "monocrop_field_v1",
  "schema_version": 1,
  "parameters": {
    "crop": "WHEAT"
  }
}
```

Use `experiments/policies/funkystrat_v1/dsl_policy.py` as the adapter template.
Set its `POLICY_ID` to the exact `Family.create ~policy_id`. The adapter is
otherwise family-independent: it loads `family.json`, binds the candidate,
and exposes `make_policy()` and `agent()`.

Do not hand-write `family.json`; the render loop emits it atomically.

## 4. Render and Verify

From the repository root:

```bash
tools/authoring_render.sh monocrop_field_v1 1234
```

A successful report must identify the selected adapter:

```text
policy       experiments.policies.monocrop_field_v1.dsl_policy
```

Then run the complete Python suite:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Doom Emacs Integration

The save hook derives the routing name from
`authoring/families/<routing-name>.ml`, but only accepts it after the matching
`experiments/policies/<routing-name>/dsl_policy.py` exists. Save the OCaml file
again after creating the policy directory so the hook records it as the
current family.

`SPC o r` should run `tools/authoring_render.sh`, and its compilation buffer
should contain `== dune build ==`, `== emit <routing-name> ==`, and a policy
module ending in `<routing-name>.dsl_policy`. If it instead directly invokes
`python -m experiments.policy_report`, reload the Doom configuration or
restart Emacs; that is the obsolete binding still resident in the running
Emacs process.

## 5. Add Candidate Packaging When Needed

Once the family has stable checks, add its `submission.json` and tiny Makefile
using the contract in
[`candidate_build_infrastructure.md`](candidate_build_infrastructure.md). The
policy then gains `make candidate` and the non-mutating `make check` gates.
