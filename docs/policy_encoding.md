# Policy Encoding Across Python, OCaml, and FPGA Backends

The current design does not mean that an entire policy is encoded as JSON.
JSON is only the portable description of which policy algorithm to use and its
tunable parameters.

There are four separate concerns:

1. **Policy algorithm** -- Python, OCaml, or FPGA logic.
2. **Policy parameters** -- thresholds, weights, reserves, and preferences.
3. **Policy state** -- plans, counters, worker assignments, and beliefs retained
   during a game.
4. **Policy interface** -- observation in, action out.

Only the policy parameters are naturally represented as JSON.

## The Intended Boundary

There is no runtime Python-to-OCaml policy call (FFI) planned. Instead, one conceptual
strategy has two implementations:

```text
policy family + version + parameters
                  |
        +---------+---------+
        |                   |
        v                   v
 Python implementation   OCaml implementation
        |                   |
        v                   v
   Kaggle agent        fast simulation
```

For example:

```json
{
  "id": "market-heuristic-v3",
  "parameters": {
    "cash_reserve": 450,
    "max_hires": 4,
    "wheat_reserve": 12,
    "sell_thresholds": {
      "WHEAT": 31,
      "MILK": 190
    },
    "liquidation_day": 27
  }
}
```

The `id` selects compiled behavior. The parameters configure that behavior.
The JSON is not a program unless the project deliberately designs a policy
language around it.

The current experiment schema is intentionally loose: it contains a policy
`id` plus a parameter object. The game plan explicitly says not to freeze the
parameter schema before baseline strategy work reveals which decisions matter.

## JSON as the Control-Plane Format

JSON is useful for:

- human editing;
- checked-in experiment artifacts;
- reproducibility;
- diffing configurations;
- search-tool output;
- Python/OCaml interoperability;
- schema validation; and
- backward-compatible defaults.

It is not a good FPGA execution format.

The recommended architecture is:

```text
Human/search output
      |
      v
Versioned policy JSON
      |
      v
Host-side policy compiler/validator
      |
      +------------------+
      |                  |
      v                  v
Typed CPU config    Packed FPGA config
                        |
                        v
                PCIe/HBM/AXI transfer
```

JSON exists outside the measured loop. The native backend should parse it once
before running a batch. The FPGA should never parse JSON.

## What Crosses the FPGA Boundary

For the proposed whole-game accelerator, observations and actions should not
cross PCIe every turn. The FPGA owns the simulator and policy for an entire
rollout:

```text
Host sends:
  seeds
  player assignments
  policy family/version
  packed policy parameters
  opponent selection

FPGA runs:
  observe -> policy -> actions -> transition
  repeatedly for the complete game

FPGA returns:
  wins/losses/ties
  money statistics
  errors
  cycle counts
```

This follows the whole-rollout hardware contract in
[`hardware_feasibility.md`](hardware_feasibility.md). A per-turn
Python/OCaml/FPGA JSON exchange would largely destroy the FPGA's potential
advantage.

The policy components map to hardware as follows:

| Policy component | FPGA representation |
| --- | --- |
| Algorithm | Logic compiled into the bitstream |
| Parameters | Fixed-width packed parameter block |
| Persistent state | BRAM/UltraRAM state per rollout context |
| Observation | Internal wires/registers derived from game state |
| Selected actions | Internal enum/operand records sent directly to transition logic |

## A Suitable Packed Policy Format

The host/device representation should have an explicit application binary
interface. For example:

```text
PolicyHeader
  magic:              u32
  abi_version:        u16
  policy_family:      u16
  payload_bytes:      u32
  parameter_version:  u32
  semantic_hash:      u64

HeuristicPolicyV1
  cash_reserve:               u32
  wheat_reserve:              u16
  max_daily_hires:            u8
  liquidation_day:            u8
  crop_weights[5]:            i16
  animal_weights[3]:          i16
  sell_thresholds[9]:         u16
  market_scarcity_weight:     i16
  opponent_exposure_weight:   i16
  distance_weight:            i16
  ...
```

