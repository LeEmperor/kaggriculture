# My First Strategy

`myfirststrategy-v1` is an intentionally small stateful policy used to establish
the policy/parameter/state architecture. It is a one-tile wheat loop, not a
competitive strategy.

The policy:

1. buys a small batch of wheat seeds while preserving a cash reserve;
2. plants early enough in the day to water before nightly refresh;
3. waters the crop;
4. harvests at the configured age;
5. drops carried produce into the adjacent shed;
6. sells at or above a configured price; and
7. irreversibly enters liquidation mode on the configured day.

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
- peak wheat price seen; and
- diagnostic counts of requested plant, harvest, and sale actions.

Current farm tiles, shed inventory, seed counts, and market inventory are not
copied into state because every observation already supplies them.

## Run it

From the repository root:

```bash
python3 -m reference.run_game \
  --seed 1234 \
  --policy-a experiments.policies.myfirststrategy.policy \
  --policy-b reference.policies.pass_policy \
  --trace /tmp/myfirststrategy.jsonl
```

Inspect the final turn:

```bash
tail -n 1 /tmp/myfirststrategy.jsonl | jq
```

For a compact iteration report instead of the full turn trace:

```bash
python3 -m experiments.policy_report --seed 1234
```

The report includes the result margin, action counts, market units, final
policy state, and the path to the generated JSONL trace.  In Doom, `SPC o r`
runs this report in the upper project-rail pane; saving this policy's Python or
JSON files refreshes the same report in the background.

The reference policy loader uses `make_policy()` when it is present, ensuring
that each player receives a separate policy instance. `agent()` remains the
submission-shaped entry point for direct use.
