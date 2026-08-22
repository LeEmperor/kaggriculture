# Hardware Acceleration for Market Simulation and Financial/Agent-Based Workloads

## Executive summary

The hardware problem in financial simulation is not one problem but a collection of very different computational regimes. **Monte Carlo pricing and risk** are dominated by independent stochastic paths, vector arithmetic, random-number generation, and reductions; **PDE pricing** adds structured grids and linear solvers; **agent-based markets** combine highly parallel agent decisions with irregular communication and synchronization; and **limit-order-book matching** has a fundamentally sequential state-dependence inside an individual continuous double-auction. That distinction determines almost everything about the appropriate accelerator. The JAX-LOB authors explicitly note that messages applied to one order book are intrinsically serial, and obtain GPU parallelism by running many books concurrently; the newer KineticSim work attacks the remaining synchronization and memory-traffic problem with persistent GPU kernels and shared-memory state. citeturn17search0turn21view0turn15academia17

**The current default architecture for a new research project should usually be CPU + GPU, not a single accelerator.** Modern GPUs are the strongest broadly programmable platform for large Monte Carlo ensembles, batched market environments, neural or differentiable agents, large PDE batches, and parallel calibration. NVIDIA's ecosystem remains particularly mature for HPC and JAX/CUDA research; AMD Instinct is increasingly attractive where very large HBM capacity/bandwidth or a more open ROCm stack matters. H100 provides HPC-oriented FP64 capability, H200 raises HBM to 141 GB at 4.8 TB/s, and AMD's current MI350 generation reaches 288 GB HBM3E and 8 TB/s. NVIDIA's DGX B200 provides 1.44 TB aggregate GPU memory and 64 TB/s aggregate HBM bandwidth across eight Blackwell GPUs, illustrating how far throughput-oriented systems have moved beyond the A100/H100 generation. citeturn23search0turn23search9turn23search1turn23search19

**FPGAs remain the compelling architecture for the opposite end of the spectrum: deterministic, wire-adjacent, ultra-low-latency processing.** AMD now sells the Alveo UL3524 and smaller UL3422 specifically for electronic trading; both advertise sub-3-ns FPGA transceiver latency. That number is a transceiver measurement, not a complete packet-to-decision-to-order latency, but it demonstrates the architectural gap between FPGA pipelines and host/GPU execution. Financial-computation evidence also exists beyond networking: an FPGA implementation of the STAC-A2 Heston/Longstaff–Schwartz risk workload reported an 8–185× improvement in an efficiency metric versus two 24-core Xeon Platinum CPUs. citeturn24search1turn24search5turn17search1

**GPU acceleration of market simulation is now moving beyond naive “vectorize everything.”** JAX-LOB showed the value of batching thousands of books and colocating simulation with reinforcement learning on the GPU. Its published experiment on a 2080 Ti reported an effective 2.6 μs/message when 1,000 books of capacity 100 were processed in parallel, versus 3.6–7 μs for the authors' single-core CPU implementation depending on message type; end-to-end RL training reached 550 versus 74 steps/s. However, the same paper emphasizes that one LOB's ordered messages cannot simply be parallelized. citeturn22view0turn21view0 KineticSim, published in June 2026, is an important next step: its custom CUDA design keeps clearing state resident in thread-block shared memory across simulation steps and reports more than 54.7 billion agent-events/s on its benchmark, 8.4× its naive custom-CUDA baseline and much larger gains over NumPy, JAX, and PyTorch implementations. Those are implementation-specific results rather than universal GPU speedup factors, but they suggest that **persistent, state-resident simulation kernels are a promising architecture for financial ABM**. citeturn15academia17

**AI ASICs such as Google TPUs, AWS Trainium/Inferentia, and Intel Gaudi occupy a narrower but potentially useful niche.** A direct financial paper demonstrated that an earlier TPU generation could run financial Monte Carlo accurately despite mixed precision and could outperform the GPUs tested by the authors. Modern TPU v6e provides tensor, vector and scalar units, 918 BF16 TFLOPS and 1.638 TB/s HBM bandwidth per chip, while AWS Trainium2 combines tensor, vector, scalar and general-purpose SIMD engines and exposes FP32/TF32/BF16/FP16/FP8 through the Neuron toolchain. These devices are nevertheless designed primarily around AI/tensor workloads, so they should be selected for financial simulation only after showing that the dominant computation maps cleanly onto their compiler/runtime model. citeturn17search2turn25search0turn15search5

**DPUs should be viewed as acceleration of the simulation system rather than the numerical simulator.** NVIDIA BlueField-3 provides programmable 400 Gb/s networking and infrastructure processing, while AWS Nitro offloads networking, storage, virtualization and security from the host. They are attractive for packet capture, timestamping, RDMA, distributed state transport, data ingestion and isolation, but neither replaces a GPU/FPGA/CPU for the core Monte Carlo or market-clearing computation. Nitro is particularly important to distinguish from a user-programmable accelerator: AWS uses dedicated Nitro hardware to make host resources available to the EC2 instance rather than exposing it as a general financial-compute device. citeturn24search2turn25search2

**Processing-in-memory, photonic and neuromorphic systems are research opportunities rather than recommended production targets today.** Samsung's HBM-PIM experiments showed the possible value of reducing memory movement; a 2025 Nature paper demonstrated a general-purpose photonic AI processor across neural-network workloads; and Intel's 1.15-billion-neuron Hala Point system demonstrates extremely large event-driven neuromorphic computation. In the primary sources reviewed for this report, however, I found no convincing finance-specific market-simulation benchmark for any of these three classes. Their most plausible near-term roles are therefore subkernels—memory-bound graph/state operations for PIM, matrix operations or learned agents for photonics, and sparse event-driven/optimization experiments for neuromorphic systems—rather than replacing a market simulator wholesale. citeturn26search2turn26search0turn26search1

My three hardware recommendations are therefore:

| Scenario | Recommended primary architecture | Recommended supporting architecture | Main reason |
|---|---|---|---|
| **Ultra-low-latency trading / hardware-in-loop exchange** | FPGA, especially a finance-specific Alveo-class device | High-clock CPU + SmartNIC/DPU | Deterministic pipeline latency and direct network integration; GPU batching is the wrong optimization target. citeturn24search1turn24search5 |
| **Large-scale Monte Carlo / derivatives / risk** | Modern HPC GPU cluster | CPU orchestration; FPGA only for stable high-volume fixed kernels | Stochastic paths are embarrassingly parallel and GPUs combine very high arithmetic throughput, HBM bandwidth and the best general HPC ecosystem. citeturn23search0turn23search1turn17search1turn18search0 |
| **Hybrid agent-based market simulator** | GPU-resident simulator, initially JAX and ultimately custom CUDA/HIP for hot clearing kernels | CPU for heterogeneous/control logic; DPU for distributed I/O | Agents parallelize well, but matching and interactions need persistent/local state rather than repeated host/kernel round trips. citeturn17search0turn17academia6turn15academia17 |

