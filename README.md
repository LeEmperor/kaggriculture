# Kaggriculture Research Platform

This repository builds a competition agent in two deliberately separate
environments:

- the pinned official Python environment is the behavioral oracle and the final
  submission target;
- a C++20 simulator is used only for fast, local strategy evaluation after it
  matches the oracle turn-for-turn.

The implementation roadmap and correctness gates live in
[`docs/kaggriculture_gameplan.md`](docs/kaggriculture_gameplan.md).

## Quick start

Prerequisites are Python 3.11+, Git, CMake 3.24+, and a C++20 compiler.

```bash
# Fetch the ignored upstream checkout at the pinned revision.
python3 reference/bootstrap.py

# Configure, build, and test the native development build.
cmake --preset dev
cmake --build --preset dev
ctest --preset dev

# Build and run the optimized baseline benchmark.
cmake --preset release
cmake --build --preset release
./build/release/fast_model/kag-sim bench --games 100000
```

Generated upstream sources, builds, virtual environments, traces, and experiment
results are ignored. The pin itself is recorded in
[`reference/upstream.lock.json`](reference/upstream.lock.json), and experiment
metadata follows [`experiments/experiment.schema.json`](experiments/experiment.schema.json).

The benchmark currently measures only the verified PASS/initialization/terminal
scaffold. Its throughput is a tooling baseline, not a simulator performance
claim; it will become meaningful as complete transitions are implemented.

## Policy portability

There is one strategy, with two implementations—not two independently trained
models. During research, the C++ policy and simulator consume versioned JSON
parameters so millions of games can be evaluated quickly. The selected policy
algorithm is then implemented in self-contained Python for `submission/main.py`
and checked for action parity over recorded observations. The submission never
loads the C++ library, and no automatic C++-to-Python conversion is assumed.

Kaggle calls `agent(observation)` and expects the action dictionary documented
in the supplied rules. At the pinned revision, each call has a 1-second budget
and a 60-second episode overage bank. Although Kaggle accepts multi-file
archives, this project targets a standard-library-only, self-contained
`submission/main.py` to minimize packaging and runtime surprises.

## Repository layout

```text
reference/     official Python oracle and deterministic trace policies
fast_model/    C++20 simulator, unit tests, and benchmarks
slow_model/    Python strategy and analysis experiments
tests/         cross-backend and submission tests
experiments/   checked-in experiment definitions (generated results ignored)
submission/    final self-contained main.py
docs/          rules, design decisions, and benchmark reports
```

## Research motivation

New Kaggle competition popped up that I might be able to exploit with some funky FPGA logic. Gives me a plausibly existing use for ```hardcaml_ml```.

# Thesis
Through the use of FPGAs and their higher level of reprogrammability, one may demonstrate some level of edge over GPUs in terms of implementation of less-paralellizable logic, and the simulation/refining capabilities for a vehicle that ultimately will not run on FPGAs themselves. 

# Overview
"In this competition, you will design, build, and deploy an autonomous AI agent to manage a virtual farm, navigate a dynamic economy, and compete head-to-head against other agents on a live leaderboard."

This simulation competition is a turn-based farming game where two players compete on separate farms to see who can earn the most profit by the end of a 30-day season (720 turns).

Your agent acts as the main farmer and can strategically hire farm hands to scale up operations. To succeed, your agent must:

    Plant, water, fertilize, and harvest a variety of crops.
    Buy, feed, and care for animals to produce eggs, milk, and wool.
    Collect and utilize fertilizer to boost crop yields.
    Buy neighboring quadrants of land to expand your farm's footprint.
    Trade smart on a dynamic market where prices react to your sales and town demand.

Kaggriculture represents a highly complex environment that models the exact same dynamics found in real-world supply chains, dynamic market pricing, and industrial resource allocation under uncertainty. Underlying mechanics like scheduling resources, optimizing labor, adjusting to supply/demand price changes, and making long-horizon capital investments, serve as a high-fidelity sandbox for training AI to solve complex enterprise operations.

# Why FPGAs
Assumes the reader has a basic understanding of these devices and their use as a faster edge-compute device compared to a GPU.

We cannot use FPGAs in our actual submission. Instead, we are meant to submit a ```main.py``` file that is our *bot*. With this in mind the question arises of where an FPGA can be used.

Noticeably the "self-play" or "simulation" aspect of the system may be implementable in such a state that we can put them on an FPGA.

The traditional Kaggle flow is something like ```dataset → GPU training → neural network inference.```

Because there are *markets* and financial items in play, this is a tad more similar to a market research flow ```strategy → simulate millions of worlds → measure P&L/win-rate → adjust strategy → repeat```.

Suppose we have a strategy that needs

```
w_crop_profitability
w_future_demand
w_market_scarcity
w_opponent_exposure
w_distance_to_tile
w_worker_cost
w_land_expansion
w_inventory_risk
sell_threshold[commodity]
...
```

We cannot solve for the best values analytically; instead we can run:

```
candidate policy #1
    × 10,000 random seeds
    × 20 opponent policies

candidate policy #2
    × 10,000 random seeds
    × 20 opponent policies
```

Here we can do *many* paralelle runs on hardware. This begs the question of why not a GPU? The answer lies in the reprogrammability. The custom logic that might go into some of the branching logic that would take place is somewhat easier to implement in an FPGA; we can hand off entire simulation rollouts to the FPGA, and simply return back resulting statistics about them.
