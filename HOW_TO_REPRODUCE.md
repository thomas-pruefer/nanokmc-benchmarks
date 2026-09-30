# Reproducing the manuscript calculations

This guide takes a fresh clone from dependency installation to the numerical Figures 5–10. Commands run in a normal **Windows PowerShell** from the repository root. The exact model, observations, counters and fits are specified in [MANUSCRIPT_REPRODUCTION_MAP.md](MANUSCRIPT_REPRODUCTION_MAP.md).

The setup, build, dry-run and optional initialization checks are separate from the expensive manuscript calculations. Sections 9–12 and 19 contain commands that start real trajectories. Choose the dataset and execution policy before starting them.

Exact toolchain acquisition and dependency-closure limits are described in [TOOLCHAINS.md](docs/TOOLCHAINS.md). Build and initialization checks establish that an installation can run the model; scientific reproduction requires the prescribed calculations and analysis. Benchmark-owned code uses the [MIT license](LICENSE); [LICENSING.md](LICENSING.md) distinguishes separately licensed dependencies and generated/linked products.

## 1. Prerequisites and a short clone location

Use Windows 11 AMD64, a normal PowerShell terminal, Git, and sufficient free storage for native snapshots and the 6468-run campaign. No reliable universal disk-space or elapsed-time estimate is established. Keep the computer awake during calculations and monitor storage.

Copy the Git clone URL from the repository page. Choose a short destination and clone it from PowerShell:

```powershell
$RepositoryUrl = Read-Host 'Git clone URL from the repository page'
$Repository = 'C:\kmc\nanokmc-benchmarks'
git clone -- $RepositoryUrl $Repository
if ($LASTEXITCODE -ne 0) { throw 'Clone failed.' }
Set-Location -LiteralPath $Repository
Get-Location
```

Choose a short, local path, for example `C:\kmc\nanokmc-benchmarks`. Some native exporters still have Windows path-length limits even when Windows long-path support is enabled. The harness uses compact run/attempt directory names and checks projected native output paths before launch. Avoid deeply nested or synchronized folders. Do not move a dataset while its processes are running.

The repository's `.gitattributes` defines stable source line endings and Windows
batch-script line endings. Model identity checks hash bytes, so use the clone as
checked out; do not bulk-convert model files before verification and compilation.

Read [TOOLCHAINS.md](docs/TOOLCHAINS.md) before installing these exact environments:

| Component | Required route |
|---|---|
| Harness, processing and plotting | CPython 3.14.4, AMD64; `requirements.lock.txt` |
| kmcos build and runtime | Separate CPython 3.10.11, AMD64; `requirements-kmcos.lock.txt` |
| NanoKMC and KMC_Lattice C++ | MSYS2 UCRT64 GNU 16.2.0, MSYS2 Rev3 |
| kmcos Fortran and Python linking | MSYS2 UCRT64 GNU Fortran/C 16.2.0 Rev3, pinned `gendef` and `dlltool` packages |
| SPPARKS serial C++ | Plain-MSYS GNU 15.3.0 |
| Build utilities | CMake 4.4.2, Ninja 1.13.2, GNU Make 4.4.1, Bash, zip/unzip and the declared MS-MPI headers |

MSYS2 is a rolling distribution. Installing its latest packages does not establish these exact versions. Follow the archive/package instructions in the toolchain document, then run the environment check to verify the installed tools. Do not replace a missing compiler with another version or change solver source to make it compile.

The reference machine was Windows 11 Pro on an Intel Core i9-9900K. Another AMD64 computer can execute the workflow, but its timings are a separate measurement. Each solver instance is single-process/single-thread; the manuscript policy allows up to eight independent jobs at once.

## 2. Create the two Python environments

The following assumes the Windows Python launcher can find both installed interpreters. If it cannot, invoke each actual `python.exe` by its full path with PowerShell's `&` operator. Do not point both environments at the same interpreter.