The central research conclusion is that the most promising frontier is **not “which chip is fastest?” but “how should financial state be decomposed so that each hardware tier sees the part of the computation it is structurally good at?”** A particularly strong project direction would be a heterogeneous simulator in which neural/parametric agents execute in GPU batches, matching is handled by persistent GPU blocks or an FPGA hardware-in-loop engine, the CPU retains irregular control and experiment orchestration, and a DPU handles distributed event transport. Existing work typically optimizes one piece of this pipeline rather than evaluating it end to end. citeturn15academia17turn18search2turn24search2

## Workload anatomy and computational bottlenecks

### Market microstructure and limit-order-book simulation

An electronic limit-order-book simulator maintains price/time priority while processing adds, cancellations, deletions and marketable orders. The natural kernels are comparisons, searches, min/max operations, scans, queue/state updates and occasionally sorting; implementations based on trees or linked structures additionally create pointer-heavy, cache-unfriendly access patterns. The main complication is not raw arithmetic but **ordered mutation of shared state**. ABIDES models an exchange through discrete messages and configurable communication latency, while JAX-LOB uses fixed-shaped array representations better suited to accelerators. citeturn19search0turn17search0

The strongest published statement about its parallel structure comes directly from JAX-LOB: a continuous double auction applied to one book has serial message processing and serial internal matching requirements. Their solution is therefore *ensemble parallelism*—vectorizing across books—not magically parallelizing a single price-time-priority sequence. citeturn21view0 This makes an important distinction between two objectives:

**Low latency for one book** favors CPU specialization, FPGA pipelines or eventually custom ASICs.

**Maximum aggregate simulation throughput across thousands or millions of independent books/scenarios** can favor GPUs dramatically. JAX-LOB's capacity-100 experiment processed a message over 1,000 GPU books in about 2.6 ms total, which the authors interpret as roughly 2.6 μs effective time/book; its corresponding single-core CPU numbers were 5.3 μs for a limit order, 3.6 μs for a cancellation and 7 μs for a crossing limit order. citeturn22view0

This distinction is the most important reason generic GPU benchmarks are misleading for electronic-market workloads. A 100-TFLOP/s device does not automatically lower a single book's decision latency because matching may expose very little arithmetic parallelism.

### Agent-based financial markets

Agent-based market models have two computational phases with very different behavior. Agent policy evaluation is often highly parallel: each agent reads local/global state, samples random variables, evaluates rules or neural networks and emits actions. Interaction and clearing introduce synchronization: messages must be routed, orders aggregated, inventories updated and one or more market mechanisms resolved. ABIDES can model tens of thousands of agents and detailed pairwise communication latency, but its original architecture is discrete-event/message oriented rather than accelerator-native. citeturn19search0 A more recent scalable financial ABM framework likewise parallelizes heterogeneous agent decisions while routing their orders into a continuous double auction. citeturn18search2

Typical kernels therefore include:

| ABM phase | Typical kernels | Accelerator issue |
|---|---|---|
| Agent policy | Vector arithmetic, random sampling, small matrix/NN inference, conditionals | Excellent GPU/AI-ASIC parallelism unless policies diverge severely. citeturn17academia6turn19search2 |
| Communication | Scatter/gather, message bucketing, sparse interaction, queues | Irregular memory access and dynamic message counts can dominate. FLAME GPU exists specifically to map agent/message abstractions onto CUDA. citeturn19search2 |
| Aggregation | Reductions, histograms, sorting, atomics | GPU-friendly when localized; contention can erase scaling. KineticSim uses shared-memory atomics and block-localized reductions. citeturn15academia17 |
| Market clearing | Ordered matching, state mutation, searches | Serial dependency within a book; parallelize across books or redesign the clearing representation. citeturn21view0turn15academia17 |
| Learning | Dense/sparse neural operations, gradients, optimizer kernels | GPUs/TPUs are particularly strong; keeping simulation and policy training on the same accelerator avoids host transfers. citeturn21view0 |

JAX/accelerator-friendly ABM also imposes a data-model constraint that traditional simulators do not: arrays generally work best when their shapes remain static. Abmax explicitly identifies immutable shapes as a difficulty for agent insertion/removal or dynamically selected updates and develops JIT-compatible approaches to work around it; it includes a financial-market example and supports vectorization of multiple ABMs. citeturn17academia6

That makes **dynamic agent populations, heterogeneous types and variable-sized messages** among the most important engineering pain points in GPU financial ABM.

### Monte Carlo, risk and stochastic simulation

Monte Carlo is the cleanest accelerator workload in quantitative finance. For \(N\) independent paths, the basic computational structure is approximately

\[
X_{t+\Delta t}^{(i)}
= f\!\left(X_t^{(i)}, \theta, Z_t^{(i)}, \Delta t\right),
\qquad i=1,\ldots,N,
\]

followed by a payoff or exposure calculation and reductions such as means, quantiles, regression coefficients or sensitivities. The dominant kernels are random-number generation, SDE stepping, exponentials/transcendentals, vector arithmetic and reductions; Longstaff–Schwartz additionally introduces regression. AMD's former Vitis Quantitative Finance library supported Monte Carlo alongside Heston, Black–Scholes, Hull–White and other models, illustrating how naturally these methods map to streaming hardware pipelines. citeturn16search3turn17search1

This class exhibits abundant path-level parallelism and little synchronization until reduction, which is why GPUs, FPGAs and even TPUs have all produced finance-specific acceleration results. A GPU Heston/Greeks study reports as much as a 200× speedup for its proposed Milstein GPU implementation relative to its exact-simulation implementation, though this result mixes **algorithmic and hardware changes** and should not be treated as a generic GPU-vs-CPU ratio. citeturn18search0 The STAC-A2 FPGA work is more directly architectural and shows strong performance-per-energy advantages for a fixed financial-risk pipeline. citeturn17search1

The bottlenecks become HBM bandwidth, RNG throughput, transcendental-function cost, reduction/quantile stages, precision requirements and—at multiple GPUs—communication. For path-dependent products, keeping the state local and minimizing full path storage can be more important than peak FLOPS.

### PDEs, option pricing and linear algebra

Finite-difference pricing transforms derivatives models into repeated structured grid updates and linear systems. Relevant kernels include stencils, tridiagonal/banded solves, sparse matrix-vector operations, reductions and boundary-condition handling. Calibration or pricing thousands of strikes/maturities provides a second dimension of parallelism even where a single PDE is relatively small. AMD's quantitative-finance accelerator library historically included finite-difference methods alongside Monte Carlo and analytical models. citeturn16search3

The architecture choice consequently depends heavily on batch size. CPUs remain efficient for small one-off grids where launch overhead and data movement matter; GPUs become much more attractive when many independent instruments, scenarios or large grids can be batched. FPGAs can turn a stable stencil or solver into an efficient streaming pipeline, but the engineering investment is harder to justify when models and discretizations change frequently. The same flexibility argument is one reason modern H100/MI350-class GPUs are generally better research hardware than an FPGA for exploratory PDE work. Their HBM systems are designed for high-throughput HPC, with H100 delivering HPC-oriented FP64 acceleration and MI350 providing up to 8 TB/s HBM3E bandwidth. citeturn23search0turn23search1

