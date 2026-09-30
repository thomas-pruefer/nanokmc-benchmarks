"""Check the locked benchmark and solver environments without changing them."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone

from kmcos_import_library import inspect as inspect_kmcos_import_library
from fetch_or_verify_sources import load_dependency_lock
from benchmark_core.local_config import read_local_config

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(argv: list[str], env: dict[str, str]) -> dict:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", env=env, timeout=45)
        return {"command": argv, "returncode": result.returncode,
                "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": argv, "returncode": None, "error": str(exc)}


def check(paths_file: Path) -> dict:
    paths = read_local_config(paths_file)
    if not paths.get("kmcos_python"):
        raise ValueError("Local paths configuration must specify kmcos_python for the dedicated Python 3.10.11 environment.")
    lock = load_dependency_lock()
    msys = Path(paths.get("msys2_root", "C:/msys64"))
    environment = os.environ.copy()
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
                        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                        "MKL_NUM_THREADS": "1"})
    environment["PATH"] = str(msys / "ucrt64/bin") + os.pathsep + str(msys / "usr/bin") + os.pathsep + environment.get("PATH", "")
    errors: list[str] = []
    tools: dict[str, dict] = {}
    specs = {
        "ucrt64_cpp": (msys / "ucrt64/bin/g++.exe", ["-dumpfullversion"], "16.2.0"),
        "ucrt64_fortran": (msys / "ucrt64/bin/gfortran.exe", ["-dumpfullversion"], "16.2.0"),
        "ucrt64_c": (msys / "ucrt64/bin/gcc.exe", ["-dumpfullversion"], "16.2.0"),
        "plain_msys_cpp": (msys / "usr/bin/g++.exe", ["-dumpfullversion"], "15.3.0"),
        "cmake": (msys / "ucrt64/bin/cmake.exe", ["--version"], "4.4.2"),
        "ninja": (msys / "ucrt64/bin/ninja.exe", ["--version"], "1.13.2"),
        "make": (msys / "usr/bin/make.exe", ["--version"], "4.4.1"),
    }
    for name, (exe, args, expected) in specs.items():
        result = probe([str(exe), *args], environment)
        result["required_version"] = expected
        version_line = result.get("stdout", "").split("\n")[0]
        versions = re.findall(r"(?<![\d.])\d+\.\d+(?:\.\d+)+(?![\d.])", version_line)
        result["verified"] = result.get("returncode") == 0 and expected in versions
        result["sha256"] = digest(exe) if exe.is_file() else None
        tools[name] = result
        if not result["verified"]:
            errors.append(f"{name}: required version {expected} not verified")
    for name in ["bash", "zip", "unzip"]:
        exe = msys / f"usr/bin/{name}.exe"
        tools[name] = {"path": str(exe), "exists": exe.is_file(), "sha256": digest(exe) if exe.is_file() else None}
        if not exe.is_file():
            errors.append(f"Required archive/runtime utility absent: {exe}")
    mpi = msys / "ucrt64/include/mpi.h"
    tools["msmpi_header"] = {"path": str(mpi), "exists": mpi.is_file()}
    if not mpi.is_file():
        errors.append("KMC_Lattice requires the declared MS-MPI headers")
    runtime_libraries = {}
    for relative in ("ucrt64/bin/libgcc_s_seh-1.dll", "ucrt64/bin/libstdc++-6.dll",
                     "ucrt64/bin/libgfortran-5.dll", "ucrt64/bin/libquadmath-0.dll",
                     "ucrt64/bin/libwinpthread-1.dll", "usr/bin/msys-2.0.dll",
                     "usr/bin/msys-gcc_s-seh-1.dll", "usr/bin/msys-stdc++-6.dll"):
        library = msys / relative
        runtime_libraries[relative] = {"path": str(library), "exists": library.is_file(),
                                       "sha256": digest(library) if library.is_file() else None}
        if not library.is_file():
            errors.append(f"Required solver runtime library absent: {library}")

    main_packages = {}
    for name, required in lock["environments"]["benchmark"]["packages"].items():
        try:
            observed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            observed = None
        main_packages[name] = {"required": required, "observed": observed}
        if observed != required:
            errors.append(f"Benchmark package {name}: {observed!r}, expected {required}")
    if platform.python_version() != lock["environments"]["benchmark"]["python"]:
        errors.append("Benchmark Python does not match the standalone harness lock; use .venv/Scripts/python.exe")

    kmcos_names = list(lock["environments"]["kmcos"]["packages"])
    python_code = ("import json,platform,sys,importlib.metadata as m; "
                   "import numpy,ase,lxml,scipy; "
                   f"print(json.dumps({{'python':platform.python_version(),'executable':sys.executable,'base_prefix':sys.base_prefix,'packages':{{n:m.version(n) for n in {kmcos_names!r}}}}}))")
    kmcos_python = Path(paths["kmcos_python"])
    if not kmcos_python.is_absolute():
        kmcos_python = ROOT / kmcos_python
    kmcos = probe([str(kmcos_python), "-B", "-c", python_code], environment)
    kmcos["verified"] = False
    if kmcos.get("returncode") == 0:
        try:
            observed = json.loads(kmcos["stdout"])
            kmcos["observed"] = observed
            kmcos["verified"] = observed["python"] == lock["environments"]["kmcos"]["python"] and observed["packages"] == lock["environments"]["kmcos"]["packages"]
        except (ValueError, KeyError):
            pass
    if not kmcos["verified"]:
        errors.append("The exact kmcos Python/packages could not be verified; inspect the probe, including permission errors")
    try:
        tools["kmcos_python_import_library"] = inspect_kmcos_import_library(
            kmcos_python, msys, env=environment,
            expected_packages=lock["toolchains"]["ucrt64_python_import_library"]["packages"])
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        tools["kmcos_python_import_library"] = {"verified": False, "error": str(exc)}
        errors.append(f"kmcos Windows import-library prerequisites: {exc}")

    host = {"system": platform.system(), "release": platform.release(), "version": platform.version(),
            "machine": platform.machine(), "hostname": socket.gethostname(),
            "processor": platform.processor(), "logical_cpu_count": os.cpu_count()}
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                host["processor_name"] = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            host["processor_name"] = "unavailable"
    return {
        "schema_version": 1, "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "failed" if errors else "verified_with_documented_provenance_discrepancies",
        "environment_verified": not errors,
        "historical_environment_reproduced": False,
        "source_lock_sha256": digest(ROOT / "dependencies.lock.json"),
        "host": host,
        "host_scope": "Observed hostname/OS/CPU; no guarantee of unchanged load, power management or thermal state during trajectories",
        "benchmark_python": {"executable": sys.executable, "version": platform.python_version(), "packages": main_packages},
        "tools": tools, "runtime_libraries_observed": runtime_libraries,
        "runtime_library_scope": "Declared MSYS2 runtime files; not a Windows loaded-module trace or historical DLL proof",
        "kmcos_python": kmcos, "errors": errors,
        "discrepancies": [
            "SPPARKS uses the evidenced unmodified plain-MSYS GCC15.3.0 route; the manuscript blanket16.2.0 claim remains unresolved.",
            "The NanoKMC v0.1.0 release build is separately identified from the unverified historical exported binary lineage.",
            "Benchmark Python/package pins describe this new verified harness, not recovered historical plotting versions."
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", type=Path, default=ROOT / "config/paths.local.json")
    args = parser.parse_args()
    if not args.paths.is_file():
        parser.error(f"Local paths file is missing: {args.paths}. Copy config/paths.example.json to config/paths.local.json and configure it as described in HOW_TO_REPRODUCE.md.")
    try:
        result = check(args.paths)
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(f"Cannot verify the configured environment: {error}")
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs/environment_check.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(result["status"])
    for error in result["errors"]:
        print("ERROR:", error)
    for discrepancy in result["discrepancies"]:
        print("PROVENANCE:", discrepancy)
    return 0 if result["environment_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
