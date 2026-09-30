# nanokmc-benchmarks

A Windows benchmark harness for reproducing the numerical **Figures 5–10** of the NanoKMC manuscript. It verifies pinned solver sources, builds the eleven benchmark paths, schedules the prescribed trajectories, checks results, and generates figure CSVs and Python figures.

The model is conserved binary nearest-neighbour Kawasaki exchange on a periodic FCC lattice. Solver physics, initialization, stochastic clocks, event counters and native timing boundaries are preserved. The [scientific contract](MANUSCRIPT_REPRODUCTION_MAP.md) defines the quantities being reproduced.

**Release status:** repository-wide licensing remains unresolved; see [LICENSING.md](LICENSING.md). A fresh source-only end-to-end validation remains outstanding; exact archive routes and dependency-closure limits are described in [TOOLCHAINS.md](docs/TOOLCHAINS.md). Source/version locks and earlier build evidence do not establish that an arbitrary fresh installation has reproduced the manuscript.

## Benchmark paths and campaigns

| Package | Included paths |
|---|---|
| NanoKMC v0.1.0 | Classical, Partial-Filter, Generic, Binary, Rate-Category, Exact-Class |
| SPPARKS 08Oct25 | Diffusion sweep, linear, tree |
| kmcos | OTF FCC model at the pinned revision |
| KMC_Lattice v2.1.0 | Selective recalculation FCC application |

| Campaign | Conditions | Exact seeds per path |
|---|---|---|
| A | k=6, N=131072, x_A=0.20, T*=0.75 | 1 |
| B | k=6; x_A=0.20/0.40 × T*=0.75/1.25 | 1; representative state reuses A |
| C | x_A=0.20, T*=0.75; k=3/4/5/6 | 1–512 / 1–64 / 1–8 / 1; k=6 reuses A |

The A–C union contains **6468 unique trajectories** with the same nineteen requested observations from 0 to 30000 common MCS: **122892 checkpoint rows**. Figures 1–4 are conceptual and outside the numerical workflow. Exported XYZ files are configuration data; no Blender or RasMol rendering is required.

## Platform and execution policy

The reference platform is Windows 11 Pro AMD64 on an Intel Core i9-9900K. Each solver instance runs as one process with one computational thread. The manuscript permits **up to eight independent jobs concurrently**: select `--jobs 8` for that policy. Actual overlap varies as jobs start and finish.

Use `--jobs 1` for a deliberate sequential comparison. The command-line default is 1, so state the intended job limit explicitly. Other supported limits, up to 32, are user-selected execution modes rather than the manuscript's reference policy. Compilation's `--jobs` is a separate setting.

A required `--run-set NAME` isolates each dataset, including raw records, completion seals, status, CSVs and figures. For example, `manuscript-jobs8` and `manuscript-sequential` can coexist. The job policy is fixed when execution starts in a run-set; use a different name to change it.

## Getting started

Start from a fresh clone and follow [HOW_TO_REPRODUCE.md](HOW_TO_REPRODUCE.md) to install the exact toolchains, create the Python environments, and configure ignored local paths. The main harness uses CPython **3.14.4**; kmcos requires its separate **3.10.11** environment. The pinned compiler routes use UCRT64 GNU **16.2.0** and plain-MSYS GNU **15.3.0** for SPPARKS.

After configuration, these commands verify, compile and preview the work without launching manuscript trajectories:

```powershell
$env:BENCHMARK_PYTHON = (Resolve-Path '.\.venv\Scripts\python.exe').Path
.\01_check_environment.bat
if ($LASTEXITCODE -ne 0) { throw 'Environment verification failed.' }
.\02_fetch_or_verify_sources.bat --fetch
if ($LASTEXITCODE -ne 0) { throw 'Pinned source acquisition failed.' }
.\03_build_all.bat --jobs 4
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
.\04_run_manuscript_campaigns.bat --list-solvers
.\04_run_manuscript_campaigns.bat --run-set manuscript-jobs8 --campaign all --jobs 8 --dry-run
```

The following commands **launch the expensive full A–C campaign**, then process and plot that run-set:

```powershell
.\04_run_manuscript_campaigns.bat --run-set manuscript-jobs8 --campaign all --jobs 8 --resume
if ($LASTEXITCODE -ne 0) { throw 'Calculation stopped; inspect status before continuing.' }
.\05_process_results.bat --run-set manuscript-jobs8
if ($LASTEXITCODE -ne 0) { throw 'Result validation failed.' }
.\06_make_figures.bat --run-set manuscript-jobs8
if ($LASTEXITCODE -ne 0) { throw 'Figure generation failed.' }
```

For a subset, provide explicit solver identifiers or documented aliases, for example `--solvers binary partial-filter rate-category spparks-sweep`. Subsets still run the full prescribed trajectories. Complete figure exports require all solver/state/seed observations belonging to those figures; missing inputs are rejected.

Check progress with `--status`, preview with `--dry-run`, and resume interrupted work with the same run-set and job limit. Resume reuses only validated, unchanged completed trajectories. Unfinished trajectories restart from their original seeds in new attempt directories.

For a visual check before a campaign is complete, `07_preview_partial_results.bat --run-set NAME --figures 5 6 7 8 9 10` draws clearly marked provisional plots from verified completed trajectories only. It can run while a campaign continues and writes solely under that run-set's `figures/previews/`. Missing solvers, states and seeds remain missing; no provisional output is accepted by the strict Figure 5–10 processing or plotting stages. Figure 10 previews show actual seed coverage and omit the manuscript size exponent until the full ensemble is processed.

## Outputs

```text
run-sets/<name>/
  results/raw/        attempts: native and per-run processed files, completion seals
  results/processed/  consolidated standardized publication tables
  results/csv/        nine Figure 5–10 CSVs and their manifest
  results/rasmol/     thirteen configurations as twenty-six A/B XYZ files
  figures/           six figures in PDF/PNG and figure provenance
  logs/              campaign status and worker logs
```

Environment/source/build reports remain under repository-level `logs/` and `build/`. Dependencies, environments and generated data are ignored by Git. See the reproduction guide for exact filenames and commands for separate parallel/sequential and selected-solver datasets.

## Reproducibility, citation and licensing

Source revisions, compiler flags and package pins are recorded in [dependencies.lock.json](dependencies.lock.json). [PROVENANCE.md](docs/PROVENANCE.md) explains two unresolved historical issues: complete NanoKMC measured-binary lineage has not been established, and the evidenced SPPARKS compiler is GNU 15.3.0 whereas the manuscript states GNU 16.2.0 generally. The modern Python analysis environment is an explicit reproduction environment, not a recovered historical package snapshot.

Different native initializers and random trajectories need not agree microscopically. Wall-clock measurements also depend on hardware, scheduling and concurrency. The harness preserves the scientific definitions and reports evidence; it does not promise identical stochastic curves or timings.

Use [CITATION.cff](CITATION.cff) for available citation metadata and [THIRD_PARTY.md](docs/THIRD_PARTY.md) for solver attribution. The NanoKMC software DOI identifies NanoKMC, not this benchmark repository. No manuscript or benchmark DOI is inferred. Redistribution must wait until the benchmark-owned licensing issue in [LICENSING.md](LICENSING.md) is resolved; upstream packages retain their own licenses.