### Graph, sparse and systemic-risk workloads

Graph traversal is less central to a conventional single-asset matching engine but becomes important in counterparty networks, contagion/systemic-risk studies, interaction graphs and some agent communication models. These workloads contain sparse gathers/scatters, frontier traversal and often highly nonuniform degrees, making them less naturally suited to dense tensor engines.

This is precisely where future processing-in-memory or event-driven systems are intellectually interesting: the limiting cost can be **moving sparse state**, not computing arithmetic. Samsung's HBM-PIM motivation is explicitly to eliminate some processor-memory traffic by placing computation inside the memory system; its early AI experiments reported more than 2× performance and over 70% lower energy versus the baseline HBM system used by Samsung. Those are vendor AI results, not financial graph benchmarks, but the architectural analogy is relevant. citeturn26search2turn26search5

## Architecture landscape and detailed comparison

The following table is an architectural assessment rather than an apples-to-apples benchmark. “Latency” refers to suitability for a dependency-sensitive single event; “throughput” refers to independent/batched simulation work.

| Architecture | Best financial workloads | Kernel fit | Single-event latency | Ensemble throughput | Representative hardware / cloud | Programmability and ecosystem | Cost / operational profile |
|---|---|---|---|---|---|---|---|
| **CPU** | Discrete-event engines, heterogeneous ABM, small LOBs, control/orchestration, serial calibration, reference models | Branching, trees/queues, irregular memory, scalar/vector code | **Excellent to good** in tuned software; predictable without batching | Moderate; scales via cores/nodes | AMD EPYC 9005/9006; Intel Xeon 6/6+; AWS Graviton4. EPYC 9005 reaches 192 cores, while newer EPYC 9006 goes to 256 cores; Xeon 6980P supports 12 memory channels. citeturn16search0turn16search20turn16search13 | **Best.** C/C++/Rust/Java/Julia/Python, mature profilers, SIMD, MPI, OpenMP | Lowest development risk. Often poor throughput/watt versus accelerators on massively parallel numerical work. |
| **GPU** | Monte Carlo, Greeks/risk, batched LOBs, RL environments, neural agents, large/batched PDEs | Vector ops, RNG, reductions, scans/sorts, dense/sparse LA, NN | **Poor-to-moderate for isolated events;** launch/queueing and parallelism requirements matter | **Excellent** | NVIDIA A100/H100/H200/B200; AMD MI300X/MI325X/MI350X. AWS P4d/P5/P5e and Google A2/A3/A4 expose A100/H100/H200/B200 generations. H200 has 141 GB/4.8 TB/s; MI350 has 288 GB/8 TB/s. citeturn23search7turn23search9turn23search1 | CUDA is deepest; JAX/XLA/PyTorch ease prototyping. ROCm is the main AMD alternative. | High accelerator/power cost, but excellent cloud availability and developer productivity relative to custom hardware. B200-class eight-GPU systems can draw roughly 14.3 kW at maximum system power. citeturn23search19 |
| **FPGA** | Wire-to-order pipelines, feed handling, pre-trade risk, matching, fixed MC/PDE pipelines | Deep pipelines, fixed-point arithmetic, RNG, state machines, packet parsing | **Excellent and deterministic** | Excellent on well-pipelined fixed kernels; less universally scalable than GPUs | AMD Alveo UL3524/UL3422/U280/U55C; AWS F1 legacy, **F2 current**. F2 exposes up to eight VU47P FPGAs with 16 GB HBM each. citeturn24search0turn24search1 | RTL/Vivado highest control; HLS lowers entry barrier but architecture knowledge still matters. | Low latency and potentially excellent perf/W, but long implementation/verification/compile cycle. Engineering cost often dominates hardware price. |
| **AI ASIC / TPU / NPU** | Tensorized Monte Carlo, learned agent policies, differentiable simulation, matrix-heavy calibration | GEMM/systolic arrays, vector/scalar ops, NN kernels | Usually **not designed for exchange-event critical paths** | **Very high** when computation maps to tensor/vector model | Google TPU v6e/Ironwood; AWS Trainium/Inferentia; Intel Gaudi 3. TPU v6e: 918 BF16 TFLOPS, 32 GB HBM, 1.638 TB/s/chip. Gaudi 3: 128 GB HBM2e, ~3.7 TB/s. citeturn25search0turn23search2 | JAX/TensorFlow/PyTorch on TPU; AWS Neuron/NKI on Trainium/Inferentia; SynapseAI for Gaudi | Attractive only if compiler mapping and precision are validated. Google currently lists v6e/Trillium at $2.70/chip-hour in some US regions and Ironwood at $12/chip-hour in us-central1 on demand. citeturn25search1 |
| **Custom ASIC** | Frozen exchange/matching/risk datapaths at extreme deployment scale | Arbitrary hardwired pipeline | **Potentially best** | Potentially excellent | Proprietary exchange/network silicon; public finance-specific simulator products are scarce | **Worst flexibility.** Requires silicon design/verification ecosystem | Highest non-recurring engineering risk and longest redesign cycle; rational only when the function is sufficiently stable and deployed at scale. |
| **DPU / SmartNIC** | Packet ingestion, timestamping, distributed simulation transport, RDMA, storage/security offload | Networking, DMA, crypto, packet processing | **Excellent for I/O path**, not general model execution | High networking throughput | NVIDIA BlueField-3/4; AWS Nitro. BlueField-3 is a 400-Gb/s programmable infrastructure processor; Nitro offloads EC2 networking/storage/virtualization functions. citeturn24search2turn25search2 | DOCA/Arm on BlueField; Nitro largely infrastructure-managed rather than user general-purpose compute | Useful when network/I/O CPU overhead is measurable; extra distributed-system complexity if added prematurely. |
| **Processing-in-memory** | Potential sparse graph traversal, large agent-state scans, memory-bound reductions | Local memory operations, reductions, low arithmetic intensity | Potentially good if data locality dominates | Potentially high for bandwidth-bound kernels | Samsung HBM-PIM; UPMEM-class PIM systems | Immature/nonstandard compared with CUDA/CPU | Research/early-deployment risk. Evidence is primarily AI/HPC rather than financial simulation. Samsung's published HBM-PIM results are prototype/vendor results. citeturn26search2 |
| **Photonic** | Future matrix-heavy learned agents/calibration; possibly linear-algebra subkernels | Matrix-vector/matrix-matrix operations | Physical compute can be extremely low latency; end-to-end system data movement remains critical | Potentially enormous for supported analog/tensor kernels | Lightmatter and research photonic processors | Highly specialized compiler/hardware stacks | Not currently a general financial-simulation platform. The 2025 Nature demonstration targets AI workloads, not irregular exchange state machines. citeturn26search0turn26search9 |
| **Neuromorphic** | Exploratory sparse event-driven agents, online adaptation, optimization | Spikes/events, asynchronous sparse computation | Architecturally attractive for sparse events | Workload dependent; conventional FLOP measures are poor comparison | Intel Loihi 2 / Hala Point research systems | Lava/neuromorphic programming requires substantial model reformulation | Research access and mapping cost dominate. Hala Point is a research system with 1.15B neurons and 1,152 Loihi 2 processors, not a drop-in accelerator. citeturn26search1 |

