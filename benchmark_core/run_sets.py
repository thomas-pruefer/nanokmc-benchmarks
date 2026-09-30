"""Portable, explicit output namespaces for independent execution datasets."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import socket

_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_DEVICES = {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$"} | {
    f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(1, 10)}


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name in _DEVICES:
        raise ValueError("--run-set must be 1..64 lowercase ASCII letters/digits/hyphens/underscores, "
                         "start with a letter/digit, and not be a Windows device name")
    return name


def cli_name(value):
    try:
        return validate_name(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def add_run_set_argument(parser, *, required=True):
    parser.add_argument("--run-set", type=cli_name, required=required, metavar="NAME",
                        help="Named independent dataset under run-sets/NAME (lowercase ASCII)")


def data_root(repository: Path, name: str) -> Path:
    """Validate without creating directories; reject links and case aliases."""
    name = validate_name(name)
    repository = Path(repository).resolve()
    base = repository / "run-sets"
    path = base / name
    for component in (base, path):
        if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
            raise ValueError(f"Run-set directories must not be links or junctions: {component}")
        if component.exists() and not component.is_dir():
            raise ValueError(f"Run-set path is not a directory: {component}")
    if base.is_dir():
        for child in base.iterdir():
            if child.name.casefold() == name.casefold() and child.name != name:
                raise ValueError(f"Run-set case alias already exists: {child.name}; rename it explicitly")
    if path.resolve() != path:
        raise ValueError("Run-set path resolves outside its canonical location")
    for relative in ("logs", "figures", "results", "results/raw", "results/processed", "results/csv",
                     "results/rasmol", "results/.processing"):
        child = path / relative
        if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
            raise ValueError(f"Run-set outputs must not be links or junctions: {child}")
        if child.exists() and not child.is_dir():
            raise ValueError(f"Run-set output path is not a directory: {child}")
    return path


def check_run_set(repository, name, jobs=None, *, create=False):
    """Once registered, a dataset keeps one requested concurrency policy."""
    from benchmark_core.run_store import atomic_json, utc_now
    root = data_root(repository, name)
    marker = root / "run_set.json"
    if marker.exists():
        record = json.loads(marker.read_text(encoding="utf-8"))
        if (not isinstance(record, dict) or record.get("schema_version") != 1
                or record.get("run_set") != name or type(record.get("requested_jobs")) is not int
                or not 1 <= record["requested_jobs"] <= 32
                or record.get("execution_policy") != "independent_single_thread_jobs"):
            raise ValueError("Invalid run_set.json; preserve it and investigate before proceeding")
        if jobs is not None and record["requested_jobs"] != jobs:
            raise ValueError(f"Run-set {name!r} is registered for --jobs {record['requested_jobs']}; "
                             f"use another --run-set for --jobs {jobs}")
        return record
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Run-set {name!r} has data but no run_set.json; refusing unregistered data")
    if not create:
        return None
    if type(jobs) is not int or not 1 <= jobs <= 32:
        raise ValueError("A new run-set requires --jobs within 1..32")
    record = {"schema_version": 1, "run_set": name, "requested_jobs": jobs,
              "execution_policy": "independent_single_thread_jobs", "created_utc": utc_now()}
    atomic_json(marker, record)
    return record


def process_token(pid):
    """Creation-time identity prevents recycled PIDs appearing to own an attempt."""
    if os.name != "nt":
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        except OSError:
            return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            return None
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def campaign_lock_held(repository):
    """Probe an existing kernel lock without creating or changing its contents."""
    path = Path(repository) / "logs/campaign.lock"
    if not path.is_file():
        return False
    try:
        with path.open("r+b") as handle:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return False
            except OSError:
                return True
    except OSError:
        return None


def owner_state(status, repository=None):
    """Liveness is diagnostic only, never acceptance of incomplete results."""
    if status.get("host") != socket.gethostname():
        return "running-unknown"
    pid = status.get("pid")
    if type(pid) is not int or pid <= 0:
        return "stale-running"
    token = process_token(pid)
    if token != status.get("process_token") or token is None:
        return "stale-running"
    locked = campaign_lock_held(repository) if repository is not None else None
    return "running" if locked is True else "stale-running" if locked is False else "running-unknown"


def unsealed_status(directory, repository=None):
    from benchmark_core.run_store import checked_output_path
    directory = Path(directory).absolute()
    # Raw/digest directories are checked by run_directory; preserve the same
    # protection when this helper is called directly for diagnostics.
    if directory.parent.name != "raw" or directory.parent.parent.name != "results":
        raise ValueError("Status directory is outside the canonical raw-result layout")
    root = directory.parents[2]
    checked_output_path(root,directory)
    for attempt in directory.glob("attempt_*") if directory.is_dir() else []:
        checked_output_path(root,attempt)
        checked_output_path(root,attempt/"attempt_status.json",directory=False)
    attempts = sorted(directory.glob("attempt_*"), key=lambda path: path.stat().st_mtime_ns,
                      reverse=True) if directory.is_dir() else []
    if not attempts:
        return "pending", "No attempt has been started"
    try:
        status = json.loads((attempts[0] / "attempt_status.json").read_text(encoding="utf-8"))
        state = status.get("status")
        if state == "running":
            return owner_state(status, repository), "Unsealed attempt; PID/creation-time/lock checks are diagnostic only"
        if state == "failed":
            return "failed", status.get("error", "Attempt failed")
        return "incomplete", f"Last attempt status: {state}; restart from original seed"
    except (ValueError, OSError, AttributeError):
        return "incomplete", "Attempt status missing or unreadable"