```powershell
py -0p
py -3.14 -c "import platform; assert platform.python_version() == '3.14.4', platform.python_version()"
if ($LASTEXITCODE -ne 0) { throw 'Install the locked CPython 3.14.4 interpreter first.' }
py -3.14 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Harness environment creation failed.' }
& .\.venv\Scripts\python.exe -m pip install -r .\requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Harness package installation failed.' }

py -3.10 -c "import platform; assert platform.python_version() == '3.10.11', platform.python_version()"
if ($LASTEXITCODE -ne 0) { throw 'Install the locked CPython 3.10.11 interpreter first.' }
py -3.10 -m venv .venv-kmcos
if ($LASTEXITCODE -ne 0) { throw 'kmcos environment creation failed.' }
& .\.venv-kmcos\Scripts\python.exe -m pip install -r .\requirements-kmcos.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'kmcos package installation failed.' }

$env:BENCHMARK_PYTHON = (Resolve-Path '.\.venv\Scripts\python.exe').Path
```

The main NumPy pin is 2.4.4; the kmcos environment uses NumPy 1.26.4 and setuptools 65.5.0 for its frozen f2py build route. Do not install kmcos dependencies into the main environment or upgrade locked packages to resolve an error. A missing archived package is an acquisition problem to investigate.

Virtual-environment activation is unnecessary. Every batch launcher uses `BENCHMARK_PYTHON`, then an optional ignored `config/python.local.bat`, then `.venv\Scripts\python.exe`. Explicitly setting `BENCHMARK_PYTHON` as above avoids ambiguity. Repeat that assignment after opening a new PowerShell session.

## 3. Configure local paths

Create your ignored configuration without overwriting an existing one:

```powershell
if (-not (Test-Path -LiteralPath '.\config\paths.local.json')) {
    Copy-Item -LiteralPath '.\config\paths.example.json' -Destination '.\config\paths.local.json'
}
notepad .\config\paths.local.json
```

Set `msys2_root` and `msys2_bash` to the installation containing the exact tools. The example's `C:/msys64` is a conventional installation location, not a requirement. JSON paths may use forward slashes. The launchers use `.venv/Scripts/python.exe`; set `kmcos_python` to `.venv-kmcos/Scripts/python.exe` in the local configuration.

Keep source and build paths at their repository-relative defaults unless you have a specific reason to change them. `source_repositories` may map the four dependency names to existing local Git repositories for offline acquisition. The mapping keys are `nanokmc`, `spparks`, `kmcos` and `kmc_lattice`; each value is the local repository path containing its exact commit. Local configuration is ignored by Git.

All stages accept `--paths` where they require tool configuration. The commands below use `config/paths.local.json`. The output run-set is chosen separately through `--run-set`; do not implement output separation by editing solver paths.

## 4. Verify the environment

```powershell
.\scripts\windows\check_environment.bat
if ($LASTEXITCODE -ne 0) { throw 'Environment verification failed; inspect logs/environment_check.json.' }
```

The environment check probes actual interpreter and package versions, compilers and required runtime components. For kmcos it also checks the base CPython `python310.dll`, UCRT64 `gcc`, `gendef` and `dlltool`, including the declared MSYS2 package versions. Read the generated report and correct every reported error before building. An installed executable with a plausible filename is not sufficient evidence of the required version.

Do not substitute the dedicated kmcos Python for the main harness Python. The recorded environment is a declared reproduction environment; the complete historical analysis-package chain is not established.

## 5. Fetch and verify the frozen sources

The first command explicitly permits network acquisition from the upstream URLs recorded in `dependencies.lock.json`:

```powershell
.\scripts\windows\fetch_or_verify_sources.bat --fetch
if ($LASTEXITCODE -ne 0) { throw 'Frozen source acquisition or verification failed.' }
```

The required revisions are:

| Dependency | Revision |
|---|---|
| NanoKMC v0.1.0 | `68764aa44c4d73a7d95f3094d031254d0f43e50d` |
| SPPARKS 08Oct25 | `fb933dd6d76600c8f0edb9a61449e1c538585134` |
| KMC_Lattice v2.1.0 | `4bc153e28eed6d3b0d3e5106998070d064968452` |
| kmcos | `4442009d73032cbe39f8201370ad6d8927c7be4c` |

The script extracts verified scientific source trees into ignored `sources/` and records file hashes. It does not accept a branch name or a similar-looking source folder as a revision lock. To check already extracted trees without acquiring them:

```powershell
.\scripts\windows\fetch_or_verify_sources.bat --verify-only
if ($LASTEXITCODE -ne 0) { throw 'Source verification failed.' }
```

For offline use, first configure local repositories containing the exact objects, then run `scripts/windows/fetch_or_verify_sources.bat` without `--fetch`. Source verification records are under `logs/source_verification.json` and `build/source-manifests/`. Do not edit extracted scientific source. Correct a build environment problem rather than patching the upstream solver.

## 6. Compile every solver

```powershell
.\scripts\windows\build_all.bat --jobs 4
if ($LASTEXITCODE -ne 0) { throw 'Compilation failed; inspect build and logs before continuing.' }
```

Here `--jobs 4` controls compilation, not campaign concurrency. This builds NanoKMC, serial SPPARKS, the KMC_Lattice FCC application and the kmcos OTF model. Their eleven execution paths share these four package builds. Build reports record source identities, tool versions, flags and artifact hashes.

The kmcos stage automatically derives the dedicated Python 3.10 base DLL and prepares its MinGW import library with the pinned UCRT64 `gendef` and `dlltool` when needed. It validates a previous library against the current Python DLL, toolchain and file hashes before reuse. The generated `.def`, `.a` and identity record stay in the ignored `.venv-kmcos/libs/` directory; `logs/build_kmcos.json` records the decision. No manual import-library command is part of setup, and this compatibility step does not edit kmcos scientific source.

NanoKMC uses its paper-build route with `-O1 -DNDEBUG`; SPPARKS and KMC_Lattice use `-O1`. kmcos keeps its declared f2py/GNU Fortran `-O3` flags. These are intentional path-specific build contracts, not interchangeable optimization defaults.

After a successful build, checking existing artifacts without recompiling is possible:

```powershell
.\scripts\windows\build_all.bat --collect-existing
if ($LASTEXITCODE -ne 0) { throw 'Existing build artifacts do not satisfy the declared build identity.' }
```

`--collect-existing` requires valid local build evidence; it is not an import mechanism for arbitrary old executables. Review [PROVENANCE.md](docs/PROVENANCE.md) for the unresolved NanoKMC historical binary lineage and the SPPARKS historical compiler discrepancy.

## 7. Optional small initialization checks and offline tests

A smoke check is a genuinely small native calculation: k=3, N=256, seed 1, x_A=0.20, T*=0.75, observations at 0 and 10 common MCS. It is excluded from manuscript datasets and does not require a run-set.

To verify the existing builds and smoke-test all eleven paths:

```powershell
.\scripts\windows\build_all.bat --collect-existing --smoke
if ($LASTEXITCODE -ne 0) { throw 'Build verification or initialization smoke test failed.' }
```

To smoke-test just Binary instead:

```powershell
& $env:BENCHMARK_PYTHON -B .\scripts\smoke_test.py --codes nanokmc_active_filtered_binary_nn
if ($LASTEXITCODE -ne 0) { throw 'Binary initialization smoke test failed.' }
```

Inspect `logs/smoke_latest.json` and repository-level `results/raw/smoke/` and `results/processed/smoke/`. Smoke success proves initialization and short evolution, not the scientific agreement or runtime behavior of the full campaigns.

The repository's offline tests use fixtures and synthetic data; they do not start manuscript solvers:

```powershell
& $env:BENCHMARK_PYTHON -B -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Offline workflow tests failed.' }
```

## 8. Select a run-set and preview the exact calculations

Run-set names are 1–64 lowercase ASCII letters/digits/hyphens/underscores, start with a letter or digit, and cannot be Windows device names. Examples are `manuscript-jobs8`, `manuscript-sequential`, `selected-jobs8` and `selected-sequential`.

All campaign, processing and plotting commands require `--run-set NAME`, except campaign help and solver listing. Each dataset gets independent raw attempts, completion seals, status, tables and figures under `run-sets/<name>/`. The first actual execution registers its job policy in `run_set.json`. You cannot later change that dataset from jobs 8 to jobs 1; use another name.