### Current GPU and AI-accelerator position

For conventional numerical finance, memory and double-precision behavior matter more than the enormous low-precision AI numbers shown in marketing material. H100 is notable because NVIDIA retained strong FP64/Tensor FP64 capability for HPC rather than building a pure inference accelerator. A100 remains relevant through installed clusters and cloud fleets, but H100/H200 and newer Blackwell systems offer much larger memory/interconnect capabilities. citeturn23search0turn23search7turn23search9

AMD is especially interesting for simulation states that approach GPU-memory limits. MI300X provides 192 GB HBM3 and about 5.3 TB/s theoretical bandwidth, while MI350 raises both to 288 GB and 8 TB/s. For enormous ABM ensembles or path-dependent Monte Carlo models, those capacities can reduce partitioning and communication. citeturn23search14turn23search1 The decision between NVIDIA and AMD should nevertheless be based on a real prototype: CUDA/JAX ecosystem maturity can outweigh a specification advantage if a project depends on specialized libraries or custom kernels.

Google TPU is the clearest proof that an AI ASIC can sometimes be useful for financial numerics: the 2019 *Tensor Processing Units for Financial Monte Carlo* paper explicitly evaluated option pricing, hedging and risk computation and found mixed-precision TPU computation sufficiently accurate in its experiments. citeturn17search2 But extrapolating that result directly to TPU v6e or Ironwood would be unsound: both the TPU architecture and competing GPUs have changed dramatically. A current benchmark is needed.

AWS Trainium is somewhat more general internally than “matrix accelerator” suggests. Trainium2's NeuronCore includes scalar, vector, tensor and GPSIMD engines, and AWS exposes a low-level Python-based Neuron Kernel Interface. citeturn15search3turn15search5 That creates an interesting research opportunity for stochastic/vector simulation, but virtually all public tuning effort is currently aimed at AI models, so financial kernels would be pioneering work rather than following a mature quant ecosystem.

Inferentia is even more clearly inference-oriented: Inferentia2 offers 32 GB HBM and up to 190 FP16 TFLOPS per chip. It makes sense for neural policies inside an agent system, but not as my first choice for a numerical Monte Carlo/PDE engine. citeturn15search7 Intel Gaudi 3 similarly provides substantial HBM bandwidth and a scalable AI architecture, but the ecosystem and hardware organization are optimized around neural-network computation rather than irregular market matching. citeturn23search2

### FPGA position and the changing software ecosystem

FPGA hardware remains unusually well aligned with finance because many production trading datapaths consist of exactly what FPGAs are good at: network decoding, deterministic state machines, small local memories, parallel checks and tightly pipelined arithmetic. AMD's UL3524 and UL3422 are unusually direct confirmation of this alignment because they are marketed specifically for electronic trading rather than generic HPC. Their specified sub-3-ns figure applies to the FPGA transceiver; application logic, protocol parsing and external network path add additional latency. citeturn24search1turn24search5

AWS provides a practical route for research without purchasing boards. F1 was AWS's original FPGA instance family; the current F2 generation uses AMD Virtex UltraScale+ HBM VU47P devices, adds 16 GB HBM per FPGA and is offered in configurations up to eight FPGAs. citeturn24search0 For algorithm exploration, that greatly lowers capital barriers, though not the HDL/HLS development barrier.

There is also an important negative ecosystem development: AMD/Xilinx's open Vitis Libraries repository states that, beginning with release 2025.2, the programmable-logic **quantitative_finance, HPC, sparse and graph libraries are no longer maintained**, though older versions remain accessible. citeturn16search3turn16search7 For a new project this means the old Vitis Quantitative Finance code is best treated as **reference IP/research material rather than a strategically safe maintained dependency**.

### A qualitative latency–throughput map

The following is a schematic synthesis, not a benchmark plot:

```text
       Higher aggregate simulation throughput
                        ↑
                        │              GPU
                        │                ●
                        │       TPU / AI ASIC ●
                        │
                        │     FPGA ●
                        │
                        │ CPU ●
                        │
                        │       PIM / photonic
                        │       (workload-specific,
                        │        emerging)
                        │
                        └────────────────────────────────→
                           worse single-event critical-path
                           latency / stronger batching need

For network-facing event latency:
ASIC  →  FPGA  →  tuned CPU / SmartNIC  →  GPU / TPU
 best                                         batch-oriented
```

The placement follows the empirical contrast between FPGA trading hardware's nanosecond-scale transceiver datapath, CPU-friendly sequential LOB logic, and GPU systems such as JAX-LOB that obtain their largest benefit by processing many books or environments at once. citeturn24search1turn21view0

A similar trade-off appears in engineering effort:

```text
Potential specialization gain
        ↑
        │                         Custom ASIC
        │                              ●
        │                    FPGA ●
        │
        │           custom CUDA/HIP ●
        │
        │       JAX / GPU ●
        │
        │   optimized CPU ●
        │
        │ Python/reference CPU ●
        └────────────────────────────────────────→
                         development + verification effort
```

For a research project, moving rightward is rational only after profiling demonstrates that the relevant stage dominates end-to-end time. KineticSim is a good example: it does not merely change hardware; it replaces higher-level GPU execution with a persistent custom kernel after identifying launch and global-memory traffic as critical costs. citeturn15academia17

## Evidence, benchmarks and open-source ecosystem

### The strongest finance-specific accelerator results

Cross-paper benchmark numbers should not be compared as if they were one benchmark suite: hardware generations, algorithms, precision, batch sizes and reference implementations differ substantially.

