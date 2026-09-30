"""Runtime verification is read-only; all tools/solver checks are offline mocks."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from benchmark_core import runtime_identity


class RuntimeIdentityTests(unittest.TestCase):
    def test_verification_creates_no_reports_and_preserves_all_input_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths_file = root / "paths.json"
            paths_file.write_text(json.dumps({"kmcos_root": str(root / "sources/kmcos"),
                "kmc_lattice_root": str(root / "sources/kmc_lattice"), "msys2_root": str(root / "toolchain")}))
            (root / "dependencies.lock.json").write_text("{}")
            python = root / "synthetic_python.exe"
            python.write_bytes(b"offline identity fixture, not executable")
            environment = {"environment_verified": True, "errors": [], "runtime_libraries_observed": {},
                "benchmark_python": {"version": "synthetic", "packages": {}},
                "kmcos_python": {"observed": {"executable": str(python), "base_prefix": str(root),
                    "python": "synthetic", "packages": {}}}, "host": {"fixture": True}}
            check = Mock(return_value=environment)
            aggregate = Mock(return_value={"builds": {}})
            verify = Mock()
            modules = {"check_environment": SimpleNamespace(check=check),
                       "build_all": SimpleNamespace(aggregate=aggregate),
                       "smoke_test": SimpleNamespace(verify_build_identity=verify)}
            before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with patch.object(runtime_identity, "ROOT", root), patch.object(sys, "executable", str(python)), \
                    patch.object(sys, "base_prefix", str(root)), patch.dict(sys.modules, modules):
                result = runtime_identity.current_runtime_identity(paths_file)
            after = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertFalse((root / "logs").exists())
            self.assertFalse((root / "build").exists())
            aggregate.assert_called_once_with(paths_file.resolve(), write_manifest=False)
            check.assert_called_once_with(paths_file.resolve())
            self.assertEqual(verify.call_count, 11)
            self.assertEqual(len(result["fingerprint"]), 64)
            self.assertEqual(sum(r.get("role") == "path_configuration" for r in result["verification_files"]), 1)


if __name__ == "__main__":
    unittest.main()
