"""Run manuscript selections with bounded independent single-thread workers.

Only sealed trajectories with identical runtime and --jobs policy are reused.
Unfinished/stale trajectories restart from their seeds in new attempt directories.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
    os.environ[_key] = "1"

from benchmark_core.campaigns import add_selection_arguments, enumerate_runs
from benchmark_core.runner import PATH_IDS, get_adapter
from benchmark_core.scenario import load_paths
from benchmark_core.process_tree import ProcessTree
from benchmark_core.run_sets import add_run_set_argument, data_root, check_run_set, unsealed_status
from benchmark_core.run_store import (MAX_JOBS, CampaignLock, CompletionError, assert_harness_unchanged,
    atomic_json, create_attempt, current_identities, identity_for_jobs, mark_attempt, recorded_jobs,
    run_directory, seal_completed, utc_now, verified_completed, checked_attempt, checked_output_path)


def execute_attempt(root, spec, paths, identity, attempt, adapter_factory=get_adapter, source_root=None):
    """One parent-created attempt; its worker exclusively writes the attempt."""
    # Validate outside the failure handler: a rejected redirection must not even
    # receive an attempt_status write through the redirected path.
    attempt = checked_attempt(root,spec,attempt)
    try:
        assert_harness_unchanged(source_root or root, identity)
        mark_attempt(attempt, "running")
        adapter = adapter_factory(spec.code, paths)
        adapter.prepare(spec.scenario, spec.seed, attempt / "native")
        adapter.run(spec.scenario, spec.seed, attempt / "native")
        adapter.collect(spec.scenario, spec.seed, attempt / "native", attempt / "processed")
        assert_harness_unchanged(source_root or root, identity)
        return seal_completed(root, spec, identity, attempt)
    except BaseException as exc:
        try:
            sealed = verified_completed(root, spec, identity).raw_dir.parent == attempt
        except CompletionError:
            sealed = False
        if not sealed:
            mark_attempt(attempt, "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed",
                         error=str(exc), exception_type=type(exc).__name__, traceback=traceback.format_exc())
        raise


class WorkerProcess:
    """Own worker + native descendants before the worker can execute any code."""
    def __init__(self, command, log_prefix, cwd=ROOT):
        self.process, self.handles = None, []
        self.tree = ProcessTree()
        try:
            log_prefix = Path(log_prefix)
            log_prefix.parent.mkdir(parents=True, exist_ok=True)
            for suffix in (".stdout.log", ".stderr.log"):
                self.handles.append(log_prefix.with_suffix(suffix).open("wb"))
            flags = (subprocess.CREATE_NEW_PROCESS_GROUP | 0x4) if os.name == "nt" else 0
            self.process = subprocess.Popen(command, cwd=cwd, env=os.environ.copy(), stdin=subprocess.DEVNULL,
                stdout=self.handles[0], stderr=self.handles[1], creationflags=flags,
                start_new_session=os.name != "nt")
            self.tree.attach(self.process)
            self.tree.resume(self.process)
        except BaseException:
            self.cancel()
            self.close()
            raise

    def poll(self):
        return self.process.poll()

    def cancel(self):
        if self.process is not None:
            self.tree.kill(self.process)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
                raise RuntimeError("Worker tree did not exit when its ownership job closed; worker was forcibly terminated")

    def close(self):
        self.tree.close()
        for handle in self.handles:
            handle.close()


@contextmanager
def _defer_sigint():
    """Register a newly created worker before honoring Ctrl+C."""
    interrupted = []
    previous = signal.signal(signal.SIGINT, lambda *_: interrupted.append(True))
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
    if interrupted:
        raise KeyboardInterrupt()


def bounded_dispatch(items, jobs, start, completed, cancelled, pause=time.sleep):
    """No more than jobs items ahead; ALL active workers stopped on any failure.

    The caller holds CampaignLock. Lifecycle callbacks allow offline fake tests.
    Logs are outside immutable attempts and closed before accepting worker exit.
    """
    iterator, active, exhausted = iter(items), [], False
    try:
        while active or not exhausted:
            while not exhausted and len(active) < jobs:
                try:
                    item = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                with _defer_sigint():
                    observed_live = sum(worker.poll() is None for _, worker in active)
                    worker = start(item, observed_live + 1)
                    active.append((item, worker))
            for item, worker in list(active):
                returncode = worker.poll()
                if returncode is not None:
                    worker.close()
                    completed(item, worker, returncode)
                    active.remove((item, worker))
            if active:
                pause(.1)
    finally:
        # Kill every tree before waiting/validating, including grandchildren.
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        cleanup_errors = []
        try:
            for item, worker in active:
                try:
                    worker.cancel()
                except BaseException as exc:
                    cleanup_errors.append(exc)
            for item, worker in active:
                try:
                    worker.close()
                    cancelled(item, worker)
                except BaseException as exc:
                    cleanup_errors.append(exc)
        finally:
            signal.signal(signal.SIGINT, previous)
        if cleanup_errors:
            raise RuntimeError("Worker cleanup failed: " + "; ".join(str(exc) for exc in cleanup_errors))


def _summary(specs, jobs):
    groups = Counter((s.scenario.id, s.scenario.nx) for s in specs)
    return {"selected_unique_runs": len(specs), "expected_checkpoint_rows": sum(len(s.scenario.mcs_points) for s in specs),
            "concurrency": jobs, "requested_jobs": jobs, "execution_policy": "independent_single_thread_jobs",
            "campaign_A_is_reused_by_B_and_C": True,
            "scenarios": [{"scenario_id": key[0], "k": key[1], "runs": count} for key, count in groups.items()]}


def _inspect(specs, identities=None, verbose=False, jobs=None, root=ROOT):
    states, details = Counter(), []
    for spec in specs:
        inferred = jobs
        try:
            identity = (identities or {}).get(spec.code)
            pointer = run_directory(root, spec) / "complete.json"
            if identity is not None and jobs is None and pointer.is_file():
                inferred = recorded_jobs(json.loads(pointer.read_text(encoding="utf-8-sig")))
            if identity is not None:
                identity = identity_for_jobs(identity, inferred or 1)
            complete = verified_completed(root, spec, identity)
            state, reason = "complete", complete.record["attempt_directory"]
            inferred = recorded_jobs(complete.record)
        except CompletionError as exc:
            state, reason = exc.status, str(exc)
            if state in ("missing", "incomplete"):
                state, reason = unsealed_status(run_directory(root, spec), ROOT)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            state, reason = "corrupt", str(exc)
        states[state] += 1
        details.append({"run_id": spec.run_id, "status": state, "detail": reason, "requested_jobs": inferred})
        if verbose:
            print(f"{state.upper():10s} {spec.run_id}: jobs={inferred}; {reason}", flush=True)
    return {"counts": dict(states), "runs": details}


def load_guarded_worker_paths(root, paths_file, identity):
    configuration = [record for record in identity.get("_verification_files", [])
                     if record.get("role") == "path_configuration"]
    if len(configuration) != 1 or Path(configuration[0]["path"]).resolve() != Path(paths_file).resolve():
        raise RuntimeError("Worker path configuration is not the file verified by its parent")
    assert_harness_unchanged(root, identity)
    paths = load_paths(paths_file)
    assert_harness_unchanged(root, identity)
    return paths


def _worker_main(request_path, paths_file):
    request_path = Path(request_path).absolute()
    relative = request_path.relative_to(ROOT.absolute())
    if len(relative.parts) != 7 or relative.parts[0] != "run-sets":
        raise ValueError("Worker request is outside the canonical run-set layout")
    checked_output_path(data_root(ROOT,relative.parts[1]),request_path,directory=False)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    specs = enumerate_runs(states=[request["scenario"]["id"]], solvers=[request["code"]], seeds=[request["seed"]])
    if len(specs) != 1 or any(request.get(key) != value for key, value in specs[0].scientific_identity().items()):
        raise ValueError("Worker request differs from locked manuscript run")
    spec = specs[0]
    dataset = data_root(ROOT, request["run_set"])
    checked_attempt(dataset,spec,request_path.parent)
    check_run_set(ROOT, request["run_set"], recorded_jobs(request))
    if request_path.parent.parent != run_directory(dataset, spec).resolve() or request_path.name != "request.json":
        raise ValueError("Worker request is outside its canonical attempt")
    recorded_jobs(request)
    identity = request["execution_identity"]
    if identity.get("run_set") != request["run_set"]:
        raise ValueError("Worker execution identity belongs to a different run-set")
    paths = load_guarded_worker_paths(ROOT, paths_file, identity)
    execute_attempt(dataset, spec, paths, identity, request_path.parent, source_root=ROOT)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", type=Path, default=ROOT / "config/paths.local.json")
    add_selection_arguments(parser)
    add_run_set_argument(parser, required=False)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="Print EVERY exact selected run; no builds/solvers or verified reuse")
    modes.add_argument("--status", action="store_true", help="Validate seals/identities; omitted --jobs infers each record's jobs")
    modes.add_argument("--list-solvers", action="store_true", help="Print eleven canonical solver IDs and exit")
    parser.add_argument("--jobs", type=int, help=f"Independent single-thread workers, 1..{MAX_JOBS}; default 1; part of identity")
    reruns = parser.add_mutually_exclusive_group()
    reruns.add_argument("--resume", action="store_true", help="Default: skip only verified completed runs with identical --jobs")
    reruns.add_argument("--rerun", action="store_true", help="New attempts for all selected runs; preserve old attempts")
    parser.add_argument("--limit", type=int, help="Maximum NEW attempts this invocation; does not change the manuscript manifest")
    parser.add_argument("--verbose", action="store_true", help="List every run in status mode; dry-run always lists all")
    parser.add_argument("--json", type=Path, help="Write full dry-run/status report to this JSON path")
    parser.add_argument("--_worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.jobs is not None and not 1 <= args.jobs <= MAX_JOBS:
        parser.error(f"--jobs must be within 1..{MAX_JOBS}")
    try:
        if args._worker:
            return _worker_main(args._worker, args.paths)
        if args.list_solvers:
            print("\n".join(PATH_IDS))
            return 0
        if args.run_set is None:
            parser.error("--run-set NAME is required (except --list-solvers/help)")
        dataset = data_root(ROOT, args.run_set)
        jobs = args.jobs or 1
        check_run_set(ROOT, args.run_set, args.jobs if args.status else jobs)
        print(f"Run-set: {args.run_set}; output root: {dataset}")
        specs = enumerate_runs(campaign=args.campaign, solvers=args.solvers, states=args.states,
                               sizes=args.sizes, seeds=args.seeds)
        summary = _summary(specs, None if args.status and args.jobs is None else jobs)
        summary["run_set"] = args.run_set
        print(f"Selected {len(specs)} unique runs, {summary['expected_checkpoint_rows']} requested observations; "
              f"jobs={'infer from records' if summary['requested_jobs'] is None else jobs}; single-thread solvers.")
        for item in summary["scenarios"]:
            print(f"  k={item['k']}: {item['runs']} runs | {item['scenario_id']}")
        # data_root/check_run_set already reject redirected or malformed roots.
        # An absent raw root has no per-run records to inspect. Observe its
        # absence once rather than resolving thousands of nonexistent paths.
        raw_missing = (args.dry_run or args.status) and not (dataset / "results/raw").exists()
        if args.dry_run:
            print("DRY RUN: full candidate selection below; runtime/reuse NOT verified. No simulations or builds run.")
            rows = []
            for spec in specs:
                prior = ("existing_unverified" if not raw_missing and
                         (run_directory(dataset, spec) / "complete.json").is_file() else "no_sealed_result")
                rows.append({**spec.scientific_identity(), "run_set": args.run_set, "requested_jobs": jobs, "concurrency": jobs,
                             "existing_completion": prior, "reuse": "not_verified"})
                print(f"{spec.run_id} | requested_jobs={jobs} | {prior} | reuse=not_verified")
            if args.limit:
                print(f"Execution would launch at most {args.limit} new attempts after strict reuse checks.")
            if args.json:
                atomic_json(args.json, {**summary, "mode": "dry_run", "limit": args.limit,
                                       "execution_identity_status": "not_verified_dry_run", "runs": rows})
            return 0
        if args.status:
            if raw_missing:
                details = [{"run_id":spec.run_id,"status":"pending",
                            "detail":"No raw result directory existed at status inspection",
                            "requested_jobs":args.jobs} for spec in specs]
                inspection = {"counts":{"pending":len(specs)},"runs":details}
                if args.verbose:
                    for row in details:
                        print(f"PENDING    {row['run_id']}: jobs={row['requested_jobs']}; {row['detail']}",flush=True)
            else:
                has_completions = any((run_directory(dataset, spec) / "complete.json").is_file() for spec in specs)
                identities = current_identities(ROOT, args.paths, jobs=jobs, run_set=args.run_set) if has_completions else None
                inspection = _inspect(specs, identities, args.verbose, args.jobs, root=dataset)
            report = {**summary, "mode": "status", "checked_utc": utc_now(),
                      **inspection}
            print("Completion status: " + ", ".join(f"{key}={value}" for key, value in report["counts"].items()))
            if args.json:
                atomic_json(args.json, report)
            return 0
        with CampaignLock(ROOT):
            print("Verifying frozen sources, binaries, models and the declared environment...", flush=True)
            identities = current_identities(ROOT, args.paths, jobs=jobs, run_set=args.run_set)
            check_run_set(ROOT, args.run_set, jobs, create=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            history = dataset / "logs" / f"campaign_{stamp}.jsonl"
            worker_logs = dataset / "logs" / f"campaign_{stamp}_workers"
            progress_path = dataset / "logs/campaign_latest.json"
            progress = {**summary, "started_utc": utc_now(), "status": "running", "pid": os.getpid(),
                        "selection": {k: getattr(args, k) for k in ("campaign", "solvers", "states", "sizes", "seeds")},
                        "history": history.relative_to(dataset).as_posix(), "new_completed": 0, "reused": 0,
                        "failed": 0, "new_attempts": 0, "limit": args.limit, "active_runs": []}
            atomic_json(progress_path, progress)
            attempts, accepted = {}, set()
            try:
                with history.open("a", encoding="utf-8") as events:
                    def publish(spec, status, **extra):
                        events.write(json.dumps({"utc": utc_now(), "run_id": spec.run_id, "status": status, **extra}) + "\n")
                        events.flush()
                        progress["updated_utc"] = utc_now()
                        atomic_json(progress_path, progress)

                    def pending():
                        for index, spec in enumerate(specs, 1):
                            if not args.rerun:
                                try:
                                    complete = verified_completed(dataset, spec, identities[spec.code])
                                    progress["reused"] += 1
                                    publish(spec, "reused", attempt=complete.record["attempt_directory"])
                                    continue
                                except CompletionError as exc:
                                    prior = exc.status
                            else:
                                prior = "explicit_rerun"
                            if args.limit is not None and progress["new_attempts"] >= args.limit:
                                return
                            yield index, spec, prior

                    def start(item, active_count):
                        index, spec, prior = item
                        attempt = create_attempt(dataset, spec, identities[spec.code], active_count)
                        attempts[spec.run_id] = attempt
                        progress["new_attempts"] += 1
                        progress["active_runs"].append(spec.run_id)
                        publish(spec, "started", attempt=attempt.relative_to(dataset).as_posix(),
                                prior_status=prior, active_jobs_at_launch=active_count, requested_jobs=jobs)
                        print(f"[{index}/{len(specs)}] START {spec.run_id} ({prior}); active={active_count}/{jobs}", flush=True)
                        try:
                            return WorkerProcess([sys.executable, "-B", str(Path(__file__).resolve()),
                                "--_worker", str(attempt / "request.json"), "--paths", str(args.paths.resolve())],
                                worker_logs / run_directory(dataset, spec).name)
                        except BaseException as exc:
                            mark_attempt(attempt, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", error=str(exc))
                            progress["active_runs"].remove(spec.run_id)
                            raise

                    def accept(spec, complete):
                        if spec.run_id not in accepted:
                            accepted.add(spec.run_id)
                            progress["new_completed"] += 1
                            if spec.run_id in progress["active_runs"]:
                                progress["active_runs"].remove(spec.run_id)
                            publish(spec, "complete", attempt=complete.record["attempt_directory"])
                            print(f"PASS {spec.run_id}: all {len(spec.scenario.mcs_points)} checkpoints sealed", flush=True)

                    def completed(item, worker, returncode):
                        spec = item[1]
                        if returncode != 0:
                            raise RuntimeError(f"Worker failed ({returncode}) for {spec.run_id}; inspect {worker_logs}")
                        complete = verified_completed(dataset, spec, identities[spec.code])
                        if complete.raw_dir.parent != attempts[spec.run_id]:
                            raise RuntimeError("Worker exited without publishing its own complete attempt")
                        accept(spec, complete)

                    def cancelled(item, worker):
                        spec = item[1]
                        try:
                            complete = verified_completed(dataset, spec, identities[spec.code])
                            if complete.raw_dir.parent == attempts[spec.run_id]:
                                accept(spec, complete)
                                return
                        except CompletionError:
                            pass
                        status_path = attempts[spec.run_id] / "attempt_status.json"
                        try:
                            previous_status = json.loads(status_path.read_text(encoding="utf-8")).get("status")
                        except (OSError, ValueError):
                            previous_status = None
                        if previous_status != "failed":
                            mark_attempt(attempts[spec.run_id], "interrupted", error="Scheduler stopped; original seed required for restart")
                        if spec.run_id in progress["active_runs"]:
                            progress["active_runs"].remove(spec.run_id)
                        publish(spec, "failed" if previous_status == "failed" else "interrupted")

                    bounded_dispatch(pending(), jobs, start, completed, cancelled)
                progress["status"] = ("complete" if progress["reused"] + progress["new_completed"] == len(specs) else "batch_limit_reached")
            except BaseException as exc:
                progress["status"] = "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed"
                progress["error"] = str(exc)
                progress["failed"] = int(progress["status"] == "failed")
                raise
            finally:
                progress["finished_utc"] = utc_now()
                atomic_json(progress_path, progress)
            print(f"{progress['status']}: {progress['new_completed']} new complete, {progress['reused']} reused. {progress_path}")
        return 0
    except KeyboardInterrupt:
        print("Interrupted: all owned workers stopped. Sealed runs remain reusable; unfinished runs restart from their seeds.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("No incomplete/stale result accepted. Inspect worker logs/attempts, correct the cause, repeat the command.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
