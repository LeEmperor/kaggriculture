# Alveo U50 FPGA Simulation Feasibility Plan

## Status and Purpose

This document specifies the FPGA feasibility experiment for Kaggriculture. It
refines Phase 9 of [`kaggriculture_gameplan.md`](kaggriculture_gameplan.md),
which remains authoritative if the documents conflict.

Current status: **planned; no FPGA implementation or performance result exists
yet**.

The FPGA is an offline strategy-evaluation coprocessor. It is not part of the
submitted `main.py`, and the final Kaggle agent must not depend on it. The target
is a production AMD Alveo U50 attached to the benchmark host through PCIe.

The experiment is worthwhile even if an optimized multicore CPU ultimately
wins. Its primary goal is a rigorous portfolio-quality Hardcaml design covering
stateful pipelines, deterministic replay, compact market state, Monte
Carlo-style scenario evaluation, parameter search, host/accelerator
partitioning, and honest performance analysis.

Kaggriculture is not an exchange simulator. Its financial relevance is in the
research workflow and accelerator architecture, not in wire-to-wire trading or
limit-order-book semantics.

## Feasibility Position

The credible FPGA advantage is spatial specialization: customized state
machines and arithmetic, explicitly banked on-chip memories, deterministic
scheduling, and concurrent independent games without instruction-fetch or
cache overhead. Reprogrammability and arbitrary branching are not advantages
over a GPU; irregular branch-heavy logic is frequently more expensive to
express and pipeline in RTL.

Raw speedup over the entire host CPU is plausible but not promised. The CPU has
high clock rates, mature branch prediction, inexpensive control flow, and a
small cache-resident game state. The FPGA can compete only if it amortizes PCIe
over large batches, keeps complete active games on-chip, and sustains enough
interleaved or replicated transition engines.

