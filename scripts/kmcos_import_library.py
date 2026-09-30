"""Prepare the Windows CPython 3.10 import library required by MinGW/F2PY.

Only the dedicated virtual environment is written. Scientific source, generated
Fortran and compiler flags are untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DLL_NAME = "python310.dll"
DEF_NAME = "python310.def"
LIB_NAME = "libpython310.a"
MANIFEST_NAME = "libpython310.build.json"
PACKAGE_NAMES = {"gendef": "mingw-w64-ucrt-x86_64-tools",
                 "dlltool": "mingw-w64-ucrt-x86_64-binutils"}


def file_record(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        size = path.stat().st_size
    except OSError as exc:
        raise RuntimeError(f"Required readable file is missing or inaccessible: {path}: {exc}") from exc
    if not size:
        raise RuntimeError(f"Required file is empty: {path}")
    return {"path": str(path), "size_bytes": size, "sha256": digest}


def _command(argv: list[str], env: dict[str, str], *, cwd: Path | None = None,
             timeout: int = 45) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(argv, env=env, cwd=cwd, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot run required kmcos import-library tool {argv[0]}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1200:]
        raise RuntimeError(f"kmcos import-library command failed ({result.returncode}): "
                           f"{' '.join(argv)}; {detail}")
    return result


def _expected_packages() -> dict[str, str]:
    lock = json.loads((ROOT / "dependencies.lock.json").read_text(encoding="utf-8"))
    return lock["toolchains"]["ucrt64_python_import_library"]["packages"]


def inspect(python_exe: Path, msys2_root: Path, *, env: dict[str, str] | None = None,
            expected_packages: dict[str, str] | None = None) -> dict:
    """Read-only check of the exact Python DLL and installed MSYS2 tools."""
    python_exe = Path(python_exe).resolve()
    msys2_root = Path(msys2_root).resolve()
    execution_env = dict(os.environ if env is None else env)
    execution_env["PATH"] = (str(msys2_root / "ucrt64/bin") + os.pathsep +
                             str(msys2_root / "usr/bin") + os.pathsep + execution_env.get("PATH", ""))
    runtime_code = (
        "import json,struct,sys; print(json.dumps({"
        "'version':sys.version.split()[0], 'executable':sys.executable, "
        "'prefix':sys.prefix, 'base_prefix':sys.base_prefix, "
        "'pointer_bits':struct.calcsize('P')*8}))"
    )
    runtime = json.loads(_command([str(python_exe), "-B", "-c", runtime_code],
                                  execution_env).stdout)
    if runtime.get("version") != "3.10.11" or runtime.get("pointer_bits") != 64:
        raise RuntimeError("kmcos import library requires AMD64 CPython 3.10.11; "
                           f"found {runtime.get('version')} / {runtime.get('pointer_bits')}-bit")
    prefix = Path(runtime["prefix"]).resolve()
    base_prefix = Path(runtime["base_prefix"]).resolve()
    if Path(runtime["executable"]).resolve() != python_exe or prefix != python_exe.parent.parent:
        raise RuntimeError("The configured kmcos Python does not match its virtual-environment prefix")
    if prefix == base_prefix:
        raise RuntimeError("kmcos requires a dedicated Python 3.10 virtual environment")
    candidates = (base_prefix / DLL_NAME, base_prefix / "DLLs" / DLL_NAME)
    dll = next((candidate for candidate in candidates if candidate.is_file()), None)
    if dll is None:
        raise RuntimeError(f"Cannot find readable {DLL_NAME} under CPython base prefix {base_prefix}; "
                           "install the declared AMD64 CPython 3.10.11 distribution")
    dll_record = file_record(dll)
    with dll.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise RuntimeError(f"Python DLL is not a PE image: {dll}")
    requirements = _expected_packages() if expected_packages is None else expected_packages
    tools = {}
    pacman = msys2_root / "usr/bin/pacman.exe"
    file_record(pacman)
    for key, package_name in PACKAGE_NAMES.items():
        path = msys2_root / "ucrt64/bin" / (key + ".exe")
        if not path.is_file():
            raise RuntimeError(f"Missing UCRT64 {key}.exe: {path}; install pinned MSYS2 "
                               f"package {package_name} as described in docs/TOOLCHAINS.md")
        package_result = _command([str(pacman), "-Q", package_name], execution_env)
        words = package_result.stdout.strip().split()
        expected = requirements[key]
        if len(words) != 2 or words != [package_name, expected]:
            raise RuntimeError(f"UCRT64 {key} requires {package_name} {expected}; "
                               f"installed: {package_result.stdout.strip()!r}")
        version_option = ["-h"] if key == "gendef" else ["--version"]
        tool_result = _command([str(path), *version_option], execution_env)
        tools[key] = {**file_record(path), "package": package_name,
                      "package_version": expected,
                      "version_output": tool_result.stdout.splitlines()[0] if tool_result.stdout else None}
    return {"python": {**runtime, "executable_file": file_record(python_exe)},
            "python_dll": dll_record, "tools": tools,
            "library_directory": str(prefix / "libs"), "msys2_root": str(msys2_root)}


def _definition_valid(path: Path) -> bool:
    try:
        contents = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return (len(contents) > 100 and 'LIBRARY "python310.dll"' in contents and
            "\nEXPORTS\n" in contents.replace("\r\n", "\n"))


def _library_target(path: Path, dlltool: Path, env: dict[str, str]) -> str | None:
    try:
        result = _command([str(dlltool), "-I", str(path)], env)
    except RuntimeError:
        return None
    target = result.stdout.strip()
    return target if target.casefold() == DLL_NAME else None


def prepare(python_exe: Path, msys2_root: Path, *, env: dict[str, str] | None = None,
            expected_packages: dict[str, str] | None = None) -> dict:
    """Reuse only a sealed matching import library, otherwise regenerate it."""
    context = inspect(python_exe, msys2_root, env=env, expected_packages=expected_packages)
    library_dir = Path(context["library_directory"])
    library_dir.mkdir(parents=True, exist_ok=True)
    definition = library_dir / DEF_NAME
    library = library_dir / LIB_NAME
    manifest = library_dir / MANIFEST_NAME
    dlltool = Path(context["tools"]["dlltool"]["path"])
    execution_env = dict(os.environ if env is None else env)
    execution_env["PATH"] = (str(Path(msys2_root).resolve() / "ucrt64/bin") + os.pathsep +
                             str(Path(msys2_root).resolve() / "usr/bin") + os.pathsep +
                             execution_env.get("PATH", ""))
    if manifest.is_file():
        try:
            old = json.loads(manifest.read_text(encoding="utf-8"))
            if (old.get("schema_version") == 1 and old.get("input_identity") == context and
                old.get("definition") == file_record(definition) and
                old.get("import_library") == file_record(library) and
                _definition_valid(definition) and
                _library_target(library, dlltool, execution_env) == DLL_NAME):
                return {**old, "action": "reused", "manifest_path": str(manifest)}
        except (OSError, ValueError, RuntimeError, KeyError):
            pass
    with tempfile.TemporaryDirectory(prefix="nanokmc_importlib_", dir=library_dir) as temp:
        staging = Path(temp)
        staged_definition = staging / DEF_NAME
        staged_library = staging / LIB_NAME
        gendef = Path(context["tools"]["gendef"]["path"])
        gendef_command = [str(gendef), Path(context["python_dll"]["path"]).as_posix()]
        _command(gendef_command, execution_env, cwd=staging, timeout=60)
        if not _definition_valid(staged_definition):
            raise RuntimeError(f"gendef did not produce a valid {DEF_NAME} with Python exports")
        # dlltool derives intermediate filenames from -l. Absolute Windows paths
        # can exceed the filesystem component limit after that transformation.
        dlltool_command = [str(dlltool), "-d", DEF_NAME,
                           "-l", LIB_NAME, "-D", DLL_NAME,
                           "-m", "i386:x86-64"]
        _command(dlltool_command, execution_env, cwd=staging, timeout=180)
        staged_record = file_record(staged_library)
        if _library_target(staged_library, dlltool, execution_env) != DLL_NAME:
            raise RuntimeError(f"dlltool created an import library that does not target {DLL_NAME}")
        if not staged_record["size_bytes"]:
            raise RuntimeError("dlltool produced an empty Python import library")
        os.replace(staged_definition, definition)
        os.replace(staged_library, library)
        record = {"schema_version": 1, "status": "PASS", "action": "generated",
                  "generated_utc": datetime.now(timezone.utc).isoformat(),
                  "purpose": "Windows CPython 3.10 MinGW/F2PY import-library compatibility",
                  "scientific_source_changed": False, "input_identity": context,
                  "definition": file_record(definition),
                  "import_library": file_record(library),
                  "identified_target_dll": DLL_NAME,
                  "temporary_working_directory": str(staging),
                  "commands": [gendef_command, dlltool_command]}
        staged_manifest = staging / MANIFEST_NAME
        staged_manifest.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        os.replace(staged_manifest, manifest)
        return {**record, "manifest_path": str(manifest)}
