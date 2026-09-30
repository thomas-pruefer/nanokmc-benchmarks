"""Build-entrypoint argument checks must neither acquire locks nor launch tools."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import build_guard


class BuildCLIReadOnly(unittest.TestCase):
    def check_parse_only(self, script, arguments, exit_code):
        with patch.object(sys, "argv", [script, *arguments]), \
             patch.object(build_guard, "build_lock", side_effect=AssertionError("Unexpected build lock")) as lock, \
             patch.object(subprocess, "Popen", side_effect=AssertionError("Unexpected process")) as popen, \
             patch.object(subprocess, "run", side_effect=AssertionError("Unexpected process")) as run, \
             patch.object(subprocess, "check_output", side_effect=AssertionError("Unexpected process")) as output, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaises(SystemExit) as result:
            runpy.run_path(str(SCRIPTS / script), run_name="__main__")
        self.assertEqual(result.exception.code, exit_code)
        for operation in (lock, popen, run, output):
            operation.assert_not_called()

    def test_help_exits_before_lock_or_process_creation(self):
        for script in ("build_all.py", "build_cpp.py", "build_kmcos.py"):
            with self.subTest(script=script):
                self.check_parse_only(script, ["--help"], 0)

    def test_invalid_arguments_exit_before_lock_or_process_creation(self):
        for script in ("build_all.py", "build_cpp.py", "build_kmcos.py"):
            with self.subTest(script=script):
                self.check_parse_only(script, ["--not-a-build-option"], 2)
        for script in ("build_all.py", "build_cpp.py"):
            for value in ("0", "-1", "not-an-integer"):
                with self.subTest(script=script, jobs=value):
                    self.check_parse_only(script, ["--jobs", value], 2)

    def test_valid_execution_still_requires_build_lock(self):
        for script in ("build_all.py", "build_cpp.py", "build_kmcos.py"):
            with self.subTest(script=script), patch.object(sys, "argv", [script]), \
                 patch.object(build_guard, "build_lock", side_effect=AssertionError("Build lock reached")) as lock, \
                 patch.object(subprocess, "Popen", side_effect=AssertionError("Unexpected process")) as popen, \
                 self.assertRaisesRegex(AssertionError, "Build lock reached"):
                runpy.run_path(str(SCRIPTS / script), run_name="__main__")
            lock.assert_called_once_with()
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
