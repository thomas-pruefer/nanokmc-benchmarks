# Benchmark models and upstream source boundary

This directory contains the benchmark-owned application code needed for the FCC Kawasaki model. Exact scientific definitions are in [MANUSCRIPT_REPRODUCTION_MAP.md](../MANUSCRIPT_REPRODUCTION_MAP.md); model-file hashes and upstream revisions are in [dependencies.lock.json](../dependencies.lock.json).

| Path | Role |
|---|---|
| `kmc_lattice/fcc_kawasaki_selective.cpp` | FCC application built against the KMC_Lattice framework: initialization, exchange events/rates, selective recalculation, observations and output |
| `kmcos/fcc_kawasaki_geometry.py` | FCC geometry, neighbourhoods and directed exchange templates |
| `kmcos/fcc_kawasaki_otf__build.py` | kmcos OTF model generation and compilation |
| `kmcos/run_kmcos_otf_scenario.py` | kmcos initialization, execution, observations and benchmark timing |
| `kmcos/__init__.py` | Python package marker |

KMC_Lattice v2.1.0 is acquired separately at commit `4bc153e28eed6d3b0d3e5106998070d064968452`. The C++ application links against that framework; upstream sources are not patched. The kmcos framework is acquired separately at commit `4442009d73032cbe39f8201370ad6d8927c7be4c`; neither upstream files nor generated scientific Fortran are patched.

NanoKMC uses the pinned v0.1.0 source and its paper-build CMake option. SPPARKS uses the pinned 08Oct25 source with its serial diffusion application and MPI stubs. Their benchmark inputs and observation collection are handled by `adapters/`; this directory does not replace their scientific implementations.

Source verification checks upstream tree hashes separately from benchmark model hashes. Generated kmcos files and all native build products belong under ignored `build/`. The Windows Python import-library preparation is documented in [KMCOS_BUILD_PROVENANCE.md](../docs/KMCOS_BUILD_PROVENANCE.md).

“Benchmark-owned” identifies the application/source boundary. Licensing and third-party distribution terms are stated separately in [LICENSING.md](../LICENSING.md) and [THIRD_PARTY.md](../docs/THIRD_PARTY.md).