The exact fields should wait until baseline strategy work establishes what is
useful.

Important properties of this representation are:

- exact-width integers;
- explicit enum values;
- explicit byte offsets;
- explicit signedness;
- defined endianness;
- a versioned layout;
- bounds validation;
- no compiler-dependent padding;
- no raw byte-copy of an ordinary in-memory structure; and
- invalid parameter blocks rejected rather than silently clamped.

The same packed representation could also drive the native policy, which would
make CPU-versus-FPGA policy parity easier to test.

## Fixed-Point Policy Arithmetic

FPGA parameters should probably be integer or fixed-point rather than
arbitrary JSON floating-point values:

```text
JSON:  "market_scarcity_weight": 1.375

Packed:
  signed Q8.8 value = 352
```

It may be better for the canonical policy artifact to make quantization
explicit:

```json
{
  "market_scarcity_weight_q8_8": 352
}
```

This prevents Python, OCaml, and hardware from rounding `1.375` differently.
Policy arithmetic does not have to use the simulator's numeric representation,
but all implementations of a policy must select identical actions.

## FPGA Policy Implementation Options

There are several reasonable flexibility levels:

| Design | Flexibility | FPGA efficiency | Cost |
| --- | ---: | ---: | ---: |
| Hardwired policy | Low | Highest | Resynthesize for algorithm changes |
| Parameterized fixed policy | Medium | High | Limited to a known policy family |
| Policy bytecode or DSL | High | Lower | Interpreter, memories, and additional verification |

The recommended first FPGA implementation is the middle option:

```text
Fixed score-and-select policy architecture
+ loadable weights, thresholds, reserves, and preferences
```

This permits large policy searches without resynthesizing for every candidate
while retaining a specialized datapath.

If later research shows that the structure of the strategy changes frequently,
rather than merely its weights, a small policy intermediate representation may
be justified. For example:

```text
feature extraction
-> fixed-size candidate generation
-> weighted scoring
-> eligibility masks
-> deterministic argmax
```

A JSON document could describe that graph, but it should be compiled into
tables or microcode before reaching hardware. Hardware should not interpret a
generic JSON abstract syntax tree.

## The Semantic Contract

The strongest cross-backend commitment should not be that every backend uses
JSON. It should be:

```text
Given:
  the same legal observation,
  the same policy family and version,
  the same canonical parameters,
  the same persistent policy state,

Python, OCaml, and FPGA select the same action.
```

This contract can survive changes to serialization formats.

Test it with golden vectors containing:

```json
{
  "policy": {"id": "heuristic-v3", "parameters": {}},
  "observation": {},
  "previous_policy_state": {},
  "expected_action": {},
  "expected_next_policy_state": {}
}
```

JSON is suitable for storing these test fixtures. Hardware simulation can load
an encoded version and compare the resulting action and next policy state.

## Recommended Repository Direction

Use three layers:

```text
1. Policy semantics
   Policy family, decisions, state transitions, and numeric rules

2. Canonical artifact
   Versioned JSON used by experiments and search tooling

3. Backend encoding
   Python typed config
   OCaml typed config
   FPGA packed binary block
```

This provides a clean separation:

```text
JSON is the interchange format.
Packed bytes are the hardware transport format.
Observation -> action is the actual policy interface.
```

The project should therefore remain only lightly tied to JSON. It is an
effective research artifact format, while the FPGA boundary should be a
compact, versioned binary ABI produced by a host-side validator/compiler. The
policy algorithm should initially be a fixed parameterized heuristic
implemented independently, but action-equivalently, in Python, OCaml, and
Hardcaml.

See also:

- [`kaggriculture_gameplan.md`](kaggriculture_gameplan.md), especially the
  strategy platform and final-agent phases;
- [`hardware_feasibility.md`](hardware_feasibility.md) for the whole-game
  accelerator contract; and
- [`../experiments/experiment.schema.json`](../experiments/experiment.schema.json)
  for the current experiment-level policy descriptor.
