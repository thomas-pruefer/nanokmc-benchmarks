"""Build the four frozen solver packages; optionally run the eleven short smoke cases."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from fetch_or_verify_sources import ROOT, PINS, load_dependency_lock, locked_model_hash, verify_sources


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(paths: Path, write_manifest: bool = True) -> dict:
    lock = load_dependency_lock()
    for name, pin in PINS.items():
        if any(lock["sources"][name].get(key) != value for key, value in pin.items()):
            raise RuntimeError(f"Source verifier and dependency lock disagree for {name}")
    sources = verify_sources(paths, verify_only=True, write_reports=write_manifest)
    environment_file = ROOT / "logs/environment_check.json"
    environment = json.loads(environment_file.read_text(encoding="utf-8"))
    if not environment.get("environment_verified"):
        raise RuntimeError("Environment has not passed the declared checks")
    if environment["source_lock_sha256"] != sha(ROOT / "dependencies.lock.json"):
        raise RuntimeError("Environment evidence predates a dependency lock change")
    builds = {}
    for name in PINS:
        report_path = ROOT / f"logs/build_{name}.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if str(report["status"]).lower() not in {"pass", "passed"}:
            raise RuntimeError(f"Build has not passed: {name}")
        if report["source"]["commit"] != PINS[name]["commit"]:
            raise RuntimeError(f"Build source does not match the lock: {name}")
        if report["source"].get("tree_sha256") != lock["sources"][name]["tree_sha256"]:
            raise RuntimeError(f"Build source tree digest does not match the lock: {name}")
        if name == "kmcos":
            import_library = report.get("python_import_library")
            if not isinstance(import_library, dict) or import_library.get("status") != "PASS":
                raise RuntimeError("kmcos build lacks verified Python-MinGW import-library evidence")
            library_directory = (Path(import_library["input_identity"]["python"]["prefix"]) / "libs").resolve()
            for key in ("definition", "import_library"):
                record = import_library[key]
                candidate = Path(record["path"]).resolve()
                if candidate.parent != library_directory:
                    raise RuntimeError(f"kmcos import-library output is outside its dedicated environment: {candidate}")
                if sha(candidate) != record["sha256"] or not candidate.stat().st_size:
                    raise RuntimeError(f"kmcos import-library output changed: {candidate}")
            artifacts = [x for x in report["artifacts"] if Path(x["path"]).name.startswith("kmc_model") and Path(x["path"]).suffix == ".pyd"]
            if len(artifacts) != 1:
                raise RuntimeError("kmcos must have exactly one compiled extension")
            artifact = artifacts[0]
            model_records = report["model_files"]
            if str(report.get("python_hash_seed")) != str(lock["build_contract"]["kmcos"]["python_hash_seed"]):
                raise RuntimeError("kmcos code-generation hash seed does not match the lock")
            for item in report["artifacts"]:
                generated = Path(item["path"])
                if not generated.is_absolute():
                    generated = ROOT / generated
                if not generated.resolve().is_relative_to((ROOT / "build/kmcos").resolve()):
                    raise RuntimeError(f"Generated kmcos artifact is outside build/: {generated}")
                if sha(generated) != item["sha256"]:
                    raise RuntimeError(f"Generated kmcos runtime/model artifact changed: {generated}")
        else:
            artifact = report["artifact"]
            model_records = [report["application"]] if "application" in report else []
        binary = Path(artifact["path"])
        if not binary.is_absolute():
            binary = ROOT / binary
        binary = binary.resolve()
        if not binary.is_relative_to((ROOT / "build").resolve()):
            raise RuntimeError(f"Build artifact is outside this repository: {binary}")
        if sha(binary) != artifact["sha256"]:
            raise RuntimeError(f"Built binary hash changed: {name}")
        expected_models = {path for path in lock["model_sha256"] if path.startswith(f"models/{name}/")}
        observed_models = set()
        for item in model_records:
            model = Path(item["path"])
            if not model.is_absolute():
                model = ROOT / model
            relative = model.resolve().relative_to(ROOT.resolve()).as_posix()
            observed_models.add(relative)
            if sha(model) != item["sha256"] or item["sha256"] != locked_model_hash(relative):
                raise RuntimeError(f"Benchmark model changed since build: {model}")
        if observed_models != expected_models:
            raise RuntimeError(f"Build report does not identify the required benchmark model files: {name}")
        builds[name] = {
            "status": "PASS", "source_revision": PINS[name]["commit"],
            "source_tree_sha256": report["source"]["tree_sha256"],
            "executable": str(binary), "executable_sha256": artifact["sha256"],
            "artifact": {"path": binary.relative_to(ROOT).as_posix(), "sha256": artifact["sha256"]},
            "source": report["source"],
            "compiler_version": report.get("compiler_version", report.get("environment", {}).get("gfortran", {}).get("version")),
            "compile_flags": report.get("compile_flags", report.get("fortran_flags")),
            "report": report_path.relative_to(ROOT).as_posix(), "report_sha256": sha(report_path),
            "upstream_source_patched": False,
        }
    manifest = {
        "schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS", "scope": "Frozen solver builds; manuscript execution is a separate manual stage",
        "dependencies_lock_sha256": sha(ROOT / "dependencies.lock.json"),
        "environment_report": "logs/environment_check.json", "environment_report_sha256": sha(environment_file),
        "sources_verified": sources, "builds": builds,
        "historical_binary_lineage_verified": False, "manuscript_compiler_discrepancy_resolved": False,
        "manuscript_campaigns_run": False,
    }
    if write_manifest:
        (ROOT / "build/build_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", type=Path, default=ROOT / "config/paths.local.json")
    parser.add_argument("--jobs", type=int, default=4, help="Compilation jobs; solver smoke cases remain sequential")
    parser.add_argument("--collect-existing", action="store_true", help="Verify completed local build reports without recompiling")
    parser.add_argument("--smoke", action="store_true", help="Run only k=3, seed1, 0/10 MCS initialization checks")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    from build_guard import build_lock
    with build_lock():
        if not args.collect_existing:
            subprocess.run([sys.executable, "-B", str(ROOT / "scripts/check_environment.py"), "--paths", str(args.paths)], check=True)
            from build_cpp import build_cpp
            from build_kmcos import build
            build_cpp(args.paths, jobs=args.jobs)
            build(args.paths)
        manifest = aggregate(args.paths)
        print("Verified frozen-source builds:", ", ".join(manifest["builds"]))
        print("Historical NanoKMC lineage and manuscript SPPARKS compiler discrepancy remain unresolved; see docs/PROVENANCE.md.")
        if args.smoke:
            subprocess.run([sys.executable, "-B", str(ROOT / "scripts/smoke_test.py"), "--paths", str(args.paths),
                            "--build-metadata", str(ROOT / "build/build_manifest.json")], check=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"BUILD BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
