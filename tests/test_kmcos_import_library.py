"""Offline tests for the dedicated Windows Python/MinGW import-library step."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import kmcos_import_library as helper


class ImportLibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="kmcos_import_library_test_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.python = self.root / "repo/.venv-kmcos/Scripts/python.exe"
        self.python.parent.mkdir(parents=True)
        self.python.write_bytes(b"synthetic CPython launcher")
        self.base = self.root / "installed-python310"
        self.base.mkdir()
        self.dll = self.base / "python310.dll"
        self.dll.write_bytes(b"MZsynthetic Python DLL exports")
        self.msys = self.root / "msys2"
        (self.msys / "ucrt64/bin").mkdir(parents=True)
        (self.msys / "usr/bin").mkdir(parents=True)
        for name in ("gendef", "dlltool"):
            (self.msys / "ucrt64/bin" / (name + ".exe")).write_bytes((name + " identity").encode())
        (self.msys / "usr/bin/pacman.exe").write_bytes(b"synthetic package manager")
        self.packages = {"gendef": "14.0.0.r283.ga7cb47123-1", "dlltool": "2.47-3"}
        self.commands = []
        self.failing_tool = None
        self.actual_command = helper._command
        self.enterContext(patch.object(helper, "_command", side_effect=self.command))

    def command(self, argv, env, *, cwd=None, timeout=45):
        self.commands.append((tuple(argv), cwd))
        executable = Path(argv[0]).stem.lower()
        if executable == "python":
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "version": "3.10.11", "executable": str(self.python),
                "prefix": str(self.python.parent.parent), "base_prefix": str(self.base),
                "pointer_bits": 64}), "")
        if executable == "pacman":
            package = argv[-1]
            key = next(name for name, full in helper.PACKAGE_NAMES.items() if full == package)
            return subprocess.CompletedProcess(argv, 0, f"{package} {self.packages[key]}\n", "")
        if executable == "gendef":
            if len(argv) == 2 and argv[1] == "-h":
                return subprocess.CompletedProcess(argv, 0, "Usage: gendef\n", "")
            if self.failing_tool == "gendef":
                raise RuntimeError("gendef failed with code 1")
            assert cwd is not None
            (cwd / helper.DEF_NAME).write_text(
                'LIBRARY "python310.dll"\nEXPORTS\n' + "\n".join(f"PySynthetic_{n}" for n in range(20)) + "\n",
                encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "Found PE+ image\n", "")
        if executable == "dlltool":
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, "GNU dlltool 2.47\n", "")
            if "-I" in argv:
                return subprocess.CompletedProcess(argv, 0, "python310.dll\n", "")
            if self.failing_tool == "dlltool":
                raise RuntimeError("dlltool failed with code 1")
            assert "-m" in argv and argv[argv.index("-m") + 1] == "i386:x86-64"
            assert cwd is not None
            self.assertEqual(argv[argv.index("-d") + 1], helper.DEF_NAME)
            self.assertEqual(argv[argv.index("-l") + 1], helper.LIB_NAME)
            (cwd / helper.LIB_NAME).write_bytes(b"nonempty MinGW import library")
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"Unexpected tool: {argv}")

    def inspect(self):
        return helper.inspect(self.python, self.msys, expected_packages=self.packages)

    def prepare(self):
        return helper.prepare(self.python, self.msys, expected_packages=self.packages)

    def test_derives_base_dll_and_dedicated_library_directory(self):
        evidence = self.inspect()
        self.assertEqual(Path(evidence["python_dll"]["path"]), self.dll)
        self.assertEqual(Path(evidence["library_directory"]), self.python.parent.parent / "libs")
        self.assertEqual(evidence["python"]["base_prefix"], str(self.base))
        self.assertFalse((self.python.parent.parent / "libs").exists())

    def test_missing_python_dll_is_actionable(self):
        self.dll.unlink()
        with self.assertRaisesRegex(RuntimeError, "python310.dll"):
            self.inspect()

    def test_missing_gendef_is_actionable(self):
        (self.msys / "ucrt64/bin/gendef.exe").unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing UCRT64 gendef"):
            self.inspect()

    def test_missing_dlltool_is_actionable(self):
        (self.msys / "ucrt64/bin/dlltool.exe").unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing UCRT64 dlltool"):
            self.inspect()

    def test_valid_library_reused_without_generation(self):
        generated = self.prepare()
        self.assertEqual(generated["action"], "generated")
        self.commands.clear()
        reused = self.prepare()
        self.assertEqual(reused["action"], "reused")
        self.assertEqual(reused["import_library"], generated["import_library"])
        self.assertFalse(any(Path(args[0]).stem == "gendef" and args[-1] != "-h"
                             for args, _ in self.commands))
        self.assertFalse(any(Path(args[0]).stem == "dlltool" and "-d" in args
                             for args, _ in self.commands))

    def test_missing_record_and_changed_input_require_regeneration(self):
        library_dir = self.python.parent.parent / "libs"
        library_dir.mkdir()
        (library_dir / helper.LIB_NAME).write_bytes(b"arbitrary old library")
        first = self.prepare()
        self.assertEqual(first["action"], "generated")
        self.dll.write_bytes(b"MZchanged Python DLL exports")
        second = self.prepare()
        self.assertEqual(second["action"], "generated")
        (library_dir / helper.LIB_NAME).write_bytes(b"stale output")
        self.assertEqual(self.prepare()["action"], "generated")
        (self.msys / "ucrt64/bin/dlltool.exe").write_bytes(b"changed dlltool identity")
        self.assertEqual(self.prepare()["action"], "generated")

    def test_failed_generation_leaves_no_accepted_library(self):
        self.failing_tool = "gendef"
        with self.assertRaisesRegex(RuntimeError, "gendef failed"):
            self.prepare()
        library_dir = self.python.parent.parent / "libs"
        for name in (helper.DEF_NAME, helper.LIB_NAME, helper.MANIFEST_NAME):
            self.assertFalse((library_dir / name).exists())
        self.failing_tool = "dlltool"
        with self.assertRaisesRegex(RuntimeError, "dlltool failed"):
            self.prepare()
        for name in (helper.DEF_NAME, helper.LIB_NAME, helper.MANIFEST_NAME):
            self.assertFalse((library_dir / name).exists())

    def test_creates_nonempty_library_and_sealed_provenance(self):
        report = self.prepare()
        self.assertEqual(report["status"], "PASS")
        self.assertGreater(report["import_library"]["size_bytes"], 0)
        self.assertEqual(report["identified_target_dll"], "python310.dll")
        self.assertFalse(report["scientific_source_changed"])
        record = json.loads(Path(report["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual(record["input_identity"]["tools"]["dlltool"]["package_version"],
                         self.packages["dlltool"])
        self.assertEqual(record["import_library"], helper.file_record(
            Path(report["import_library"]["path"])))

    def test_command_failure_reports_actionable_error(self):
        with patch.object(helper.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["gendef.exe"], 1, "", "permission denied")):
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                self.actual_command(["gendef.exe", "python310.dll"], {})


if __name__ == "__main__":
    unittest.main()
