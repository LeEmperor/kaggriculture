# Kaggle Submission Contract and Runbook

## Status and Scope

This document is the repository reference for the Kaggriculture submission
boundary: the file Kaggle receives, the callable and action shapes it expects,
and the commands used to validate, upload, and monitor an agent. Strategy design
belongs elsewhere; code and generated artifacts should reference this document
for packaging and entry-point requirements.

The stable interface below is verified against the pinned Kaggriculture 0.1.0
environment. The live-competition details were verified on 2026-08-21 against
the [official Kaggriculture competition page](https://www.kaggle.com/competitions/kaggriculture/overview).
Recheck that page before relying on dates, quotas, or resource limits.

## Upload Artifact

The default project artifact is the single file:

```text
submission/main.py
```

It must:

- be named `main.py` when uploaded;
- define a top-level callable named `agent`;
- accept one observation argument;
- return one JSON-serializable action object per call;
- use only the Python standard library for this project's final artifact;
- avoid local project, native-code, network, and hardware dependencies; and
- finish within the 1-second per-call budget and 60-second episode overage bank.

Kaggle also accepts a `.tar.gz` for a multi-file agent. In that archive,
`main.py` must be at the archive root. The repository intentionally prefers one
self-contained file to reduce packaging and import failures. Kaggle places
uploaded files under `/kaggle_simulations/agent/` at runtime.

`submission/main.py` is generated output. Do not edit it by hand; change a
runtime source, family, or candidate and rebuild it.

## Submission-System Architecture

Submission packaging is an interaction-layer concern and is independent of the
native simulator, multicore evaluation, FPGA, and other HPC work:

```text
authoring/families/*.ml
          |
          | OCaml family emitter
          v
experiments/policies/<family>/family.json ----+
                                               |
candidate_<name>.json -------------------------+-- tools/build_submission.py
                                               |            |
submission/dsl/*.py ---------------------------+            v
submission/{vocabulary,actions}.py ------------+   submission/main.py
                                                            |
                                                            v
                                              Kaggle browser upload
```

The source-of-truth inputs are:

- `family.json`: the emitted, versioned strategy algorithm;
- the selected candidate JSON: immutable policy parameters;
- `submission/dsl/`: the domain-free Python interpreter; and
- `submission/vocabulary.py` plus `submission/actions.py`: the Kaggriculture
  observation and action seams.

`tools/build_submission.py` validates the family and candidate together, then
embeds those documents and the Python runtime sources into one deterministic
artifact. At startup, that artifact installs the embedded sources under a
private in-memory module namespace, constructs the interpreter and one policy
register bank, and exposes only the required top-level `agent` entry point. This
preserves the tested module boundaries without requiring any project files at
Kaggle runtime.

The generated file records `POLICY_ID`, `POLICY_FAMILY`,
`POLICY_FAMILY_VERSION`, `POLICY_PARAMETERS`, and SHA-256 hashes of its family,
candidate, runtime, and builder inputs. Those fields make the uploaded artifact's
provenance inspectable without the research tree.

## Python Entry Point

The minimum valid entry point is:

```python
def agent(observation):
    return {"farmer": ["PASS"], "hands": [], "market": []}
```

Kaggle owns the call loop. Do not add a CLI loop, read observations from
standard input, or print the returned action yourself.

The observation is the official per-player object described in
[`kaggle_supplied_instructions.md`](kaggle_supplied_instructions.md). It includes
public farms, market, and town state plus only the calling player's private
state. A submission must not rely on the research oracle's diagnostic state or
the opponent's private state.

## Returned Action Shape

Every call should return all three top-level fields:

```json
{
  "farmer": ["PASS"],
  "hands": [],
  "market": []
}
```

Their forms are:

| Field | Shape | Meaning |
| --- | --- | --- |
| `farmer` | `[operation, ...arguments]` | One action for the main farmer. |
| `hands` | `[[operation, ...arguments], ...]` | One action per hired hand, in hand order. |
| `market` | `[[operation, ...arguments], ...]` | Ordered market actions, at most the configured per-turn limit. |

Examples:

```python
# Move the farmer.
{"farmer": ["NORTH"], "hands": [], "market": []}

# Plant while two hired hands water and pass.
{
    "farmer": ["PLANT", "WHEAT"],
    "hands": [["WATER"], ["PASS"]],
    "market": [],
}

# Pass units while buying seeds and selling shed inventory.
{
    "farmer": ["PASS"],
    "hands": [],
    "market": [
        ["BUY_SEED", "WHEAT", 2],
        ["SELL", "CARROT", 3],
    ],
}
```

The complete operation vocabulary and argument rules remain authoritative in
[`kaggle_supplied_instructions.md`](kaggle_supplied_instructions.md). A safe
fallback for any internal policy failure is the complete PASS bundle shown
above.

## Candidate 000: PASS (Historical Smoke Test)

The first upload candidate ignored the observation and returned a fresh PASS
bundle on every turn. It existed to verify the initial pipeline before a real
policy was promoted:

```text
local file -> Kaggle upload -> validation episode -> matchmaking -> episodes/logs -> leaderboard
```

Its policy identity is intentionally outside the strategy DSL:

| Property | Value |
| --- | --- |
| Candidate | `candidate-000` |
| Strategy | PASS-only boundary smoke test |
| Policy family | None |
| Parameter set | None (`{}`) |
| Equivalent research behavior | `reference.policies.pass_policy` |

Candidate 000 did not load a `family.json`, candidate JSON, or DSL interpreter.
It implemented the same action as the reference PASS policy directly so
packaging failures could not be confused with policy-family failures.

## Candidate 001: Monocrop Reorder Baseline (Current)

The current [`../submission/main.py`](../submission/main.py) is generated from:

| Property | Value |
| --- | --- |
| Candidate | `candidate-001` |
| Policy ID | `monocrop-reorder-v1` |
| Policy family | `monocrop_reorder` |
| Family version | `1` |
| Family artifact | `experiments/policies/monocrop_reorder/family.json` |
| Parameter set | `experiments/policies/monocrop_reorder/candidate_baseline.json` |

Its selected parameters are embedded into the file and exposed as
`POLICY_PARAMETERS`. This remains a small one-tile wheat strategy used to prove
the policy architecture, not a champion selected by competitive evaluation.

## Generate the Artifact

Generate the current default family and candidate:

```bash
python3 -m tools.build_submission
```

Confirm that the checked-in artifact is current without rewriting it:

```bash
python3 -m tools.build_submission --check
```

Build another family/candidate pair by naming both inputs explicitly:

```bash
python3 -m tools.build_submission \
  --family experiments/policies/FAMILY/family.json \
  --candidate experiments/policies/FAMILY/candidate_NAME.json \
  --output submission/main.py
```

Generation fails before writing when the JSON is malformed, the policy IDs or
candidate schema disagree, a parameter is missing or extra, a parameter has the
wrong kind/range, or the family does not type-check against the submission
vocabulary and action set.

## Local Validation

Run the candidate-specific and submission-boundary tests:

```bash
python3 -m unittest \
  tests.test_submission_candidate \
  tests.test_submission_boundary
```

Run a full game in the repository's pinned official interpreter and keep the
trace outside the working tree:

```bash
python3 -m reference.run_game \
  --seed 1234 \
  --policy-a submission.main \
  --policy-b reference.policies.pass_policy \
  --trace /tmp/kaggriculture-submission.jsonl
```

For an additional file-loading check with an installed `kaggle-environments`
package:

```bash
python3 -c 'from kaggle_environments import make; e = make("kaggriculture", debug=True); e.run(["submission/main.py", "pass"]); print([(s.status, s.reward) for s in e.steps[-1]])'
```

The pinned repository oracle is the behavioral authority. The installed
package check is useful for reproducing Kaggle-style file loading, but a newer
package version may differ from the pin.

## Browser Upload and Live-Ladder Runbook

First, join the competition and accept its rules on Kaggle. The 2026 competition
page lists an entry deadline of **2026-09-23 23:59 UTC** and a final submission
deadline of **2026-09-30 23:59 UTC**.

To submit through the website:

1. Open the [Kaggriculture competition page](https://www.kaggle.com/competitions/kaggriculture).
2. Click **Join Competition** and accept the rules if you have not already.
3. Click **Submit Agent**. If Kaggle opens the Submissions page first, choose
   **New Submission** there.
4. Choose the single local file `submission/main.py` in the upload picker. Do
   not upload the `submission/` directory and do not rename it to a `.txt` file.
5. Use a description such as `candidate-001 monocrop-reorder baseline`, then
   submit.
6. Leave the Submissions page open while Kaggle runs the self-play validation
   episode. A successful candidate changes from validation/queued state to an
   active state; `Error` means the validation failed.
7. Open that submission to watch its episodes. Use the competition's
   **Leaderboard** tab to watch its rating once matches have run.

The actual file selected in the browser must therefore be:

```text
/home/wayne/devel/shaw/kaggriculture/submission/main.py
```

### Optional CLI equivalent

With the Kaggle CLI installed and authenticated:

```bash
# Confirm that the competition appears among those you entered.
kaggle competitions list --group entered

# Upload the candidate. Its basename is main.py, as required.
kaggle competitions submit kaggriculture \
  -f submission/main.py \
  -m "candidate-001 monocrop-reorder baseline"

# Watch validation and submission state.
kaggle competitions submissions kaggriculture

# After matchmaking begins, inspect one submission's games.
kaggle competitions episodes SUBMISSION_ID

# Inspect an episode or download one agent's logs.
kaggle competitions replay EPISODE_ID -p ./replays
kaggle competitions logs EPISODE_ID 0 -p ./logs

# View the live leaderboard.
kaggle competitions leaderboard kaggriculture -s
```

Do not commit downloaded replays or logs unless they are deliberately reduced
to a stable test fixture.

As verified on 2026-08-21, the live ladder behaves as follows:

- each team may submit up to five agents per day;
- a new submission first plays a validation episode against itself;
- a successful validation joins rating-based matchmaking;
- only the latest two submissions are actively tracked and used for final
  evaluation;
- the leaderboard shows the team's best-scoring bot, while the Submissions page
  shows every submission's progress; and
- after the final submission deadline, games continue for roughly two weeks
  before the final leaderboard converges.

Candidate 001 supersedes the PASS pipeline test. Neither candidate has passed a
champion-promotion evaluation, so the latest-two slots should eventually be
occupied by evaluated competitive candidates.

## Pre-Upload Checklist

- The uploaded file is named `main.py` and defines callable `agent`.
- `python3 -m tools.build_submission --check` confirms it matches its recorded
  inputs.
- Calling `agent(observation)` returns a JSON-serializable object with
  `farmer`, `hands`, and `market` fields.
- The candidate passes the local boundary tests and a full pinned-oracle game.
- The artifact has no local-path, third-party-package, native, network, or
  hardware dependency.
- The submission message identifies the candidate/version.
- Kaggle validation reaches a non-error state before the candidate is treated
  as live.
- Episodes and logs are checked after matchmaking starts.
