"""Immutable run attempts, strict completion seals and a single-runner OS lock.

Resume means retaining verified complete trajectories and restarting unfinished
trajectories from their original seeds in new directories. It does not restore
unsupported native solver state from a partial snapshot.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import uuid

from benchmark_core.campaigns import RunSpec
from benchmark_core.runner import PATH_IDS
from benchmark_core.validation import sha256, validate_collection

SCHEMA_VERSION = 2
MAX_JOBS = 32
THREAD_POLICY = {name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS")}
REQUIRED_PROCESSED = {"metrics_common.csv", "timing_common.csv", "snapshot_manifest_common.csv",
                      "cluster_distribution_common.csv", "run_metadata.json"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest_object(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class CompletionError(ValueError):
    def __init__(self, message: str, status: str = "corrupt"):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Completion:
    record: dict
    raw_dir: Path
    processed_dir: Path
    completion_path: Path


class CampaignLock(AbstractContextManager):
    """Kernel lock survives stale lock-file contents, but releases on process exit.

    The file remains for owner diagnostics. Its mere existence never means that
    a campaign is active. Do not delete it while another command is running.
    """
    def __init__(self, root: Path):
        self.path = Path(root) / "logs/campaign.lock"
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b" ")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("Another campaign or processing command holds logs/campaign.lock. "
                               "Wait for it or stop it normally; do not delete the lock file.") from exc
        self.handle.seek(1)
        self.handle.truncate()
        self.handle.write(json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                                      "acquired_utc": utc_now()}).encode("utf-8"))
        self.handle.flush()
        return self

    def __exit__(self, *args):
        if self.handle is not None:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
        return False


def checked_output_path(root: Path, path: Path, *, directory=True) -> Path:
    """Reject redirected output components before reading or creating children."""
    root, path = Path(root).absolute(), Path(path).absolute()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise CompletionError("Output path leaves its canonical data root") from exc
    if any(part in (".", "..") for part in relative.parts):
        raise CompletionError("Output paths must not contain relative traversal")
    components = [root]
    for part in relative.parts:
        components.append(components[-1] / part)
    for component in components:
        if component.is_symlink() or (hasattr(component,"is_junction") and component.is_junction()):
            raise CompletionError(f"Output paths must not contain links or junctions: {component}")
        if component.resolve() != component:
            raise CompletionError(f"Output path resolves outside its canonical location: {component}")
        if component.exists() and (component != path or directory) and not component.is_dir():
            raise CompletionError(f"Output directory is not a directory: {component}")
    return path


def run_directory(root: Path, spec: RunSpec) -> Path:
    # These components originate in the locked manifest, not an arbitrary path selector.
    for component in (spec.scenario.id, spec.code):
        if not component or any(ch in component for ch in "/\\:") or component in {".", ".."}:
            raise ValueError("Invalid run path component")
    # Full identities remain in every request/seal. Compact paths avoid
    # native Windows I/O silently dropping files at the MAX_PATH boundary.
    key = digest_object(spec.scientific_identity())[:24]
    directory = checked_output_path(root, Path(root) / "results/raw" / key)
    checked_output_path(root,directory/"complete.json",directory=False)
    # Even abandoned attempts must not redirect reads/writes to another dataset.
    if directory.is_dir():
        for attempt in directory.glob("attempt_*"):
            checked_output_path(root,attempt)
    return directory


def checked_attempt(root: Path, spec: RunSpec, attempt: Path) -> Path:
    attempt = checked_output_path(root,attempt)
    if attempt.parent != run_directory(root,spec) or not attempt.name.startswith("attempt_"):
        raise CompletionError("Attempt is outside its canonical run directory")
    for component in ("native","processed"):
        checked_output_path(root,attempt/component)
    for component in ("request.json","attempt_status.json"):
        checked_output_path(root,attempt/component,directory=False)
    return attempt


def check_native_path_length(attempt: Path, spec: RunSpec, *, windows=None):
    check_native_output_path(Path(attempt) / "native", spec.code, windows=windows)


def check_native_output_path(native_directory: Path, code: str, *, windows=None):
    """Fail before native execution, even when Windows/Python long paths work."""
    if windows is None:
        windows = os.name == "nt"
    if windows and code.startswith("nanokmc_"):
        longest = Path(native_directory).resolve() / "evaluation/Rasmol/00030000_S0.xyz"
        length = len(str(longest).encode("utf-16-le")) // 2
        if length > 259:
            raise ValueError(f"NanoKMC native output path would exceed 259 Windows path characters ({length}): "
                             f"{longest}. Use a shorter clone location or --run-set name before running.")


def identity_hash(spec: RunSpec, execution_identity: dict) -> str:
    # Absolute guard paths are operational metadata. Their content hashes are
    # already covered by the runtime fingerprint, so relocation alone is not stale.
    semantic = {key: value for key, value in execution_identity.items() if key != "_verification_files"}
    return digest_object({"run": spec.scientific_identity(), "execution": semantic})


def identity_for_jobs(identity: dict, jobs: int) -> dict:
    if type(jobs) is not int or not 1 <= jobs <= MAX_JOBS:
        raise ValueError(f"--jobs must be an integer within 1..{MAX_JOBS}")
    return {**identity, "requested_jobs": jobs, "concurrency": jobs,
            "execution_policy": "independent_single_thread_jobs"}


def recorded_jobs(record: dict) -> int:
    if not isinstance(record, dict) or not isinstance(record.get("execution_identity"), dict):
        raise CompletionError("Completion/request or execution identity is not an object")
    identity = record["execution_identity"]
    jobs = identity.get("requested_jobs")
    if type(jobs) is not int or not 1 <= jobs <= MAX_JOBS:
        raise CompletionError("Missing or invalid requested_jobs in execution identity", "stale")
    if (any(type(value) is not int for value in (identity.get("concurrency"), record.get("requested_jobs"), record.get("concurrency")))
            or identity.get("concurrency") != jobs or record.get("requested_jobs") != jobs
            or record.get("concurrency") != jobs
            or identity.get("execution_policy") != "independent_single_thread_jobs"):
        raise CompletionError("Completion/request concurrency policy is inconsistent")
    return jobs


def current_identities(root: Path, paths_file: Path, verify: bool = True, jobs: int = 1, run_set: str | None = None) -> dict[str, dict]:
    """Stable identity: source/build/runtime plus scientific harness bytes, no log dates.

    ``verify`` is retained for callers; both modes verify rather than permitting
    an unsafe identity-only resume. This function never runs a simulation.
    """
    from benchmark_core.runtime_identity import current_runtime_identity
    identity_for_jobs({}, jobs)
    root = Path(root).resolve()
    runtime = current_runtime_identity(Path(paths_file))
    common = ["adapters/base_adapter.py", "benchmark_core/runner.py", "benchmark_core/scenario.py", "benchmark_core/timing.py",
              "benchmark_core/process_tree.py", "benchmark_core/runtime_identity.py",
              "benchmark_core/snapshots.py", "benchmark_core/common_snapshot.py", "benchmark_core/metrics.py",
              "benchmark_core/validation.py", "benchmark_core/campaigns.py", "benchmark_core/run_store.py", "benchmark_core/run_sets.py",
              "scripts/run_manuscript_campaigns.py"]
    adapter_files = {"nanokmc": "adapters/nanokmc_adapter.py", "spparks": "adapters/spparks_adapter.py",
                     "kmcos": "adapters/kmcos_adapter.py", "kmc_lattice": "adapters/kmc_lattice_adapter.py"}
    results = {}
    for code in PATH_IDS:
        family = "kmc_lattice" if code.startswith("kmc_lattice_") else code.split("_")[0]
        harness = {name: sha256(root / name) for name in common + [adapter_files[family]]}
        results[code] = identity_for_jobs({"schema_version": SCHEMA_VERSION, "scope": "manuscript", "code": code,
                         "run_set": run_set,
                         "runtime_fingerprint": runtime["fingerprint"], "runtime": runtime["details"],
                         "harness_sha256": harness,
                         "_verification_files": runtime.get("verification_files", []),
                         "single_thread_environment": THREAD_POLICY,
                         "timing_policy": "native_evolution_intervals_no_overhead_subtraction_v1"}, jobs)
    return results


def assert_harness_unchanged(root: Path, identity: dict) -> None:
    for name, expected in identity.get("harness_sha256", {}).items():
        if sha256(Path(root) / name) != expected:
            raise RuntimeError(f"Harness changed during the campaign: {name}; stop and restart after review")
    for record in identity.get("_verification_files", []):
        if sha256(Path(record["path"])) != record["sha256"]:
            raise RuntimeError(f"Runtime/build/model changed during the campaign: {record['path']}")


def create_attempt(root: Path, spec: RunSpec, execution_identity: dict, active_jobs_at_launch: int = 1) -> Path:
    _check_dataset_identity(root,execution_identity)
    jobs = execution_identity["requested_jobs"]
    recorded_jobs({"execution_identity": execution_identity, "requested_jobs": jobs, "concurrency": jobs})
    if type(active_jobs_at_launch) is not int or not 1 <= active_jobs_at_launch <= jobs:
        raise ValueError("Observed active workers must be within the requested jobs limit")
    attempt = run_directory(root, spec) / f"attempt_{uuid.uuid4().hex[:12]}"
    checked_attempt(root,spec,attempt)
    check_native_path_length(attempt, spec)
    (attempt / "native").mkdir(parents=True, exist_ok=False)
    (attempt / "processed").mkdir()
    atomic_json(attempt / "request.json", {"schema_version": SCHEMA_VERSION, "scope": "manuscript",
        **spec.scientific_identity(), "campaigns": spec.campaigns, "created_utc": utc_now(),
        "identity_sha256": identity_hash(spec, execution_identity), "execution_identity": execution_identity,
        "run_set": execution_identity.get("run_set"),
        "concurrency": jobs, "requested_jobs": jobs, "active_jobs_at_launch": active_jobs_at_launch,
        "resume_semantics": "unfinished trajectories restart from the original seed"})
    mark_attempt(attempt, "running")
    return attempt


def mark_attempt(attempt: Path, status: str, **details) -> None:
    from benchmark_core.run_sets import process_token
    atomic_json(attempt / "attempt_status.json", {"status": status, "updated_utc": utc_now(),
                "pid": os.getpid(), "host": socket.gethostname(), "process_token": process_token(os.getpid()), **details})


def _file_inventory(attempt: Path) -> dict:
    files = {}
    pending = [attempt]
    while pending:
        for path in sorted(pending.pop().iterdir()):
            checked_output_path(attempt,path,directory=False)
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                files[path.relative_to(attempt).as_posix()] = {"size": path.stat().st_size, "sha256": sha256(path)}
    return files


def _check_request(attempt: Path, spec: RunSpec, execution_identity: dict) -> None:
    request = json.loads((attempt / "request.json").read_text(encoding="utf-8-sig"))
    if not isinstance(request, dict) or request.get("scope") != "manuscript":
        raise CompletionError("Attempt request scope differs from publication policy")
    jobs = recorded_jobs(request)
    if request.get("run_set") != execution_identity.get("run_set"):
        raise CompletionError("Attempt request run-set identity differs")
    if type(request.get("active_jobs_at_launch")) is not int or not 1 <= request["active_jobs_at_launch"] <= jobs:
        raise CompletionError("Attempt request active worker count is invalid")
    for key, value in spec.scientific_identity().items():
        if request.get(key) != value:
            raise CompletionError(f"Attempt request scientific identity differs: {key}")
    expected = identity_hash(spec, execution_identity)
    if request.get("identity_sha256") != expected or identity_hash(spec, request["execution_identity"]) != expected:
        raise CompletionError("Attempt request build/runtime/harness identity differs from the completion")


def _check_dataset_identity(root,identity):
    name = identity.get("run_set")
    if name is not None:
        from benchmark_core.run_sets import validate_name
        validate_name(name)
        root = Path(root).resolve()
        if root.name != name or root.parent.name != "run-sets":
            raise CompletionError("Execution identity belongs to another run-set output directory")


def seal_completed(root: Path, spec: RunSpec, execution_identity: dict, attempt: Path) -> Completion:
    """Only this atomic publication step can make an attempt eligible for reuse."""
    root = Path(root).absolute()
    attempt = checked_attempt(root,spec,attempt)
    _check_dataset_identity(root,execution_identity)
    expected_parent = run_directory(root, spec).resolve()
    if attempt.parent != expected_parent or not attempt.name.startswith("attempt_"):
        raise ValueError("Attempt is outside its canonical run directory")
    _check_request(attempt, spec, execution_identity)
    request = json.loads((attempt / "request.json").read_text(encoding="utf-8"))
    concurrency = {key: request[key] for key in ("run_set", "requested_jobs", "concurrency", "active_jobs_at_launch")}
    raw, processed = attempt / "native", attempt / "processed"
    missing = REQUIRED_PROCESSED - {p.name for p in processed.iterdir() if p.is_file()}
    if missing:
        raise ValueError(f"Missing required processed files: {sorted(missing)}")
    external = json.loads((raw / "external_timing.json").read_text(encoding="utf-8-sig"))
    if external.get("returncode") != 0:
        raise ValueError("Native solver exit was not successful")
    validation = validate_collection(spec.scenario, spec.code, spec.seed, processed)
    metadata_path = processed / "run_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    metadata.update({"scope": "manuscript", "run_id": spec.run_id, "scenario": asdict(spec.scenario),
                     "code": spec.code, "seed": spec.seed, "campaigns": spec.campaigns,
                     "execution_identity": execution_identity, **concurrency,
                     "identity_sha256": identity_hash(spec, execution_identity), "strict_completion": "passed"})
    atomic_json(metadata_path, metadata)
    mark_attempt(attempt, "complete", validation=validation)
    record = {"schema_version": SCHEMA_VERSION, "scope": "manuscript", "status": "complete",
        **spec.scientific_identity(), "campaigns": spec.campaigns, "completed_utc": utc_now(),
        "identity_sha256": identity_hash(spec, execution_identity), "execution_identity": execution_identity, **concurrency,
        "attempt_directory": attempt.relative_to(root).as_posix(), "raw_directory": (raw.relative_to(root)).as_posix(),
        "processed_directory": processed.relative_to(root).as_posix(), "validation": validation,
        "files": _file_inventory(attempt)}
    completion_path = expected_parent / "complete.json"
    atomic_json(completion_path, record)
    return Completion(record, raw, processed, completion_path)


def verified_completed(root: Path, spec: RunSpec, expected_identity: dict | str | None = None) -> Completion:
    root = Path(root).absolute()
    run_dir = run_directory(root, spec)
    pointer = checked_output_path(root,run_dir / "complete.json",directory=False)
    if not pointer.is_file():
        incomplete = run_dir.is_dir() and any(run_dir.glob("attempt_*"))
        raise CompletionError("No sealed complete attempt", "incomplete" if incomplete else "missing")
    try:
        record = json.loads(pointer.read_text(encoding="utf-8-sig"))
        if not isinstance(record, dict):
            raise CompletionError("Completion record is not an object")
        if record.get("schema_version") != SCHEMA_VERSION or record.get("status") != "complete" or record.get("scope") != "manuscript":
            raise CompletionError("Completion schema/status/scope is invalid")
        for key, value in spec.scientific_identity().items():
            if record.get(key) != value:
                raise CompletionError(f"Completion scientific identity differs: {key}", "stale")
        embedded = record["execution_identity"]
        recorded_jobs(record)
        _check_dataset_identity(root,embedded)
        if record.get("run_set") != embedded.get("run_set"):
            raise CompletionError("Completion run-set identity is inconsistent")
        if record["identity_sha256"] != identity_hash(spec, embedded):
            raise CompletionError("Completion execution identity digest is inconsistent")
        expected_hash = (identity_hash(spec, expected_identity) if isinstance(expected_identity, dict) else expected_identity)
        if expected_hash is not None and record["identity_sha256"] != expected_hash:
            raise CompletionError("Source/build/runtime/model/harness identity has changed", "stale")
        attempt = checked_attempt(root,spec,root / record["attempt_directory"])
        _check_request(attempt, spec, embedded)
        request = json.loads((attempt / "request.json").read_text(encoding="utf-8"))
        if record.get("active_jobs_at_launch") != request.get("active_jobs_at_launch"):
            raise CompletionError("Completion active worker count differs from its sealed request")
        raw, processed = attempt / "native", attempt / "processed"
        if (root / record["raw_directory"]).resolve() != raw or (root / record["processed_directory"]).resolve() != processed:
            raise CompletionError("Completion directory mapping is inconsistent")
        actual = _file_inventory(attempt)
        if actual != record["files"]:
            changed = sorted(set(actual) ^ set(record["files"]))
            if not changed:
                changed = [name for name in actual if actual[name] != record["files"][name]]
            raise CompletionError(f"Sealed native/processed files changed or are missing: {changed[:5]}")
        if not {"processed/" + name for name in REQUIRED_PROCESSED} <= set(actual):
            raise CompletionError("Required processed products are absent from the seal")
        if json.loads((raw / "external_timing.json").read_text(encoding="utf-8-sig")).get("returncode") != 0:
            raise CompletionError("Native exit status is invalid")
        validation = validate_collection(spec.scenario, spec.code, spec.seed, processed)
        if validation != record["validation"]:
            raise CompletionError("Scientific validation differs from the sealed validation")
        return Completion(record, raw, processed, pointer)
    except CompletionError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise CompletionError(f"Cannot verify completion: {exc}") from exc