`--jobs 8` follows the manuscript's maximum-eight independent-job policy. `--jobs 1` is a deliberate sequential comparison and the technical default. Other limits from 1 through 32 are accepted as user-selected execution modes. Specify the intended limit explicitly. The requested maximum is recorded separately from the number of jobs alive at each launch; neither implies a constant eight-way overlap.

List the eleven paths and preview the full union:

```powershell
.\scripts\windows\run_campaigns.bat --list-solvers
.\scripts\windows\run_campaigns.bat --run-set manuscript-jobs8 --campaign all --jobs 8 --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Campaign selection is invalid.' }
```

A dry run enumerates selections without launching solvers, processing results or certifying that existing results can be reused. `all` deduplicates Campaigns A, B and C into **6468 trajectories**, each with nineteen requested observations, giving **122892 rows**. The observation labels are:

```text
0,10,20,30,40,50,100,200,300,400,500,1000,2000,3000,4000,5000,10000,20000,30000
```

## 9. Run Campaign A and inspect its figures

The command below starts eleven full trajectories, each at k=6, N=131072, x_A=0.20, T*=0.75 and seed 1. These are real manuscript calculations, not initialization checks.

```powershell
.\scripts\windows\run_campaigns.bat --run-set manuscript-jobs8 --campaign A --jobs 8 --resume
if ($LASTEXITCODE -ne 0) { throw 'Campaign A stopped; inspect its status and resume after correcting the cause.' }
```

Once all eleven are complete, Figures 5, 7 and 8 can be processed and generated without waiting for B or C:

```powershell
.\scripts\windows\process_results.bat --run-set manuscript-jobs8 --figures 5 7 8
if ($LASTEXITCODE -ne 0) { throw 'Campaign A processing failed.' }
.\scripts\windows\make_figures.bat --run-set manuscript-jobs8 --figures 5 7 8
if ($LASTEXITCODE -ne 0) { throw 'Campaign A figure generation failed.' }
```

To inspect progress visually before all eleven finish, use the separate **provisional preview** command. It reads only complete, current, scientifically validated trajectories and may be run while the campaign is still running:

```powershell
.\scripts\windows\preview_results.bat --run-set manuscript-jobs8 --figures 5 7 8
if ($LASTEXITCODE -ne 0) { throw 'Partial preview failed; inspect its reported coverage.' }
```

The preview writes time-stamped PNG files and a coverage/provenance manifest under `run-sets/manuscript-jobs8/figures/previews/`. Select any of `--figures 5 6 7 8 9 10`; use `--format pdf` or `--format both` if useful, and `--dry-run` for coverage without files. A missing solver or state is omitted visibly, and a Figure 6 comparison appears only when its Binary reference and comparator are both complete. Figure 10 means use the completed seeds available at preview time and explicitly show `n/expected`; the size exponent is withheld. Figures 5 and 10 preview their quantitative content without the final manuscript morphology or size-fit panels. All previews are watermarked **PROVISIONAL** and are never accepted as publication CSVs or final Figures 5–10. Use `process_results.bat` and `make_figures.bat` after the prescribed runs finish.

## 10. Run Campaign B

B uses all four k=6 states: x_A=0.20/0.40 crossed with T*=0.75/1.25, seed 1, eleven paths. It contains 44 identities; the representative state reuses valid A results, leaving 33 additional trajectories.

```powershell
.\scripts\windows\run_campaigns.bat --run-set manuscript-jobs8 --campaign B --jobs 8 --resume
if ($LASTEXITCODE -ne 0) { throw 'Campaign B stopped; inspect status before continuing.' }
```

B supports Figures 6 and 9. To process all figures supported by the completed A+B dataset:

```powershell
.\scripts\windows\process_results.bat --run-set manuscript-jobs8 --figures 5 6 7 8 9
if ($LASTEXITCODE -ne 0) { throw 'Campaign A+B processing failed.' }
.\scripts\windows\make_figures.bat --run-set manuscript-jobs8 --figures 5 6 7 8 9
if ($LASTEXITCODE -ne 0) { throw 'Campaign A+B plotting failed.' }
```

## 11. Run Campaign C and size ensembles

