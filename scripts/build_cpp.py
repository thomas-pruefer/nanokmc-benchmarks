"""Build the frozen three C++ packages without modifying scientific sources."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from fetch_or_verify_sources import ROOT, load_paths, locked_model_hash, sha256, verify_sources


def build_cpp(paths=None, solver="all", jobs=4):
    config = load_paths(paths)
    verification = verify_sources(paths, verify_only=True)
    sources = {entry["name"]: entry for entry in verification["sources"]}
    msys = Path(config.get("msys2_root", "C:/msys64"))
    ucrt = msys / "ucrt64/bin"
    plain = msys / "usr/bin"
    selected = ("nanokmc", "spparks", "kmc_lattice") if solver == "all" else (solver,)
    reports = []
    for name in selected:
        build = ROOT / "build" / name
        build.mkdir(parents=True, exist_ok=True)
        logs = ROOT / "logs"
        logs.mkdir(exist_ok=True)
        compiler = (plain if name == "spparks" else ucrt) / "g++.exe"
        env = os.environ.copy()
        for inherited_flag in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "MAKEFLAGS", "MAKEOVERRIDES"):
            env.pop(inherited_flag, None)
        env["PATH"] = os.pathsep.join([str(compiler.parent), str(plain), env.get("PATH", "")])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        report = {"solver": name, "status": "started", "timestamp_utc": datetime.now(timezone.utc).isoformat(), "source": sources[name], "compiler": str(compiler), "commands": [], "upstream_source_patch": False, "simulations_performed": False, "build_jobs": jobs}
        if name == "spparks":
            report["provenance_issue"] = "Verified plain-MSYS GCC15.3.0 preserves evidenced source-unmodified serial route; differs from manuscript blanket16.2.0. Historical per-run artifact chain remains unavailable."
        try:
            version = subprocess.check_output([str(compiler), "-dumpfullversion"], env=env, text=True).strip()
            report["compiler_version"] = version
            report["compiler_sha256"] = sha256(compiler.read_bytes())
            expected = "15.3.0" if name == "spparks" else "16.2.0"
            if version != expected:
                raise RuntimeError(f"Expected verified compiler {expected}, got {version}; review environment lock before building")
            with (logs / f"build_{name}.log").open("w", encoding="utf-8") as log:
                def run(command, cwd=ROOT):
                    command = [str(arg) for arg in command]
                    report["commands"].append({"argv": command, "cwd": str(cwd)})
                    log.write("\nCOMMAND " + json.dumps(command) + "\n"); log.flush()
                    subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
                source = ROOT / "sources" / name
                if name == "nanokmc":
                    report["compile_flags"] = ["-std=c++17", "-O1", "-DNDEBUG"]
                    run([ucrt / "cmake.exe", "-S", source, "-B", build, "-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={(ucrt / 'ninja.exe').as_posix()}", f"-DCMAKE_CXX_COMPILER={compiler.as_posix()}", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_FLAGS=", "-DCMAKE_CXX_FLAGS_RELEASE=-O3 -DNDEBUG", "-DCMAKE_EXE_LINKER_FLAGS=", "-DCMAKE_EXE_LINKER_FLAGS_RELEASE=", "-DNANOKMC_PAPER_BUILD=ON", "-DBUILD_TESTING=OFF"])
                    run([ucrt / "cmake.exe", "--build", build, "--parallel", jobs, "--verbose"])
                    executable = build / "nanokmc.exe"
                    report["build_layout"] = "unmodified release CMake separate translation units, paper build ON"
                    report["effective_optimization"] = "-O1 follows CMake Release -O3; no LTO; link retains upstream Release defaults"
                elif name == "spparks":
                    report["compile_flags"] = ["-O1", "-std=gnu++17"]
                    report["link_flags"] = ["-O1"]
                    # SPPARKS writes generated headers/objects in its source tree.
                    # Supply a verified build-only copy, leaving sources/ frozen.
                    staging = build / "staging"
                    if not staging.exists():
                        shutil.copytree(source, staging)
                    for original in source.rglob("*"):
                        if original.is_file():
                            copy = staging / original.relative_to(source)
                            if not copy.exists() or sha256(copy.read_bytes()) != sha256(original.read_bytes()):
                                raise RuntimeError(f"SPPARKS staged scientific source differs: {copy}")
                    # Set PATH inside bash so UCRT64 cannot shadow plain MSYS g++.
                    # The build directory is a positional argument, not shell text.
                    run([plain / "bash.exe", "--noprofile", "--norc", "-c", 'export PATH=/usr/bin:/bin; cd "$(cygpath -u "$1")" && exec make -j"$2" serial CCFLAGS="-O1 -std=gnu++17" LINKFLAGS="-O1"', "build-spparks", staging / "src", jobs])
                    built = staging / "src/spk_serial.exe"
                    executable = build / "spk_serial.exe"
                    shutil.copy2(built, executable)
                    report["build_layout"] = "upstream serial Makefile and MPI stubs in build-only source copy"
                else:
                    app = ROOT / "models/kmc_lattice/fcc_kawasaki_selective.cpp"
                    if not app.is_file():
                        raise RuntimeError(f"Benchmark-owned FCC model is missing: {app}")
                    if sha256(app.read_bytes()) != locked_model_hash("models/kmc_lattice/fcc_kawasaki_selective.cpp"):
                        raise RuntimeError("Benchmark-owned KMC_Lattice model differs from its locked identity")
                    report["application"] = {"path": app.relative_to(ROOT).as_posix(), "sha256": sha256(app.read_bytes())}
                    report["compile_flags"] = ["-O1", "-std=gnu++17", "-Wall", "-Wextra"]
                    executable = build / "kmc_lattice_fcc.exe"
                    units = [source / "src" / (unit + ".cpp") for unit in ("Event", "Lattice", "Object", "Parameters_Lattice", "Parameters_Simulation", "Simulation", "Site")]
                    run([compiler, *report["compile_flags"], "-I" + str(source / "src"), app, *units, "-o", executable])
                    report["build_layout"] = "benchmark FCC selective application and seven unmodified upstream framework units"
                if not executable.is_file():
                    raise RuntimeError(f"Build returned success without expected executable: {executable}")
                data = executable.read_bytes()
                report["artifact"] = {"path": executable.relative_to(ROOT).as_posix(), "sha256": sha256(data), "size_bytes": len(data), "compiler_markers": sorted(set(marker.decode("ascii", "replace") for marker in re.findall(rb"GCC: [\x20-\x7e]+", data)))}
                objdump = subprocess.check_output([str(compiler.parent / "objdump.exe"), "-p", str(executable)], env=env, text=True, errors="replace")
                report["runtime_dll_names"] = re.findall(r"DLL Name:\s*(\S+)", objdump)
                (logs / f"build_{name}_pe.txt").write_text(objdump, encoding="utf-8")
            # Verify sources again after all external tools for this build.
            verify_sources(paths, verify_only=True)
            report["status"] = "passed"
            print(f"BUILT {name}: {report['artifact']['path']}", flush=True)
        except Exception as error:
            report["status"] = "failed"
            report["error"] = str(error)
            raise
        finally:
            report["finished_utc"] = datetime.now(timezone.utc).isoformat()
            (logs / f"build_{name}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        reports.append(report)
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", type=Path)
    parser.add_argument("--solver", choices=("all", "nanokmc", "spparks", "kmc_lattice"), default="all")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    from build_guard import build_lock
    with build_lock():
        build_cpp(args.paths, args.solver, args.jobs)


if __name__ == "__main__":
    main()
