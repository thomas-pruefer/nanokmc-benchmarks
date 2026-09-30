"""Strict Figure 5–10 processing of sealed runs.

The manifest is the final commit marker. Interrupted replacement fails closed on
hash checks. Requested checkpoints define pairing; native clocks remain evidence.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
import csv
import json
import math
import os
from pathlib import Path
import shutil
import uuid
from benchmark_core.validation import sha256, validate_collection
from benchmark_core.figure_data import (BINARY, POINTS, FIGURE_FILES, number,
    cutoff12_concentration, event_counters, log_fit, derive_tables)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))

def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"Refusing empty publication CSV: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()

def safe_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    path.relative_to(root.resolve())
    return path

def required_runs(root: Path, figures: list[int]) -> list:
    from benchmark_core.campaigns import enumerate_runs
    campaigns = {"A" if f in (5,7,8) else "B" if f in (6,9) else "C" for f in figures}
    unique = {}
    for campaign in sorted(campaigns):
        for spec in enumerate_runs(root / "config/manuscript.json", campaign=campaign):
            unique[spec.run_id] = spec
    return list(unique.values())


def record_requested_jobs(record):
    """A queue's requested limit is provenance, not constant measured overlap."""
    if not isinstance(record,dict) or not isinstance(record.get("execution_identity"),dict):
        raise ValueError("Completion execution identity metadata is missing or malformed")
    identity = record.get("execution_identity",{})
    values = (record.get("requested_jobs"),record.get("concurrency"),
              identity.get("requested_jobs"),identity.get("concurrency"))
    if any(type(value) is not int or not 1 <= value <= 32 for value in values) or len(set(values)) != 1:
        raise ValueError("Missing, invalid or inconsistent requested_jobs/concurrency metadata; expected one integer 1..32")
    if identity.get("execution_policy") != "independent_single_thread_jobs":
        raise ValueError("Execution policy is not independent single-thread solver jobs")
    if not identity.get("timing_policy"):
        raise ValueError("Native timing policy metadata is missing")
    return values[0]

def infer_publication_jobs(root,specs,run_set=None):
    """Inspect metadata only to choose verification identity; seals are checked later."""
    from benchmark_core.run_store import run_directory
    groups = defaultdict(list)
    for spec in specs:
        path = run_directory(root,spec)/"complete.json"
        if not path.is_file():
            raise ValueError(f"Required complete run is absent: {spec.run_id}")
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(record,dict) or record.get("scope") != "manuscript" or record.get("status") != "complete" or record.get("run_id") != spec.run_id:
            raise ValueError(f"Invalid completion metadata: {spec.run_id}")
        if run_set is not None and (record.get("run_set") != run_set or record.get("execution_identity",{}).get("run_set") != run_set):
            raise ValueError(f"Completion belongs to a different run-set: {spec.run_id}")
        groups[record_requested_jobs(record)].append(spec.run_id)
    if not groups:
        raise ValueError("No required completed runs were selected")
    if len(groups) != 1:
        details = "; ".join(f"jobs={jobs}: {len(ids)} runs (e.g. {ids[0]})" for jobs,ids in sorted(groups.items()))
        raise ValueError("Mixed requested job limits cannot form a strict publication dataset: " + details +
                         ". Use one --jobs policy for all required runs, or select a complete figure subset with a uniform policy.")
    return next(iter(groups))

