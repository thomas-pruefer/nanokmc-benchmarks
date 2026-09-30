"""Native qualification checks for the benchmark-owned KMC_Lattice application.

These tests are intentionally absent from the ordinary offline test cost. Set
``KMC_LATTICE_TEST_EXE`` to a freshly built ``kmc_lattice_fcc.exe`` to enable
them. Every run writes only to a test-owned temporary directory.
"""
from __future__ import annotations

import csv
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest


FCC_OFFSETS = (
    (1, 1, 0), (1, -1, 0), (-1, 1, 0), (-1, -1, 0),
    (1, 0, 1), (1, 0, -1), (-1, 0, 1), (-1, 0, -1),
    (0, 1, 1), (0, 1, -1), (0, -1, 1), (0, -1, -1),
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_snapshot(path: Path) -> tuple[int, set[tuple[int, int, int]]]:
    data = path.read_bytes()
    if len(data) < 20 or data[:8] != b"KMCLAT01":
        raise AssertionError(f"Invalid KMC_Lattice snapshot header: {path}")
    period = struct.unpack_from("<I", data, 8)[0]
    site_count = struct.unpack_from("<Q", data, 12)[0]
    species = data[20:]
    if len(species) != site_count:
        raise AssertionError(
            f"Snapshot species length {len(species)} != declared site count {site_count}"
        )
    coordinates = [
        (x, y, z)
        for x in range(period)
        for y in range(period)
        for z in range(period)
        if (x + y + z) % 2 == 0
    ]
    if len(coordinates) != site_count:
        raise AssertionError(
            f"FCC parity site count {len(coordinates)} != snapshot site count {site_count}"
        )
    if any(value not in (0, 1) for value in species):
        raise AssertionError("Snapshot contains a species byte outside {0,1}")
    return period, {coords for coords, value in zip(coordinates, species) if value == 0}


def _neighbor(coords: tuple[int, int, int], offset: tuple[int, int, int], period: int):
    return tuple((coords[axis] + offset[axis]) % period for axis in range(3))


def _a_neighbors(coords: tuple[int, int, int], a_sites: set[tuple[int, int, int]], period: int) -> int:
    return sum(_neighbor(coords, offset, period) in a_sites for offset in FCC_OFFSETS)


def _rate(
    source: tuple[int, int, int],
    destination: tuple[int, int, int],
    a_sites: set[tuple[int, int, int]],
    period: int,
    *,
    ea: float,
    kt: float,
) -> float:
    initial_neighbors = _a_neighbors(source, a_sites, period)
    final_neighbors = _a_neighbors(destination, a_sites, period) - 1
    delta = initial_neighbors - final_neighbors
    return 1.0 if delta <= 0 else math.exp(-ea * delta / kt)


def _outgoing_rate(
    source: tuple[int, int, int],
    a_sites: set[tuple[int, int, int]],
    period: int,
    *,
    ea: float,
    kt: float,
) -> float:
    return sum(
        _rate(source, destination, a_sites, period, ea=ea, kt=kt)
        for offset in FCC_OFFSETS
        for destination in (_neighbor(source, offset, period),)
        if destination not in a_sites
    )


def _generator(
    a_sites: set[tuple[int, int, int]], period: int, *, ea: float, kt: float
) -> tuple[int, float]:
    active_bonds = 0
    total_rate = 0.0
    for source in a_sites:
        for offset in FCC_OFFSETS:
            destination = _neighbor(source, offset, period)
            if destination not in a_sites:
                active_bonds += 1
                total_rate += _rate(source, destination, a_sites, period, ea=ea, kt=kt)
    return active_bonds, total_rate


class KMCLatticeNativeQualification(unittest.TestCase):
    """Small native tests, enabled only for an explicitly selected executable."""

    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("KMC_LATTICE_TEST_EXE")
        if not configured:
            raise unittest.SkipTest("set KMC_LATTICE_TEST_EXE to enable native KMC_Lattice tests")
        cls.executable = Path(configured).expanduser().resolve()
        if not cls.executable.is_file():
            raise AssertionError(f"KMC_LATTICE_TEST_EXE is not a file: {cls.executable}")
        cls.native_env = os.environ.copy()
        ucrt_bin = Path(os.environ.get("KMC_LATTICE_UCRT64_BIN", "C:/msys64/ucrt64/bin"))
        cls.native_env["PATH"] = os.pathsep.join((str(ucrt_bin), cls.native_env.get("PATH", "")))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nanokmc_kmc_lattice_native_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_index = 0

    def _run(
        self,
        *,
        mcs_points: str = "0",
        composition: float = 0.5,
        kt: float = 1.0,
        extra: tuple[str, ...] = (),
    ) -> tuple[subprocess.CompletedProcess[str], Path, list[str]]:
        self.run_index += 1
        output = self.root / f"run_{self.run_index:02d}"
        command = [
            str(self.executable),
            "--fcc-cells", "4",
            "--composition-a", str(composition),
            "--kt", str(kt),
            "--ea", "1",
            "--seed", "1",
            "--mcs-points", mcs_points,
            "--recalc-mode", "selective",
            "--output-dir", str(output),
            *extra,
        ]
        completed = subprocess.run(
            command,
            cwd=self.root,
            env=self.native_env,
            text=True,
            capture_output=True,
            timeout=60,
        )
        return completed, output, command

    def _assert_failed(self, *, extra: tuple[str, ...], mcs_points: str = "0", message: str):
        completed, _, command = self._run(mcs_points=mcs_points, extra=extra)
        self.assertNotEqual(
            completed.returncode,
            0,
            msg=f"Command unexpectedly succeeded: {command}\nstdout:\n{completed.stdout}",
        )
        combined = completed.stdout + "\n" + completed.stderr
        self.assertIn(message, combined)

    def test_selective_event_replay_and_stored_rate_validation(self):
        completed, output, command = self._run(
            extra=("--validation-events", "25", "--validate-rates-each-event")
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"Native validation failed: {command}\n{completed.stdout}\n{completed.stderr}",
        )

        progress = _read_rows(output / "progress.csv")
        self.assertEqual(len(progress), 1)
        initial = progress[0]
        self.assertEqual(int(initial["save_index"]), 0)
        self.assertEqual(float(initial["requested_mcs"]), 0.0)
        self.assertEqual(float(initial["requested_native_time"]), 0.0)
        self.assertEqual(float(initial["native_last_event_time"]), 0.0)
        self.assertEqual(int(initial["executed_moves"]), 0)
        self.assertEqual(int(initial["events_since_previous"]), 0)

        period, a_sites = _read_snapshot(output / initial["snapshot"])
        self.assertEqual(period, 8)
        self.assertEqual(len(a_sites), 128)
        self.assertEqual(int(initial["N_A"]), 128)
        self.assertEqual(int(initial["N_total"]), 256)
        active_bonds, total_rate = _generator(a_sites, period, ea=1.0, kt=1.0)
        self.assertEqual(int(initial["active_bonds"]), active_bonds)
        self.assertTrue(math.isclose(float(initial["total_rate"]), total_rate, rel_tol=2e-12, abs_tol=2e-12))

        events = _read_rows(output / "validation_events.csv")
        self.assertEqual(len(events), 25)
        previous_time = 0.0
        for expected_index, row in enumerate(events, 1):
            self.assertEqual(int(row["event_index"]), expected_index)
            source = (int(row["start_x"]), int(row["start_y"]), int(row["start_z"]))
            destination = (int(row["dest_x"]), int(row["dest_y"]), int(row["dest_z"]))
            native_time = float(row["native_time"])
            self.assertTrue(math.isfinite(native_time) and native_time > previous_time)
            previous_time = native_time
            self.assertIn(source, a_sites)
            self.assertNotIn(destination, a_sites)
            self.assertIn(destination, {_neighbor(source, offset, period) for offset in FCC_OFFSETS})
            expected_rate = _rate(source, destination, a_sites, period, ea=1.0, kt=1.0)
            expected_outgoing = _outgoing_rate(source, a_sites, period, ea=1.0, kt=1.0)
            self.assertTrue(math.isclose(float(row["selected_rate"]), expected_rate, rel_tol=2e-12, abs_tol=2e-12))
            self.assertTrue(
                math.isclose(
                    float(row["object_total_rate"]), expected_outgoing, rel_tol=2e-12, abs_tol=2e-12
                )
            )
            a_sites.remove(source)
            a_sites.add(destination)
            self.assertEqual(len(a_sites), 128)

        final_period, final_a_sites = _read_snapshot(output / "snapshots" / "validation_final.bin")
        self.assertEqual(final_period, period)
        self.assertEqual(final_a_sites, a_sites)
        summary = dict(
            line.split("=", 1)
            for line in (output / "summary.txt").read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        self.assertEqual(summary["adapter_code"], "kmc_lattice_selective")
        self.assertEqual(int(summary["executed_moves"]), 25)

    def test_production_observation_boundaries_counters_and_snapshots(self):
        completed, output, command = self._run(mcs_points="0,1,2", composition=0.2, kt=0.75)
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"Native production probe failed: {command}\n{completed.stdout}\n{completed.stderr}",
        )
        self.assertFalse((output / "validation_events.csv").exists())
        self.assertFalse((output / "snapshots" / "validation_final.bin").exists())

        rows = _read_rows(output / "progress.csv")
        self.assertEqual([float(row["requested_mcs"]) for row in rows], [0.0, 1.0, 2.0])
        previous_moves = 0
        previous_last_event = 0.0
        previous_wall = 0.0
        wall_segment_sum = 0.0
        for index, row in enumerate(rows):
            requested_mcs = float(row["requested_mcs"])
            target_native_time = requested_mcs / 6.0
            last_event_time = float(row["native_last_event_time"])
            moves = int(row["executed_moves"])
            segment_moves = int(row["events_since_previous"])
            segment_wall = float(row["evolution_wall_seconds_segment"])
            cumulative_wall = float(row["evolution_wall_seconds_cumulative"])
            self.assertEqual(int(row["save_index"]), index)
            self.assertTrue(math.isclose(float(row["requested_native_time"]), target_native_time))
            self.assertLessEqual(last_event_time, target_native_time + 1e-15)
            self.assertGreaterEqual(last_event_time, previous_last_event)
            self.assertEqual(segment_moves, moves - previous_moves)
            self.assertGreaterEqual(moves, previous_moves)
            self.assertGreaterEqual(segment_wall, 0.0)
            self.assertGreaterEqual(cumulative_wall, previous_wall)
            wall_segment_sum += segment_wall
            self.assertTrue(math.isclose(cumulative_wall, wall_segment_sum, rel_tol=1e-12, abs_tol=1e-12))

            period, a_sites = _read_snapshot(output / row["snapshot"])
            self.assertEqual(period, 8)
            self.assertEqual(len(a_sites), 51)
            self.assertEqual(int(row["N_A"]), 51)
            self.assertEqual(int(row["N_total"]), 256)
            active_bonds, total_rate = _generator(a_sites, period, ea=1.0, kt=0.75)
            self.assertEqual(int(row["active_bonds"]), active_bonds)
            self.assertTrue(
                math.isclose(float(row["total_rate"]), total_rate, rel_tol=2e-12, abs_tol=2e-12)
            )
            previous_moves = moves
            previous_last_event = last_event_time
            previous_wall = cumulative_wall

        self.assertEqual(int(rows[0]["executed_moves"]), 0)
        self.assertEqual(float(rows[0]["evolution_wall_seconds_cumulative"]), 0.0)
        self.assertGreater(int(rows[-1]["executed_moves"]), 0)

    def test_retired_modes_and_restart_options_are_rejected(self):
        cases = (
            (("--recalc-mode", "full"), "--recalc-mode must be selective"),
            (("--recalc-mode", "frm"), "--recalc-mode must be selective"),
            (("--initial-snapshot", "retired.bin"), "Unknown argument: --initial-snapshot"),
            (("--start-mcs", "0"), "Unknown argument: --start-mcs"),
            (("--executed-moves-offset", "0"), "Unknown argument: --executed-moves-offset"),
            (("--cumulative-wall-offset", "0"), "Unknown argument: --cumulative-wall-offset"),
        )
        for extra, message in cases:
            with self.subTest(option=extra[0], value=extra[1]):
                self._assert_failed(extra=extra, message=message)

    def test_regression_validation_guards(self):
        cases = (
            (("--validation-events", "-1"), "--validation-events must be non-negative", "0"),
            (("--validate-rates-each-event",),
             "--validate-rates-each-event requires positive --validation-events", "0"),
            (("--validation-events", "1"),
             "--validation-events requires --mcs-points 0 (regression only)", "0,1"),
        )
        for extra, message, points in cases:
            with self.subTest(extra=extra, mcs_points=points):
                self._assert_failed(extra=extra, mcs_points=points, message=message)


if __name__ == "__main__":
    unittest.main()
