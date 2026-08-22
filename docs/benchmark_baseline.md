# Policy Workload Benchmark

Date: 2026-08-21. This is the Phase 5 same-workload result. The historical PASS
tape measurements are retained separately at the end of this document and are
not Python/native comparisons.

## Result

The official Python oracle plus the OCaml policy subprocess and native
`kag_sim evaluate` ran the same `monocrop-reorder-v1` family and baseline
candidate against PASS, on seeds 0–9, in both player seats. Every repetition
matched on 20 games, 14,380 turns, a 20/0/0 record, candidate-money total
73,834, and opponent-money total 60,000.

One warmup and five retained repetitions were run in alternating forward/reverse
backend order. The table reports medians; family/candidate loading and policy
subprocess startup were outside both timers.

| Backend | Wall time | Games/s | Turns/s | ns/turn |
| --- | ---: | ---: | ---: | ---: |
| Official Python oracle + OCaml policy subprocess | 10.468488 s | 1.910 | 1,373.646 | 727,989.430 |
| Native OCaml, 1 worker | 0.077217 s | 259.011 | 186,228.750 | 5,369.740 |

For this exact workload on this host, native scalar evaluation is **135.573x**
faster by median wall time. This is an end-to-end backend result, not a generic
Python-versus-OCaml language ratio: the Python path performs the official
environment's observation construction/copying and one JSON line-protocol
round trip per policy turn, while native evaluation uses zero-copy model
observations and typed actions. It is also not the submission-policy latency
measurement, which has a different scope.

## Reproducible workload and harness

The manifest is
[`experiments/benchmarks/phase5_policy/workload.json`](../experiments/benchmarks/phase5_policy/workload.json).
It points to the one shared family and candidate plus the checked-in opponent
and seed files. `tools.benchmark_policy` resolves those inputs once, hashes all
four, sends the same expanded job set to both backends, and refuses to emit a
speedup if aggregate results differ.

The Python side uses `reference.oracle.evaluate_game`, which executes the same
pinned upstream interpreter as `run_game` but retains only terminal aggregates.
It intentionally omits trace serialization and post-turn diagnostic copies,
because native `evaluate` does not perform those operations. The policy itself
still runs through `kag_policy.exe` over the documented subprocess protocol.

```bash
dune build --profile release
python3 -m tools.benchmark_policy run \
  --warmups 1 --repetitions 5 --threads 1 2 4 8 --scaling-copies 50
```

The final raw artifact, including every repetition, input and executable hashes,
host/build metadata, correctness signatures, and summaries, is retained at:

```text
experiments/results/benchmarks/
  phase5-monocrop-reorder-v1-vs-pass-10-seeds-20260822T040700.json
```

`experiments/results/` is deliberately gitignored. The artifact validates
against `experiments/experiment.schema.json`; reruns create a new timestamped
artifact rather than overwriting it.

## Fixed worker pool and scaling

`kag_sim evaluate --threads N` uses a fixed pool of `N` total OCaml domains,
including the calling domain. Games are independent jobs obtained through one
atomic index. Model state, policy register banks, and result accumulators are
worker-local; only the job index is mutated across workers, and results are
reduced after all domains join.

The 20-game comparison batch is too short for a scaling curve, so each retained
native scaling repetition executes 50 copies of that exact batch: 1,000 games
and 719,000 turns. These longer 1/2/4/8-worker runs use the same family,
candidate, opponent, seeds, and positions. Their aggregate correctness
signature is exactly 50 times the scalar signature.

| Workers | Median wall | Games/s | Turns/s | ns/turn | Speedup | Efficiency |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 3.820052 s | 261.777 | 188,217.338 | 5,313.007 | 1.000x | 100.0% |
| 2 | 2.425813 s | 412.233 | 296,395.481 | 3,373.871 | 1.575x | 78.7% |
| 4 | 1.916116 s | 521.889 | 375,238.243 | 2,664.974 | 1.994x | 49.8% |
| 8 | 1.865237 s | 536.125 | 385,473.804 | 2,594.210 | 2.048x | 25.6% |

Median process CPU utilization was approximately 99%, 200%, 397%, and 604%
respectively. Scaling plateaus around 2x: eight logical workers improve the
median only 2.7% over four on this 4-core/8-thread i7-1165G7, while individual
4- and 8-worker times overlap broadly. The raw scalar and scaling samples vary
by roughly 25% as the laptop changes frequency/thermal state, so the small
4-to-8 median difference is not a defensible SMT gain. The result does not yet
identify whether the plateau is GC, cache, memory bandwidth, scheduling, or
thermal behavior.

Hardware counters were requested with `perf stat`, but this host has
`perf_event_paranoid=4`, so the kernel refused all supported events without
elevated privileges. No privilege change was made. State-layout, batching,
SIMD, and specialized-dispatch work remain deferred until a counter-capable
profile is available.

## Build and host

```text
Python:   CPython 3.12
OCaml:    5.2.0+ox (OxCaml)
Build:    dune --profile release
Host:     Intel Core i7-1165G7, 4 cores / 8 threads
OS:       Linux 7.0.0-28-generic x86_64
```

## Historical PASS-tape measurements — not comparable

The `kag_sim bench` command drives PASS actions rather than a policy workload.
It is retained as a microbenchmark/tooling record only and must not be compared
with the policy table above.

The first OCaml scaffold (2026-08-21) only validated PASS, incremented the
clock, and applied the terminal convention:

```text
backend=ocaml-scalar-pass-scaffold
games=100000
transitions=71900000
seconds=0.411
games_per_second=243544.789
transitions_per_second=175108703.221
nanoseconds_per_transition=5.711
checksum=300000000.000
```

After all Phase 3 rules landed, the same PASS-tape command ran the complete
transition rule set and measured about 1.3 us/transition (about 1,100 full
games/s). This still omitted the policy workload and was never a head-to-head
result.

The removed C++ scaffold from 2026-08-20 was narrower still:

```text
backend=cpp-scalar-pass-scaffold
compiler=GCC 13.3.0 (-O3 -DNDEBUG)
games=100000
transitions=71900000
seconds=0.157
nanoseconds_per_transition=2.183
```