def normalize_run(spec, completion) -> tuple[list[dict], list[dict]]:
    """Validate native records, recompute cutoff-12 concentration and event mapping."""
    directory, scenario = completion.processed_dir, spec.scenario
    jobs = record_requested_jobs(completion.record)
    execution = completion.record["execution_identity"]
    active_at_launch = completion.record.get("active_jobs_at_launch","")
    if active_at_launch != "" and (type(active_at_launch) is not int or not 1 <= active_at_launch <= jobs):
        raise ValueError("Invalid active_jobs_at_launch provenance")
    validate_collection(scenario, spec.code, spec.seed, directory)
    raw = read_csv(directory / "metrics_common.csv")
    distribution = read_csv(directory / "cluster_distribution_common.csv")
    timings = read_csv(directory / "timing_common.csv")
    snapshots = read_csv(directory / "snapshot_manifest_common.csv")
    if tuple(float(r["requested_mcs"]) for r in raw) != POINTS:
        raise ValueError(f"{spec.run_id}: all nineteen manuscript checkpoints are required")
    by_index, seen = defaultdict(list), set()
    for r in distribution:
        if r["scenario_id"] != scenario.id or r["code"] != spec.code or int(r["seed"]) != spec.seed:
            raise ValueError(f"{spec.run_id}: cluster identity mismatch")
        i = number(r["save_index"], "cluster index", integer=True)
        size = number(r["cluster_size"], "cluster size", integer=True, minimum=1)
        count = number(r["cluster_count"], "cluster count", integer=True, minimum=1)
        species = r["common_species"]
        key = i,species,size
        if key in seen or i >= len(POINTS) or species not in ("A","B"):
            raise ValueError(f"{spec.run_id}: duplicate/invalid cluster key {key}")
        seen.add(key)
        if int(r["sites_in_clusters"]) != size * count:
            raise ValueError(f"{spec.run_id}: inconsistent cluster mass field")
        by_index[i].append(dict(r,cluster_size=size,cluster_count=count))
    rows = []
    for i,r in enumerate(raw):
        for record in (timings[i],snapshots[i]):
            if record["scenario_id"] != scenario.id or record["code"] != spec.code or int(record["seed"]) != spec.seed or int(record["save_index"]) != i:
                raise ValueError(f"{spec.run_id}: timer/snapshot association mismatch")
        runtime = number(r["diagnostic_runtime_seconds_cumulative"], "native runtime")
        if float(timings[i]["diagnostic_runtime_seconds_cumulative"]) != runtime:
            raise ValueError(f"{spec.run_id}: common timer disagrees with metrics")
        n = number(r["total_sites"], "site count", integer=True, minimum=1)
        n_a,n_b = int(r["N_A_common"]),int(r["N_B_common"])
        for species,mass in (("A",n_a),("B",n_b)):
            if sum(c["cluster_size"]*c["cluster_count"] for c in by_index[i] if c["common_species"] == species) != mass:
                raise ValueError(f"{spec.run_id}: cluster mass mismatch")
        small, concentration = cutoff12_concentration(by_index[i], n_b)
        bonds = number(r["interface_bonds_common"], "unlike bonds", integer=True)
        rho = bonds/(6*n)
        if not math.isclose(float(r["interface_density_common"]),rho,rel_tol=1e-12,abs_tol=1e-14):
            raise ValueError(f"{spec.run_id}: rho is not N_AB/(6N)")
        candidates, executed, meaning, source = event_counters(spec.code, r)
        out = dict(run_id=spec.run_id,code=spec.code,scenario_id=scenario.id,k=scenario.nx,N=n,seed=spec.seed,
            x_A_nominal=scenario.composition_A,T_star=scenario.kT,requested_mcs=POINTS[i],
            realized_common_mcs=float(r["common_mcs_equivalent"]),runtime_seconds=runtime,
            runtime_source=r["diagnostic_runtime_source"],source_identity_sha256=completion.record["identity_sha256"],save_index=i,
            run_set=completion.record.get("run_set"),requested_jobs=jobs,active_jobs_at_launch=active_at_launch,
            execution_policy=execution["execution_policy"],timing_policy=execution["timing_policy"],
            N_A=n_a,N_B=n_b,N_AB=bonds,rho_AB=rho,N_A_lt12=small,cutoff=12,c_A_B=concentration,
            candidate_events=candidates,executed_events=executed,candidate_events_per_site=candidates/n,
            executed_events_per_site=executed/n,n_ex=executed/n,counter_meaning=meaning,native_counter_source=source)
        # External adapters separately retain the observation boundary and the
        # last executed event. The latter may lie below/above the requested time.
        last_event = r.get("kmcos_native_last_event_time",r.get("kmc_lattice_native_last_event_time",""))
        out.update(x_A_actual=n_a/n,runtime_units="seconds",event_counter_units="cumulative events",
            native_clock_units="common MCS" if spec.code.startswith("nanokmc_") else "solver time; common MCS=6*native time",
            observation_common_mcs=float(r["common_mcs_equivalent"]),last_event_common_mcs=6*float(last_event) if last_event!="" else "")
        if last_event != "":
            out["realized_common_mcs"] = 6*float(last_event)
        for field in ("native_step","native_mcs","native_simulation_time","native_last_event_time",
                      "spparks_naccept_native","spparks_nreject_native","spparks_nsweeps_native",
                      "attempted_exchanges_total","accepted_exchanges_total"):
            out[field] = r.get(field,"")
        out["native_last_event_time"] = last_event
        for field in ("native_counter_from_snapshot","native_time_from_snapshot"):
            out[field] = snapshots[i].get(field,"")
        rows.append(out)
    if spec.code == "nanokmc_bit_encoded" and rows[-1]["candidate_events_per_site"] != 30000:
        raise ValueError(f"{spec.run_id}: Classical must retain 30000 proposals/site")
    if spec.code == "spparks_diffusion_sweep_random" and rows[-1]["candidate_events_per_site"] != 60000:
        raise ValueError(f"{spec.run_id}: SPPARKS sweep must retain 60000 selections/site")
    return rows,distribution


