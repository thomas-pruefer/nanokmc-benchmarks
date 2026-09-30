# Source and build provenance

This repository identifies a reproducible source/build specification separately
from the incompletely archived executables measured for the manuscript. A
successful build or startup test does not establish measured-binary identity
or reproduce a scientific result.

## Frozen sources

| Software | Revision | Version |
|---|---|---|
| [NanoKMC](https://github.com/thomas-pruefer/NanoKMC) | `68764aa44c4d73a7d95f3094d031254d0f43e50d` | `v0.1.0` |
| [SPPARKS](https://github.com/spparks/spparks) | `fb933dd6d76600c8f0edb9a61449e1c538585134` | `08Oct25` |
| [kmcos](https://github.com/kmcos/kmcos) | `4442009d73032cbe39f8201370ad6d8927c7be4c` | internal package version `1.1.0`; OTF backend |
| [KMC_Lattice](https://github.com/MikeHeiber/KMC_Lattice) | `4bc153e28eed6d3b0d3e5106998070d064968452` | `v2.1.0` |

Schema 2 of `dependencies.lock.json` records each upstream URL, exact commit,
tag and canonical `tree_sha256` under `sources`. The tree digest is SHA-256 of
the compact, key-sorted JSON mapping from relative file paths to their byte sizes
and SHA-256 hashes. Verification computes that mapping from the actual source
files on every call and compares its digest with the committed lock. Changed,
missing or extra files fail verification and are never silently overwritten.

Acquisition exports Git objects with `git archive`; named tags must resolve to
the locked commits. `--fetch` explicitly enables acquisition, while existing
exports verify offline. Detailed per-file manifests are generated only under
ignored `build/source-manifests/`, with the verification report in
`logs/source_verification.json`. Generated reports are not verification inputs.
Status suppresses report writes so it cannot replace source or build evidence
while another process is executing.

The repository does not vendor solver exports or binaries. Builds verify frozen
files before and after invoking tools. SPPARKS receives a separate verified
staging copy under `build/` because its build generates headers and objects
inside the source directory. Scientific upstream sources are not patched.

The lock's `model_sha256` section separately identifies the benchmark-owned
KMC_Lattice FCC application and the three kmcos model/worker inputs. Builds check
those hashes independently of upstream revisions. The kmcos build also records
generated Fortran hashes.

## NanoKMC measured-binary limitation

The public `v0.1.0` tag is the source of builds. The source export associated
with the supplied manuscript executable has no recoverable originating Git
revision. That executable's SHA-256 is
`f04c5bf4876e224e6192bc5d3ad1be5846a8917a45f77c2719c60abd14e51723`.
That artifact contains GNU 16.2.0 markers and a Release/UCRT64 paper-build
configuration. The manuscript run records contain launch paths but do not contain
an immutable per-run executable hash and source/build chain. Consequently, the
inspected artifact cannot be assigned conclusively to every manuscript timing.

Builds use the unchanged release CMake project with
`NANOKMC_PAPER_BUILD=ON`. Compilation includes `-std=c++17`, `-DNDEBUG` and a final
`-O1` after CMake's Release `-O3`; effective compilation optimization is O1.
Linking keeps upstream Release defaults without LTO. The lineage from measured
executables to the tagged release remains unresolved when comparing results.

## SPPARKS compiler discrepancy

The manuscript hardware table gives GNU C++/Fortran 16.2.0 generally. The supplied
SPPARKS artifact instead contains `GCC: (GNU) 15.3.0`; its inspected SHA-256 is
`24e06482cf88e509baed4d0581b27080202fba99043faba36ef70fe8cf9c3a7d`.
The associated build script uses plain MSYS `g++`, whose POSIX/GNU environment is
distinct from UCRT64. The pinned build therefore preserves plain MSYS GCC 15.3.0,
the upstream serial Makefile/MPI stubs, `CCFLAGS='-O1 -std=gnu++17'` and
`LINKFLAGS='-O1'`.

The per-run executable/build chain is incomplete, so the compiler discrepancy
remains unresolved. A shell's `MSYSTEM=UCRT64` launch setting is not compiler
provenance. Author clarification or an immutable artifact chain is required to
establish the exact environment of each manuscript timing.

## Toolchains, evidence and timing

[TOOLCHAINS.md](TOOLCHAINS.md) describes the exact compiler/Python requirements,
archive acquisition and its limits. [KMCOS_BUILD_PROVENANCE.md](KMCOS_BUILD_PROVENANCE.md)
describes code generation, f2py and Fortran flags.

Each build writes compiler versions/hashes, commands, source identities, executable
hashes and imported DLL names to ignored `logs/build_*.json`; full command logs
and PE import listings accompany them. `logs/environment_check.json` records
observed host, interpreter, package and runtime-library identities. Imported DLL
names alone are not a complete loaded-library trace. Generated evidence belongs
to the run that produced it and is not bundled as a promise about future builds.

The manuscript analysis/plotting package versions, per-run hardware/power/load
state and complete package dependency closure remain unestablished. The pinned
analysis harness is an explicit reproduction environment. The kmcos generator
uses the locked `PYTHONHASHSEED=0` and verifies repeated source generation; the
hash seed used for manuscript builds was not recorded. These settings do not
establish the manuscript's complete environment identity. Manuscript-style execution
uses up to eight independent single-thread jobs (`--jobs 8`); `--jobs 1` is a
deliberate sequential comparison. Keep different policies in different named
run sets. Neither source matching nor a common host guarantees identical
stochastic trajectories or wall-clock timings.
