"""Build the frozen benchmark-owned FCC OTF model with the declared Windows environment.

This wrapper verifies source bytes and exact Python/NumPy/compiler versions,
uses the locked generator and f2py flags, and records generated artifacts.
It neither patches upstream kmcos nor runs a manuscript campaign.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from fetch_or_verify_sources import ROOT, load_paths, locked_model_hash, verify_frozen_export
from kmcos_import_library import prepare as prepare_import_library

REQUIRED_PACKAGES = {
    "numpy": "1.26.4", "setuptools": "65.5.0", "ase": "3.29.0",
    "lxml": "6.1.2", "scipy": "1.15.3", "matplotlib": "3.10.9",
}
F90FLAGS = "-ffree-line-length-none -ffree-form -xf95-cpp-input -Wall -O3 -fmax-identifier-length=63"


def file_record(path: Path) -> dict:
    return {"path": str(path), "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def build(paths=None) -> dict:
    config = load_paths(paths)
    # Builds consume the already exported, hash-locked source tree. Git is only
    # needed by stage 2 when acquiring/exporting absent sources, never here.
    source = verify_frozen_export("kmcos")
    for name in ("fcc_kawasaki_geometry.py", "fcc_kawasaki_otf__build.py", "run_kmcos_otf_scenario.py"):
        relative = "models/kmcos/" + name
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != locked_model_hash(relative):
            raise RuntimeError(f"Benchmark model differs from the dependency lock: {relative}")
    python = Path(config.get("kmcos_python", ROOT / ".venv-kmcos/Scripts/python.exe")).resolve()
    ucrt = Path(config.get("msys2_root", "C:/msys64")).resolve() / "ucrt64/bin"
    output = (ROOT / "build/kmcos").resolve()
    # The original generator removes only its model-specific generated export.
    # Fixing its parent here confines every generated or removed file to build/.
    if not output.is_relative_to((ROOT / "build").resolve()):
        raise RuntimeError("kmcos build output must remain inside this repository's build directory")
    output.mkdir(parents=True, exist_ok=True)
    temp_dir = output / "tmp"
    temp_dir.mkdir(exist_ok=True)
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "PYTHONHASHSEED": "0", "TEMP": str(temp_dir),
                "TMP": str(temp_dir), "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1", "PATH": str(ucrt) + os.pathsep + env.get("PATH", "")})
    probe = (
        "import sys,json,importlib.metadata as m; import numpy,lxml,ase,scipy; "
        "print(json.dumps({'python':sys.version.split()[0], 'executable':sys.executable, 'base_prefix':sys.base_prefix,"
        "'packages':{p:m.version(p) for p in " + repr(list(REQUIRED_PACKAGES)) + "}}))"
    )
    environment = json.loads(subprocess.check_output([str(python), "-B", "-c", probe], env=env, text=True))
    environment["PYTHONHASHSEED"] = "0"
    environment["codegen_hash_seed_provenance"] = "New explicit reproducibility setting; historical generator hash seed was not recorded."
    if environment["python"] != "3.10.11":
        raise RuntimeError(f"Required Python 3.10.11, found {environment['python']}")
    for package, required in REQUIRED_PACKAGES.items():
        if environment["packages"].get(package) != required:
            raise RuntimeError(f"Required {package}=={required}, found {environment['packages'].get(package)}")
    for compiler in ("gfortran", "gcc"):
        version = subprocess.check_output([str(ucrt / (compiler + ".exe")), "-dumpfullversion"], env=env, text=True).strip()
        if version != "16.2.0":
            raise RuntimeError(f"Required UCRT64 {compiler} 16.2.0, found {version}")
        environment[compiler] = {"version": version, **file_record(ucrt / (compiler + ".exe"))}
    lock = json.loads((ROOT / "dependencies.lock.json").read_text(encoding="utf-8"))
    python_import_library = prepare_import_library(
        python, Path(config.get("msys2_root", "C:/msys64")), env=env,
        expected_packages=lock["toolchains"]["ucrt64_python_import_library"]["packages"])
    generator = ROOT / "models/kmcos/fcc_kawasaki_otf__build.py"
    command = [str(python), "-B", str(generator), "--kmcos-root", str(ROOT / "sources/kmcos"),
               "--output-dir", str(output), "--msys2-ucrt-bin", str(ucrt)]
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "solver": "kmcos_otf",
              "python_hash_seed": "0",
              "source": source, "environment": environment, "command": command,
              "fortran_flags": F90FLAGS.split(), "f2py_compiler_family": "gnu95",
              "f2py_c_compiler": "mingw32", "upstream_patched": False,
              "python_import_library": python_import_library,
              "model_files": [file_record(ROOT / "models/kmcos" / name) for name in
                              ("fcc_kawasaki_geometry.py", "fcc_kawasaki_otf__build.py", "run_kmcos_otf_scenario.py")]}
    log = logs / "build_kmcos.log"
    with log.open("w", encoding="utf-8") as stream:
        stream.write("Command: " + json.dumps(command) + "\n")
        stream.flush()
        result = subprocess.run(command, cwd=output, env=env, stdout=stream, stderr=subprocess.STDOUT)
    report["exit_code"] = result.returncode
    report["log"] = str(log)
    model_src = output / "fcc_kawasaki_otf_otf/src"
    report["generated_model_dir"] = str(model_src)
    report["artifacts"] = [file_record(p) for p in sorted(output.rglob("*")) if p.is_file()
                           and temp_dir not in p.parents and p.suffix in (".f90", ".pyd", ".xml", ".ini", ".py")]
    log_lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    report["actual_compiler_invocation_evidence"] = {
        "fortran_f90": next((line for line in reversed(log_lines) if line.startswith("Fortran f90 compiler:")), None),
        "generated_c_wrapper": next((line for line in log_lines if line.startswith("INFO: gcc ") and "kmc_modelmodule.c" in line), None),
        "interpretation": "Scientific Fortran O3 with NumPy-added -funroll-loops; generated C wrapper retains inherited O0/debug flags.",
    }
    try:
        if result.returncode:
            raise RuntimeError(f"kmcos build failed with code {result.returncode}; see {log}")
        extensions = list(model_src.glob("kmc_model*.pyd"))
        if len(extensions) != 1:
            raise RuntimeError("Build did not produce exactly one kmc_model extension")
        report["compiled_extension"] = file_record(extensions[0])
        pe = subprocess.check_output([str(ucrt / "objdump.exe"), "-p", str(extensions[0])], text=True)
        report["runtime_dlls"] = []
        for name in re.findall(r"DLL Name:\s*(\S+)", pe):
            local = next((p for p in (ucrt / name, Path(environment["base_prefix"]) / name) if p.exists()), None)
            report["runtime_dlls"].append({"name": name, "resolved": file_record(local) if local else None,
                                          "resolution_scope": "UCRT64 compiler runtime or Python base; Windows system DLLs are not copied"})
        # Revalidate frozen files after importing/building; no bytecode/source writes allowed.
        verify_frozen_export("kmcos")
        report["source_postbuild_verification"] = "PASS"
        if "FULL KMCOS OTF MODEL IMPORT PASS:" not in "\n".join(log_lines):
            raise RuntimeError("Required compiled extension import validation was not recorded")
        report["extension_import_validation"] = "PASS"
        # Repeat only source generation, not compilation, to verify the seed pin.
        repeat_dir = output / "repeat_codegen"
        repeat_command = [str(python), "-B", "-c", (
            "import sys; from pathlib import Path; "
            "sys.path.insert(0," + repr(str(ROOT)) + "); "
            "sys.path.insert(0," + repr(str(ROOT / "sources/kmcos")) + "); "
            "import kmcos; from models.kmcos.fcc_kawasaki_otf__build import _export_source_only; "
            "_export_source_only(kmcos,Path(" + repr(str(output / "fcc_kawasaki_otf.xml")) + "),"
            "Path(" + repr(str(repeat_dir)) + "))"
        )]
        with (logs / "build_kmcos_repeat_codegen.log").open("w", encoding="utf-8") as stream:
            subprocess.run(repeat_command, cwd=output, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
        repeated = sorted((repeat_dir / "src").glob("*.f90"))
        if not repeated:
            raise RuntimeError("Repeat code generation did not produce Fortran")
        changed = [p.name for p in repeated if not (model_src / p.name).exists()
                   or p.read_bytes() != (model_src / p.name).read_bytes()]
        if changed:
            raise RuntimeError(f"Generated Fortran is not repeatable under PYTHONHASHSEED=0: {changed}")
        report["codegen_repeatability"] = {"status": "PASS", "same_bytes_fortran_file_count": len(repeated),
                                           "PYTHONHASHSEED": "0", "second_compilation_performed": False}
        verify_frozen_export("kmcos")
        report["status"] = "PASS"
    except Exception as error:
        report["status"] = "FAIL"
        report["error"] = str(error)
        (logs / "build_kmcos.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        raise
    (logs / "build_kmcos.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", type=Path)
    options = parser.parse_args()
    from build_guard import build_lock
    with build_lock():
        report = build(options.paths)
        print(f"kmcos OTF build {report['status']}: {report['generated_model_dir']}")


if __name__ == "__main__":
    main()