def export_configurations(root,stage,completions,rows_by_run):
    from benchmark_core.snapshots import read_nanokmc_rasmol_xyz,write_rasmol_xyz
    from benchmark_core.common_snapshot import validate_canonical_snapshot
    configurations = []
    for spec,completion in completions:
        if spec.scenario.nx != 6 or spec.scenario.composition_A != 0.2 or spec.scenario.kT != 0.75 or spec.seed != 1:
            continue
        points = (0,3000,30000) if spec.code == BINARY else (3000,)
        periods = (2**spec.scenario.nx,)*3
        for point in points:
            snapshot = read_nanokmc_rasmol_xyz(completion.raw_dir,point,periods=periods)
            if snapshot is None or not validate_canonical_snapshot(snapshot).valid:
                raise ValueError(f"Missing/invalid configuration: {spec.run_id} at {point}")
            row = next(r for r in rows_by_run[spec.run_id] if r["requested_mcs"] == point)
            if (snapshot.count_A,snapshot.count_B) != (row["N_A"],row["N_B"]):
                raise ValueError(f"Configuration species mismatch: {spec.run_id} at {point}")
            from benchmark_core.run_store import run_directory
            target = Path("results/rasmol")/run_directory(root,spec).name
            a,b = write_rasmol_xyz(snapshot,stage/target,label_step=point)
            configurations.append(dict(run_set=completion.record.get("run_set"),run_id=spec.run_id,code=spec.code,requested_mcs=point,
                requested_jobs=row["requested_jobs"],execution_policy=row["execution_policy"],timing_policy=row["timing_policy"],
                realized_common_mcs=row["realized_common_mcs"],periods=list(periods),N_A=snapshot.count_A,N_B=snapshot.count_B,
                a_xyz=(target/a.name).as_posix(),b_xyz=(target/b.name).as_posix(),a_sha256=sha256(a),b_sha256=sha256(b),
                coordinates="integer doubled-conventional-cell FCC coordinates; periodic box",species_mapping="S0=A,S1=B; all sites retained",
                native_run_directory=relative(root,completion.raw_dir)))
    if len(configurations) != 13:
        raise ValueError(f"Expected thirteen configurations; found {len(configurations)}")
    return configurations

