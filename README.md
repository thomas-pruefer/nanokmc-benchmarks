# nanokmc-benchmarks

Reproduce the numerical **Figures 5–10** accompanying the NanoKMC manuscript: acquire pinned sources, build eleven solver paths, run the prescribed campaigns, validate results, and generate CSVs and Python figures.

The benchmark models conserved binary nearest-neighbour Kawasaki exchange on a three-dimensional periodic FCC lattice. It compares solver paths using common Monte Carlo step (MCS) observations while retaining each implementation's initialization, clock, event counters and native timing boundary.

**Platform:** Windows 11 AMD64 with the pinned MSYS2 and Python environments. Each solver instance is single-threaded; `--jobs 8` permits up to eight independent jobs concurrently, and `--jobs 1` runs sequentially. Follow [HOW_TO_REPRODUCE.md](HOW_TO_REPRODUCE.md) for complete installation and execution instructions. [Citation](CITATION.cff) · [MIT license](LICENSE).

## Benchmark scope

| Campaign | Conditions | Seeds per solver path |
|---|---|---|
| A | Representative trajectory: k=6, N=131072, x_A=0.20, T*=0.75 | 1 |
| B | Four k=6 states: x_A=0.20/0.40 × T*=0.75/1.25 | 1; reuses A |
| C | Size scaling: k=3/4/5/6, x_A=0.20, T*=0.75 | 1–512 / 1–64 / 1–8 / 1; reuses A at k=6 |

The complete union has **6468 unique trajectories**, each with nineteen observations from 0 to 30000 common MCS. The [scientific contract](MANUSCRIPT_REPRODUCTION_MAP.md) specifies the model, exact observations, counters, timing definitions and figure calculations.

## Included implementations

| Package | Solver paths |
|---|---|
| NanoKMC v0.1.0 | Classical, Partial-Filter, Generic, Binary, Rate-Category, Exact-Class |
| SPPARKS 08Oct25 | Diffusion sweep, linear, tree |
| kmcos | On-the-fly (OTF) FCC model |
| KMC_Lattice v2.1.0 | Selective recalculation FCC application |

Upstream packages are fetched separately at exact revisions. [models/README.md](models/README.md) describes the benchmark-owned application code and its boundary with upstream sources.

## Quick start

First complete the prerequisite, Python-environment and local-path steps in the [reproduction guide](HOW_TO_REPRODUCE.md). From the clone root in PowerShell:

```powershell
$env:BENCHMARK_PYTHON = (Resolve-Path '.\.venv\Scripts\python.exe').Path
.\scripts\windows\check_environment.bat
if ($LASTEXITCODE -ne 0) { throw 'Environment verification failed.' }
.\scripts\windows\fetch_or_verify_sources.bat --fetch
if ($LASTEXITCODE -ne 0) { throw 'Source verification failed.' }
.\scripts\windows\build_all.bat --jobs 4
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
.\scripts\windows\run_campaigns.bat --list-solvers
.\scripts\windows\run_campaigns.bat --run-set manuscript-jobs8 --campaign all --jobs 8 --dry-run
```

These commands set up the solvers and preview campaign selection. **The next command starts the expensive full manuscript calculations:**

```powershell
.\scripts\windows\run_campaigns.bat --run-set manuscript-jobs8 --campaign all --jobs 8 --resume
if ($LASTEXITCODE -ne 0) { throw 'Calculation stopped; inspect status before continuing.' }
.\scripts\windows\process_results.bat --run-set manuscript-jobs8
if ($LASTEXITCODE -ne 0) { throw 'Result validation failed.' }
.\scripts\windows\make_figures.bat --run-set manuscript-jobs8
```

Use `--campaign A`, `B` or `C` for staged execution and `--solvers` for a subset. `--status` checks progress; `--resume` reuses only unchanged, validated completions. An unfinished trajectory restarts from its original seed. Separate `--run-set` names keep sequential and concurrent datasets independent; a run-set's job policy cannot change after registration.

For incomplete campaigns, `scripts/windows/preview_results.bat --run-set NAME --figures 5 6 7 8 9 10` produces provisional plots from completed trajectories only. Missing data stay missing. Strict manuscript figures require their complete prescribed coverage.

## Repository and outputs

| Location | Purpose |
|---|---|
| `config/`, dependency and requirements locks | Scientific configuration, example local paths, exact source/toolchain/package pins |
| `adapters/`, `benchmark_core/` | Solver integration, validation, orchestration and shared analysis |
| `models/` | Benchmark-owned FCC applications and kmcos generation/worker code |
| `plotting/`, `scripts/`, `scripts/windows/` | Figure rendering, workflow commands and Windows launchers |
| `tests/`, `docs/` | Offline/native tests and technical documentation |

Generated data live under `run-sets/<name>/`: native attempts in `results/raw/`, standardized tables in `results/processed/`, nine figure CSVs in `results/csv/`, RasMol-compatible XYZ data in `results/rasmol/`, and PDF/PNG figures in `figures/`. Provisional plots have their own `figures/previews/` directory. Environments, fetched sources, builds, logs and results are ignored by Git and created on demand.

## Reproducibility

The reference machine is Windows 11 Pro on an Intel Core i9-9900K. The harness uses CPython 3.14.4, kmcos uses a separate CPython 3.10.11 environment, and compiler routes are UCRT64 GNU 16.2.0 or plain-MSYS GNU 15.3.0 for SPPARKS. Exact requirements and acquisition instructions are in [TOOLCHAINS.md](docs/TOOLCHAINS.md).

[PROVENANCE.md](docs/PROVENANCE.md) qualifies the incomplete NanoKMC measured-binary lineage, the SPPARKS manuscript/compiler discrepancy and the limits of environment reconstruction. Different native random streams can produce different trajectories; hardware, toolchains and job overlap affect timings. Source/build and smoke checks do not by themselves establish reproduction of the scientific results.

## Citation and license

[CITATION.cff](CITATION.cff) distinguishes this benchmark repository, the NanoKMC software release and the manuscript. A NanoKMC software DOI is not a DOI for this repository. Solver attribution and upstream license information are in [THIRD_PARTY.md](docs/THIRD_PARTY.md).

Benchmark-owned repository code is distributed under the [MIT license](LICENSE). Fetched third-party packages retain their own licenses; [LICENSING.md](LICENSING.md) explains the boundary and generated/linked product qualifications.