| Work | Architecture / workload | Result worth retaining | Interpretation |
|---|---|---|---|
| **JAX-LOB** | GPU limit-order-book simulation / RL | On 1,000 parallel capacity-100 books, ~2.6 μs effective/message on 2080 Ti; single-core M1 CPU implementation reported 3.6–7 μs depending operation. RL training: 550 vs 74 steps/s. citeturn22view0turn21view0 | Strong evidence for **ensemble/batched LOB acceleration**, not evidence that a GPU beats an FPGA or tuned CPU for one live book. |
| **KineticSim** | Custom CUDA agent-market execution engine | >54.7B agent-events/s peak; reported 8.4× vs naive CUDA, 27.8× PyTorch GPU, 42.8× JAX GPU and 3,406× NumPy CPU on the authors' workload. citeturn15academia17 | Very important 2026 evidence that persistent shared-memory state can beat framework-level GPU simulation by a large margin. Needs independent replication and more realistic exchange models. |
| **Low-power option Greeks / STAC-A2** | Alveo U280 FPGA, Heston + Longstaff–Schwartz | 8–185× improvement in the paper's efficiency metric versus two 24-core Xeon Platinum CPUs. citeturn17search1 | Strongest finance-specific FPGA evidence for risk simulation/performance-per-energy. |
| **Heston Greeks on GPU** | GPU Monte Carlo | Proposed Milstein GPU implementation up to 200× faster than the paper's exact-simulation implementation; Rho accuracy was weaker. citeturn18search0 | Demonstrates potential, but **not a pure hardware comparison** because algorithms differ. |
| **Financial Monte Carlo on TPU** | Google TPU | Authors found TPU estimators accurate despite mixed precision, fast relative to tested GPUs and convenient via TensorFlow/autodiff. citeturn17search2 | Important proof of concept for tensor ASICs; hardware generation is now old. |
| **Nested MLMC on FPGA** | FPGA Monte Carlo | Uses approximate random variables and optimized fixed-point widths to reduce the cost of most SDE paths. citeturn18search1 | Shows a unique FPGA advantage: the arithmetic representation itself can be specialized to each MLMC level. |
| **AMD UL35/UL34 trading hardware** | FPGA electronic trading | Vendor specifies <3 ns FPGA transceiver latency. citeturn24search1turn24search5 | Relevant to wire-to-logic latency, **not** a full simulator or complete tick-to-trade latency. |

The trajectory from JAX-LOB to KineticSim is particularly instructive. JAX-LOB shows that replacing tree/list structures with accelerator-compatible arrays and vectorizing over environments is enough to make GPU financial simulation practical. KineticSim argues that the next order of optimization comes from eliminating repeated kernel launches and global-memory state traffic by persistently retaining clearing state in shared memory. citeturn17search0turn15academia17 For a new market simulator, this suggests a sensible optimization ladder:

**correct reference implementation → JAX/vectorized GPU → profile → fuse/persist clearing kernels → only then evaluate FPGA or custom heterogeneous offload.**

### Open-source projects most relevant to a new build

**JAX-LOB / AlphaTrade** is currently the most directly relevant open GPU project for limit-order-book research. The GitHub repository contains the JAX order book, trading environment and GPU-native RL loop corresponding to the paper. citeturn19search3 It is the best starting point when your goal is thousands of environments, optimal execution, market making or accelerator-resident reinforcement learning.

**FLAME GPU 2** is the most mature general-purpose GPU ABM analogue identified in this review. It exposes CUDA C++ and Python interfaces and maps agent definitions, communication, birth/death and other ABM operations onto optimized CUDA execution. citeturn19search2 It is not finance-specific, but its design is highly relevant if your market agents interact through more general graphs/messages than JAX-LOB supports.

**Abmax** is a newer JAX-native ABM framework with a financial-market example. Its research contribution is particularly relevant to the awkward mismatch between dynamic ABMs and JAX's static-array/JIT preference, and it supports vectorizing multiple ABMs. citeturn17academia6

**ABIDES** remains one of the most important high-fidelity financial-market references. The public JPMorgan repository separates ABIDES-Core, Markets and Gym and supports exchange and latency modeling. citeturn15search0 However, the JPMorgan public repository was archived in June 2025 and is now read-only, so I would use ABIDES primarily as a behavioral/reference architecture rather than choosing it as the foundation for an aggressive new accelerator project. citeturn15search2

**Xilinx/AMD Vitis Libraries quantitative_finance** provides valuable FPGA reference implementations, but as noted above, AMD has discontinued maintenance of the programmable-logic quantitative-finance library beginning with 2025.2. citeturn16search3 The `markxio/delta-hedging` repository is a useful concrete derivative: it extends Vitis's Monte Carlo infrastructure with a reusable path pricer and validates against CPU QuantLib. citeturn16search23

**CoinTossX** is useful as an architectural CPU baseline rather than an accelerator library. It separates the order-generation/simulation process from a low-latency matching engine and uses binary SBE/UDP and Aeron transport, making it a good reference for hardware-in-loop testing and asynchronous simulator/exchange decomposition. citeturn18academia3

The 2025 photonic Nature work also publishes its datasets and analysis code through the Lightmatter `upaia-paper-2025` GitHub repository. That is useful for understanding the programming/accuracy characteristics of photonic matrix acceleration, although there is currently no finance-specific implementation. citeturn26search0

### Cloud availability and experimentation

For GPU experiments, the most useful fact is that multiple generations are cloud accessible, so there is little reason to buy hardware before profiling. The A100 remains a sensible low-cost baseline; H100/H200 are more attractive where FP64 performance or memory matters, while B200-class systems provide significantly larger aggregate memory/bandwidth. NVIDIA's A100 80 GB exceeds 2 TB/s memory bandwidth; H200 reaches 141 GB and 4.8 TB/s; eight-GPU DGX B200 reaches 1.44 TB aggregate HBM and 64 TB/s aggregate bandwidth. citeturn23search7turn23search9turn23search19

For FPGA development, AWS F2 is particularly valuable because it allows an FPGA hypothesis to be tested without committing to a trading card or server. F2 offers one, two or eight VU47P devices depending instance size, with 16 GB HBM per FPGA and up to 100-Gb/s instance networking. citeturn24search0 A physical UL3524/UL3422 remains more representative for an actual exchange-colocated low-latency design because it integrates trading-specific transceiver architecture. citeturn24search1turn24search5

For TPU experiments, Google provides a particularly transparent cost baseline. As of August 2026, its official pricing page lists Trillium/v6e at $2.70 per chip-hour on demand in selected US regions and Ironwood at $12/chip-hour on demand in us-central1, with lower flex-start and commitment pricing. citeturn25search1 This makes a small TPU Monte Carlo replication experiment inexpensive enough to be scientifically worthwhile before contemplating a TPU-centered architecture.

AWS Trainium2 is more ambitious: one Trn2 instance contains 16 Trainium2 devices and 1.5 TiB aggregate HBM with 46 TB/s aggregate bandwidth, according to AWS. citeturn15search5 That is a formidable hardware platform, but the key research question is still compiler utilization on non-neural financial kernels rather than aggregate specifications.

## Recommended architectures for the target scenarios

### Low-latency trading and exchange hardware-in-loop

For **production-style low-latency order processing**, my recommended architecture is:

```text
exchange / feed
      │
      ▼
FPGA transceiver + parser + book / pre-trade checks
      │
      ├──────── fast-path decision / order transmission
      │
      ▼
high-clock CPU
complex strategy, controls, logging, slow-path handling
      │
      ▼
GPU / analytics tier (optional, not on critical event path)
```

