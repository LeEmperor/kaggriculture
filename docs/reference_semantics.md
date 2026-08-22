# Verified Reference Semantics

Status: partial. This records only the initialization and terminal slice used by
the first native scaffold. It is not yet the complete Phase 1 rules audit.

Authority inspected:

```text
Repository:  https://github.com/Kaggle/kaggle-environments.git
Commit:      28b6d8af3ce73926b3d0fda1410c1ddd8384ab8c
Package:     kaggle-environments 1.32.7
Environment: kaggriculture 0.1.0
Retrieved:   2026-08-20
```

## Runtime contract

- Default `episodeSteps` is 720.
- Default `actTimeout` is 1 second.
- Each agent starts with 60 seconds of overage time. Only time beyond the
  per-action timeout is deducted from this bank by the local framework.
- Default `turnsPerDay` is 24.

## Initialization

- The episode seed is resolved from `env.info["seed"]`, then configuration
  `seed`, then a random 31-bit integer. It is removed from configuration and
  persisted in `env.info`, so policies cannot observe it directly.
- Both farms start with `$3000.0`, no hands, and zero hires that day.
- On the default 10x10 board, the 5x5 NW quadrant is empty and unlocked. The
  other 75 tiles are the string `"LOCKED"`.
- The main farmer begins at `(4, 4)`, the NW shed-access tile.
- Sheds contain zero of every product and animal. Seed counts are separately
  initialized to zero. The farmer inventory is empty.
- Every market product begins at its configured `I0` inventory and base price.
  No town shops are unlocked.
- Day and hour begin at zero.

## PASS-only transition and terminal convention

- A PASS action does not mutate the farms. Town-center consumption still runs
  on interpreter step zero, so a full default PASS game is not globally static.
- The interpreter processes prior observation steps `0..episodeSteps-2`.
- While it processes step `episodeSteps-2`, it marks both players DONE and sets
  each reward to that player's money. Consequently, the default episode has 719
  action transitions after its initial observation, despite the prose shorthand
  calling the season “720 turns.”
- The surrounding framework assigns the shared observation step after each
  interpreter call and resets it to zero when the environment is done. Day/hour
  are assigned inside the game interpreter from `previous_step + 1`.

## Adapter boundary

`reference.oracle` executes the pinned `kaggriculture.py` directly. Its small
adapter reproduces seed resolution, shared/private observation projection, and
framework step assignment without importing all optional dependencies from the
upstream package. It does not yet reproduce general framework schema validation,
timeouts, logging, or invalid-agent status transitions. Those boundaries must be
added before malformed-action differential tests are considered authoritative.

That limit is narrower than it sounds, and is now pinned down. Upstream treats
almost every malformed action as a silent no-op, and those *are* covered: the
Phase 4 fuzz tapes drive them through both backends and compare the result. Only
the cases where upstream raises out of the interpreter are excluded — the
uncaught `int(action[2])` in PICKUP / PLACE, and the dict lookups that hash an
unhashable `action[0]` or `action[1]` — because the framework turns those into an
agent ERROR that this adapter does not model. See
[`differential_testing.md`](differential_testing.md) for the exact boundary and
how the runner enforces it.

