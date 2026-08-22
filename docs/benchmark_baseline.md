# Native Benchmark Baseline

Date: 2026-08-21 (supersedes the 2026-08-20 C++ baseline, kept below for the
record; the C++ backend was removed when the game plan's native-language
decision changed to OCaml — see
[`ocaml_migration_decisions.md`](ocaml_migration_decisions.md)).

This is a tooling baseline for the first native slice, not a performance result
for the complete Kaggriculture simulator. The measured transition currently
validates PASS, increments day/hour, and applies the terminal convention. It
does not yet process the town, market, crops, animals, inventory, or
randomness. The number will fall sharply as real rules are added. Python/native
speedup must not be reported until both backends execute the same full
transition workload and match differentially.

## Build and host

```text
Backend:  ocaml-scalar-pass-scaffold
Compiler: OCaml 5.2.0+ox (OxCaml), flambda per the switch default
Build:    dune --profile release
Host:     Intel Core i7-1165G7, 4 cores / 8 threads
OS:       Linux 7.0.0-28-generic x86_64
Threads:  1
```

## Command

```bash
dune exec --profile release fast_model/bin/kag_sim.exe -- bench --games 100000
```

## Result

```text
games=100000
transitions=71900000
seconds=0.411
games_per_second=243544.789
transitions_per_second=175108703.221
nanoseconds_per_transition=5.711
```

The checksum was `300000000.000`. It prevents the outer game loop from being
discarded. The ~2.6x gap to the retired C++ number is a scaffold artifact, not
a language result: at this size the loop measures little beyond call and
increment overhead, and no optimization work has been done. Judge the language
choice at Phase 4, on the full transition workload.

Update (2026-08-21, all Phase 3 rule groups landed): the bench still drives
PASS tapes, but every transition now runs the complete rule set — market
refresh, town demand, decay, end-of-day RNG — so the same command measures
~1.3 µs/transition (~1,100 full games/second single-threaded, before any
optimization work). Exactly as predicted above, the number moved with scope.
A meaningful Python/native comparison and any layout optimization wait for
the Phase 4 gate, per the game plan.

## Historical: C++ scaffold (2026-08-20, backend removed)

```text
Backend:  cpp-scalar-pass-scaffold
Compiler: GCC 13.3.0 (CMake Release, -O3 -DNDEBUG)
games=100000  transitions=71900000  seconds=0.157
nanoseconds_per_transition=2.183
```
