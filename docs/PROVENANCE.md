# Source, build and measurement provenance

The repository locks the source and build specification used for reproduction. The manuscript's complete per-run executable/environment chain is unavailable; source matching and successful builds do not establish identity with every measured manuscript binary.

## Pinned sources

| Software | Exact commit | Version |
|---|---|---|
| [NanoKMC](https://github.com/thomas-pruefer/NanoKMC) | `68764aa44c4d73a7d95f3094d031254d0f43e50d` | `v0.1.0` |
| [SPPARKS](https://github.com/spparks/spparks) | `fb933dd6d76600c8f0edb9a61449e1c538585134` | `08Oct25` |
| [kmcos](https://github.com/kmcos/kmcos) | `4442009d73032cbe39f8201370ad6d8927c7be4c` | Internal package version `1.1.0`; OTF backend |
| [KMC_Lattice](https://github.com/MikeHeiber/KMC_Lattice) | `4bc153e28eed6d3b0d3e5106998070d064968452` | `v2.1.0` |

[dependencies.lock.json](../dependencies.lock.json) records upstream URLs, commits, tags and canonical tree digests. A tree digest is SHA-256 of the compact, key-sorted JSON mapping from each relative file path to its byte size and SHA-256. Verification hashes the actual complete source tree; missing, modified or additional files fail. Generated manifests cannot authorize different source bytes.

Acquisition exports exact Git objects with `git archive`; named tags must resolve to the locked commits. `--fetch` permits network acquisition. Existing exports verify offline. No upstream solver sources or binaries are bundled in the repository. Builds verify upstream bytes before and after compilation. SPPARKS builds in a separate verified staging copy because its build creates files in its source directory; scientific source is not patched.

The lock separately records hashes of the benchmark-owned KMC_Lattice application and kmcos model/worker inputs. The kmcos build also hashes generated Fortran and repeats generation under `PYTHONHASHSEED=0`. See [models/README.md](../models/README.md) and [KMCOS_BUILD_PROVENANCE.md](KMCOS_BUILD_PROVENANCE.md).

## Build specification

| Package | Compiler route and scientific flags |
|---|---|
| NanoKMC | UCRT64 GNU C++ 16.2.0; unchanged Release CMake project with `NANOKMC_PAPER_BUILD=ON`, C++17, `-DNDEBUG`, effective `-O1` after Release `-O3`; upstream link defaults, no LTO |
| SPPARKS | Plain-MSYS GNU C++ 15.3.0; serial Makefile/MPI stubs, `CCFLAGS='-O1 -std=gnu++17'`, `LINKFLAGS='-O1'` |
| KMC_Lattice | UCRT64 GNU C++ 16.2.0; `-O1 -std=gnu++17 -Wall -Wextra` |
| kmcos | UCRT64 GNU Fortran/C 16.2.0; dedicated CPython 3.10.11 and NumPy 1.26.4 F2PY, scientific Fortran `-O3` with the locked flags |

The main harness uses CPython 3.14.4 and the analysis-package lock. [TOOLCHAINS.md](TOOLCHAINS.md) describes installation, exact package archives and dependency-closure limits. A shell's `MSYSTEM` label does not identify the compiler actually invoked.

## Reproducibility qualifications

- **NanoKMC measured-binary lineage:** builds use public tag `v0.1.0`. The complete connection between that tag and every executable measured in the manuscript is not established: the available run records do not contain immutable per-run executable hashes with a source/build chain. The release build must therefore be identified independently of the manuscript timings.
- **SPPARKS compiler attribution:** the manuscript reports GNU C++/Fortran 16.2.0 generally, whereas the available SPPARKS executable and build route identify plain-MSYS GNU 15.3.0. This repository pins that evidenced route. The complete per-run chain is unavailable, so the discrepancy remains unresolved.
- **Python and generated code:** the analysis-package lock is the declared reproduction environment; manuscript plotting-package versions were not recorded. The kmcos generator's manuscript hash seed was not recorded. Explicit `PYTHONHASHSEED=0` and repeated generation establish current repeatability, not byte identity with manuscript executables.
- **System environment:** the toolchain package list is not a complete operating-system or transitive dependency lock. Per-run power, thermal and background-load states and the full loaded-library chain are unavailable.

Different native initializers and random streams may yield different microscopic trajectories. Wall-clock results depend on hardware, compilers, runtime libraries and concurrency. The reference execution policy allows up to eight independent single-thread jobs; sequential runs use a separate run-set. Neither matching sources nor a common host guarantees identical trajectories or timings.

## Recorded evidence and timing

Local `logs/environment_check.json` records observed host, interpreter, package, compiler and declared runtime-library identities. `logs/build_*.json` and companion command/import logs record source hashes, compiler versions/hashes, commands, built artifacts and imported DLL names. Those names are not a complete loaded-library trace. Source reports are written to `logs/source_verification.json` and `build/source-manifests/`; read-only status checks do not replace them.

Each run seals its inputs, execution policy, native/processed data and implementation identity. Keep this evidence with the results. The [scientific contract](../MANUSCRIPT_REPRODUCTION_MAP.md#observables-and-native-timing) defines the path-specific timing boundaries: no processing step substitutes process duration for the native timer or subtracts estimated output overhead. Build/startup success and internal validation remain distinct from running and assessing the scientific reproduction.
