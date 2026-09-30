# Manuscript numerical reproduction contract

This document defines the scientific scope of `nanokmc-benchmarks`: numerical Figures **5–10** of the NanoKMC manuscript. It is an executable-data contract for the campaign manifest, validation, processing and plotting. Source revisions, build flags and provenance qualifications are recorded separately in [dependencies.lock.json](dependencies.lock.json) and [docs/PROVENANCE.md](docs/PROVENANCE.md).

## Model, clocks and path identity

The system is three-dimensional periodic FCC with twelve nearest neighbours per site and `6N` undirected bonds. Its size is `N = 2^(3k−1) = 4(2^(k−1))^3`; integer doubled-cell coordinates wrap modulo `2^k`. Binary A/B occupation is conserved by nearest-neighbour Kawasaki exchange. The manuscript energy scale is `E_NN=1`, represented by the existing input `Ea=1`, with Metropolis factor `min(1, exp(−ΔE/T*))` where applicable.

| Path | Canonical identifier | Native counter meaning |
|---|---|---|
| NanoKMC Classical | `nanokmc_bit_encoded` | All site-neighbour proposals, including null and rejected proposals |
| Partial-Filter | `nanokmc_partial_filter_optimized` | A-site/neighbour proposals, including remaining nulls and rejections |
| NanoKMC Generic | `nanokmc_active_filtered_generic` | Uniform complete unlike-bond population; Metropolis proposals |
| NanoKMC Binary | `nanokmc_active_filtered_binary_nn` | Same Active-Filtered population and semantics, optimized binary representation |
| Rate-Category | `nanokmc_rate_category_optimized` | Four rate groups; selected proposals include residual rejection |
| Exact-Class | `nanokmc_exact_class_optimized` | Exact-class selections execute |
| SPPARKS sweep | `spparks_diffusion_sweep_random` | `Naccept + Nreject` proposals; `Naccept` executions |
| SPPARKS linear | `spparks_diffusion_linear` | Rejection-free selections/executions from `Naccept` |
| SPPARKS tree | `spparks_diffusion_tree` | Rejection-free selections/executions from `Naccept` |
| kmcos OTF | `kmcos_otf` | Native KMC steps are selected/executed exchanges |
| KMC_Lattice selective | `kmc_lattice_selective` | `executed_moves` are selected/executed exchanges |

The canonical identifier `nanokmc_bit_encoded` denotes Classical. It must not be mistaken for the Active-Filtered Binary path.

Classical advances `1/N` common MCS per proposal; Partial-Filter uses `1/(2M_A)`; Generic/Binary use `6/B` for the pre-proposal unlike-bond population B. Rate-Category preserves its weighted envelope and residual rejection. Exact-Class uses the exact weighted population. The selective NanoKMC paths accumulate deterministic mean clock increments; they are not replaced by exponential waiting times. External native clocks map as `common MCS = 6 × native time`, and SPPARKS uses `T_app = 2T*`.

Keep each package's initializer: NanoKMC per-site random assignment, SPPARKS fractional assignment, and kmcos/KMC_Lattice fixed rounded A counts on randomly selected sites. Record nominal and actual composition. Equal integer seeds do not prescribe equal initial configurations across packages.

Requested observation labels define correspondence. Store native counters, observation boundaries and realized progress separately. Preserve NanoKMC fractional overshoot, SPPARKS threshold crossing, KMC_Lattice last-event observations and the kmcos first-event overshoot check. Never interpolate trajectories to make native clocks coincide.

## Campaigns and reuse

Every core trajectory uses exactly these nineteen requested common-MCS observations:

```text
0,10,20,30,40,50,100,200,300,400,500,1000,2000,3000,4000,5000,10000,20000,30000
```

| State ID | Campaigns | k / N | x_A / T* | Seeds per path |
|---|---|---|---|---|
| `representative_k6_x20_t075` | A, B, C | 6 / 131072 | 0.20 / 0.75 | 1 |
| `screen_k6_x20_t125` | B | 6 / 131072 | 0.20 / 1.25 | 1 |
| `screen_k6_x40_t075` | B | 6 / 131072 | 0.40 / 0.75 | 1 |
| `screen_k6_x40_t125` | B | 6 / 131072 | 0.40 / 1.25 | 1 |
| `scaling_k3_x20_t075` | C | 3 / 256 | 0.20 / 0.75 | 1–512 inclusive |
| `scaling_k4_x20_t075` | C | 4 / 2048 | 0.20 / 0.75 | 1–64 inclusive |
| `scaling_k5_x20_t075` | C | 5 / 16384 | 0.20 / 0.75 | 1–8 inclusive |

[config/manuscript.json](config/manuscript.json) defines the shared `benchmark`
model, observation targets, solver list and output policy once. Its `states`
record size index `k`, A fraction, reduced temperature and inclusive `seed_range`;
`campaigns` lists the state IDs belonging to A, B and C. In an ID, `x20` means
20% A and `t075` means T*=0.75. The parser rejects unknown fields and deviations
from this fixed scientific contract. Installation paths belong only in ignored
`config/paths.local.json`; source/model/toolchain pins belong in `dependencies.lock.json`.

A has 11 runs. B contains 44 run identities, including A, so adds 33. C contains 6435 run identities, including A, so adds 6424. The deduplicated union is **6468 runs and 122892 observations**. Keep the nineteen-point schedule even when only the endpoint enters a size-scaling figure; native in-loop output can affect timing.

