# Policy System Architecture

## Purpose

This document defines how a Kaggriculture strategy is represented during
research and how the selected strategy becomes the final Kaggle agent. It
focuses on the boundary between policy algorithm, tunable parameters, and
per-game state. The cross-backend serialization and FPGA representation are
described in [`policy_encoding.md`](policy_encoding.md).

The architecture is based on a sequential policy:

```text
(action_t, state_t+1) = F(observation_t, state_t; parameters)
```

- `F` is the fixed policy algorithm and state-transition logic.
- `observation_t` is the legal information supplied by Kaggle on turn `t`.
- `state_t` is private memory accumulated by the policy during this game.
- `parameters` are immutable candidate values selected before the game.
- `action_t` contains the farmer, hand, and market actions for the turn.

In digital-design terms, `F` is the combinational decision logic and
`PolicyState` is the register bank. The result is similar to a Mealy machine:
the output and next state depend on both current inputs and current state.

## Terminology and Granularity

This project uses the following terms deliberately:

| Term | Meaning | Example |
| --- | --- | --- |
| Strategy | A human-level behavioral or economic plan. It describes what the player is trying to do, but is not by itself a code or repository boundary. | Sell a wheat monoculture; grow wheat as feed for a dairy operation. |
| Policy family | A versioned decision algorithm, parameter schema, and state semantics capable of executing one or more related strategies. A directory under `experiments/policies/` normally represents one family. | `scripted-wheat-v1`, `dairy-heuristic-v1`, or `farm-allocator-v1`. |
| Candidate | One complete immutable parameter assignment for one policy family/version. | A `farm-allocator-v1` configuration with a high cow-investment weight and a wheat-feed reserve of 24. |
| Policy | The runnable observation-to-action system produced by selecting a policy family and candidate parameters. “Policy” may also be used generically when the distinction between family and configured instance is unimportant. | `farm-allocator-v1(candidate_dairy)` instantiated for one player. |
| Policy component | A cooperating implementation unit inside a policy family. It does not independently own the final action. | Crop task generation, animal maintenance, labor planning, routing, or market scoring. |
| Experiment | An evaluation specification that references candidates, opponents, seeds, player positions, and metrics. It does not own or define the policies. | Compare wheat and dairy candidates over common seeds and both player positions. |

The core relationship is:

```text
policy = policy_family(candidate_parameters)
```

An object-oriented analogy is useful, although the implementation need not use
classes:

```text
policy family ~= class
candidate     ~= constructor arguments
policy        ~= configured object
```

The policy family is the reusable machinery. It defines the decisions the
algorithm is capable of considering, its parameter schema, its per-game state,
its scoring and conflict-resolution rules, and its deterministic tie-breaking.
A policy is one runnable configuration of that machinery. Construct a fresh
runtime instance, including fresh `PolicyState`, for every player and episode.

For example, a general `farm-allocator-v1` family might already know how to
grow crops, raise animals, reserve feed, hire and route workers, allocate
capital, sell products, and liquidate. Different candidates can configure that
same family into distinct policies:

```text
                         farm-allocator-v1 family
                         /                       \
                        /                         \
       candidate_wheat.json               candidate_dairy.json
       cow_weight = 0                      cow_weight = 80
       feed_reserve = 0                    feed_reserve = 24
                 |                                  |
                 v                                  v
       configured wheat policy            configured dairy policy
```

Search evaluates many such configured policies without rewriting the family.
Parameters can select among capabilities already present in the family, but
they cannot create missing decisions. If a family has no pasture construction,
cow acquisition, feeding, care, or milk collection logic, adding
`target_cows: 4` to a candidate does not make it a dairy policy.

A strategy is therefore an idea expressed by a configured policy, not a
workspace within an experiment. A policy-family directory is a separately
testable implementation boundary, while an experiment is a consumer of one or
more candidates.

Strategies and policy families are not necessarily one-to-one. A fixed wheat
FSM may support only a wheat-monoculture strategy, while a general farm
allocator may support wheat monoculture, wheat-fed dairy, and mixed production
using different candidates. Conversely, several families can implement the
same dairy strategy using different machinery, such as a scripted FSM, a
scored task allocator, or a rollout planner. “Strategy” describes the intended
game plan; “policy family” identifies the decision algorithm; and “policy”
identifies a runnable configuration of that algorithm.

The deciding question when organizing an implementation is whether the
existing family already knows how to perform the behavior:

- changing how much, when, or how strongly an existing decision is preferred is
  normally a candidate-parameter change;
- adding decisions or state to the same recognizable algorithm is a policy-family
  version change; and
- changing the way decisions are fundamentally generated or coordinated is a
  new policy family.