C holds x_A=0.20 and T*=0.75 fixed. Every seed still runs to 30000 common MCS with all nineteen observations:

| k | N | Exact seeds | Runs across eleven paths |
|---|---:|---|---:|
| 3 | 256 | 1–512 inclusive | 5632 |
| 4 | 2048 | 1–64 inclusive | 704 |
| 5 | 16384 | 1–8 inclusive | 88 |
| 6 | 131072 | 1 | 11, reused from A |

C contains 6435 identities and adds 6424 after A. Figure 10 requires every prescribed endpoint, including the k=6 A results.

```powershell
.\scripts\windows\run_campaigns.bat --run-set manuscript-jobs8 --campaign C --jobs 8 --resume
if ($LASTEXITCODE -ne 0) { throw 'Campaign C stopped; inspect status before continuing.' }
```

You may instead run/resume A–C together with `--campaign all`. Changing the selection does not duplicate scientific runs within the same dataset: the run identity is scenario, solver and seed, with shared states reused after verification.

## 12. Four independent execution examples and useful subsets

Each example below launches real calculations. Choose the examples you intend to measure; they are not four mandatory setup steps.

**Full matrix, manuscript maximum-eight policy:**

```powershell
.\scripts\windows\run_campaigns.bat --run-set manuscript-jobs8 --campaign all --jobs 8 --resume
```

**Full matrix, deliberate sequential comparison:**

```powershell
.\scripts\windows\run_campaigns.bat --run-set manuscript-sequential --campaign all --jobs 1 --resume
```

**Selected solvers, maximum-eight policy:**

```powershell
.\scripts\windows\run_campaigns.bat --run-set selected-jobs8 --campaign all --solvers binary partial-filter rate-category spparks-sweep --jobs 8 --resume
```

**The same selected solvers, sequential comparison:**

```powershell
.\scripts\windows\run_campaigns.bat --run-set selected-sequential --campaign all --solvers binary partial-filter rate-category spparks-sweep --jobs 1 --resume
```

Full solver IDs shown by `--list-solvers` are accepted. Friendly aliases are `classical`, `partial-filter`, `generic`, `binary`, `rate-category`, `exact-class`, `spparks-sweep`, `spparks-linear`, `spparks-tree`, `kmcos` and `kmc-lattice`. Here `binary` means Active-Filtered Binary; the canonical ID `nanokmc_bit_encoded` means Classical.

State aliases are `x20-t075`, `x20-t125`, `x40-t075`, `x40-t125` and `k3`/`k4`/`k5`/`k6`. `--sizes` selects k, not N. Selections intersect; seeds outside a state's prescribed ensemble are not invented. Preview examples:

```powershell
.\scripts\windows\run_campaigns.bat --run-set selected-jobs8 --campaign B --solvers binary spparks-sweep --states x40-t125 --seeds 1 --jobs 8 --dry-run
.\scripts\windows\run_campaigns.bat --run-set selected-jobs8 --campaign C --solvers binary --sizes 3 --seeds 1-8 --jobs 8 --dry-run
```

Remove `--dry-run` only when you intend to run those full trajectories. Space-separated lists and quoted comma lists, such as `--seeds '1,3,5'`, are supported. `--limit 1` limits the invocation to one **new full trajectory**; it does not shorten that trajectory. The small check in section 7 is the appropriate initialization test.

A selected-solver dataset remains incomplete for an eleven-path publication figure. You can later add the remaining required runs by resuming `--campaign all` in the same run-set with its original job policy. Processing never silently removes missing solvers or seeds.

## 13. Progress, interruption and strict resume

For a detailed read-only status report:

```powershell
.\scripts\windows\run_campaigns.bat --run-set manuscript-jobs8 --campaign all --status
```

Status distinguishes pending, running, failed/incomplete, complete and stale/corrupt records as applicable. Running-state diagnosis checks process identity and the operating-system lock; it does not accept an unsealed attempt as complete. A stale running record left after a terminated process is not proof that a job is still alive.

For lightweight progress during a live run, read the coordinator's latest report:

```powershell
Get-Content -LiteralPath '.\run-sets\manuscript-jobs8\logs\campaign_latest.json'
```

