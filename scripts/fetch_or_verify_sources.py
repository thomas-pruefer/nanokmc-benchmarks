"""Verify self-contained frozen exports, or export exact Git objects.

Default operation is offline. --fetch explicitly permits a private clone under
build/source-cache when neither the frozen export nor a configured clone exists.
Existing files are never silently replaced and solver source is never patched.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from benchmark_core.local_config import read_local_config

SOURCE_NAMES = ("nanokmc", "spparks", "kmcos", "kmc_lattice")
MODEL_PATHS = ("models/kmc_lattice/fcc_kawasaki_selective.cpp",
               "models/kmcos/fcc_kawasaki_geometry.py",
               "models/kmcos/fcc_kawasaki_otf__build.py",
               "models/kmcos/run_kmcos_otf_scenario.py")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate dependency-lock key: {key}")
        result[key] = value
    return result


def _fields(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError(f"Unknown or missing {label} fields")


def _strings(value, label):
    if not isinstance(value, dict) or not value or any(not isinstance(key, str) or
            not isinstance(item, str) or not item for key, item in value.items()):
        raise ValueError(f"Invalid string mapping: {label}")


def _string_list(value, label):
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"Invalid string list: {label}")


def _relative_path(value, label):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or str(path) != value:
        raise ValueError(f"Expected a portable relative path: {label}")


def _validate_build_lock(lock):
    environments = lock["environments"]
    _fields(environments, ("benchmark", "kmcos"), "environment")
    for name, fields in (("benchmark", ("python", "implementation", "architecture", "packages")),
                         ("kmcos", ("python", "architecture", "packages"))):
        environment = environments[name]
        _fields(environment, fields, f"{name} environment")
        _strings({key: value for key, value in environment.items() if key != "packages"}, name)
        _strings(environment["packages"], f"{name} packages")
    toolchains = lock["toolchains"]
    _fields(toolchains, ("ucrt64_cpp", "ucrt64_fortran", "ucrt64_python_import_library", "plain_msys_cpp",
                        "cmake", "ninja", "pacman_packages_current", "package_archive_manifest"), "toolchain")
    for name in ("ucrt64_cpp", "ucrt64_fortran", "plain_msys_cpp"):
        fields = {"version", "executable_relative_to_msys2_root"}
        if name != "plain_msys_cpp":
            fields.add("revision")
        _fields(toolchains[name], fields, name)
        _strings(toolchains[name], name)
        _relative_path(toolchains[name]["executable_relative_to_msys2_root"], name)
    tools = toolchains["ucrt64_python_import_library"]
    _fields(tools, ("purpose", "tools_relative_to_msys2_root", "packages"), "Python import-library toolchain")
    _strings({"purpose": tools["purpose"]}, "Python import-library purpose")
    for field in ("tools_relative_to_msys2_root", "packages"):
        _fields(tools[field], ("gendef", "dlltool"), f"import-library {field}")
        _strings(tools[field], f"import-library {field}")
    for name, value in tools["tools_relative_to_msys2_root"].items():
        _relative_path(value, name)
    _strings({key: toolchains[key] for key in ("cmake", "ninja", "package_archive_manifest")}, "build tools")
    _relative_path(toolchains["package_archive_manifest"], "package_archive_manifest")
    _string_list(toolchains["pacman_packages_current"], "toolchain packages")
    contracts = lock["build_contract"]
    _fields(contracts, SOURCE_NAMES, "build contract")
    required = {
        "nanokmc": ("compiler", "paper_build", "compile_flags", "source_changes_allowed"),
        "spparks": ("compiler", "compile_flags", "link_flags", "serial", "source_changes_allowed"),
        "kmc_lattice": ("compiler", "compile_flags", "source_changes_allowed"),
        "kmcos": ("compiler", "backend", "f2py_compiler_family", "f2py_c_compiler", "fortran_flags",
                  "source_changes_allowed", "python_hash_seed"),
    }
    for name, fields in required.items():
        contract = contracts[name]
        _fields(contract, fields, f"{name} build contract")
        if contract["compiler"] not in ("ucrt64_cpp", "plain_msys_cpp", "ucrt64_fortran"):
            raise ValueError(f"Unknown compiler in {name} build contract")
        if contract["source_changes_allowed"] is not False:
            raise ValueError(f"Upstream source changes must be forbidden: {name}")
        for key, value in contract.items():
            if key.endswith("flags"):
                _string_list(value, f"{name}/{key}")
            elif key in ("paper_build", "serial"):
                if value is not True:
                    raise ValueError(f"Required build mode disabled: {name}/{key}")
            elif key != "source_changes_allowed":
                _strings({key: value}, f"{name}/{key}")


def load_dependency_lock():
    """Read the committed lock; generated reports are never verification inputs."""
    try:
        lock = json.loads((ROOT / "dependencies.lock.json").read_text(encoding="utf-8"),
                          object_pairs_hook=_unique_keys)
        if not isinstance(lock, dict) or type(lock.get("schema_version")) is not int or lock["schema_version"] != 2:
            raise ValueError("Expected dependency lock schema_version 2")
        if set(lock) != {"schema_version", "sources", "environments", "toolchains",
                         "build_contract", "model_sha256"}:
            raise ValueError("Unknown or missing dependency-lock fields")
        if not isinstance(lock["sources"], dict) or set(lock["sources"]) != set(SOURCE_NAMES):
            raise ValueError("Expected exactly four pinned upstream sources")
        for name, pin in lock["sources"].items():
            if not isinstance(pin, dict) or set(pin) != {"url", "commit", "tag", "tree_sha256"}:
                raise ValueError(f"Invalid source pin fields: {name}")
            if not isinstance(pin["url"], str) or not pin["url"].startswith("https://"):
                raise ValueError(f"Invalid upstream URL: {name}")
            if not isinstance(pin["commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", pin["commit"]):
                raise ValueError(f"Invalid exact Git commit: {name}")
            if pin["tag"] is not None and (not isinstance(pin["tag"], str) or not pin["tag"] or pin["tag"].startswith("-")):
                raise ValueError(f"Invalid source tag: {name}")
            if not isinstance(pin["tree_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", pin["tree_sha256"]):
                raise ValueError(f"Invalid canonical tree SHA-256: {name}")
        models = lock["model_sha256"]
        if not isinstance(models, dict) or set(models) != set(MODEL_PATHS):
            raise ValueError("Expected exactly the four benchmark model hashes")
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in models.values()):
            raise ValueError("Invalid benchmark model SHA-256")
        _validate_build_lock(lock)
        return lock
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError(f"Invalid dependency lock: {error}") from error


def locked_model_hash(relative):
    return load_dependency_lock()["model_sha256"][relative]


# Public source identities come only from the dependency lock.
PINS = {name: {key: value for key, value in pin.items() if key != "tree_sha256"}
        for name, pin in load_dependency_lock()["sources"].items()}


def load_paths(path=None):
    file = Path(path) if path else ROOT / "config/paths.local.json"
    return read_local_config(file, missing_ok=True)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args):
    if shutil.which("git") is None:
        raise RuntimeError("Git is required to acquire/export absent sources. Install Git for Windows and add git.exe to PATH, then retry stage 2. Already verified frozen exports do not require Git.")
    return subprocess.check_output(["git", "--no-optional-locks", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo), *args], stderr=subprocess.PIPE)


def source_repositories(paths):
    defaults = {name: ROOT / "build/source-cache" / name for name in PINS}
    defaults.update(paths.get("source_repositories", {}))
    return {key: (Path(value) if Path(value).is_absolute() else ROOT / value).resolve()
            for key, value in defaults.items()}


def canonical_tree_digest(entries):
    return sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode())


def _reject_link(path):
    record = path.lstat()
    if stat.S_ISLNK(record.st_mode) or getattr(record, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise RuntimeError(f"Symlink/reparse point is not allowed in frozen sources: {path}")
    return record


def _source_root(name):
    if name not in SOURCE_NAMES:
        raise RuntimeError(f"Unknown source name: {name}")
    destination = ROOT / "sources" / name
    for path in (ROOT / "sources", destination):
        if os.path.lexists(path):
            if not stat.S_ISDIR(_reject_link(path).st_mode):
                raise RuntimeError(f"Frozen source directory is not a directory: {path}")
    if not destination.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError(f"Frozen source path escapes repository: {destination}")
    return destination


def _tree_entries(destination):
    if not destination.is_dir():
        raise RuntimeError(f"Frozen source missing: {destination}")
    entries = {}

    def walk(directory):
        for path in sorted(directory.iterdir()):
            record = _reject_link(path)
            if not path.resolve().is_relative_to(destination.resolve()):
                raise RuntimeError(f"Frozen source path escapes source directory: {path}")
            if stat.S_ISDIR(record.st_mode):
                walk(path)
            elif stat.S_ISREG(record.st_mode):
                data = path.read_bytes()
                entries[path.relative_to(destination).as_posix()] = {"size": len(data), "sha256": sha256(data)}
            else:
                raise RuntimeError(f"Unsupported frozen source file type: {path}")

    walk(destination)
    return entries


def _write_manifest(manifest):
    out = ROOT / "build/source-manifests"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{manifest['name']}.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def verify_frozen_export(name, *, write_report=True):
    pin = load_dependency_lock()["sources"][name]
    entries = _tree_entries(_source_root(name))
    observed = canonical_tree_digest(entries)
    if observed != pin["tree_sha256"]:
        raise RuntimeError(f"Frozen source missing or changed, or unexpected files present: {name}. "
                           f"Canonical tree SHA-256 {observed} does not match lock {pin['tree_sha256']}. No automatic overwrite is allowed.")
    manifest = {"name": name, **pin, "file_count": len(entries), "files": entries}
    if write_report:
        _write_manifest(manifest)
    return {key: value for key, value in manifest.items() if key != "files"}


def verify_export(name, repo, verify_only=False, *, write_report=True):
    pin = load_dependency_lock()["sources"][name]
    commit = git(repo, "rev-parse", pin["commit"] + "^{commit}").decode().strip()
    if commit != pin["commit"]:
        raise RuntimeError(f"{name}: required commit does not resolve exactly")
    if pin["tag"] and git(repo, "rev-parse", pin["tag"] + "^{commit}").decode().strip() != commit:
        raise RuntimeError(f"{name}: tag does not resolve to required commit")
    archive = git(repo, "archive", "--format=zip", commit)
    destination = _source_root(name)
    entries = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        for item in package.infolist():
            relative = PurePosixPath(item.filename)
            if relative.is_absolute() or ".." in relative.parts or item.orig_filename != item.filename or "\\" in item.filename or ":" in item.filename or str(relative) != item.filename.rstrip("/"):
                raise RuntimeError(f"Unsafe archive path {item.filename}")
            mode = (item.external_attr >> 16) & 0o170000
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise RuntimeError(f"Unsupported archive file type {item.filename}")
            if item.is_dir():
                continue
            if mode == stat.S_IFDIR:
                raise RuntimeError(f"Unsupported archive file type {item.filename}")
            if item.filename.casefold() in {key.casefold() for key in entries}:
                raise RuntimeError(f"Duplicate/case-colliding archive path {item.filename}")
            data = package.read(item)
            entries[item.filename] = {"size": len(data), "sha256": sha256(data)}
    if canonical_tree_digest(entries) != pin["tree_sha256"]:
        raise RuntimeError(f"Git export differs from the locked source tree: {name}")
    existing = _tree_entries(destination) if destination.is_dir() else {}
    for relative, record in existing.items():
        if entries.get(relative) != record:
            raise RuntimeError(f"Frozen source changed or unexpected file: {destination / relative}. Refusing to overwrite.")
    if verify_only or not write_report:
        if existing != entries:
            raise RuntimeError(f"Frozen source missing: {destination}. Read-only verification cannot acquire files.")
    else:
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            for relative in entries.keys() - existing.keys():
                output = destination / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(package.read(relative))
    # Re-read bytes actually written, rather than trusting archive metadata.
    verify_frozen_export(name, write_report=False)
    manifest = {"name": name, **pin, "local_repository": str(repo), "observed_head": git(repo, "rev-parse", "HEAD").decode().strip(), "archive_sha256": sha256(archive), "file_count": len(entries), "files": entries}
    if write_report:
        _write_manifest(manifest)
    return {key: value for key, value in manifest.items() if key != "files"}


def verify_sources(paths=None, verify_only=False, fetch=False, *, write_reports=True):
    config = load_paths(paths)
    repositories = source_repositories(config)
    results = []
    network_used = False
    pins = load_dependency_lock()["sources"]
    for name in SOURCE_NAMES:
        pin = pins[name]
        if _source_root(name).is_dir():
            results.append(verify_frozen_export(name, write_report=write_reports))
            continue
        repo = repositories[name]
        if not repo.is_dir():
            if not fetch or verify_only or not write_reports:
                raise RuntimeError(f"{name}: no frozen export or local clone at {repo}. Configure source_repositories or explicitly use --fetch.")
            repo = ROOT / "build/source-cache" / name
            if repo.exists():
                raise RuntimeError(f"Incomplete private source cache exists: {repo}. Inspect it before retrying.")
            if shutil.which("git") is None:
                raise RuntimeError("Git is required for --fetch. Install Git for Windows and add git.exe to PATH, then retry stage 2.")
            repo.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--no-checkout", pin["url"], str(repo)], check=True)
            network_used = True
        results.append(verify_export(name, repo, verify_only, write_report=write_reports))
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "network_used": network_used, "upstream_mutated": False, "verification": "recomputed canonical source-tree SHA-256 against committed lock; refuses changed, missing or extra source files and links", "sources": results}
    if write_reports:
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs/source_verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--fetch", action="store_true", help="Allow missing pinned sources to be cloned into this repository's build/source-cache")
    options = parser.parse_args()
    if options.paths is not None and not options.paths.is_file():
        parser.error(f"Specified local paths configuration does not exist: {options.paths}")
    try:
        report = verify_sources(options.paths, options.verify_only, options.fetch)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", "replace").strip() if isinstance(error.stderr, bytes) else error.stderr
        print(f"SOURCE VERIFICATION FAILED: Git failed ({error.returncode}). {detail or ''} Verify the configured clone contains the pinned commit and tag.", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError) as error:
        print(f"SOURCE VERIFICATION FAILED: {error}", file=sys.stderr)
        if isinstance(error, FileNotFoundError) and error.filename == "git":
            print("Install Git for Windows and add git.exe to PATH before source acquisition.", file=sys.stderr)
        return 2
    for source in report["sources"]:
        print(f"VERIFIED {source['name']}: {source['commit']} ({source['file_count']} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