For example, `monocrop-reorder-v1` cannot become a dairy policy merely by adding
`target_cows` to its candidate. Its algorithm has no pasture construction, cow
acquisition, wheat-feed reservation, animal maintenance, or milk-sale behavior.
Those capabilities require another family or a deliberate evolution into a
more general allocator. Once those decisions exist, values such as target cow
count, feed reserve, and milk-sale threshold are candidate parameters.

## Kaggle Entry Point

Kaggle requires a top-level callable named `agent`:

```python
_POLICY = ChampionPolicy(CHAMPION_PARAMETERS)


def agent(observation):
    if observation["step"] == 0:
        _POLICY.reset()
    return _POLICY.act(observation)
```

`agent` is the external interface. Internally, it may delegate to a large FSM,
planner, routing system, belief model, or other deterministic decision system.
One call controls the main farmer, every hired hand, and the ordered market
actions for that turn.

## One Submitted Policy, Composed Internally

The Kaggle submission exposes one top-level `agent`, so the submitted artifact
ultimately runs one policy for the player. That policy may be internally
composed from many components or may contain several specialized policy
implementations selected by a meta-controller. In either case, the complete
assembly is itself the one submitted policy:

```text
crop component ---------+
animal component -------+--> coordinator --> one legal action bundle
labor/routing component +
market component --------+
```

Components should normally emit task proposals, resource demands, scores, or
constraints rather than independent final actions. A coordinator must resolve
shared concerns including:

- farmer and hand assignments;
- money reserved for seeds, hires, animals, structures, and land;
- wheat divided between sale and animal feed;
- simultaneous seed demand across workers;
- maintenance deadlines and travel time;
- shed capacity; and
- ordering and count limits for market actions.

For a wheat-and-dairy policy, the crop component might propose planting,
watering, harvesting, and selling opportunities. The dairy component might
propose pasture investment, cow acquisition, feeding, care, milk collection,
and a minimum feed reserve. Capital and labor coordinators decide which
proposals can coexist and assign the resulting work. This is composition of
policy components inside one family, not two autonomous policies controlling
the same farm.

Combining complete policies is also possible, but it requires an explicit
meta-policy. Examples include choosing a wheat specialist or dairy specialist
once at the start of a game, switching between production and liquidation
controllers, or asking several planners for proposals and arbitrating between
them. The meta-policy must define state ownership, switching rules, and conflict
resolution. A bundle of policies without those semantics is not composable,
because each policy may assume it owns the same workers, inventory, and money.

During research, distinct complete policy families remain useful as separate
opponents and experimental alternatives. They should only be combined in the
submission when the combined family and its coordinator can be tested as one
deterministic observation/state-to-action system.

## The Three Kinds of Policy Data

### 1. Policy algorithm

The algorithm determines which features are computed, which decisions exist,
how state transitions work, and how actions are selected. Examples include:

- maintaining crops and animals before expanding;
- generating worker tasks and assigning routes;
- scoring possible investments by remaining-season value;
- entering an irreversible liquidation mode near the end; and
- choosing whether to sell now or hold inventory.

Changing the algorithm is a source-code and policy-version change. Python and
C++ implement the same conceptual algorithm independently and are tested for
action and next-state parity. FPGA logic may later implement a compatible
restricted policy family.

### 2. Policy parameters

Parameters are the fixed values being optimized. A parameter changes how the
algorithm behaves without changing what algorithm is executed. Parameters are
loaded once before a game and must remain immutable during that game.

Useful early parameter families include:

| Responsibility | Example parameter | Effect |
| --- | --- | --- |
| Capital | `cash_reserve` | Do not invest below this bank balance. |
| Labor | `max_daily_hires` | Upper bound on temporary workers. |
| Labor | `mandatory_work_margin` | Extra capacity reserved for maintenance. |
| Crops | `crop_score_weights` | Relative preference when selecting crops. |
| Animals | `animal_score_weights` | Relative preference for goose/cow/sheep plans. |
| Logistics | `distance_penalty` | Cost assigned to worker travel. |
| Inventory | `shed_safety_capacity` | Avoid plans likely to overflow the shed. |
| Feed | `wheat_reserve` | Wheat protected from sale for animal feeding. |
| Market | `sell_price_thresholds` | Minimum normal selling price per product. |
| Market | `scarcity_weight` | Value assigned to falling market inventory. |
| Opponent | `opponent_exposure_weight` | Penalize crowded production categories. |
| Expansion | `land_value_threshold` | Required expected return before buying land. |
| Endgame | `liquidation_start_day` | Enter the terminal sell-down phase. |
| Risk | `minimum_payback_margin` | Reject investments with fragile expected return. |

A candidate artifact contains a policy family/version and a concrete set of
these values. C++ search evaluates many candidates over common seeds and an
opponent population. It should not compile a new Python program for each
candidate.