## Run-sets and concurrency

A run-set is an output/execution identity supplied through `--run-set NAME`. Distinct run-sets retain independent canonical completion pointers, status, processed data and figures, even when they contain the same scientific cases.

Each solver instance is single-process/single-thread. The manuscript permits **up to eight independent jobs concurrently**, selected explicitly with `--jobs 8`. `--jobs 1` is a deliberate sequential comparison; it is also the technical command-line default. Other supported limits up to 32 are user-selected execution modes. The requested limit is not constant measured overlap.

The requested job policy is sealed with a run-set and its attempts. Do not mix sequential and concurrent runtimes or relabel one as the other. Processing targets one run-set and requires one uniform requested-job policy. Keep separate names, such as `manuscript-jobs8` and `manuscript-sequential`, for a comparison.

## Observables and native timing

- `rho_AB = N_AB/(6N)`, using unlike undirected nearest-neighbour bonds.
- Periodic connected A components define the distribution `C_s`.
- `N_A_lt12 = sum(s*C_s, s=1..11)`.
- Dissolved matrix concentration is `c_A^B = N_A_lt12/(N_B + N_A_lt12)`. It is neither a monomer fraction nor a quantity normalized by total N.
- `n_ex` is cumulative native executed unlike exchanges divided by N. Snapshots cannot reconstruct this counter.
- Proposed/selected counts retain the path-specific meaning in the table above. Do not suppress Rate-Category residual rejection or equate SPPARKS sweep's native count with Classical's.

| Package | Runtime used for figures |
|---|---|
| NanoKMC | Accumulated `steady_clock` evolution intervals; subsequent reporting and export excluded |
| SPPARKS | Cumulative native `CPU` column, an elapsed wall timer over the iteration loop; preceding diagnostics and earlier in-loop output remain included |
| kmcos | Accumulated `perf_counter` around `do_steps_time` |
| KMC_Lattice | Accumulated `steady_clock` advance-to-observation intervals |

Setup and downstream common analysis are excluded. Do not replace these measurements with command wall duration or subtract estimated output overhead. Small nonnegative initial timer values are retained; positive observation runtimes must be finite and positive.

## Figure data and fits

| Figure | Required data and calculation | Export |
|---|---|---|
| 5 | A Binary morphology at 0/3000/30000; all A coordinates plotted in Python with a common view. All eleven paths' A-cluster counts at 3000 in bins 12–49, 50–199, 200–999, 1000–3999, ≥4000, without bin-width normalization. Binary rho_AB and cutoff-12 concentration at all nineteen observations. Time axis linear through 30, logarithmic thereafter. | `fig05_observables.csv` (19), `fig05_clusters.csv` (55), configuration manifest |
| 6 | Ten comparators against Binary, four B states, seed 1, eighteen positive targets. Join by state/seed/requested target; preserve both realized times. Three observables: rho_AB, c_A^B, n_ex. Seventy-two pairs per comparator per panel, 2160 pairs overall. Linear axes for rho_AB/concentration; log axes for n_ex. | `fig06_correspondence.csv` (2160) |
| 7 | Eleven A endpoints at 30000; raw candidates/selections and executions plus per-site normalization. Classical has 30000 proposals/site; SPPARKS sweep retains 60000 native selections/site. | `fig07_event_accounting.csv` (11) |
| 8 | Eleven A runtime histories. Preserve all nineteen observations; display and fit exactly sixteen requested targets 30..30000. Unweighted OLS of log(runtime) on log(requested MCS), giving gamma and an exponentiated intercept. | `fig08_runtime_horizon.csv` (209), `fig08_gamma.csv` (11) |
| 9 | All eleven paths at the four B-state endpoints, using seed 1 and the native cumulative runtime. | `fig09_runtime_screen.csv` (44) |
| 10 | All 6435 C endpoints; arithmetic runtime mean per size/path, sample SD and SEM `SD(ddof=1)/sqrt(n)` for n>1. SD/SEM are unavailable for n=1. Fit unweighted OLS of log(arithmetic mean runtime) on log(N), using four equally weighted size means per path, giving alpha. | `fig10_size_scaling.csv` (44), `fig10_alpha.csv` (11) |

The Figure 6 pairs are repeated observations from four trajectories, not independent replicas. Figure 10's alpha is a finite-range fit, not an asserted asymptotic complexity.

Configuration export retains thirteen distinct instances: all eleven paths at requested 3000 plus Binary at 0 and 30000. Each instance has full A/B species-split XYZ data, canonical coordinates, box dimensions, source identity and hashes. This gives twenty-six XYZ files without invoking a renderer.

## Validation and interpretation

Completion requires the exact requested sequence, run-set/run/seed identity, periodic FCC topology, conserved species, complete cluster mass, finite native timing and consistent counters. Native and processed files are sealed by hashes. Missing, duplicate, stale or changed inputs are rejected rather than silently dropped, filled with zero or interpolated.

Processing commits explicit figure coverage and provenance. Plotting checks the committed CSV/configuration hashes, fit definitions and execution policy. A solver subset cannot satisfy an eleven-path figure by omission.

These checks establish internal consistency and traceability. Agreement with the published science still requires running and examining the prescribed calculations. Different random trajectories and wall-clock results are expected within the qualifications in [HOW_TO_REPRODUCE.md](HOW_TO_REPRODUCE.md). No universal cross-code trajectory or runtime tolerance is specified.
