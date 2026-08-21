# Research Policies

Each subdirectory contains one policy family: a fixed algorithm, its explicit
per-game state, and a versioned parameter schema. Parameter candidates should
be data artifacts rather than copies of the Python implementation.

These policies are research code. A promoted champion will later be distilled
into `submission/main.py` and checked for action parity.

The [`common`](common/) package contains stable game-contract helpers shared by
policy families: action constructors, observation accessors, fixed game data,
and candidate-envelope validation. Strategy decisions, state transitions, and
tunable parameter validation should remain inside each policy family.

The first example is [`monocrop_reorder`](monocrop_reorder/README.md).
