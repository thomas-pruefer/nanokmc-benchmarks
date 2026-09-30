# Third-party software and attribution

The benchmark uses the following independently maintained solver packages.
Full revision hashes are recorded in [PROVENANCE.md](PROVENANCE.md) and
`dependencies.lock.json`. Upstream license files are preserved by exact source
export and must remain with any redistributed copies.

| Software | Pinned release / revision | License evidence at that revision |
|---|---|---|
| [NanoKMC](https://github.com/thomas-pruefer/NanoKMC) | `v0.1.0` | `LICENSE`: BSD 3-Clause; copyright 2026 Thomas Prüfer |
| [SPPARKS](https://github.com/spparks/spparks) | `08Oct25` | `LICENSE`: GNU GPL version 2; source notices identify National Technology & Engineering Solutions of Sandia, LLC and retained US Government rights |
| [kmcos](https://github.com/kmcos/kmcos) | `4442009d73032cbe39f8201370ad6d8927c7be4c` | `COPYING`: GNU GPL version 3; upstream README identifies its creator, maintainers and developers |
| [KMC_Lattice](https://github.com/MikeHeiber/KMC_Lattice) | `v2.1.0` | `LICENSE`: MIT; copyright 2017–2020 Michael C. Heiber |

These license descriptions report the supplied upstream notices; they are not a
repository-wide grant or a determination of the licensing of combined works.
Generated kmcos code and linked executables are build products, not part of this
source repository. Preserve applicable notices and review distribution obligations
before distributing those products.

Python, NumPy/f2py, pandas, Matplotlib, ASE, SciPy, lxml, GNU/MSYS2, CMake, Ninja
and the remaining packages are separately installed dependencies. Exact package
identities are recorded in the environment locks; their own distributions carry
their licenses.

For NanoKMC, cite Thomas Prüfer, *NanoKMC*, version 0.1.0 (2026),
[doi:10.5281/zenodo.22916915](https://doi.org/10.5281/zenodo.22916915).
Consult each other upstream project's citation instructions when reporting work
using it. The benchmark repository's citation metadata is in `CITATION.cff`;
its separate release-license blocker is described in [LICENSING.md](../LICENSING.md).