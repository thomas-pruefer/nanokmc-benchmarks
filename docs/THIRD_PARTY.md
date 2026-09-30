# Third-party software

The four solver packages are fetched separately into ignored `sources/` directories.
Their source files and license notices are preserved unchanged. The benchmark
repository's license does not replace these upstream terms.

| Project | Release and exact commit | Upstream license and attribution |
|---|---|---|
| [NanoKMC](https://github.com/thomas-pruefer/NanoKMC) | `v0.1.0` · `68764aa44c4d73a7d95f3094d031254d0f43e50d` | [BSD 3-Clause](https://github.com/thomas-pruefer/NanoKMC/blob/68764aa44c4d73a7d95f3094d031254d0f43e50d/LICENSE); copyright 2026 Thomas Prüfer |
| [SPPARKS](https://github.com/spparks/spparks) | `08Oct25` · `fb933dd6d76600c8f0edb9a61449e1c538585134` | [GNU GPL version 2](https://github.com/spparks/spparks/blob/fb933dd6d76600c8f0edb9a61449e1c538585134/LICENSE); source notices identify National Technology & Engineering Solutions of Sandia, LLC and retained U.S. Government rights |
| [kmcos](https://github.com/kmcos/kmcos) | `4442009d73032cbe39f8201370ad6d8927c7be4c` | [GNU GPL version 3](https://github.com/kmcos/kmcos/blob/4442009d73032cbe39f8201370ad6d8927c7be4c/COPYING); package source permits version 3 or later. Created by Max J. Hoffmann; [upstream contributor list](https://github.com/kmcos/kmcos/blob/4442009d73032cbe39f8201370ad6d8927c7be4c/README.rst) |
| [KMC_Lattice](https://github.com/MikeHeiber/KMC_Lattice) | `v2.1.0` · `4bc153e28eed6d3b0d3e5106998070d064968452` | [MIT](https://github.com/MikeHeiber/KMC_Lattice/blob/4bc153e28eed6d3b0d3e5106998070d064968452/LICENSE); copyright 2017–2020 Michael C. Heiber |

The benchmark-owned FCC applications are described in [models/README.md](../models/README.md).
Generated kmcos Fortran, compiled Python extensions and linked executables are
build products, not distributed repository source. Any distribution of these
products must comply with the applicable upstream terms, including required
source and license notices. No MIT grant for benchmark-owned files relicenses
the upstream packages or their generated code.

Python, NumPy/f2py, pandas, Matplotlib, ASE, SciPy, lxml, GNU/MSYS2, CMake, Ninja
and the other separately installed dependencies retain the licenses supplied by
their own distributions. Exact identities are recorded in the dependency locks.

For NanoKMC, cite Thomas Prüfer, *NanoKMC*, version 0.1.0 (2026),
[doi:10.5281/zenodo.22916915](https://doi.org/10.5281/zenodo.22916915).
Follow the other upstream projects' citation instructions when reporting work
using them. [CITATION.cff](../CITATION.cff) distinguishes the benchmark repository,
NanoKMC software release and accompanying manuscript.
