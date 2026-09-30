"""Current machine-path and pinned-solver input contracts; no native execution."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark_core.local_config import read_local_config
from benchmark_core.snapshots import read_nanokmc_rasmol_xyz, read_spparks_sites_dump
from benchmark_core.timing import msys2_bash_command, msys2_runtime_env


class CurrentInputs(unittest.TestCase):
    def test_local_paths_keep_science_and_dependency_data_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paths.json"
            expected = {"msys2_root": "C:/tools/msys64", "kmcos_python": ".venv-kmcos/Scripts/python.exe",
                        "source_repositories": {"nanokmc": "../sources/nanokmc"}}
            path.write_text(json.dumps(expected))
            self.assertEqual(read_local_config(path), expected)
            for extra in ({"campaigns": {}}, {"toolchains": {}}, {"msys_root": "C:/tools"},
                          {"benchmark_python": "python.exe"}):
                with self.subTest(extra=extra):
                    path.write_text(json.dumps({**expected, **extra}))
                    with self.assertRaisesRegex(ValueError, "Unknown local"):
                        read_local_config(path)

    def test_local_paths_reject_invalid_types_duplicate_keys_and_search_order(self):
        cases = [[], {"msys2_root": None}, {"msys2_root": ""}, {"source_repositories": []},
                 {"source_repositories": {"unknown": "../repo"}},
                 {"source_repositories": {"kmcos": 7}}, {"msys2_path_prefix": "/usr/bin"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paths.json"
            for value in cases:
                with self.subTest(value=value):
                    path.write_text(json.dumps(value))
                    with self.assertRaises(ValueError):
                        read_local_config(path)
            path.write_text('{"msys2_root":"first", "msys2_root":"second"}')
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                read_local_config(path)
            self.assertEqual(read_local_config(path.parent / "absent.json", missing_ok=True), {})
            with self.assertRaises(FileNotFoundError):
                read_local_config(path.parent / "absent.json")

    def test_runtime_paths_match_declared_toolchain_families(self):
        root = Path("toolchain").resolve()
        with patch("benchmark_core.timing.Path.exists", return_value=True), patch.dict(os.environ, {"PATH": "remaining"}):
            environment = msys2_runtime_env({"msys2_root": str(root)})
        self.assertEqual(environment["PATH"], os.pathsep.join((str(root / "ucrt64/bin"), str(root / "usr/bin"), "remaining")))
        with patch("benchmark_core.timing.resolve_msys2_bash", return_value="bash.exe"):
            self.assertIn("export PATH=/ucrt64/bin:/usr/bin:$PATH", msys2_bash_command({}, "true")[-1])
            with self.assertRaises(ValueError):
                msys2_bash_command({"msys2_path_prefix": "/usr/bin"}, "true")

    def test_spparks_requires_explicit_native_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.dump"
            fixture = ("ITEM: TIMESTEP\n3 1.6666666666666667\nITEM: NUMBER OF ATOMS\n2\n"
                       "ITEM: BOX BOUNDS pp pp pp\n0 4\n0 4\n0 4\n"
                       "ITEM: ATOMS id type x y z\n1 2 0 0 0\n2 1 0 0.5 0.5\n")
            path.write_text(fixture)
            snapshot, = read_spparks_sites_dump(path, fcc_cells=4, common_time_scale=6)
            self.assertEqual(snapshot.step, 10)
            self.assertAlmostEqual(snapshot.native_time, 10 / 6)
            self.assertEqual(snapshot.sites, ((1, 0, 0, 0, "A"), (2, 0, 1, 1, "B")))
            path.write_text(fixture.replace("3 1.6666666666666667", "3"))
            with self.assertRaisesRegex(ValueError, "dump index and native time"):
                read_spparks_sites_dump(path, fcc_cells=4)

    def test_spparks_requires_current_type_and_explicit_site_identity(self):
        fixture = ("ITEM: TIMESTEP\n0 0\nITEM: NUMBER OF ATOMS\n1\n"
                   "ITEM: BOX BOUNDS\n0 4\n0 4\n0 4\n"
                   "ITEM: ATOMS id type x y z\n1 2 0 0 0\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.dump"
            for invalid in (fixture.replace("id type", "type"),
                            fixture.replace("id type", "id site")):
                path.write_text(invalid)
                with self.assertRaisesRegex(ValueError, "id type x y z"):
                    read_spparks_sites_dump(path, fcc_cells=4)

    def test_snapshot_readers_require_configured_periods(self):
        with self.assertRaises(TypeError):
            read_spparks_sites_dump(Path("unused.dump"))
        with self.assertRaises(TypeError):
            read_nanokmc_rasmol_xyz(Path("unused"), 0)

    def test_nanokmc_reads_only_current_species_split_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "evaluation/Rasmol"
            output.mkdir(parents=True)
            (output / "00000010_S0.xyz").write_text("1\nA\nA 0 0 0\n")
            (output / "00000010_S1.xyz").write_text("1\nB\nB 0 1 1\n")
            (output / "00000010.dat_S0.xyz").write_text("1\nunselected\nA 1 1 0\n")
            snapshot = read_nanokmc_rasmol_xyz(root, 10, periods=(8, 8, 8))
            self.assertEqual(len(snapshot.sites), 2)
            self.assertEqual({row[-1] for row in snapshot.sites}, {"A", "B"})
            self.assertIsNone(read_nanokmc_rasmol_xyz(root, 20, periods=(8, 8, 8)))


if __name__ == "__main__":
    unittest.main()