Use a finance-specific FPGA such as **AMD Alveo UL3422 or UL3524** when the research target is genuinely nanosecond-sensitive strategy execution, feed handling, pre-trade risk or hardware-in-loop exchange emulation. AMD's purpose-built cards expose the clearest commercially available path to experimenting with this class of system. citeturn24search1turn24search5 An AWS F2 instance is better for learning/algorithm development than for reproducing exchange-colocation latency because it necessarily sits behind cloud infrastructure. citeturn24search0

Keep a modern CPU beside the FPGA. Complex order types, risk configuration, strategy reconfiguration and experimental instrumentation are substantially easier in software. Current EPYC and Xeon platforms offer very large core counts and memory/I/O capacity while retaining the flexibility to run irregular event logic. citeturn16search20turn16search13

A **BlueField-class DPU/SmartNIC** is useful when the project's challenge includes capture, timestamped distribution, RDMA or multi-host simulation, but it should not be confused with the main matching accelerator. BlueField's 400-Gb/s infrastructure processing is exactly the sort of capability that can keep packet movement from consuming CPU cores. citeturn24search2 AWS Nitro provides analogous cloud-level I/O offload but is controlled as part of AWS infrastructure rather than as an FPGA-like user datapath. citeturn25search2

I would **not put an H100/B200/TPU on the critical path of a single price-time-priority book merely because it has more FLOPS**. The JAX-LOB evidence shows the GPU advantage comes from parallel books/environments; one book remains dependency ordered. citeturn21view0 GPUs still make sense beside this system for batched inference, calibration or simulation.

A custom ASIC is architecturally the endpoint if the critical function becomes stable, extremely high-volume and valuable enough to justify fixed silicon. For a research project, however, the FPGA is substantially more valuable because the market mechanism, protocol, risk checks and numerical representation can still change.

### Large-scale Monte Carlo, XVA, risk and option simulation

For this scenario, I would start with **one modern HPC GPU and scale out only after measuring**.

The strongest general choices are NVIDIA H100/H200/B200-class systems or AMD MI300X/MI350-class systems. H100 is particularly attractive where FP64 remains substantial, while H200 adds memory capacity/bandwidth and MI350 is attractive for memory-heavy path states with 288 GB HBM3E and 8 TB/s. citeturn23search0turn23search9turn23search1

A robust architecture is:

```text
CPU:
instrument construction, calibration orchestration,
scenario generation, I/O, validation
       │
       ▼
GPU:
RNG → SDE paths → payoff/exposure → Greeks
                  │
                  ├─ local reductions
                  ├─ regression / LA
                  └─ partial VaR / exposure statistics
       │
       ▼
CPU/GPU final reduction + risk reporting
```

The most important implementation rule is to **avoid round-tripping every simulation step through the CPU**. Paths and intermediate state should remain accelerator-resident until a meaningful reduction has occurred. That principle is supported more generally by accelerator-native simulation systems: JAX-LOB avoids CPU/GPU communication by putting environment rollout and learning updates on the same device, while KineticSim obtains large gains by retaining mutable simulation state locally rather than repeatedly writing it through global memory. citeturn21view0turn15academia17

I would choose NVIDIA first when development velocity, CUDA/JAX integration and mature libraries dominate; I would benchmark AMD seriously when simulation state is so large that MI300/MI350's HBM capacity materially reduces partitioning. The decision should be made on **paths/s per dollar and per watt at the required accuracy**, not nominal BF16/FP8 TFLOPS.

An FPGA becomes attractive when the Monte Carlo kernel has stabilized and is run continuously at enormous volume or under a strict power envelope. The STAC-A2 result demonstrates that FPGA specialization can produce very high efficiency, while the MLMC work shows that configurable bit widths and approximate RNG can yield optimizations unavailable to standard CPUs/GPUs. citeturn17search1turn18search1

TPU/Trainium experiments are worth doing if your stochastic model is naturally expressed as large tensor/vector programs and you can validate numerical precision. The direct TPU finance paper makes this scientifically credible. citeturn17search2 I would nevertheless treat TPU/Trainium as a **benchmark candidate, not the default**, because the finance ecosystem is much thinner and current accelerators are optimized around AI utilization patterns. TPU v6e's inexpensive cloud pricing makes a controlled experiment practical. citeturn25search0turn25search1

For risk simulation, benchmark at least:

\[
\text{throughput} =
\frac{\text{correctly completed paths/scenarios}}{\text{second}},
\]

\[
\text{cost efficiency} =
\frac{\text{correctly completed paths/scenarios}}
{\text{dollar}},
\]

and

\[
\text{energy efficiency} =
\frac{\text{correctly completed paths/scenarios}}
{\text{joule}},
\]

while separately recording confidence-interval error, Greeks error and tail-quantile error. Reduced precision can materially change risk estimates even when raw throughput looks excellent; the Heston GPU paper's weaker Rho result and the TPU paper's explicit mixed-precision validation illustrate why numerical error belongs in the benchmark definition. citeturn18search0turn17search2

### Hybrid agent-based market simulation

This is the scenario with the most interesting research opportunity.

I would build the first version around **GPU-resident state plus a CPU orchestrator**:

```text
                    GPU
┌─────────────────────────────────────────────┐
│ batched agent states                        │
│      │                                      │
│      ▼                                      │
│ policy / RNG / neural inference             │
│      │                                      │
│      ▼                                      │
│ action aggregation                          │
│      │                                      │
│      ▼                                      │
│ persistent market-clearing / LOB kernels    │
│      │                                      │
│      ▼                                      │
│ observations / rewards / inventory updates  │
└─────────────────────────────────────────────┘
          ▲                         │
          │ occasional control      │
          ▼                         ▼
        CPU                 RL / calibration
configuration,            preferably on GPU too
I/O, heterogeneous
slow-path logic
```

Start with **JAX** because it provides automatic batching, JIT compilation, automatic differentiation and direct compatibility with learned agents. JAX-LOB proves that this arrangement is practical for market simulation, and Abmax provides a more general JAX ABM framework with a financial example. citeturn17search0turn17academia6

The first serious optimization target should be **market clearing**, not agent arithmetic. If profiling reveals repeated kernel launches and global-memory synchronization dominating, adopt KineticSim's core idea: one book or localized market state per persistent cooperative GPU block, state retained in shared/local memory, agent actions accumulated through localized reductions/atomics, and only aggregate/output state written globally. citeturn15academia17

For complex heterogeneous communication, FLAME GPU 2 is the best nearby open-source design to study. It already provides GPU abstractions for agent types, communication and birth/death, albeit outside finance. citeturn19search2

A **CPU/GPU split** becomes preferable to pure GPU execution when agent behavior is radically heterogeneous, agents construct variable-sized objects, event rates are extremely sparse, or much of the model is unpredictable branch-heavy logic. Trying to encode all such behavior into fixed JAX arrays may increase padding and compilation complexity; Abmax's work on dynamic-agent operations demonstrates that this is a real constraint rather than merely a programming inconvenience. citeturn17academia6

