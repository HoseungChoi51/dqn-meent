# Mapping the 2022 paper to this implementation

This package recreates the paper's DQN-centered inverse-design loop with current Python tools and MEENT forward simulation. It is a framework reconstruction, with deliberately changed learning and episode settings. `paper_geometry.json` preserves the physical geometry and original Fourier truncation; its name does not claim exact training reproduction.

## Sources and provenance

The reference is [Seo et al., *Structural Optimization of a One-Dimensional Freeform Metagrating Deflector via Deep Reinforcement Learning*, ACS Photonics 9, 452-458 (2022)](https://www.janglab.org/documents/publications/2022ACSPhotonics.pdf), [DOI 10.1021/acsphotonics.1c00839](https://doi.org/10.1021/acsphotonics.1c00839). We inspected its equations and rendered Figure 1 to establish illumination direction.

Implementation details absent from the main article were recovered from the [authors' public code](https://github.com/dongjin-seo2020/1DFreeFormDQN) at commit `0fdb2887e45f82c6ae9613144f0019bf1e2e4965` (2023-09-05). This repository version can differ from the version used for the paper. The ACS supporting-information PDF could not be retrieved; we do not claim to have verified it.

## Physical problem

Figure 1 shows normal-incidence TM light entering **through silica**, passing through a 325 nm patterned Si/air layer, and leaving into air. There are 64 equal-width binary cells per period. The objective is absolute transmission efficiency in order +1. The reported study uses the nine combinations of wavelengths 900, 1000 and 1100 nm and angles 50, 60 and 70 degrees. Its highlighted case is 1100 nm/50 degrees. The article reports 98.4% after two million design evaluations for that case; this is a literature result, not a new training result. [Paper, Figures 1 and 3](https://www.janglab.org/documents/publications/2022ACSPhotonics.pdf)

The [original MATLAB solver](https://github.com/dongjin-seo2020/1DFreeFormDQN/blob/0fdb2887e45f82c6ae9613144f0019bf1e2e4965/solvers/Eval_Eff_1D.m) confirms the direction by selecting `inc_bottom_transmitted` for an air/pattern/glass stack. MEENT is configured in propagation order:

| Physical component | MEENT representation |
|---|---|
| Incident silica half-space | `n_incident = 1.45` |
| Si/air grating | One 325 nm layer; one refractive index per binary cell |
| Exit air half-space | `n_exit = 1.0` |
| Illumination | Normal incidence; TM |
| Target | Transmitted order +1; fraction of incident power |
| Period | `wavelength_nm / (n_exit * sin(deflection_angle))` |

For the default condition the period is 1435.9480182655066 nm. The change in stack orientation is a coordinate convention; it does not reverse which material receives the incident light. Relative efficiency normalized by total transmitted power is not the optimization objective.

### Silicon dispersion

The original solver interpolates real `n` from `solvers/p_Si.mat` and ignores its `k` column. The default constant `silicon_n = 3.551726470588235` is the exact linear-interpolation result at **1100 nm** from that table. `silicon_k = 0` preserves the source's lossless approximation.

| Wavelength (nm) | Interpolated real n in original material table |
|---|---:|
| 900 | 3.6349961538461537 |
| 1000 | 3.5859457271364317 |
| 1100 | 3.551726470588235 |

Changing the wavelength while retaining the default constant index is a constant-index experiment, not a recreation of the original dispersive material. Set the corresponding value explicitly or select the optional MEENT Green material model. The Green model is a different dataset and includes its extinction coefficient; record it as a separate physical assumption.

Material source: [authors' `p_Si.mat`](https://github.com/dongjin-seo2020/1DFreeFormDQN/blob/0fdb2887e45f82c6ae9613144f0019bf1e2e4965/solvers/p_Si.mat), Git blob `7566d8e8c689cd5b0923e6184b51c72e8ec4e437`. The source cites the MetaNet data work in the article. We extracted the values above directly from this file; no claim is made that an unrelated built-in Si table is identical.

### Numerical resolution

The original solver retains orders -40 through +40: Fourier order 40 means **81 harmonics**, not 40. The starter configuration uses order 15 for faster experimentation. A winning candidate must be re-evaluated across increasing Fourier orders before interpreting its efficiency. Energy conservation alone does not establish convergence of the target order.

## Learning problem and deliberate changes

The paper uses a cell-flipping action and reward `eta(next_state)**3`, starting each episode from all Si. Its reported network has two 128-unit ReLU hidden layers. [Paper, Figures 1-2 and methods](https://www.janglab.org/documents/publications/2022ACSPhotonics.pdf)

The table below distinguishes the [original config](https://github.com/dongjin-seo2020/1DFreeFormDQN/blob/0fdb2887e45f82c6ae9613144f0019bf1e2e4965/config/config.json), [network implementation](https://github.com/dongjin-seo2020/1DFreeFormDQN/blob/0fdb2887e45f82c6ae9613144f0019bf1e2e4965/network.py) and [training loop](https://github.com/dongjin-seo2020/1DFreeFormDQN/blob/0fdb2887e45f82c6ae9613144f0019bf1e2e4965/main.py) from this package's starter defaults.

| Aspect | Authors' public implementation | This package, starter defaults |
|---|---|---|
| Algorithm | Vanilla DQN default; Double/Dueling options also present | Double DQN; vanilla DQN configurable |
| State | 64 Si/air values in {-1,+1} | Same values plus normalized remaining horizon; 65 inputs |
| Action | Flip one of 64 cells | Same |
| Initial design | All Si | Same |
| Horizon | Reset after 128 actions, but always bootstrap | True finite horizon of 128 actions |
| Hidden layers | 128, 128 | 128, 128 |
| Activation | LeakyReLU(0.1), differing from article's ReLU | ReLU |
| Reward | New-state efficiency cubed | Same by default |
| Discount | 0.99 | 0.99 |
| Optimizer | Adam, learning rate 0.001 | Adam, learning rate 0.0001 |
| Loss | Smooth L1 / Huber | Smooth L1 / Huber |
| Replay capacity | 1,000,000 | 100,000 |
| Batch size | 512 | 64 |
| Warmup | Training after more than 5000 transitions | 512 transitions |
| Update frequency | One optimizer update every 2 steps | One every step |
| Target update | Soft mix tau=0.1 every 20,000 steps | Hard copy every 250 steps |
| Exploration | `max(0.01, 0.9*(1-step/1_000_000))` | Linear 0.9 to 0.01 over first 80% of run |
| Training budget | 2,000,000 steps configured | 10,000 steps |
| Forward solver | MATLAB RETICOLO | MEENT NumPy RCWA |

### Finite-horizon semantics

The historical [environment](https://github.com/dongjin-seo2020/1DFreeFormDQN/blob/0fdb2887e45f82c6ae9613144f0019bf1e2e4965/deflector_reticolo.py) always returned `done=False`; the training loop reset it at the horizon without marking a terminal transition. It therefore trained a continuing-task Bellman target despite bounded trajectory collection.

This package deliberately defines a finite-horizon task. Remaining time is observable, so the same binary structure at different times is not aliased. At the horizon it returns a true terminal transition and does not bootstrap. The Double DQN target is

```text
next_action = argmax_a Q_online(next_observation, a)
target = reward + gamma * (1 - terminated) * Q_target(next_observation, next_action)
```

This is a transparent MDP change, not a hidden attempt to match the original implementation. A run stopped by its overall compute budget is separately distinct from the environment's episode terminal state.

### Reward choices and what the result means

With the default cubed-efficiency reward, the policy optimizes discounted efficiency accumulated along a trajectory. It is not trained directly on best-seen efficiency or final-state efficiency alone. Since every action flips a cell, reversible cycles can be attractive; no-op actions and cycle penalties would alter the task.

The optional `difference` reward uses `eta(next)-eta(current)` and requires `gamma=1`. Over a complete episode, the return telescopes to final efficiency minus initial efficiency. This directly targets final design improvement, but is a different objective from the paper. Truncating the overall training run midway through an episode does not create a completed-episode return.

Track the best design discovered during training separately from deterministic policy-rollout quality, episode return and final-state efficiency. Finding a good design demonstrates a successful search; it does not alone show a broadly reusable policy or prove DQN superior to other optimizers.

## Configuration scope

| Configuration | Cells | Fourier order / harmonics | Horizon | Training steps | Purpose |
|---|---:|---:|---:|---:|---|
| `smoke.json` | 16 | 5 / 11 | 32 | 256 | Exercise real simulation, replay, learning and artifacts cheaply |
| `starter.json` | 64 | 15 / 31 | 128 | 10,000 | Practical first optimization experiment |
| `validation64.json` | 64 | 40 / 81 | 128 | 4,096 | Short full-geometry integration run executed for this delivery |
| `paper_geometry.json` | 64 | 40 / 81 | 128 | 200,000 | Original physical geometry and Fourier truncation; larger modern run |

`paper_geometry.json` also uses the original learning rate, batch size, replay size and warmup. It retains Double DQN, time-aware finite episodes, a shorter budget, a different epsilon schedule, and hard target copies every 2000 steps. It is therefore not an exact historical hyperparameter preset. None of these budgets guarantees the paper's efficiency or harmonic convergence.

## Reference fixture and validation

`tests/fixtures/wl1100_ang50_eff98.4.npy` is an unchanged design from the [authors' published structures](https://github.com/dongjin-seo2020/1DFreeFormDQN/blob/0fdb2887e45f82c6ae9613144f0019bf1e2e4965/structures/wl1100_ang50_eff98.4.npy), Git blob `45cb58f2624c7b0e6af98332d64aeb5e4fc0cb2e`. The numeric suffix is the authors' reported efficiency. It is a solver regression reference and is **not a trained result produced by this package**.

Use that asymmetric reference at 1100 nm, 50 degrees, real n=3.551726470588235, thickness 325 nm, silica-to-air incidence and Fourier order 40 to check the order sign and forward formulation. Mirror reversal should interchange +1 and -1 at normal incidence. Separately check uniform-layer Fresnel behavior, total reflected/transmitted power and candidate convergence at increasing order. Repeated seeds and matched solver-evaluation budgets are needed to compare learning performance with baselines.

The original code and associated reference data are MIT licensed, copyright (c) 2021 Dongjin Seo; the unchanged license accompanies the fixture as `tests/fixtures/ORIGINAL_MIT_LICENSE.txt`. The publisher's PDF is not redistributed.
