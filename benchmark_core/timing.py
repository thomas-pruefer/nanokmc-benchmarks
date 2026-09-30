from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
import time
import shutil
from pathlib import Path
from typing import Optional
from benchmark_core.process_tree import ProcessTree


@dataclass
class TimedCommandResult:
    returncode: int
    wall_seconds: float
    stdout: str
    stderr: str


def msys2_runtime_env(paths: dict | None = None) -> dict:
    """Return an environment that can run MSYS2-built executables from PowerShell/Python.

    Windows return code 3221225781 / 0xC0000135 usually means a DLL such as
    libstdc++/libgcc/UCRT runtime could not be found. NanoKMC and KMC_Lattice
    use UCRT64; the source-unmodified SPPARKS build uses plain MSYS. Both
    corresponding bin folders must be on PATH; verified build reports identify them.
    """
    env = os.environ.copy()
    paths = paths or {}

    # Derive C:/msys64 from C:/msys64/usr/bin/bash.exe when available.
    msys2_root = None
    if paths.get("msys2_root"):
        msys2_root = Path(paths["msys2_root"])
    elif paths.get("msys2_bash"):
        bash_path = Path(paths["msys2_bash"])
        # C:/msys64/usr/bin/bash.exe -> C:/msys64
        try:
            msys2_root = bash_path.parents[2]
        except IndexError:
            msys2_root = None
    if msys2_root is None:
        msys2_root = Path("C:/msys64")

    candidates = [
        msys2_root / "ucrt64" / "bin",
        msys2_root / "usr" / "bin",
    ]
    prefix = os.pathsep.join(str(p) for p in candidates if p.exists())
    if prefix:
        env["PATH"] = prefix + os.pathsep + env.get("PATH", "")
    return env


def explain_windows_returncode(returncode: int) -> str:
    # Python on Windows returns signed/unsigned NTSTATUS values depending on context.
    if returncode in (3221225781, -1073741515):
        return "0xC0000135: a required DLL was not found. Add MSYS2 UCRT64 bin folders to PATH."
    return ""


def run_timed(
    cmd: list[str],
    cwd: Path,
    stdin_file: Optional[Path] = None,
    env: Optional[dict] = None,
) -> TimedCommandResult:
    stdin = None
    if stdin_file is not None:
        stdin = stdin_file.open("r", encoding="utf-8")
    tree = None
    stdout_path, stderr_path = cwd / "process.stdout.log", cwd / "process.stderr.log"
    try:
        tree = ProcessTree()
        t0 = time.perf_counter()
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=stdin,
                                        stdout=stdout, stderr=stderr, env=env,
                                        creationflags=0x4 if os.name == "nt" else 0,
                                        start_new_session=os.name != "nt")
                try:
                    tree.attach(proc)
                    tree.resume(proc)
                    proc.wait()
                except BaseException:
                    tree.kill(proc)
                    proc.wait()
                    raise
        except FileNotFoundError as exc:
            detail = (
                f"Executable or working directory not found while launching command.\n"
                f"Command: {cmd}\n"
                f"cwd: {cwd}\n"
                f"cwd exists: {Path(cwd).exists()}"
            )
            raise FileNotFoundError(detail) from exc
        t1 = time.perf_counter()
    finally:
        if tree is not None:
            tree.close()
        if stdin is not None:
            stdin.close()
    return TimedCommandResult(proc.returncode, t1 - t0,
                              stdout_path.read_text(encoding="utf-8", errors="replace"),
                              stderr_path.read_text(encoding="utf-8", errors="replace"))



def windows_path_to_msys(path: str | Path) -> str:
    """Convert a Windows path such as D:/MBE/file.exe to an MSYS2 path /d/MBE/file.exe.

    Paths that already look POSIX/MSYS are returned with backslashes normalized.
    """
    text = str(path).replace("\\", "/")
    if len(text) >= 3 and text[1] == ":" and text[2] == "/":
        drive = text[0].lower()
        return f"/{drive}{text[2:]}"
    return text



def resolve_msys2_bash(paths: dict | None = None) -> str:
    """Find MSYS2 bash.exe robustly on Windows.

    A configured path is used when valid. Otherwise common MSYS2 install
    locations and PATH are searched. This avoids an unhelpful WinError 2 from
    subprocess when paths.local.json contains an incorrect bash path.
    """
    paths = paths or {}
    candidates: list[str] = []

    if paths.get("msys2_bash"):
        candidates.append(str(paths["msys2_bash"]))
    if paths.get("msys2_root"):
        root = str(paths["msys2_root"]).rstrip("/\\")
        candidates.extend([
            root + "/usr/bin/bash.exe",
            root + "\\usr\\bin\\bash.exe",
        ])

    candidates.extend([
        "C:/msys64/usr/bin/bash.exe",
        "C:\\msys64\\usr\\bin\\bash.exe",
        "D:/msys64/usr/bin/bash.exe",
        "D:\\msys64\\usr\\bin\\bash.exe",
    ])

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate

    which_bash = shutil.which("bash")
    if which_bash:
        return which_bash

    checked = "\n  ".join(candidates)
    raise FileNotFoundError(
        "Could not find MSYS2 bash.exe. Set msys2_bash in config/paths.local.json. "
        "Checked:\n  " + checked
    )


def msys2_bash_command(paths: dict | None, shell_command: str) -> list[str]:
    """Return a command list that runs shell_command inside MSYS2 Bash/UCRT64."""
    paths = paths or {}
    bash = resolve_msys2_bash(paths)
    path_prefix = paths.get("msys2_path_prefix", "/ucrt64/bin:/usr/bin")
    if path_prefix != "/ucrt64/bin:/usr/bin":
        raise ValueError("msys2_path_prefix must be /ucrt64/bin:/usr/bin")
    return [str(bash), "-lc", f"export MSYSTEM=UCRT64; export PATH={path_prefix}:$PATH; {shell_command}"]