For multi-node experiments, add a BlueField/SmartNIC only after GPU profiling shows network/event distribution is material. BlueField-3 provides 400-Gb/s programmable network processing and can logically own transport/RDMA and data-placement operations while CPUs/GPUs focus on model state. citeturn24search2

An FPGA can later become a powerful **hardware-in-loop market kernel**: GPU agents emit orders into an FPGA exchange model, and FPGA-generated executions return to the agents. That architecture would let the same research environment address both high-throughput agent learning and realistic deterministic exchange timing. The absence of a widely used open framework combining JAX/FLAME-style GPU agents with a trading FPGA is itself an important literature gap.

## Literature gaps and promising research directions

### A missing common benchmark suite

The literature lacks a hardware-neutral equivalent of MLPerf for financial simulation. JAX-LOB reports messages/s and RL steps/s, KineticSim reports agent-events/s, FPGA derivatives work reports an efficiency metric, and low-latency FPGA products use transceiver or tick-to-trade-oriented measurements. They are all legitimate, but they measure different objectives and cannot produce a clean CPU-vs-GPU-vs-FPGA ranking. citeturn22view0turn15academia17turn17search1turn24search1

A valuable research contribution would be an open benchmark with at least five tracks:

| Track | Required metrics |
|---|---|
| LOB replay / matching | p50, p99, p99.9 event latency; messages/s; jitter; memory/book; correctness |
| Ensemble market simulation | books/s; agent-events/s; state size; scaling with number of books |
| ABM | agents × steps/s; message edges/s; dynamic-population overhead |
| Monte Carlo | paths/s; paths/$; paths/J; numerical error; RNG quality |
| PDE | instruments/s; grid points/s; solver residual/error; batch scaling |

Each test should include **bitwise or tolerance-based correctness**, and results should distinguish warm execution from JIT/FPGA build/startup costs. JAX's own benchmarking documentation demonstrates why this matters: compilation and host-device transfer costs can be large compared with steady-state GPU execution. citeturn20search10

### Single-book parallelism remains unresolved

JAX-LOB establishes that strict continuous-auction processing is serial inside an individual book. citeturn21view0 KineticSim obtains enormous aggregate performance using ensembles and a redesigned clearing algorithm, but the broader question remains: **how much parallelism can be extracted from one realistic exchange engine without changing market semantics?** citeturn15academia17

Promising avenues include speculative processing with rollback, parallel preprocessing of independent risk checks, price-level decomposition, batching events known not to conflict, hardware transactional techniques, and separating immutable event parsing from mutable book state. FPGAs provide a particularly useful experimental vehicle because several stages can execute concurrently in a deterministic pipeline even if final state commit remains ordered.

### Persistent GPU simulation is underexplored

KineticSim's 2026 result makes persistent state-carrying kernels one of the highest-priority research directions. citeturn15academia17 Similar ideas are well established in other simulation domains, where eliminating CPU/GPU handoffs can produce very large gains, but financial market simulation has only recently started exploiting the architecture systematically. JAX-LOB already showed the benefit of colocating rollout and RL training on the GPU. citeturn21view0

A strong project could compare four implementations of exactly the same market semantics:

```text
CPU event-driven
        ↓
JAX vectorized
        ↓
custom CUDA/HIP, launch-per-step
        ↓
persistent CUDA/HIP, state-resident
```

Running this across sparse/dense traffic, number of agents, depth of book, dynamic order sizes and number of independent markets would produce substantially more informative evidence than another headline “GPU × times faster than CPU” number.

### FPGA software is simultaneously promising and weakening

Finance has unusually good evidence for FPGA acceleration, yet the open software ecosystem is not improving uniformly. AMD continues to release trading-specific Alveo hardware, while its Vitis programmable-logic quantitative-finance library is no longer maintained after the 2025.2 refocus. citeturn24search5turn16search3

This creates a useful open-source opportunity: a modern **vendor-neutral HLS/RTL library for market simulation**, with components for order books, RNG/SDE stepping, risk checks, fixed-point numerical validation, protocol parsing and host/GPU interoperability. Such a library could target physical Alveo boards and AWS F2 while maintaining a CPU reference model.

### Accelerator-friendly market semantics need more study

Many GPU designs obtain performance by adopting static arrays, fixed maximum capacities, padding or simplified communication patterns. JAX-LOB and Abmax make these trade-offs visible. citeturn21view0turn17academia6 The unanswered question is when these implementation choices subtly change experimental conclusions.

Research should quantify whether accelerator-friendly representations alter fill priority, queue-position distributions, cancellation behavior, latency realism, stylized facts or RL policy behavior. This matters more in financial simulation than in many physical simulations because strategy performance can exploit tiny implementation artifacts.

### Cross-accelerator numerical precision is an open finance problem

Financial Monte Carlo is one of the few domains where lower precision can increase throughput dramatically while errors in tail quantities or Greeks can remain economically meaningful. The TPU study found acceptable mixed-precision estimators in its experiments; the Heston GPU study found one sensitivity, Rho, less accurate in its proposed GPU scheme; FPGA work explicitly tunes fixed-point precision per computation. citeturn17search2turn18search0turn18search1

An especially promising project would jointly optimize

\[
(\text{precision}, \text{architecture}, \text{algorithm}, \text{error budget})
\]

instead of fixing IEEE FP64 everywhere. MLMC is a natural setting: coarse levels can use lower precision or approximate RNG while fine levels restore accuracy. citeturn18search1 This is one area where FPGAs, GPUs and TPUs could be compared on scientifically equal ground.

### DPUs and distributed simulation are barely explored in finance

The public finance literature reviewed here contains much more CPU/GPU/FPGA work than DPU-based market simulation. Yet distributed ABM increasingly makes networking part of the critical path: agents, venues or assets may be partitioned across hosts, and messages need to carry simulated network latency as well as real physical transport latency. ABIDES explicitly models pairwise network latency, while BlueField provides programmable high-speed networking and AWS Nitro offloads infrastructure functions. citeturn19search0turn24search2turn25search2

A compelling experiment would put the simulated network into the SmartNIC/DPU: timestamp, delay, reorder or drop agent messages in hardware while GPUs execute agents and books. That would provide a cleaner separation between **simulated market latency** and **host-scheduling artifacts**.

### Emerging accelerators lack direct finance evidence

For PIM, photonics and neuromorphic computing, the closest useful primary evidence remains outside finance. Samsung demonstrates PIM's ability to reduce data movement in AI workloads; the 2025 Nature photonic system demonstrates neural inference/training-relevant computations and publishes supporting code; Intel's Hala Point demonstrates a 1.15-billion-neuron event-driven research computer. citeturn26search2turn26search0turn26search1

That leaves several high-risk/high-novelty research questions:

**PIM:** Can agent inventories, sparse interaction graphs or historical-state scans be partitioned so that local processing outweighs the limited compute model?

**Photonics:** Can calibration, covariance/factor operations or neural agent policies exploit optical matrix engines while market state remains electronic? Current photonic research is concentrated on matrix/AI computation rather than stateful branching. citeturn26search0turn26search9