Detailed status verification may read many hashes and can add disk activity. Prefer the progress report during timing measurements and detailed verification between invocations. Worker and event-history logs are in the same run-set's `logs/` directory.

Press **Ctrl+C once and allow cleanup to finish**. The coordinator stops its owned workers and descendants. A completed trajectory is reusable only after all nineteen native/processed observations have passed validation and its completion seal has been published. Unfinished trajectories restart from their original seed in a new attempt directory; this is run-level resume, not restoration of a solver's internal checkpoint state.

Repeat the same selection and policy to resume:

```powershell
.\scripts\windows\run_campaigns.bat --run-set manuscript-jobs8 --campaign all --jobs 8 --resume
```

`--resume` is the normal behavior even when omitted. Existing results are reused only when their run identity, run-set, requested jobs, frozen sources, built artifacts, model, harness and environment fingerprints still match and their files validate. `--rerun` explicitly creates new attempts for selected runs while retaining old attempts. It does not change a run-set's job policy.

Do not copy completion files between run-sets, modify `run_set.json`, delete an operating-system lock file to force a second invocation, or change sources/configuration while a stage is active. Build, execution, processing and figure generation share a repository-level lock at `logs/campaign.lock`; use one active workflow invocation at a time. Different run-sets coexist on disk and can be run one after another. The lock file may remain after exit; the operating-system lock, not file existence, determines ownership.

If you rebuild, update the harness or change dependencies, stale results may no longer be accepted. Preserve the previous dataset and its provenance, investigate the change, then use a new consistent run-set or deliberately rerun the affected selection. Do not edit hashes or weaken checks to make old records pass.

## 14. Process complete results

After A, B and C are complete in one run-set:

```powershell
.\scripts\windows\process_results.bat --run-set manuscript-jobs8 --validate-only
if ($LASTEXITCODE -ne 0) { throw 'Input validation failed; no publication tables should be trusted.' }
.\scripts\windows\process_results.bat --run-set manuscript-jobs8
if ($LASTEXITCODE -ne 0) { throw 'Result processing failed.' }
```

The validation-only command is optional; the normal processing command performs the checks itself. `--dry-run` lists required coverage without processing actual results.

Processing infers the requested concurrency from sealed records. It requires the run-set's single policy and never merges timings from jobs 1 and jobs 8. It validates every required source, completion record, observation, seed, native file and processed file. Missing, duplicate, incomplete or stale data cause an error. No missing rows are filled with zero or silently dropped.

`--figures 5 7 8` requires A, `--figures 6 9` requires B, and `--figures 10` requires C. The default requires all figures and the full 6468-run union. Each successful processing invocation commits a manifest for its explicit figure selection; after adding more calculations, process the full desired figure set again. Old CSVs outside the current manifest are not automatically accepted as current results.

The scientific definitions are fixed: `rho_AB=N_AB/(6N)`, cutoff-12 matrix concentration, native executed-exchange counters, sixteen-point Figure 8 fits from 30 through 30000 common MCS, and Figure 10 arithmetic means with sample-SEM. No interpolation forces unequal native clocks to coincide.

## 15. Generate Figures 5–10 in Python

```powershell
.\scripts\windows\make_figures.bat --run-set manuscript-jobs8 --validate-only
if ($LASTEXITCODE -ne 0) { throw 'Figure inputs or provenance are invalid.' }
.\scripts\windows\make_figures.bat --run-set manuscript-jobs8
if ($LASTEXITCODE -ne 0) { throw 'Figure generation failed.' }
```

The optional validation-only command checks inputs without writing images. Normal generation also performs these checks. By default, all six figures are written as PDF and PNG using Matplotlib. Use `--figures 5 7 8`, `--format pdf`, `--format png` or `--dpi 300` where appropriate. A plot cannot bypass missing publication coverage or changed CSV/configuration hashes.

Morphology panels use the exported coordinates and Python rendering. RasMol-compatible XYZ export is retained as data; RasMol rendering is unnecessary. Blender has no role in the workflow.

For a sequential dataset, use its name consistently at both stages:

