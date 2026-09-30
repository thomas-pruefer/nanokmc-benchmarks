"""Windows launchers resolve the clone root and forward arguments/exit status."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = {
    "scripts/windows/check_environment.bat": "check_environment.py",
    "scripts/windows/fetch_or_verify_sources.bat": "fetch_or_verify_sources.py",
    "scripts/windows/build_all.bat": "build_all.py",
    "scripts/windows/run_campaigns.bat": "run_manuscript_campaigns.py",
    "scripts/windows/process_results.bat": "process_results.py",
    "scripts/windows/make_figures.bat": "make_figures.py",
    "scripts/windows/preview_results.bat": "preview_partial_results.py",
    "reproduce_manuscript.bat": "reproduce_manuscript.py",
}


@unittest.skipUnless(os.name == "nt", "Windows batch launcher checks")
class WindowsLaunchers(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nanokmc launcher test ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "clone with spaces"
        self.caller = Path(temporary.name) / "unrelated caller"
        self.caller.mkdir()
        (self.root / "scripts/windows").mkdir(parents=True)
        (self.root / "config").mkdir()
        # Substitute inert entry points: no source acquisition, native build,
        # processing or solver can be launched by these wrapper tests.
        probe = (
            "import json, os, pathlib, sys\n"
            "print(json.dumps({'cwd': str(pathlib.Path.cwd()), 'args': sys.argv[1:], "
            "'entry': pathlib.Path(__file__).name, 'utf8': sys.flags.utf8_mode, "
            "'no_bytecode': sys.dont_write_bytecode}))\n"
            "raise SystemExit(23 if '--fail' in sys.argv else 0)\n"
        )
        for wrapper, script in LAUNCHERS.items():
            shutil.copyfile(ROOT / wrapper, self.root / wrapper)
            (self.root / "scripts" / script).write_text(probe, encoding="utf-8")
        self.environment = os.environ.copy()
        self.environment["BENCHMARK_PYTHON"] = sys.executable

    def invoke(self, wrapper, arguments="", environment=None):
        interpreter = os.environ.get("COMSPEC", "cmd.exe")
        # cmd uses its own quoting rules; list2cmdline would escape the quoted
        # batch path for a C runtime instead of preserving cmd's quote syntax.
        command = f'"{interpreter}" /d /c call "{self.root / wrapper}" {arguments}'
        return subprocess.run(
            command,
            cwd=self.caller, env=self.environment if environment is None else environment,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )

    def test_each_launcher_uses_clone_root_and_preserves_spaced_arguments(self):
        for wrapper, script in LAUNCHERS.items():
            with self.subTest(wrapper=wrapper):
                result = self.invoke(wrapper, '--paths "config/path with spaces.json" --jobs 8')
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                record = json.loads(result.stdout)
                self.assertEqual(Path(record["cwd"]), self.root)
                self.assertEqual(record["entry"], script)
                self.assertEqual(record["args"], ["--paths", "config/path with spaces.json", "--jobs", "8"])
                self.assertEqual(record["utf8"], 1)
                self.assertTrue(record["no_bytecode"])
        self.assertEqual(list(self.root.rglob("__pycache__")), [])

    def test_each_launcher_preserves_failure_exit_code(self):
        for wrapper in LAUNCHERS:
            with self.subTest(wrapper=wrapper):
                result = self.invoke(wrapper, "--fail")
                self.assertEqual(result.returncode, 23, result.stderr + result.stdout)

    def test_local_python_configuration_and_environment_override(self):
        local = self.root / "config/python.local.bat"
        local.write_bytes(f'@set "BENCHMARK_PYTHON={sys.executable}"\r\n'.encode("utf-8"))
        environment = self.environment.copy()
        environment.pop("BENCHMARK_PYTHON")
        result = self.invoke("scripts/windows/run_campaigns.bat", environment=environment)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(Path(json.loads(result.stdout)["cwd"]), self.root)
        local.write_bytes(b'@set "BENCHMARK_PYTHON=absent-interpreter.exe"\r\n')
        result = self.invoke("scripts/windows/run_campaigns.bat")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_missing_python_fails_before_any_entry_point(self):
        environment = self.environment.copy()
        environment.pop("BENCHMARK_PYTHON")
        result = self.invoke("scripts/windows/run_campaigns.bat", environment=environment)
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertIn("Set BENCHMARK_PYTHON or create .venv", result.stdout)
        self.assertNotIn('"entry"', result.stdout)


if __name__ == "__main__":
    unittest.main()