**Neuromorphic:** Can market agents or event processes be represented naturally as asynchronous spiking systems without destroying the semantics being studied? Hala Point's asynchronous/event-oriented architecture makes the analogy attractive, but it remains a research system rather than a commercial finance accelerator. citeturn26search1

These categories are therefore better framed as **thesis topics** than near-term hardware purchasing decisions.

## Prioritized papers, repositories and vendor references

The following sources are ordered by how useful I would consider them for actually building the proposed project rather than simply by publication date.

| Priority | Source | Why it matters |
|---|---|---|
| **Highest** | **Frey et al., “JAX-LOB: A GPU-Accelerated limit order book simulator…”** — arXiv 2308.13289. citeturn17search0 | Best established finance-specific GPU market-simulation paper; clearly explains the serial-within-book / parallel-across-books constraint. |
| **Highest** | **JAX-LOB / AlphaTrade GitHub repository.** citeturn19search3 | Best practical starting codebase for GPU-resident LOB/RL experiments. |
| **Highest** | **Jayakody & Jayakody, “KineticSim: A Lightweight, High-Performance Execution Engine for Real-Time Market Simulators,” 2026.** citeturn15academia17 | Most interesting recent architecture result: persistent shared-memory market state and extremely high ensemble throughput. |
| **Highest** | **Byrd, Hybinette & Balch, “ABIDES: Towards High-Fidelity Market Simulation for AI Research.”** citeturn19search0 | Key reference for high-fidelity event-driven financial ABM and latency semantics. |
| **High** | **JPMorgan ABIDES public repository.** citeturn15search0turn15search2 | Important reference implementation, though now archived/read-only. |
| **High** | **Chaturvedi et al., “Abmax: A JAX-based Agent-based Modeling Framework,” 2025.** citeturn17academia6 | Useful for generalizing beyond LOB-only JAX simulation and handling dynamic-agent operations. |
| **High** | **FLAME GPU 2 repository.** citeturn19search2 | Most useful mature analogue for general high-performance GPU agent communication. |
| **High** | **Klaisoongnoen, Brown & Brown, “Low-power option Greeks: Efficiency-driven market risk analysis using FPGAs.”** citeturn17search1 | Strong finance-specific FPGA/STAC-A2 evidence and useful discussion of numerical specialization. |
| **High** | **Belletti et al., “Tensor Processing Units for Financial Monte Carlo.”** citeturn17search2 | Essential if evaluating TPU/AI ASICs for stochastic finance; one of the rare direct TPU-finance studies. |
| **High** | **Haas & Giles, “A nested MLMC framework for efficient simulations on FPGAs,” 2025.** citeturn18search1 | Particularly relevant for mixed precision, approximate RNG and FPGA-specific Monte Carlo research. |
| **High** | **Arsaguet & Bilokon, “Derivatives Sensitivities Computation under Heston Model on GPU.”** citeturn18search0 | Useful GPU derivatives case study and an important warning that speed and Greek accuracy must be assessed together. |
| **High** | **Wheeler & Varner, “Scalable Agent-Based Modeling for Complex Financial Market Simulations.”** citeturn18search2 | Relevant distributed/parallel financial ABM architecture with heterogeneous agents and continuous double auction. |
| **Medium-high** | **CoinTossX: An open-source low-latency high-throughput matching engine.** citeturn18academia3 | Useful software exchange/reference design and hardware-in-loop architectural analogue. |
| **Medium-high** | **AMD Alveo UL3524 / UL3422 official documentation.** citeturn24search1turn24search5 | Most directly relevant current commercial FPGA products for ultra-low-latency trading. |
| **Medium-high** | **AWS EC2 F2 official documentation.** citeturn24search0 | Best convenient cloud FPGA route; also clarifies that F2 is the modern successor to the older F1 generation. |
| **Medium-high** | **AMD/Xilinx Vitis Libraries GitHub.** citeturn16search3 | Source for historical open FPGA quantitative-finance kernels; importantly records that QF PL library maintenance ended with the 2025.2 refocus. |
| **Medium** | **`markxio/delta-hedging` GitHub project.** citeturn16search23 | Small but directly relevant open example extending Vitis Monte Carlo derivative pricing. |
| **Medium-high** | **NVIDIA H100/H200 and DGX B200 official specifications.** citeturn23search0turn23search9turn23search19 | Primary hardware references when planning GPU Monte Carlo/ABM experiments. |
| **Medium-high** | **AMD Instinct MI300/MI350 documentation.** citeturn23search14turn23search1 | Primary alternative GPU reference; unusually large HBM capacity/bandwidth is relevant to simulation state. |
| **Medium** | **Google TPU v6e architecture and TPU pricing.** citeturn25search0turn25search1 | Provides exact current architecture and cloud-cost basis for replicating the older financial TPU work on modern hardware. |
| **Medium** | **AWS Trainium2/Neuron documentation.** citeturn15search5turn15search1 | Important if testing whether financial vector/tensor kernels can exploit non-GPU AI ASICs. |
| **Medium** | **Intel Gaudi architecture documentation.** citeturn23search2 | Useful third AI-accelerator comparison point, particularly for memory-intensive tensor workloads. |
| **Medium** | **NVIDIA BlueField DPU documentation.** citeturn24search2 | Primary reference for a DPU-based distributed-simulation/network-offload experiment. |
| **Medium** | **AWS Nitro documentation.** citeturn25search2 | Clarifies Nitro's role as host/network/storage offload rather than a general numerical accelerator. |
| **Exploratory** | **Samsung HBM-PIM.** citeturn26search2turn26search5 | Closest commercial-research analogue for reducing memory traffic in sparse/state-heavy workloads; no direct financial benchmark found. |
| **Exploratory** | **Ahmed et al., “Universal photonic artificial intelligence acceleration,” Nature, 2025.** citeturn26search0 | Strong current primary evidence for programmable photonic acceleration; includes a public supporting GitHub repository, but not finance-specific. |
| **Exploratory** | **Intel Hala Point / Loihi 2.** citeturn26search1 | Best current large-scale neuromorphic reference for experiments involving asynchronous sparse agents/events. |

Taken together, the literature supports a fairly clear engineering strategy. **Use CPUs as the correctness/control baseline; use GPUs as the main scalable simulation engine; use FPGAs when the objective becomes deterministic event latency or extreme specialization; treat TPU/Trainium/Gaudi as targeted experiments for tensorizable components; use DPUs to move data rather than to do the financial mathematics; and regard PIM, photonic and neuromorphic hardware as research substrates.** The most novel project space lies in the interfaces between these tiers—particularly persistent GPU market clearing, GPU–FPGA hardware-in-loop simulation, DPU-mediated distributed ABMs, and accuracy-aware mixed-precision stochastic simulation—because the public literature currently contains strong point solutions but relatively little rigorous end-to-end comparison of heterogeneous financial simulators. citeturn15academia17turn17search1turn24search2turn18search1