```powershell
.\scripts\windows\process_results.bat --run-set manuscript-sequential
if ($LASTEXITCODE -ne 0) { throw 'Sequential result processing failed.' }
.\scripts\windows\make_figures.bat --run-set manuscript-sequential
if ($LASTEXITCODE -ne 0) { throw 'Sequential figure generation failed.' }
```

## 16. Locate raw data, CSVs, XYZ configurations and figures

All paths below are relative to `run-sets/<name>/`, except the final row:

| Location | Contents |
|---|---|
| `run_set.json` | Dataset name and immutable requested-job policy |
| `results/raw/<24-character digest>/` | Canonical completion record and separate `attempt_<12-character UUID>/` directories; full scientific IDs are in manifests and command output |
| Each attempt's `native/` | Native input, output, counters, snapshots and solver diagnostics |
| Each attempt's `processed/` | Common metrics, timing, cluster and snapshot tables plus metadata |
| `results/processed/publication/standardized_checkpoints.csv` | Normalized observations, native progress/units, counters, runtime and provenance; 122892 rows for all figures |
| `results/processed/publication/cluster_distribution.csv` | Periodic A-cluster distributions used in publication processing |
| `results/processed/publication/scaling_endpoints.csv` | All 6435 size-scaling endpoint records when Figure 10 is included |
| `results/csv/` | Nine figure CSVs listed below, configuration manifest and `publication_manifest.json` |
| `results/rasmol/` | Thirteen configuration instances, each with complete separate A and B XYZ files; coordinates/box/source hashes recorded |
| `figures/` | Figures 5–10 as PDF/PNG and `figure_manifest.json` |
| `logs/` | Dataset status, event history and worker logs |
| Repository-level `logs/`, `build/` and smoke `results/` | Environment/source/build/smoke evidence; outside manuscript run-sets |

The nine CSV exports and complete row counts are:

| File | Rows |
|---|---:|
| `fig05_observables.csv` | 19 |
| `fig05_clusters.csv` | 55 |
| `fig06_correspondence.csv` | 2160 |
| `fig07_event_accounting.csv` | 11 |
| `fig08_runtime_horizon.csv` | 209 |
| `fig08_gamma.csv` | 11 |
| `fig09_runtime_screen.csv` | 44 |
| `fig10_size_scaling.csv` | 44 |
| `fig10_alpha.csv` | 11 |

`fig05_configurations.json` identifies the thirteen exports: all eleven paths at requested 3000 common MCS, plus Binary at 0 and 30000. This produces twenty-six species-split XYZ files without invoking RasMol. Keep configuration and publication manifests with the data; filenames alone do not establish source or execution identity.

## 17. Interpreting differences from the manuscript

The target is the specified model, observables, campaign and algorithms. Do not expect pixel-identical morphology or equal pointwise trajectories. NanoKMC, SPPARKS, kmcos and KMC_Lattice have different native initializers and random streams. Equal seed labels do not imply identical initial site assignments or exactly equal A counts. Requested observation labels correspond across solvers; realized native times and counters may differ.

Figure 6 uses repeated observations from four trajectories per comparator, not 72 independent runs. Figure 10 uses independent prescribed seeds within each size: 512, 64, 8 and 1. Its SD/SEM are unavailable at n=1; zero error bars would falsely imply measured certainty. Alpha is a fit over four finite sizes, not proof of asymptotic complexity.

Runtime differences depend on processor, operating system, background load, frequency/power management, compiler, runtime libraries and job overlap. Jobs 8 and jobs 1 are separate measurements. The harness preserves each solver's native timer boundaries and in-loop reporting; it does not replace them with launch-to-exit wall time or subtract estimated I/O overhead. Do not interpret a different wall-clock value alone as a model failure.

Two manuscript-provenance uncertainties remain explicit: the complete lineage from measured NanoKMC binaries to the frozen release is not established, and the evidenced SPPARKS build uses plain-MSYS GNU 15.3.0 although the manuscript states GNU 16.2.0 generally. The current main Python environment and explicit kmcos code-generation hash seed are declared reproduction settings; the manuscript values are not established. See [PROVENANCE.md](docs/PROVENANCE.md) and [KMCOS_BUILD_PROVENANCE.md](docs/KMCOS_BUILD_PROVENANCE.md).

