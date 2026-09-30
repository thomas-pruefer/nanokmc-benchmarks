"""Tiny synthetic child processes verify interruption safety; no scientific solver."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from benchmark_core.timing import run_timed

ROOT = Path(__file__).resolve().parents[1]


def is_running(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        return False
    try:
        return api.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
    finally:
        api.CloseHandle(handle)


class ProcessLifecycleTests(unittest.TestCase):
    def test_fast_child_finishes_and_logs_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_timed([sys.executable, "-B", "-c", "print('synthetic child completed')"], Path(directory))
            self.assertEqual(result.returncode, 0)
            self.assertIn("synthetic child completed", result.stdout)
            self.assertGreater(result.wall_seconds, 0)
            self.assertTrue((Path(directory) / "process.stdout.log").exists())

    @unittest.skipUnless(os.name == "nt", "Windows Job Object termination test")
    def test_abrupt_driver_exit_kills_child_and_grandchild(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            grandchild = folder / "grandchild.py"
            grandchild.write_text("import os,time\nfrom pathlib import Path\n"
                                 "Path('grandchild.pid').write_text(str(os.getpid()))\ntime.sleep(30)\n")
            child = folder / "child.py"
            child.write_text("import os,subprocess,sys,time\nfrom pathlib import Path\n"
                             "Path('child.pid').write_text(str(os.getpid()))\n"
                             "subprocess.Popen([sys.executable,'-B','grandchild.py'])\n"
                             "while not Path('grandchild.pid').exists(): time.sleep(.01)\n"
                             "Path('ready').write_text('synthetic children ready')\ntime.sleep(30)\n")
            driver = folder / "driver.py"
            driver.write_text("import sys\nfrom pathlib import Path\n"
                              f"sys.path.insert(0, {str(ROOT)!r})\n"
                              "from benchmark_core.timing import run_timed\n"
                              "run_timed([sys.executable,'-B','child.py'],Path.cwd())\n")
            process = subprocess.Popen([sys.executable, "-B", str(driver)], cwd=folder,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            pids = []
            try:
                deadline = time.monotonic() + 10
                while not (folder / "ready").exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(f"Synthetic driver exited before child readiness: {process.stderr.read().decode(errors='replace')}")
                    time.sleep(.02)
                self.assertTrue((folder / "ready").exists(), "Synthetic child startup timed out")
                pids = [int((folder / name).read_text()) for name in ("child.pid", "grandchild.pid")]
                self.assertTrue(all(is_running(pid) for pid in pids))
                process.kill()  # no Python finally handlers run in the abruptly terminated driver
                process.wait(timeout=5)
                deadline = time.monotonic() + 5
                while any(is_running(pid) for pid in pids) and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertFalse(any(is_running(pid) for pid in pids), "Job close left a synthetic descendant running")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                for name in ("child.pid", "grandchild.pid"):
                    pid_file = folder / name
                    if pid_file.exists():
                        pid = int(pid_file.read_text())
                        if is_running(pid):
                            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
