"""Initialize and briefly evolve eleven paths, sequentially, at N=256."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_all import aggregate
from benchmark_core.runner import PATH_IDS, get_adapter
from benchmark_core.run_store import check_native_output_path
from benchmark_core.scenario import BenchmarkScenario, load_paths
from benchmark_core.validation import sha256, validate_collection


def family(code: str) -> str:
    return "kmc_lattice" if code.startswith("kmc_lattice_") else code.split("_")[0]


def verify_build_identity(code: str, paths: dict, manifest: dict) -> dict:
    name = family(code)
    build = manifest["builds"][name]
    if build["status"].lower() not in {"pass", "passed"}:
        raise ValueError(f"Build has not passed: {name}")
    artifact = build["artifact"]
    artifact_path = Path(artifact["path"])
    if not artifact_path.is_absolute():
        artifact_path = ROOT / artifact_path
    if sha256(artifact_path) != artifact["sha256"]:
        raise ValueError(f"Build artifact changed: {artifact_path}")
    if name != "kmcos":
        configured = Path(paths[name + "_exe"]).resolve()
        if configured != artifact_path.resolve():
            raise ValueError(f"Configured executable differs from verified build: {configured}")
    else:
        search = Path(paths["kmcos_compiled_src"]).resolve()
        if artifact_path.resolve().parent not in {search, search.parent}:
            raise ValueError("Configured kmcos model is outside verified build")
    return {"family": name, "artifact": artifact, "source": build.get("source"),
            "compiler_version": build.get("compiler_version"), "compile_flags": build.get("compile_flags")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", type=Path, default=ROOT / "config" / "paths.local.json")
    parser.add_argument("--build-metadata", type=Path, default=ROOT / "build" / "build_manifest.json")
    parser.add_argument("--codes", nargs="+", choices=PATH_IDS, default=list(PATH_IDS))
    args = parser.parse_args()
    if len(args.codes) != len(set(args.codes)):
        parser.error("duplicate path selections")
    paths = load_paths(args.paths)
    manifest = json.loads(args.build_metadata.read_text(encoding="utf-8-sig"))
    current_build_evidence = aggregate(args.paths, write_manifest=False)
    if current_build_evidence["builds"] != manifest["builds"]:
        raise ValueError("Build reports/artifacts have changed since the saved build manifest")
    build_hash = sha256(args.build_metadata)
    lock_path = ROOT / "dependencies.lock.json"
    if not lock_path.exists():
        raise FileNotFoundError("Source/version lock is required before smoke testing")
    source_lock_hash = sha256(lock_path)
    env_path = ROOT / "logs" / "environment_check.json"
    env_hash = sha256(env_path)
    environment = json.loads(env_path.read_text(encoding="utf-8-sig"))
    if not environment.get("environment_verified"):
        raise ValueError("The required compiler/Python environment has not been verified")
    if environment.get("source_lock_sha256") != source_lock_hash:
        raise ValueError("Environment evidence belongs to a different source/environment lock")
    if manifest.get("status", "").upper() not in {"PASS", "PASSED"}:
        raise ValueError("Not all required builds have passed")
    if manifest.get("dependencies_lock_sha256") != source_lock_hash:
        raise ValueError("Build evidence belongs to a different source/environment lock")
    if manifest.get("environment_report_sha256") != env_hash:
        raise ValueError("Build evidence belongs to a different verified environment report")
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
        os.environ[key] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    scenario = BenchmarkScenario(
        id="smoke_k3_xA020_T075", description="Initialization and short evolution; not manuscript results",
        geometry="fcc", dimension=3, nx=3, ny=3, nz=3, composition_A=0.2, kT=0.75, Ea=1.0,
        mcs_points=[0, 10], seeds=[1], codes=list(args.codes),
        spparks_loglinfreq_n=5, spparks_loglinfreq_factor=10.0,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = {"schema_version": 1, "scope": "initialization_smoke", "status": "running",
              "started_utc": stamp, "concurrency": 1, "scenario": asdict(scenario), "seed": 1,
              "all_eleven_selected": tuple(args.codes) == PATH_IDS, "python": sys.version,
              "build_manifest_sha256": build_hash, "dependencies_lock_sha256": source_lock_hash,
              "environment_check_sha256": env_hash, "paths": []}
    report_path = ROOT / "logs" / f"smoke_{stamp}.json"
    failed = False
    for code in args.codes:
        print(f"[smoke] {code}", flush=True)
        run_dir = ROOT / "results" / "raw" / "smoke" / stamp / code / "seed_0001"
        out_dir = ROOT / "results" / "processed" / "smoke" / stamp / code / "seed_0001"
        record = {"code": code, "raw_directory": str(run_dir.relative_to(ROOT)),
                  "processed_directory": str(out_dir.relative_to(ROOT))}
        try:
            identity = verify_build_identity(code, paths, manifest)
            record["build_identity"] = identity
            check_native_output_path(run_dir, code)
            adapter = get_adapter(code, paths)
            adapter.prepare(scenario, 1, run_dir)
            adapter.run(scenario, 1, run_dir)
            adapter.collect(scenario, 1, run_dir, out_dir)
            record.update(validate_collection(scenario, code, 1, out_dir))
            metadata_path = out_dir / "run_metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update({"scope": "smoke_not_publication", "concurrency": 1,
                             "scenario": asdict(scenario), "seed": 1, "build_identity": identity,
                             "build_manifest_sha256": build_hash, "dependencies_lock_sha256": source_lock_hash,
                             "environment_check_sha256": env_hash, "strict_completion": "passed"})
            metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            print(f"[PASS] {code}: 2 observations, conserved {record['N_A']}/{record['N_B']} A/B", flush=True)
        except Exception as exc:
            failed = True
            record.update({"status": "FAIL", "error": str(exc), "traceback": traceback.format_exc()})
            print(f"[FAIL] {code}: {exc}", flush=True)
        report["paths"].append(record)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["status"] = "FAIL" if failed else "PASS"
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    report["passed_paths"] = sum(r["status"] == "PASS" for r in report["paths"])
    report["failed_paths"] = sum(r["status"] == "FAIL" for r in report["paths"])
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (ROOT / "logs" / "smoke_latest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}: {report['passed_paths']}/{len(args.codes)}; {report_path}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
