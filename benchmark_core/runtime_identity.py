"""Verify current build/runtime bytes and produce a timestamp/path-independent identity."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")


def current_runtime_identity(paths_file: Path) -> dict:
    # Full checks happen once per invocation; the returned bytes can be guarded
    # cheaply immediately before/after each trajectory without re-probing tools.
    paths_file = Path(paths_file).resolve()
    paths_before = hashlib.sha256(paths_file.read_bytes()).hexdigest()
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_environment import check
    from build_all import aggregate
    from benchmark_core.scenario import load_paths
    from benchmark_core.runner import PATH_IDS
    from smoke_test import verify_build_identity
    environment = check(paths_file)
    if not environment["environment_verified"]:
        raise RuntimeError("Environment verification failed: " + "; ".join(environment["errors"]))
    # Verification/status must not rewrite reports while a campaign is active.
    # Stages 01 and 03 explicitly produce their environment/build reports.
    manifest = aggregate(paths_file, write_manifest=False)
    paths = load_paths(paths_file)
    for code in PATH_IDS:
        verify_build_identity(code, paths, manifest)
    for name in ("kmcos", "kmc_lattice"):
        if Path(paths[name + "_root"]).resolve() != (ROOT / "sources" / name).resolve():
            raise RuntimeError(f"{name}_root must point to the repository's verified frozen source")
    msys = Path(paths.get("msys2_root", "C:/msys64")).resolve()
    if Path(paths.get("msys2_bash", msys / "usr/bin/bash.exe")).resolve() != (msys / "usr/bin/bash.exe").resolve():
        raise RuntimeError("msys2_bash must belong to the verified msys2_root")
    if Path(paths.get("kmcos_msys2_ucrt_bin", msys / "ucrt64/bin")).resolve() != (msys / "ucrt64/bin").resolve():
        raise RuntimeError("kmcos runtime must use the verified UCRT64 directory")
    if paths.get("msys2_path_prefix", "/ucrt64/bin:/usr/bin") != "/ucrt64/bin:/usr/bin":
        raise RuntimeError("Unsupported MSYS2 runtime search order; use /ucrt64/bin:/usr/bin")
    files = {}

    def guard(path):
        p = Path(path)
        if not p.is_absolute():
            p = ROOT / p
        p = p.resolve()
        if not p.is_file():
            raise RuntimeError(f"Required runtime/model file missing: {p}")
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        files[str(p)] = digest
        return digest

    builds = {}
    for name, build in sorted(manifest["builds"].items()):
        report = json.loads((ROOT / build["report"]).read_text(encoding="utf-8"))
        model = {}
        for item in report.get("model_files", []) + ([report["application"]] if "application" in report else []):
            p = Path(item["path"])
            if not p.is_absolute():
                p = ROOT / p
            model[p.resolve().relative_to(ROOT).as_posix()] = guard(p)
        generated = {}
        for item in report.get("artifacts", []):
            p = Path(item["path"])
            if not p.is_absolute():
                p = ROOT / p
            generated[p.resolve().relative_to(ROOT).as_posix()] = guard(p)
        builds[name] = {key: build[key] for key in ("source_revision", "source_tree_sha256", "compiler_version", "compile_flags")}
        builds[name].update(binary_sha256=guard(build["executable"]), model_files=model, generated_files=generated)
    runtimes = {}
    for name, record in environment["runtime_libraries_observed"].items():
        if not record["exists"]:
            raise RuntimeError(f"Required runtime library missing: {record['path']}")
        runtimes[name] = guard(record["path"])
    python_hashes = {"harness": guard(sys.executable), "kmcos": guard(environment["kmcos_python"]["observed"]["executable"])}
    # A venv executable is a launcher. Also seal the actual interpreter DLLs.
    for label, base in (("harness", Path(sys.base_prefix)),
                        ("kmcos", Path(environment["kmcos_python"]["observed"]["base_prefix"]))):
        for dll in sorted(base.glob("python3*.dll")):
            python_hashes[f"{label}/{dll.name}"] = guard(dll)
    # Freeze operational path configuration for this invocation. Its bytes are
    # deliberately outside the semantic fingerprint: moving the same verified
    # binaries between invocations is allowed, editing paths mid-run is not.
    if guard(paths_file) != paths_before:
        raise RuntimeError("Path configuration changed during environment/build preflight; retry with a stable file")
    details = {
        "schema_version": 1, "builds": builds,
        "dependencies_lock_sha256": guard(ROOT / "dependencies.lock.json"),
        "benchmark_python": {"version": environment["benchmark_python"]["version"], "packages": environment["benchmark_python"]["packages"]},
        "kmcos_python": {k: environment["kmcos_python"]["observed"][k] for k in ("python", "packages")},
        "python_binary_hashes": python_hashes, "runtime_libraries": runtimes,
        "host": environment["host"], "threads": {key: "1" for key in THREAD_VARIABLES},
    }
    digest = hashlib.sha256(json.dumps(details, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"fingerprint": digest, "details": details,
            "verification_files": [{"path": p, "sha256": h,
                                    **({"role": "path_configuration"} if p == str(paths_file) else {})}
                                   for p, h in sorted(files.items())]}
