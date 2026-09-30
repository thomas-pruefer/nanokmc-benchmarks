"""Frozen-source builds must not require Git or the original source clones.

All inputs/artifacts are tiny temporary fixtures and all compiler processes are
mocked. These tests never build, run or alter a real scientific solver.
"""
from __future__ import annotations

import json
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_kmcos
import build_all
import check_environment
import fetch_or_verify_sources as sources

DEPENDENCY_LOCK = sources.load_dependency_lock()


class OfflineBuildSources(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nanokmc_offline_build_test_")
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        lock = json.loads(json.dumps(DEPENDENCY_LOCK))
        for module in (sources, build_kmcos, build_all, check_environment):
            self.enterContext(patch.object(module, "ROOT", self.root))
        for name, pin in sources.PINS.items():
            data = f"Synthetic source fixture for {name}\n".encode()
            file = self.root / "sources" / name / "source.txt"
            file.parent.mkdir(parents=True)
            file.write_bytes(data)
            entries = {"source.txt": {"size": len(data), "sha256": sources.sha256(data)}}
            lock["sources"][name]["tree_sha256"] = sources.canonical_tree_digest(entries)
        for relative in sources.MODEL_PATHS:
            model = self.root / relative
            model.parent.mkdir(parents=True, exist_ok=True)
            model.write_bytes(b"# synthetic test fixture\n")
            lock["model_sha256"][relative] = sources.sha256(model.read_bytes())
        (self.root / "dependencies.lock.json").write_text(json.dumps(lock), encoding="utf-8")
        self.config = {
            "source_repositories": {name: str(self.root / "absent_original_clones" / name) for name in sources.PINS},
            "msys2_root": str(self.root / "toolchain"),
            "kmcos_python": str(self.root / "python.exe"),
        }
        (self.root / "config").mkdir()
        (self.root / "config/paths.local.json").write_text(json.dumps(self.config), encoding="utf-8")
        self.git = self.enterContext(patch.object(sources, "git", side_effect=FileNotFoundError("Git is unavailable")))

    def test_all_frozen_exports_verify_without_git_or_original_clones(self):
        tracked = [self.root / "dependencies.lock.json"]
        original_bytes = {path: path.read_bytes() for path in tracked}
        with patch.object(sources.subprocess, "run", side_effect=AssertionError("No subprocess is needed")):
            report = sources.verify_sources(verify_only=True)
        self.assertFalse(report["network_used"])
        self.assertEqual({r["name"] for r in report["sources"]}, set(sources.PINS))
        self.assertEqual({path: path.read_bytes() for path in tracked}, original_bytes)
        self.assertTrue((self.root / "logs/source_verification.json").is_file())
        self.assertEqual(len(list((self.root / "build/source-manifests").glob("*.json"))), len(sources.PINS))
        self.git.assert_not_called()

    def test_kmcos_pre_post_and_repeat_codegen_checks_use_frozen_source(self):
        ucrt = Path(self.config["msys2_root"]) / "ucrt64/bin"
        ucrt.mkdir(parents=True)
        for compiler in ("gcc", "gfortran"):
            (ucrt / f"{compiler}.exe").write_bytes(b"synthetic compiler identity")
        output = self.root / "build/kmcos"

        def check_output(command, **kwargs):
            executable = Path(command[0]).name
            if executable in ("gcc.exe", "gfortran.exe"):
                return "16.2.0\n"
            if executable == "objdump.exe":
                return ""
            if executable == "python.exe":
                return json.dumps({"python": "3.10.11", "executable": self.config["kmcos_python"],
                    "base_prefix": str(self.root), "packages": build_kmcos.REQUIRED_PACKAGES})
            raise FileNotFoundError(f"Unexpected process, including unavailable Git: {command}")

        def run(command, **kwargs):
            if "-c" in command:
                target = output / "repeat_codegen/src"
                target.mkdir(parents=True)
                (target / "model.f90").write_bytes(b"synthetic Fortran")
            else:
                target = output / "fcc_kawasaki_otf_otf/src"
                target.mkdir(parents=True)
                (target / "model.f90").write_bytes(b"synthetic Fortran")
                (target / "kmc_model.cp310-win_amd64.pyd").write_bytes(b"synthetic extension")
                kwargs["stdout"].write("FULL KMCOS OTF MODEL IMPORT PASS: synthetic test\n")
            return SimpleNamespace(returncode=0)

        with patch.object(build_kmcos.subprocess, "check_output", side_effect=check_output), \
             patch.object(build_kmcos.subprocess, "run", side_effect=run), \
             patch.object(build_kmcos, "prepare_import_library", return_value={"status": "PASS",
                 "action": "generated", "scientific_source_changed": False}) as import_library, \
             patch.object(build_kmcos, "verify_frozen_export", wraps=sources.verify_frozen_export) as verification:
            report = build_kmcos.build()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["source_postbuild_verification"], "PASS")
        self.assertEqual(report["codegen_repeatability"]["status"], "PASS")
        self.assertEqual(verification.call_count, 3)
        import_library.assert_called_once()
        self.git.assert_not_called()

    def test_changed_frozen_source_still_fails_without_git(self):
        (self.root / "sources/kmcos/source.txt").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "missing or changed"):
            build_kmcos.build()
        self.git.assert_not_called()

    def test_changed_benchmark_model_blocks_build_before_compilers(self):
        model = self.root / "models/kmcos/fcc_kawasaki_geometry.py"
        model.write_bytes(b"changed benchmark model")
        with patch.object(build_kmcos.subprocess, "check_output", side_effect=AssertionError("No compiler is needed")):
            with self.assertRaisesRegex(RuntimeError, "Benchmark model differs from the dependency lock"):
                build_kmcos.build()
        self.git.assert_not_called()

    def test_environment_rejects_stale_local_fields_before_any_probe(self):
        path = self.root / "config/paths.local.json"
        path.write_text(json.dumps({**self.config, "msys_root": "obsolete"}), encoding="utf-8")
        with patch.object(check_environment, "probe", side_effect=AssertionError("No tool probe is needed")):
            with self.assertRaisesRegex(ValueError, "Unknown local configuration fields"):
                check_environment.check(path)
            error = io.StringIO()
            with patch.object(sys, "argv", ["check_environment.py", "--paths", str(path)]), \
                    patch.object(sys, "stderr", error), self.assertRaises(SystemExit) as raised:
                check_environment.main()
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("Unknown local configuration fields", error.getvalue())
        self.assertFalse((self.root / "logs").exists())

    def test_environment_rejects_malformed_dependency_lock_before_any_probe(self):
        lock = json.loads((self.root / "dependencies.lock.json").read_text(encoding="utf-8"))
        lock["toolchains"]["ucrt64_cpp"]["unused"] = True
        (self.root / "dependencies.lock.json").write_text(json.dumps(lock), encoding="utf-8")
        with patch.object(check_environment, "probe", side_effect=AssertionError("No tool probe is needed")):
            with self.assertRaisesRegex(RuntimeError, "Invalid dependency lock"):
                check_environment.check(self.root / "config/paths.local.json")

    def test_read_only_verification_does_not_create_or_modify_files(self):
        def snapshot():
            return {path.relative_to(self.root): path.read_bytes()
                    for path in self.root.rglob("*") if path.is_file()}

        before = snapshot()
        sources.verify_sources(verify_only=True, write_reports=False)
        self.assertEqual(snapshot(), before)
        self.assertFalse((self.root / "logs").exists())
        self.assertFalse((self.root / "build").exists())
        sources.verify_sources(verify_only=True)
        before = snapshot()
        sources.verify_sources(verify_only=True, write_reports=False)
        self.assertEqual(snapshot(), before)
        self.git.assert_not_called()

    def test_relative_clone_configuration_resolves_against_repository_root(self):
        resolved = sources.source_repositories({"source_repositories": {"kmcos": "relative/clone"}})
        self.assertEqual(resolved["kmcos"], (self.root / "relative/clone").resolve())

    def test_read_only_aggregate_verifies_artifacts_without_rewriting_reports(self):
        lock_file = self.root / "dependencies.lock.json"
        lock = json.loads(lock_file.read_text(encoding="utf-8"))
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "environment_check.json").write_text(json.dumps({
            "environment_verified": True, "source_lock_sha256": build_all.sha(lock_file)}), encoding="utf-8")
        for name in sources.PINS:
            filename = "kmc_model.cp310-win_amd64.pyd" if name == "kmcos" else "fixture.exe"
            artifact = self.root / "build" / name / filename
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(f"Synthetic artifact for {name}".encode())
            record = {"path": str(artifact), "sha256": build_all.sha(artifact)}
            report = {"status": "PASS", "source": sources.verify_frozen_export(name, write_report=False)}
            if name == "kmcos":
                libs = self.root / ".venv-kmcos/libs"
                libs.mkdir(parents=True)
                records = {}
                for key, filename in (("definition", "python310.def"),
                                      ("import_library", "libpython310.a")):
                    target = libs / filename
                    target.write_bytes(b"synthetic import-library artifact")
                    records[key] = {"path": str(target), "sha256": build_all.sha(target)}
                model_records = [{"path": str(self.root / relative), "sha256": digest}
                                 for relative, digest in lock["model_sha256"].items()
                                 if relative.startswith("models/kmcos/")]
                report.update(artifacts=[record], model_files=model_records, python_hash_seed="0",
                    python_import_library={"status": "PASS", "input_identity": {
                        "python": {"prefix": str(libs.parent)}}, **records})
            else:
                report["artifact"] = record
                if name == "kmc_lattice":
                    relative = "models/kmc_lattice/fcc_kawasaki_selective.cpp"
                    report["application"] = {"path": str(self.root / relative),
                                             "sha256": lock["model_sha256"][relative]}
            (logs / f"build_{name}.json").write_text(json.dumps(report), encoding="utf-8")
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = build_all.aggregate(self.root / "config/paths.local.json", write_manifest=False)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(set(result["builds"]), set(sources.PINS))
        self.assertEqual(after, before)
        self.git.assert_not_called()
        for name in sources.PINS:
            with self.subTest(stale_source_digest=name):
                path = logs / f"build_{name}.json"
                original = path.read_bytes()
                report = json.loads(original)
                report["source"]["tree_sha256"] = "0" * 64
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "Build source tree digest does not match the lock"):
                    build_all.aggregate(self.root / "config/paths.local.json", write_manifest=False)
                path.write_bytes(original)
        for name in ("kmcos", "kmc_lattice"):
            with self.subTest(missing_model_record=name):
                path = logs / f"build_{name}.json"
                original = path.read_bytes()
                report = json.loads(original)
                if name == "kmcos":
                    report["model_files"].pop()
                else:
                    report.pop("application")
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "required benchmark model files"):
                    build_all.aggregate(self.root / "config/paths.local.json", write_manifest=False)
                path.write_bytes(original)
        model = self.root / "models/kmcos/fcc_kawasaki_geometry.py"
        model.write_bytes(b"changed model")
        with self.assertRaisesRegex(RuntimeError, "Benchmark model changed since build"):
            build_all.aggregate(self.root / "config/paths.local.json", write_manifest=False)


if __name__ == "__main__":
    unittest.main()
