# Toolchain installation

The supported build path is 64-bit Windows with MSYS2. Compiler families are
not interchangeable: NanoKMC and KMC_Lattice use UCRT64 C++, kmcos uses UCRT64
Fortran/C, and the evidenced SPPARKS build uses plain MSYS C++.

| Component | Required version |
|---|---|
| UCRT64 GCC/GFortran | 16.2.0, MSYS2 revision 3 |
| Plain MSYS GCC | 15.3.0, package revision 1 |
| CMake / Ninja / GNU Make | 4.4.2 / 1.13.2 / 4.4.1 |
| Analysis/orchestration CPython | 3.14.4, AMD64 |
| Dedicated kmcos CPython | 3.10.11, AMD64 |
| kmcos NumPy / setuptools | 1.26.4 / 65.5.0 |
| UCRT64 `gendef` / `dlltool` | `mingw-w64-ucrt-x86_64-tools` 14.0.0.r283.ga7cb47123-1 / `mingw-w64-ucrt-x86_64-binutils` 2.47-3 |

Full Python pins are in `requirements.lock.txt` and
`requirements-kmcos.lock.txt`; versions are also checked against
`dependencies.lock.json`. Do not silently change the locks to accept a newer
installation. These versions define the reproduction environments; they do not identify
the unavailable complete manuscript analysis-package environment.

## Python

Use the **Windows installer (64-bit)** from the official release pages for
[CPython 3.14.4](https://www.python.org/downloads/release/python-3144/) and
[CPython 3.10.11](https://www.python.org/downloads/release/python-31011/).
Select the conventional AMD64 interpreter, not ARM64, an embeddable distribution
or the optional free-threaded build. Point the two environment-creation commands
in [HOW_TO_REPRODUCE.md](../HOW_TO_REPRODUCE.md) to those exact interpreters.
A command such as `py -3.14` can select another patch release; the guide checks the exact version before creating each environment. Both official pages offer conventional 64-bit installers even when the main Windows download button points to the Python install manager.

## Exact MSYS2 archives

MSYS2 is rolling-release software. An ordinary current `pacman -S` transaction
does not promise the required revisions. MSYS2 documents installation of retained
package archives using `pacman -U`, with compatible dependencies selected as
necessary. See [MSYS2 package management](https://www.msys2.org/docs/package-management/).

`config/msys2-packages.lock.json` records the sixteen explicitly declared
packages, archive URLs, sizes and SHA-256 hashes from retained package bytes.
Archive availability is not guaranteed, and this lock alone does not specify a
complete MSYS2 installation. The archives are hosted
by the official [UCRT64](https://repo.msys2.org/mingw/ucrt64/) and
[MSYS x86_64](https://repo.msys2.org/msys/x86_64/) repositories.

Use a **dedicated MSYS2 installation** for this benchmark. Install the x86_64
distribution and initialize its base environment and trusted package keyrings
using the [official MSYS2 instructions](https://www.msys2.org/). Follow its
restart instructions when the runtime is updated. Configure that installation's
root and Bash paths in `config/paths.local.json`; the example uses `C:/msys64`.
Keep its compiler packages at the locked versions for builds and measurements.

The following PowerShell commands, run at the repository root, download the
declared packages and detached signatures and verify their recorded hashes.
They do not install packages:

```powershell
$ErrorActionPreference = 'Stop'
$archiveDir = Join-Path (Get-Location) 'build/toolchain-archives'
New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
$packageLock = Get-Content '.\config\msys2-packages.lock.json' -Raw | ConvertFrom-Json
foreach ($package in $packageLock.packages) {
    $archive = Join-Path $archiveDir $package.filename
    Invoke-WebRequest -Uri $package.url -OutFile $archive
    if ((Get-Item -LiteralPath $archive).Length -ne $package.size_bytes) {
        throw "Archive size mismatch: $($package.filename)"
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $package.sha256) {
        throw "Archive hash mismatch: $($package.filename)"
    }
    Invoke-WebRequest -Uri $package.signature_url -OutFile ($archive + '.sig')
    if ((Get-FileHash -LiteralPath ($archive + '.sig') -Algorithm SHA256).Hash.ToLowerInvariant() -ne $package.signature_sha256) {
        throw "Signature-file hash mismatch: $($package.filename)"
    }
}
```

After configuring `msys2_root` in the ignored local paths file, this command
asks pacman to install that declared set. **It changes the dedicated MSYS2
installation.** Inspect the proposed transaction and retain signature checking:

```powershell
$localPaths = Get-Content '.\config\paths.local.json' -Raw | ConvertFrom-Json
$bash = Join-Path $localPaths.msys2_root 'usr/bin/bash.exe'
$archiveDir = (Resolve-Path '.\build\toolchain-archives').Path
& $bash --noprofile --norc -c 'export PATH=/usr/bin:/bin:$PATH; cd "$(cygpath -u "$1")" && pacman -U -- *.pkg.tar.zst' 'install-pinned-toolchain' $archiveDir
if ($LASTEXITCODE -ne 0) { throw 'Pinned package installation failed; inspect dependency/signature diagnostics.' }
```

Package hashes identify retained bytes; they do not replace signature
verification against MSYS2's trusted keyring. If an archive is unavailable,
a signature fails or dependency versions conflict, stop and record the failure.
Do not disable signature/dependency checks, force overwrites or substitute
another compiler version. Obtain a compatible dependency set from the official
archive or a properly retained environment before continuing.

The sixteen-package manifest includes CMake, Ninja, Make, zip/unzip, MS-MPI
headers, compiler/runtime packages and the `gendef`/`dlltool` packages. It is
**not a complete transitive dependency or OS image lock**: compatible MSYS2/UCRT
headers, compression libraries and other dependencies are also required. Let
pacman check dependency consistency and signatures. Keep its installation
output with the build evidence; a successful package transaction must still be
followed by the repository's environment check.

Python requirements pin versions, not every downloaded wheel or source archive.
The complete manuscript dependency closure is unavailable. Local build/smoke
validation establishes the declared toolchain route on the tested host; it does
not establish bit-identical reconstruction of the manuscript environment or
continued availability of every external archive.

## Build controls and evidence

Environment verification rejects mismatched compiler, Python and package
versions. NanoKMC uses the upstream paper-build CMake option (effective C++
`-O1`); SPPARKS uses serial GNU C++17 `-O1`; KMC_Lattice uses GNU C++17 `-O1`;
kmcos preserves its scientific Fortran `-O3` flags. Exact flags are recorded in
`dependencies.lock.json` and the generated build reports.

On Windows, NumPy/F2PY's MinGW route also needs an import library for the
dedicated CPython 3.10 DLL. Environment checking verifies a readable `python310.dll`, UCRT64
`gcc`, `gendef`, `dlltool`, and the pinned packages that own the latter two tools.
The kmcos build derives the Python base and virtual-environment paths from that interpreter,
then builds `.venv-kmcos/libs/libpython310.a` automatically when no verified
matching library exists. The generated definition, library and hash record are
local build artifacts. An existing file without a matching identity record is
regenerated. This affects Python linking only; it does not patch solver source.

Run `scripts/windows/check_environment.bat` before compilation, then inspect
`logs/environment_check.json`. Archive the environment/build evidence with any
results. An environment check establishes the observed locked versions; it does
not resolve the historical NanoKMC binary-lineage or SPPARKS compiler discrepancies
described in [PROVENANCE.md](PROVENANCE.md).