def file_entry(stage,path,rows=None):
    entry = dict(path=relative(stage,path),sha256=sha256(path),bytes=path.stat().st_size)
    if rows is not None:
        entry.update(rows=len(rows),columns=list(rows[0]))
    return entry

def implementation_guards(root,identities):
    """Cheap byte checks for plotting; no compiler or Python environment probing."""
    guards = {}
    for identity in identities.values():
        for name,digest in identity["harness_sha256"].items():
            guards[name] = dict(path=name,sha256=digest,repository_relative=True)
        for item in identity.get("_verification_files",[]):
            path = Path(item["path"]).resolve()
            local = path.is_relative_to(root)
            name = relative(root,path) if local else str(path)
            guards[name] = dict(path=name,sha256=item["sha256"],repository_relative=local)
    for name in ("dependencies.lock.json","benchmark_core/runtime_identity.py","scripts/process_results.py"):
        guards[name] = dict(path=name,sha256=sha256(root/name),repository_relative=True)
    return list(guards.values())

def verify_implementation_guards(root,guards):
    if not guards:
        raise ValueError("Source/build/harness guards missing; rerun scripts/windows/process_results.bat")
    for guard in guards:
        path = safe_path(root,guard["path"]) if guard["repository_relative"] else Path(guard["path"])
        if sha256(path) != guard["sha256"]:
            raise ValueError(f"Build/runtime/model/scientific harness changed: {guard['path']}; rerun scripts/windows/process_results.bat")

def process_publication(root,figures,paths_file,*,validate_only=False,source_root=None,run_set=None):
    from benchmark_core.run_store import verified_completed,current_identities,CampaignLock
    root = root.resolve()
    source_root = Path(source_root or root).resolve()
    figures = sorted(set(figures))
    if not figures or not set(figures).issubset(FIGURE_FILES):
        raise ValueError("Select Figures 5–10 only")
    with CampaignLock(source_root):
        return _process_locked(root,figures,paths_file,validate_only,verified_completed,current_identities,
                               source_root=source_root,run_set=run_set)

