"""Native SPPARKS observation association; synthetic dumps, no solver execution."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from adapters.spparks_adapter import SPPARKSAdapter
from benchmark_core.scenario import BenchmarkScenario, load_scenarios
from benchmark_core.validation import validate_collection


class SPPARKSObservationAssociation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.adapter = SPPARKSAdapter({}, "diffusion_sweep_random")

    @staticmethod
    def scenario(points=(0, 10, 20), *, loglin=False):
        return BenchmarkScenario(
            id="association_fixture", description="Synthetic observation fixture",
            geometry="fcc", dimension=3, nx=3, ny=3, nz=3,
            composition_A=.25, kT=.75, Ea=1.0,
            mcs_points=list(points), seeds=[1], codes=["spparks_diffusion_sweep_random"],
            spparks_loglinfreq_n=5 if loglin else None,
            spparks_loglinfreq_factor=10.0 if loglin else None,
        )

    @staticmethod
    def sites(shift=0):
        # A complete periodic FCC lattice with 64 A and 192 B sites.
        return [(x, y, z, "A" if (x - shift) % 8 < 2 else "B")
                for x in range(8) for y in range(8) for z in range(8)
                if (x + y + z) % 2 == 0]

    def fixture(self, common_times, *, stats_times=None, shifts=None):
        run_dir = self.root / "run"
        (run_dir / "snapshots").mkdir(parents=True, exist_ok=True)
        (run_dir / "external_timing.json").write_text(
            json.dumps({"returncode": 0, "wall_seconds": 9.0}), encoding="utf-8")
        shifts = [0] * len(common_times) if shifts is None else shifts
        dump = []
        for ordinal, (common_time, shift) in enumerate(zip(common_times, shifts)):
            dump.extend([
                "ITEM: TIMESTEP", f"{ordinal} {common_time / 6.0:.17g}",
                "ITEM: NUMBER OF ATOMS", "256", "ITEM: BOX BOUNDS pp pp pp",
                "0 4", "0 4", "0 4", "ITEM: ATOMS id type x y z",
            ])
            dump.extend(f"{sid} {2 if species == 'A' else 1} {x / 2:g} {y / 2:g} {z / 2:g}"
                        for sid, (x, y, z, species) in enumerate(self.sites(shift), 1))
        (run_dir / "snapshots/state_all.dump").write_text("\n".join(dump) + "\n", encoding="utf-8")
        stats_times = common_times if stats_times is None else stats_times
        stats = ["Time Naccept Nreject Nsweeps CPU Energy"]
        for index, common_time in enumerate(stats_times):
            stats.append(f"{common_time / 6.0:.17g} {index * 100} {index * 23} "
                         f"{index * 2} {index * .125:.17g} {-1000 - index * 7}")
        stats.append("Loop time of 0.875 on 1 procs")
        (run_dir / "log.spparks").write_text("\n".join(stats) + "\n", encoding="utf-8")
        return run_dir, self.root / "collected"

    @staticmethod
    def rows(out_dir, filename="metrics_common.csv"):
        with (out_dir / filename).open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def collect(self, scenario, run_dir, out_dir, *, validate=True):
        self.adapter.collect(scenario, 1, run_dir, out_dir)
        if validate:
            result = validate_collection(scenario, self.adapter.name, 1, out_dir)
            self.assertEqual(result["status"], "PASS")
        return self.rows(out_dir)

    def test_exact_rounded_checkpoint_preserves_actual_native_time_all_modes(self):
        scenario = self.scenario()
        run_dir, out_dir = self.fixture([0, 10.25, 20])
        for mode in ("diffusion_sweep_random", "diffusion_linear", "diffusion_tree"):
            with self.subTest(mode=mode):
                self.adapter = SPPARKSAdapter({}, mode)
                rows = self.collect(scenario, run_dir, out_dir)
                self.assertEqual([float(row["requested_mcs"]) for row in rows], [0, 10, 20])
                self.assertEqual(float(rows[1]["native_simulation_time"]), 10.25 / 6)
                self.assertEqual(float(rows[1]["common_mcs_equivalent"]), 10.25)
                snapshots = self.rows(out_dir, "snapshot_manifest_common.csv")
                self.assertEqual([int(row["snapshot_ordinal"]) for row in snapshots], [0, 1, 2])
                self.assertEqual(int(snapshots[1]["native_step"]), 10)
                self.assertEqual(self.adapter.common_mcs_per_native_time, 6.0)

    def test_threshold_overshoot_fallback_preserves_selected_observation(self):
        scenario = self.scenario()
        run_dir, out_dir = self.fixture([0, 10.75, 20], shifts=[0, 3, 1])
        rows = self.collect(scenario, run_dir, out_dir)
        snapshots = self.rows(out_dir, "snapshot_manifest_common.csv")
        self.assertEqual(int(snapshots[1]["native_step"]), 11)
        self.assertEqual(float(snapshots[1]["native_time_from_snapshot"]), 10.75 / 6)
        self.assertEqual(float(rows[1]["common_mcs_equivalent"]), 10.75)
        self.assertEqual(float(rows[1]["native_simulation_time"]), 10.75 / 6)
        self.assertEqual(float(rows[1]["spparks_naccept_native"]), 100)
        self.assertEqual(float(rows[1]["spparks_nreject_native"]), 23)
        self.assertEqual(float(rows[1]["spparks_energy_native"]), -1007)
        self.assertEqual(float(rows[1]["diagnostic_runtime_seconds_cumulative"]), .125)
        for species, suffix in (("A", 0), ("B", 1)):
            xyz = (run_dir / f"evaluation/Rasmol/00000010_S{suffix}.xyz").read_text().splitlines()
            coordinates = [tuple(map(int, line.split()[1:4])) for line in xyz[2:]]
            expected = [(x, y, z) for x, y, z, label in self.sites(3) if label == species]
            self.assertEqual(coordinates, expected)

    def test_nearest_valid_snapshot_wins_without_interpolation(self):
        scenario = self.scenario()
        run_dir, out_dir = self.fixture([0, 7, 10.75, 14, 20])
        rows = self.collect(scenario, run_dir, out_dir)
        snapshots = self.rows(out_dir, "snapshot_manifest_common.csv")
        self.assertEqual([int(row["snapshot_ordinal"]) for row in snapshots], [0, 2, 4])
        self.assertEqual(float(rows[1]["common_mcs_equivalent"]), 10.75)
        self.assertEqual(float(rows[1]["spparks_naccept_native"]), 200)
        self.assertEqual(float(rows[1]["diagnostic_runtime_seconds_cumulative"]), .25)

    def test_snapshot_outside_configured_tolerance_is_rejected(self):
        scenario = self.scenario((0, 10))
        run_dir, out_dir = self.fixture([0, 16])
        with self.assertRaisesRegex(FileNotFoundError, "snapshot missing at requested point 10"):
            self.collect(scenario, run_dir, out_dir)
        self.assertFalse((out_dir / "metrics_common.csv").exists())

    def test_rounded_snapshot_match_does_not_expand_native_stats_tolerance(self):
        scenario = self.scenario((0, 10))
        run_dir, out_dir = self.fixture([0, 15.2])
        rows = self.collect(scenario, run_dir, out_dir, validate=False)
        snapshots = self.rows(out_dir, "snapshot_manifest_common.csv")
        self.assertEqual(int(snapshots[1]["native_step"]), 15)
        self.assertEqual(float(rows[1]["native_simulation_time"]), 15.2 / 6)
        self.assertEqual(rows[1]["spparks_naccept_native"], "")
        self.assertEqual(rows[1]["diagnostic_runtime_seconds_cumulative"], "")
        with self.assertRaises(ValueError):
            validate_collection(scenario, self.adapter.name, 1, out_dir)

    def test_loglin_tolerance_uses_base_interval_not_late_checkpoint_gap(self):
        scenario = self.scenario((0, 10, 20, 30, 40, 50, 100), loglin=True)
        run_dir, out_dir = self.fixture([0, 10, 20, 30, 40, 50, 106])
        with self.assertRaisesRegex(FileNotFoundError, "snapshot missing at requested point 100"):
            self.collect(scenario, run_dir, out_dir)

    def test_reused_snapshot_is_rejected_by_strict_validation(self):
        scenario = self.scenario()
        run_dir, out_dir = self.fixture([0, 15])
        self.collect(scenario, run_dir, out_dir, validate=False)
        snapshots = self.rows(out_dir, "snapshot_manifest_common.csv")
        self.assertEqual([int(row["snapshot_ordinal"]) for row in snapshots], [0, 1, 1])
        with self.assertRaisesRegex(ValueError, "snapshot was reused for multiple observations"):
            validate_collection(scenario, self.adapter.name, 1, out_dir)

    def test_reused_stats_row_cannot_validate_against_distinct_dumps(self):
        scenario = self.scenario()
        run_dir, out_dir = self.fixture([0, 10, 20], stats_times=[0, 15])
        rows = self.collect(scenario, run_dir, out_dir, validate=False)
        self.assertEqual([float(row["native_simulation_time"]) for row in rows], [0, 2.5, 2.5])
        with self.assertRaisesRegex(ValueError, "snapshot and native stats time do not match"):
            validate_collection(scenario, self.adapter.name, 1, out_dir)

    def test_prepare_preserves_fixed_native_schedule_and_tail(self):
        scenario = self.scenario()
        self.adapter.prepare(scenario, 1, self.root)
        lines = (self.root / "in.spparks").read_text().splitlines()
        self.assertIn("stats 1.66666666666667 tol 1.0e-8", lines)
        self.assertIn("dump d1 text 1.66666666666667 snapshots/state_all.dump id site x y z", lines)
        self.assertIn("dump_modify d1 sort id", lines)
        self.assertIn("run 3.5", lines)
        self.assertIn("temperature 1.5", lines)
        self.assertIn("app_style diffusion linear hop", lines)
        self.assertIn("sweep random", lines)

    def test_prepare_preserves_current_manuscript_loglin_schedule(self):
        root = Path(__file__).resolve().parents[1]
        scenario = load_scenarios(root / "config/manuscript.json")[0]
        self.adapter.prepare(scenario, 1, self.root)
        lines = (self.root / "in.spparks").read_text().splitlines()
        self.assertIn("stats 1.66666666666667 loglinfreq 5 10 tol 1.0e-8", lines)
        self.assertIn("dump d1 text 1.66666666666667 snapshots/state_all.dump id site x y z", lines)
        self.assertIn("dump_modify d1 loglinfreq 5 10 sort id tol 1.0e-8", lines)
        self.assertIn("run 5000.16666666667", lines)


if __name__ == "__main__":
    unittest.main()