## 18. Troubleshooting

| Symptom | Action |
|---|---|
| A batch launcher cannot find Python | Re-enter the clone root and set `BENCHMARK_PYTHON` to `.venv\Scripts\python.exe`; inspect local path configuration. |
| Environment version or package check fails | Install the exact locked interpreter/package in its intended environment. Read `logs/environment_check.json`; do not bypass the check or silently upgrade. |
| An exact compiler/package archive is unavailable | Follow the verified routes and qualifications in `docs/TOOLCHAINS.md`. Preserve the error; an arbitrary current MSYS2 package is not an equivalent lock. |
| Sources are missing or the pinned object is absent | Use `fetch_or_verify_sources.bat --fetch` for network acquisition, or configure an offline repository containing the exact commit. |
| Build fails, including a C++/Fortran compatibility error | Inspect the package build log and exact compiler route. Do not patch upstream scientific source or change flags ad hoc. |
| kmcos Python import-library preparation fails | Read `kmcos_python_import_library` in the environment report and the build error. Confirm the declared Python 3.10.11 DLL and pinned MSYS2 `gendef`/`dlltool` packages; rerun `build_all.bat` after correcting the environment. It regenerates an absent, stale or unverified library automatically. |
| kmcos f2py fails or the compiled extension cannot import | Check the dedicated CPython 3.10.11, NumPy 1.26.4, setuptools 65.5.0 environment, the import-library record in `logs/build_kmcos.json`, and declared UCRT64 runtime DLLs. |
| A projected path is too long, or a native snapshot is missing | Use a shorter local clone location and short run-set name before starting a fresh dataset. The native exporter may still fail at the traditional Windows path limit. Preserve an existing failed attempt for diagnosis. |
| A run-set is registered for another job limit | Choose another `--run-set`; do not edit `run_set.json` or merge execution policies. |
| Another workflow owns the lock | Let it finish or interrupt it normally and wait for cleanup. A leftover lock file alone does not mean the lock is held. |
| Status reports failed, incomplete, stale or corrupt | Inspect attempt/worker logs and identity differences. Correct the cause, then repeat the same run-set/job selection; only sealed unchanged completions are reused. |
| Status reports `stale-running` | The recorded process identity no longer owns a live run. Resume creates a new attempt from its original seed; do not relabel the old attempt complete. |
| Processing rejects missing solvers, states or seeds | Complete the selected figure's prescribed coverage. A four-solver subset cannot produce an eleven-solver manuscript figure. |
| Figure generation rejects a manifest or hash | Regenerate processing products from verified completed records, then plot. Do not hand-edit CSVs or manifests to suppress the error. |
| A source/configuration file changes during execution | Stop edits, inspect the rejected identity and rerun consistently. The loaded executable and recorded configuration must refer to the same declared inputs. |
| Storage fills or the machine restarts | Preserve existing data, restore sufficient space, check status and resume. Unsealed partial files are not accepted. |
| Runtime differs from the manuscript | Compare the recorded hardware, toolchains, concurrency and native timing definitions before drawing a scientific conclusion. |

## 19. Optional full-workflow launcher

The staged commands above are easier to inspect and diagnose. After dependencies are installed, sources acquired and local paths configured, the root launcher can orchestrate the same full workflow. Its dry run does not verify builds or launch calculations:

```powershell
.\reproduce_manuscript.bat --run-set manuscript-jobs8 --jobs 8 --dry-run
```

The next command **builds/verifies and starts the full A–C reproduction**, then processes and plots:

```powershell
.\reproduce_manuscript.bat --run-set manuscript-jobs8 --jobs 8 --build-jobs 4 --execute
if ($LASTEXITCODE -ne 0) { throw 'Reproduction stopped; inspect the failed stage before resuming.' }
```

`--collect-existing` may be added to verify existing builds instead of recompiling; `--smoke` adds the small initialization checks. The root launcher does not fetch missing upstream repositories. Run `fetch_or_verify_sources.bat --fetch` explicitly first. Running it without `--execute` or `--dry-run` displays help.