def _process_locked(root,figures,paths_file,validate_only,verified_completed,current_identities,*,source_root=None,run_set=None):
    source_root = Path(source_root or root).resolve()
    # Capture before reading the manifest or doing any potentially long checks.
    # Loaded Python code must never be attributed to bytes edited during a run.
    early_guards = {name:dict(path=name,sha256=sha256(source_root/name),repository_relative=True)
        for name in ("benchmark_core/publication.py","benchmark_core/figure_data.py",
                     "scripts/process_results.py","config/manuscript.json")}
    specs = required_runs(source_root,figures)
    jobs = infer_publication_jobs(root,specs,run_set)
    if run_set is not None:
        from benchmark_core.run_sets import check_run_set
        check_run_set(source_root,run_set,jobs)
    print(f"Required runs use a uniform requested job limit of {jobs}; native solver threads remain one.",flush=True)
    identities = current_identities(source_root,paths_file,verify=True,jobs=jobs,**({"run_set":run_set} if run_set is not None else {}))
    guards_by_path = {guard["path"]:guard for guard in implementation_guards(source_root,identities)}
    guards_by_path.update(early_guards)
    starting_guards = list(guards_by_path.values())
    verify_implementation_guards(source_root,starting_guards)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"_"+uuid.uuid4().hex[:8]
    stage = root/"results/.processing"/stamp
    stage.mkdir(parents=True)
    try:
        rows,completions,run_records,a_clusters,rows_by_run = [],[],[],{},{}
        distribution_path = stage/"results/processed/publication/cluster_distribution.csv"
        distribution_path.parent.mkdir(parents=True,exist_ok=True)
        with distribution_path.open("w",newline="",encoding="utf-8") as handle:
            writer = None
            for count,spec in enumerate(specs,1):
                completion = verified_completed(root,spec,expected_identity=identities[spec.code])
                if record_requested_jobs(completion.record) != jobs:
                    raise ValueError("Requested concurrency changed between metadata inspection and seal verification")
                normalized,distribution = normalize_run(spec,completion)
                rows.extend(normalized)
                representative = spec.scenario.nx == 6 and spec.scenario.composition_A == 0.2 and spec.scenario.kT == 0.75
                if representative:
                    # Large ensemble seals need not stay in memory: only A is
                    # used again for configuration export.
                    rows_by_run[spec.run_id] = normalized
                    completions.append((spec,completion))
                for cluster in distribution:
                    output = dict(run_set=run_set,run_id=spec.run_id,requested_jobs=jobs,requested_mcs=POINTS[int(cluster["save_index"])],**cluster)
                    if writer is None:
                        writer = csv.DictWriter(handle,fieldnames=list(output)); writer.writeheader()
                    writer.writerow(output)
                if representative:
                    a_clusters[spec.code] = [r for r in distribution if POINTS[int(r["save_index"])] == 3000]
                marker = completion.completion_path
                run_records.append(dict(run_set=run_set,run_id=spec.run_id,completion_path=relative(root,marker),completion_sha256=sha256(marker),
                    requested_jobs=jobs,active_jobs_at_launch=completion.record.get("active_jobs_at_launch",""),
                    source_identity_sha256=completion.record["identity_sha256"],raw_directory=relative(root,completion.raw_dir),
                    processed_directory=relative(root,completion.processed_dir)))
                if count == 1 or count%100 == 0 or count == len(specs):
                    print(f"Verified and processed {count}/{len(specs)} runs",flush=True)
        tables = derive_tables(rows,a_clusters,figures)
        report = dict(schema_version=1,scope="manuscript",test_only=False,publication_complete=True,selected_figures=figures,
            run_set=run_set,requested_jobs=jobs,concurrency=jobs,execution_policy="independent_single_thread_jobs",
            timing_policy=next(iter(identities.values()))["timing_policy"] if identities else "",
            concurrency_interpretation="requested maximum simultaneous independent single-thread solver jobs; actual overlap varies",
            created_utc=datetime.now(timezone.utc).isoformat(),run_count=len(specs),checkpoint_count=len(rows),generation=stamp,
            manuscript_manifest_sha256=early_guards["config/manuscript.json"]["sha256"],
            processor_sha256=early_guards["benchmark_core/publication.py"]["sha256"],
            files=[],configurations=[],runs=run_records,implementation_guards=starting_guards,
            scientific_conventions=dict(rho_AB="N_AB/(6N)",c_A_B="sum(s*C_s,s<12)/(N_B+sum(s*C_s,s<12))",
                timing="native cumulative evolution runtime; no overhead subtraction",pairing="state/seed/requested MCS",
                SEM="sample SD ddof=1/sqrt(n); unavailable at n=1",gamma="unweighted log OLS; sixteen points 30..30000",
                alpha="unweighted log OLS; four arithmetic size means"))
        verify_implementation_guards(source_root,starting_guards)
        if validate_only:
            return report
        for name,table in tables.items():
            path = stage/"results/csv"/name
            write_csv(path,table); report["files"].append(file_entry(stage,path,table))
        path = stage/"results/processed/publication/standardized_checkpoints.csv"
        write_csv(path,rows); report["files"].append(file_entry(stage,path,rows))
        report["files"].append(file_entry(stage,distribution_path))
        if 10 in figures:
            endpoints = [r for r in rows if r["requested_mcs"] == 30000 and r["x_A_nominal"] == 0.2 and r["T_star"] == 0.75]
            if len(endpoints) != 6435:
                raise ValueError("Scaling endpoint count must be 6435")
            path = stage/"results/processed/publication/scaling_endpoints.csv"
            write_csv(path,endpoints); report["files"].append(file_entry(stage,path,endpoints))
        if 5 in figures:
            report["configurations"] = export_configurations(root,stage,completions,rows_by_run)
            path = stage/"results/csv/fig05_configurations.json"
            path.write_text(json.dumps(report["configurations"],indent=2)+"\n",encoding="utf-8")
            report["files"].append(file_entry(stage,path))
            for config in report["configurations"]:
                for key in ("a_xyz","b_xyz"):
                    report["files"].append(file_entry(stage,stage/config[key]))
        for record in run_records:
            if sha256(root/record["completion_path"]) != record["completion_sha256"]:
                raise ValueError("A run completion changed during processing")
        verify_implementation_guards(source_root,report["implementation_guards"])
        for entry in report["files"]:
            src,dest = stage/entry["path"],root/entry["path"]
            dest.parent.mkdir(parents=True,exist_ok=True)
            os.replace(src,dest)
        marker = stage/"publication_manifest.json"
        marker.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n",encoding="utf-8")
        verify_implementation_guards(source_root,starting_guards)
        os.replace(marker,root/"results/csv/publication_manifest.json")
        return report
    finally:
        # This is our unique generated directory, never a source or input directory.
        if stage.resolve().is_relative_to((root/"results/.processing").resolve()):
            shutil.rmtree(stage,ignore_errors=True)