The U50 is an appropriate target. AMD documents 872K LUTs, 5,952 DSP slices,
1,344 36-Kb block RAMs, 640 288-Kb UltraRAMs, and 8 GB of HBM2. The card exposes
32 HBM channels and a Gen3 x16-capable PCIe interface, within a 75 W card power
envelope. These are physical-card totals; the selected platform shell consumes
some fabric resources. See the official
[U50 data sheet](https://docs.amd.com/r/en-US/ds965-u50/Product-Details) and
[HBM description](https://docs.amd.com/r/en-US/ug1371-u50-reconfig-accel/HBM-Memory).

Active game contexts should reside in BRAM and UltraRAM. HBM is useful for seed
lists, job descriptors, policy parameter tables, and result buffers, but its
capacity and peak bandwidth do not justify accessing it for every tile or turn.

## Prerequisites and Feasibility Gate

Complete the trusted software and strategy platform before optimizing RTL:

1. Pin and inspect the official Python environment.
2. Build the deterministic Python trace oracle.
3. Differentially validate the C++20 simulator for at least 1,000 complete
   games.
4. Implement baseline opponents and the versioned parameterized heuristic.
5. Benchmark one optimized CPU core and the entire U50 host CPU.
6. Profile transition logic, policy evaluation, market processing, RNG,
   end-of-day tile scans, state copying, scheduling, and aggregation.

Before implementing the complete accelerator, record:

- state bytes per game context;
- CPU turns and games per second;
- measured time in each profiled subsystem;
- estimated hardware cycles per turn and per game;
- post-synthesis resource use for one candidate transition engine;
- feasible context and engine counts;
- expected host/device bytes per batch;
- expected end-to-end games per second and games per joule.

Because portfolio depth is the primary objective, a predicted multicore CPU
win does not automatically cancel the experiment. It does require narrowing
the hardware milestone to the smallest complete, honestly benchmarkable
architecture that answers why the FPGA loses or wins.

## Supported Hardware Contract

The first accelerator supports the default competition configuration and a
fixed family of hardware-compatible policies. Non-default board sizes,
arbitrary market functions, malformed agent inputs, arbitrary Python policies,
and unbounded user-supplied policy programs remain software-only until they are
deliberately added.

The host submits a batch containing:

```text
BatchCommand
  seed_buffer
  game_count
  player_position_assignments
  policy_a_id
  policy_b_id
  policy_a_parameter_block
  policy_b_parameter_block
  result_buffer
```

The device autonomously runs complete 720-turn games and returns:

```text
BatchResult
  games_completed
  wins
  losses
  ties
  money_difference_sum
  money_difference_squared_sum
  illegal_action_count
  internal_error_count
  device_cycle_count
```

There is no host interaction between turns. A hardware policy receives only
the observation legally available to its player. Private diagnostic state may
be exposed in simulation-only builds for differential testing, but must not
feed policy decisions.

The host API should support explicit seed arrays rather than only contiguous
seed ranges so the same immutable training, validation, and holdout sets can be
used across CPU and FPGA backends. Player swaps are explicit jobs and contribute
separate results before aggregation.

## Board and Host Integration

Use an XRT-controlled Vitis RTL kernel around Hardcaml-generated Verilog. The
kernel exposes AXI4-Lite control and AXI4 global-memory interfaces and is
packaged as an `.xo`, then linked into an `.xclbin` for the installed U50 XDMA
platform.

The standard Alveo shell owns PCIe, DMA, card management, clocks, reset, and
runtime loading; Kaggriculture owns only the user-region kernel and host
application. AMD documents this shell/user split and warns that PCIe and
external-memory transfers must be amortized. See
[Using Alveo Accelerator Cards](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Using-Alveo-Accelerator-Cards)
and AMD's
[bottom-up RTL kernel workflow](https://xilinx.github.io/Vitis-Tutorials/master/docs-jp/docs/Hardware_Acceleration/Design_Tutorials/05-bottom_up_rtl_kernel/README.html).

Before pinning build files, inventory the installed card SKU, flashed shell,
XRT version, development platform, host PCIe link width, and a mutually
supported Vitis/Vivado release. The initial planning target is the production
U50 Gen3 x16 XDMA platform; do not silently substitute a U50LV or a different
shell.

QSFP/Ethernet integration is out of scope. It would add board-integration work
without improving this batch-evaluation workload.

## Simulator Microarchitecture

### Transition engine

Build one authoritative Hardcaml transition engine before replication. Its
state ordering must match the trusted simulator:

1. validate submitted actions;
2. execute worker actions and simultaneous conflicts;
3. process the two ordered market queues with the official quoting semantics;
4. apply town demand;
5. update observations and state;
6. perform end-of-day lifecycle and RNG work when required;
7. detect the terminal state and aggregate the result.

After synthesis establishes the cost and critical path, replicate the engine
until limited by timing, routing, power, or on-chip memory. Treat 200–250 MHz as
an initial planning range, not a performance promise.

One engine should interleave multiple independent game contexts when a
transition contains multicycle operations or RAM latency. This keeps the
pipeline occupied without requiring a completely duplicated datapath for every
game. Fully replicated engines and interleaved contexts are separate scaling
axes and must be measured independently.

### State storage

Keep each active context in explicitly banked BRAM or UltraRAM. It includes:

- two 10x10 farm tile arrays;
- worker positions and inventories;
- sheds, seeds, money, land, and hiring state;
- market inventory and current prices;
- shop, day, hour, and terminal state;
- policy parameters and persistent policy state;
- environment RNG state.

Choose field widths from proved software bounds. Do not narrow values merely
because observed traces happened to fit. Place bulk job queues, inactive
parameter blocks, seed arrays, and result buffers in HBM; stage them into local
memory before running games.

### Market arithmetic

Do not implement approximate floating-point price curves in the exact backend.
Generate integer lookup tables from the trusted reference for every inventory
value reachable under the supported configuration and hardware policies.
Document and prove those reachability bounds. Lookup entries must already
contain the official rounding and price-floor result.

If a later policy or configuration can exceed the proved range, expand the
table or reject the job before launch. Do not clamp silently.

### Randomness

Bring up RNG in two explicit stages:

1. Feed oracle-generated random-event tapes to the hardware model. This
   isolates transition and policy errors from RNG errors.
2. Implement the official Python-compatible seed derivation, generator state,
   draw conversion, and draw ordering on-card. Autonomous exact-RNG execution
   is required for the final end-to-end benchmark.

Any faster statistical RNG must be exposed as a separately named mode and must
not run differential tests or be compared as though it reproduced the official
seed semantics.

## Hardware Policy

Implement a fixed parameterized heuristic rather than arbitrary Python or a
policy virtual machine. It has the same four responsibilities as the software
strategy platform:

1. daily obligations;
2. routing and labor;
3. capital allocation;
4. market behavior.

Use fixed-size candidate sets and deterministic score-and-select logic.
Loadable parameters include weights, reserves, crop and animal preferences,
hiring and land thresholds, product selling thresholds, and the endgame
liquidation horizon.

Include hardware-compatible opponent modes for pass-only, crop-greedy,
animal-focused, expansion-focused, market-aware, and archived parameterized
heuristics. The CPU and hardware implementations must have action parity:
identical legal observation, policy identity, state, and parameters produce
identical actions.

Neural policies, arbitrary policy bytecode, Monte Carlo tree search, and a
host-resident policy called every turn are out of scope for the first design.
The per-turn host round trip would defeat the coarse-grained offload model.

## Verification Plan

Verification proceeds through the following representations:

```text
official Python oracle
        -> trusted C++20 simulator
        -> Hardcaml Cyclesim
        -> generated RTL simulation
        -> Vitis hardware emulation
        -> physical U50
```

Required checks are:

- Python and C++ match for at least 1,000 complete per-turn differential games.
- Cyclesim and C++ match after every turn, including actions, full diagnostic
  state, RNG output, market rounding, status, and reward.
- Generated RTL under Verilator or XSim matches the same compact replay vectors.
- Hardware emulation and the physical U50 return identical batch results.
- Repeated physical runs are bit-identical for the same job buffers.
- CPU and U50 aggregate results match for identical seeds, policies,
  parameters, and player positions.

Focused vectors must cover:

- simultaneous planting and ordered market interactions;
- movement, shed access, and inventory overflow;
- crop and animal production, care, decay, and escape;
- worker hiring, spawning, and daily reset;
- end-of-day weed and shop RNG ordering;
- lookup-table endpoints and price floors;
- maximum supported workers, quantities, and state values;
- malformed batch descriptors, unsupported modes, and output-buffer bounds.

Simulation-only diagnostic ports or trace buffers may capture the first
divergent field and cycle. They must be removable from benchmark builds.

## Benchmark and Reporting Plan

Benchmark all of the following on the same host:

- official Python oracle;
- optimized C++ on one CPU core;
- optimized C++ using the complete host CPU;
- FPGA kernel-only throughput;
- FPGA end-to-end throughput including XRT, PCIe, and result collection;
- FPGA games per joule using card telemetry.

Use warm and cold measurements and report throughput against batch size so the
PCIe amortization point is visible. CPU and FPGA runs use the same seed sets,
policies, parameter blocks, and player-position swaps.

Every report includes:

- turns and complete games per second;
- achieved clock and cycles per turn/game;
- context count and replicated engine count;
- LUT, flip-flop, DSP, BRAM, and UltraRAM utilization;
- HBM and PCIe traffic per game;
- host CPU utilization during FPGA runs;
- card power and games per joule;
- build configuration and tool versions;
- synthesis, implementation, and bitstream build time;
- correctness result for the benchmarked artifact.

Publish the result if the CPU wins. Attribute the gap to measured causes such
as sequential transition cost, branch-heavy policy logic, exact RNG overhead,
routing/timing closure, insufficient engine replication, or host integration.
Do not compare a sanitized CPU build with an optimized FPGA build, exclude
transfer time from an end-to-end claim, or report projected engines as measured
throughput.

## Repository Ownership

- Kaggriculture-specific transition engines, policies, replay logic, scoring,
  host protocol, and U50 kernel top belong in `hardcaml_kaggriculture` within
  this project until repository structure is revisited deliberately.
- Thin vendor wrappers, kernel metadata, and XRT host integration stay beside
  the board target.
- A circuit moves to `hardcaml_ml` only after it has a competition-independent
  interface and a demonstrated second use.
- Ethernet and generic packet transport remain outside this project.
- The final Kaggle submission remains self-contained Python.

## Exit Criteria

The FPGA milestone is complete when:

- the supported hardware contract and exclusions are documented;
- a complete parameterized-policy game runs without per-turn host interaction;
- the physical U50 matches CPU results on the declared validation suite;
- resource, clock, throughput, power, and end-to-end PCIe measurements are
  reproducible;
- the report compares the U50 with both one CPU core and the full host CPU;
- the conclusion explicitly recommends continuing, narrowing, or stopping the
  accelerator branch based on measured evidence.