### 3. Policy state

`PolicyState` is fresh mutable memory for one player in one episode. It may
contain information derived only from legal observations and previous actions.

It is not intended to be a second copy of the complete game state. Current
money, farm tiles, shed contents, workers, market inventory, and town shops are
already authoritative in the latest observation and should normally be read
from there.

Good policy-state fields include:

| State category | Examples | Why retain it? |
| --- | --- | --- |
| FSM progress | `mode`, `mode_entered_step` | Preserve irreversible or hysteretic decisions. |
| Plans | worker task queues, target tiles | Remember multi-turn commitments. |
| Assignments | worker-to-task mapping | Avoid replanning all routes every turn. |
| History | previous money, last prices | Compute deltas and trends not supplied directly. |
| Beliefs | estimated opponent stockpile | Summarize evidence from public behavior. |
| Timing | last sale step, cooldown counters | Enforce temporal strategy rules. |
| Diagnostics | requested plants/harvests | Explain behavior and compare backends. |
| Safety | last processed step | Detect resets, skipped turns, or stale state. |

State should be the smallest sufficient summary of history. Large caches are
reasonable when they save meaningful compute, but a growing list of every past
observation is usually slower, harder to port, and unnecessary.

## How Parameters Affect State

Parameters often control state transitions and how stored state is interpreted.
For example:

```text
if observation.day >= parameters.liquidation_start_day:
    state.mode = LIQUIDATION

if market_price >= parameters.sell_price_threshold[product]:
    state.last_sale_step = observation.step
    emit SELL

if required_work + parameters.mandatory_work_margin > available_workers:
    state.expansion_plan = NONE
```

The mutable mode, plan, and last-sale time are state. The liquidation day,
threshold, and safety margin are parameters. Search changes the parameters
between games; the policy changes its state while a game is running.

Online adaptation can also be expressed without mutating the candidate. For
example, `opponent_stockpile_estimate` may be updated each turn using an
immutable `opponent_evidence_gain` parameter. If online learning eventually
modifies weights, those changing weights must be treated as explicit policy
state and included in parity tests.

## Candidate and Champion Flow

```text
Fixed policy family/version
            +
Generated parameter candidates
            |
            v
C++ evaluation on seeds/opponents/positions
            |
            v
Validation and champion promotion
            |
            v
Champion parameter artifact
       +----------+----------+
       |                     |
       v                     v
Python parity tests    C++ regression tests
       |
       v
Parameters embedded into submission/main.py
```

JSON is the research interchange format. The final self-contained submission
may embed the winning parameters as Python literals rather than loading JSON at
runtime. Candidate generation normally happens in memory; only experiment
definitions, notable candidates, and champions need to be checked in.

## Repository Convention

```text
experiments/policies/
  monocrop_reorder/
    README.md                 policy purpose and commands
    candidate_baseline.json  one reproducible parameter candidate
    parameters.schema.json   candidate validation contract
    policy.py                Python algorithm and PolicyState

fast_model/
  ...                        equivalent native policy implementation later

submission/
  main.py                    one promoted policy and embedded parameters
```

Separate Python policy modules are useful for genuinely different algorithms
and baseline opponents, such as crop-greedy versus animal-focused behavior.
Parameter variants of the same algorithm belong in candidate artifacts, not in
thousands of nearly identical Python files.

## Lifecycle and Correctness Rules

- Construct a separate policy instance for each player and each game.
- Reset state at the initial observation (`step == 0`).
- Treat the latest observation as authoritative and reconcile stale plans.
- Never put opponent-private or diagnostic oracle data into policy state.
- Give all state fields and parameter arithmetic deterministic semantics.
- Version parameter schemas and policy-state semantics together when needed.
- Test both selected action and next policy state across Python and C++.
- Provide a legal fallback action if state reconciliation or planning fails.

Golden parity cases should eventually contain:

```json
{
  "policy": {"id": "heuristic-v1", "parameters": {}},
  "observation": {},
  "previous_policy_state": {},
  "expected_action": {},
  "expected_next_policy_state": {}
}
```

The first concrete example of this architecture lives in
[`../experiments/policies/monocrop_reorder/README.md`](../experiments/policies/monocrop_reorder/README.md).

# Extra Elaboration
A policy-family p_f is a collection of
  1. algorithm
  2. parameter schema
  3. state semantics
  
"knobs" can be thought of as the generaliziation of the parameters, both kaggle-supplied and the research-determined params. These are represented in a neutral jsonl format currently.

The state semantics such as ```PolicyState``` fields and update rules for the state are currently back-end specific. Aka for each back-end (Python, C++, OCaml) a code-specific representation of the semantics must be used.

The algorithm is the overencompassing FSM that composes the actual strategy/policy. This is also back-end specific at the moment.