def load_publication(root,figures=None,manifest_path=None,*,allow_test_only=False,source_root=None,run_set=None):
    """Fail closed on partial, edited, stale or interrupted processing output."""
    root = root.resolve()
    source_root = Path(source_root or root).resolve()
    manifest_path = manifest_path or root/"results/csv/publication_manifest.json"
    report = json.loads(manifest_path.read_text(encoding="utf-8"))
    if run_set is not None and report.get("run_set") != run_set:
        raise ValueError("Publication manifest belongs to another run-set")
    if report.get("schema_version") != 1 or not report.get("publication_complete") or report.get("scope") != "manuscript":
        raise ValueError("Not a complete manuscript publication manifest; run scripts/windows/process_results.bat")
    if report.get("test_only") and not allow_test_only:
        raise ValueError("Synthetic test data cannot be used as manuscript results")
    if figures is not None and not set(figures).issubset(report["selected_figures"]):
        raise ValueError("Figure coverage is absent; rerun scripts/windows/process_results.bat with these --figures")
    paths = set()
    for entry in report["files"]:
        if entry["path"] in paths:
            raise ValueError("Duplicate file in publication manifest")
        paths.add(entry["path"])
        if sha256(safe_path(root,entry["path"])) != entry["sha256"]:
            raise ValueError(f"Changed file or interrupted processing: {entry['path']}; rerun scripts/windows/process_results.bat")
    for figure in figures or report["selected_figures"]:
        for name in FIGURE_FILES[figure]:
            if f"results/csv/{name}" not in paths:
                raise ValueError(f"Missing required figure data: {name}")
    if not report.get("test_only"):
        jobs = report.get("requested_jobs")
        if (type(jobs) is not int or not 1 <= jobs <= 32 or report.get("concurrency") != jobs
                or report.get("execution_policy") != "independent_single_thread_jobs" or not report.get("timing_policy")):
            raise ValueError("Publication concurrency/timing policy is missing or invalid; rerun scripts/windows/process_results.bat")
        if any(run.get("requested_jobs") != jobs or run.get("run_set") != report.get("run_set") for run in report["runs"]):
            raise ValueError("Publication manifest contains mixed requested job limits")
        if run_set is not None:
            from benchmark_core.run_sets import check_run_set
            check_run_set(source_root,run_set,jobs)
        if report.get("processor_sha256") != sha256(Path(__file__)) or report.get("manuscript_manifest_sha256") != sha256(source_root/"config/manuscript.json"):
            raise ValueError("Processing implementation or campaign manifest changed; rerun scripts/windows/process_results.bat")
        verify_implementation_guards(source_root,report.get("implementation_guards"))
        for run in report["runs"]:
            if sha256(safe_path(root,run["completion_path"])) != run["completion_sha256"]:
                raise ValueError(f"Run completion changed: {run['run_id']}; rerun scripts/windows/process_results.bat")
    return report
