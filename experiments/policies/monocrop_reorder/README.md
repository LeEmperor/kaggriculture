# Monocrop Reorder

`monocrop-reorder-v1` is an intentionally small stateful policy used to establish
the policy/parameter/state architecture. It is a single-tile monoculture loop,
not a competitive strategy.

The family is named for its algorithm rather than its crop: a one-tile
monoculture loop whose seed purchasing is a reorder-point `(s, Q)` inventory
rule — order `seed_buy_batch` units when stock falls to `seed_reorder_point`,
subject to a `cash_reserve` floor. `crop` is a parameter; v1 restricts it to
`WHEAT`, which is a *version* restriction rather than a family one.

The policy:

1. buys a small batch of seeds while preserving a cash reserve;
2. plants early enough in the day to water before nightly refresh;
3. waters the crop;
4. harvests at the configured age;
5. drops carried produce into the adjacent shed;
6. sells at or above a configured price; and
7. irreversibly enters liquidation mode on the configured day.

## Two implementations, on purpose

This family exists twice:

| File | What it is |
| --- | --- |
| `policy.py` | the hand-written class, `MonocropReorder` |
| `family.json` + `dsl_policy.py` | the same algorithm as *data*, run by `submission/dsl/` |

They are not alternatives to choose between. The DSL one is the destination —
`docs/policy_dsl.md` explains why a family expressed as data needs one
interpreter per backend rather than one implementation per family per backend —
and the hand-written one is the thing it is checked against. `family.json` is
the artifact the OCaml authoring layer will eventually emit rather than a file
to hand-edit indefinitely.

`tests/test_dsl_interpreter.py` asserts they select the same action and the same
next decision-register values, per fixture and over a full seeded episode, and
`tests/test_golden_vectors.py` replays the recorded vectors in `golden/` against
the interpreter. **A behavioural change must land in both** — and then in the
fixtures, via `python3 -m experiments.golden record` — or those tests fail. That
is the cost of keeping the check, and it ends when the hand-written class is
retired.

`family.json` declares one register the prose below does not mention:
`money_seen`, a flag that stands in for the `previous_money is None` case, since
the DSL has no null. Money reaches the DSL as an integer — see
`submission/vocabulary.py` for why that is exact rather than a rounding.

## Parameters versus state

The immutable candidate in `candidate_baseline.json` controls thresholds and
choices such as seed batch size, cash reserve, harvest age, selling price, and
liquidation day.

This `candidate_baseline.json` represents the params of the model. Aka a given strategy s that implements a policy, has a series of initial params that we feed it. In hardware terms, think of these as configuration registers. We get to decide which params are present and what to do with them.

This stands in contrast to the kaggle-fed params such as the initial prices to a crop, or whatever initial conditions the state space (of the market) takes. Think of this as the configuration of the planet at a time t when we start "farming".

`PolicyState` contains only per-game memory:

- current FSM mode and the step at which it was entered;
- last processed step and last requested farmer action;
- previous money and the observed money delta;
- peak price seen for the configured crop; and
- diagnostic counts of requested plant, harvest, and sale actions.

Current farm tiles, shed inventory, seed counts, and market inventory are not
copied into state because every observation already supplies them.

## Run it

From the repository root:

```bash
python3 -m reference.run_game \
  --seed 1234 \
  --policy-a experiments.policies.monocrop_reorder.policy \
  --policy-b reference.policies.pass_policy \
  --trace /tmp/monocrop-reorder.jsonl
```

Inspect the final turn:

```bash
tail -n 1 /tmp/monocrop-reorder.jsonl | jq
```

For a compact iteration report instead of the full turn trace:

```bash
python3 -m experiments.policy_report --seed 1234
```

The report includes the result margin, action counts, market units, final
policy state, and the path to the generated JSONL trace.  In Doom, `SPC o r`
runs this report in the upper project-rail pane; saving this policy's Python or
JSON files refreshes the same report in the background.

Swap `.policy` for `.dsl_policy` in any of the above to run the interpreter
instead of the hand-written class; both export the same entry points.

The reference policy loader uses `make_policy()` when it is present, ensuring
that each player receives a separate policy instance. `agent()` remains the
submission-shaped entry point for direct use.
