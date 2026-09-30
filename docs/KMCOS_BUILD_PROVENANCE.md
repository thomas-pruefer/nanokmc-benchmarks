# kmcos OTF build provenance

The framework revision is `4442009d73032cbe39f8201370ad6d8927c7be4c`
(internal package version `1.1.0`). Exact Git-archive file hashes are checked
before and after the build. Neither upstream files nor generated scientific
Fortran are patched.

The benchmark model consists of `models/kmcos/fcc_kawasaki_geometry.py`,
`fcc_kawasaki_otf__build.py` and `run_kmcos_otf_scenario.py`. It retains the
48 directed nearest-neighbour templates, 7+7 unique environment partition,
initialization, rates, clock, counters, output sampling and timed intervals of
the supplied manuscript benchmark. Repository-relative imports and output paths
route generated files under `build/kmcos/`.

## Environment and flags

The dedicated runtime uses CPython **3.10.11** (AMD64), NumPy **1.26.4**,
setuptools **65.5.0**, ASE **3.29.0**, lxml **6.1.2**, SciPy **1.15.3**,
Matplotlib **3.10.9**, and UCRT64 GCC/GFortran **16.2.0**. Complete Python package
pins are in `dependencies.lock.json` and `requirements-kmcos.lock.txt`. The
analysis harness uses a separate environment. These requirements define the
current build; the manuscript's complete per-run Python dependency chain is
unavailable.

The unchanged f2py compiler-family arguments are
`--fcompiler=gnu95 --compiler=mingw32`. Explicit Fortran flags are:

```text
-ffree-line-length-none -ffree-form -xf95-cpp-input -Wall -O3 -fmax-identifier-length=63
```

NumPy adds `-O3 -funroll-loops` to the Fortran build; its generated C wrapper
retains `-g -DDEBUG -DMS_WIN64 -O0`. The manuscript's O3 description concerns the
scientific Fortran route, not every wrapper translation unit. GNU95 selects a
compiler family, not a compiler version. No additional global
`-fimplicit-none` flag is introduced.

## Windows Python import-library preparation

NumPy 1.26.4's Windows-MinGW linking step needs `libpython310.a` for the selected
CPython 3.10.11 DLL. The build wrapper queries that interpreter's `sys.prefix`
and `sys.base_prefix`, verifies the actual `python310.dll`, then uses the pinned
UCRT64 `gendef` and `dlltool` packages to generate a definition and AMD64 import
library under the dedicated environment's `libs/` directory. No user path is
hard-coded. This is an F2PY build compatibility step; no kmcos scientific code or
generated Fortran semantics are modified.

The identity record `libs/libpython310.build.json` contains Python executable and
DLL paths/hashes, tool paths/hashes and installed package versions, generated
file hashes, target DLL name and generation time. Reuse requires all identities
and output hashes to match and `dlltool` to identify the expected DLL; otherwise
the files are safely regenerated. `logs/build_kmcos.json` embeds the same evidence.

## Code-generation repeatability

Upstream generation iterates a Python set when emitting mutually exclusive
A/B `select case` branches. Python hash randomization can therefore alter the
order of emitted branches. The wrapper explicitly uses `PYTHONHASHSEED=0`, then
repeats source generation and compares exported Fortran bytes before reporting
success. It neither compiles twice nor edits generated solver code. The generator
hash seed used for the manuscript measurements was not recorded, so byte-identical
manuscript executables or trajectories are not established by this check.

## Recorded build evidence

`scripts/build_kmcos.py` verifies source bytes and exact interpreter/package/compiler
versions, invokes the model generator, validates the compiled extension import,
records input/generated artifact hashes and checks repeatable generation.
Git is needed only to acquire sources; an already exported, verified source tree
is sufficient for compilation.

The generated runtime directory is `build/kmcos/fcc_kawasaki_otf_otf/src`.
`logs/build_kmcos.json` records environment, commands, flags, result, artifact
hashes and observed Python/GNU runtime DLLs. Full compiler output is in
`logs/build_kmcos.log`. Temporary compiler and auxiliary generation files remain
inside `build/kmcos/`. Startup/conservation tests are separate from compilation;
neither establishes manuscript result reproduction.
