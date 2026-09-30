"""Independent scientific definitions at the shared figure-data boundary."""
import math
import unittest

from benchmark_core.figure_data import (
    BINARY, POINTS, cutoff12_concentration, derive_tables, event_counters, log_fit,
)
from benchmark_core.runner import PATH_IDS
from tests.test_publication import synthetic_rows


class SharedFigureData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.clusters = synthetic_rows()

    def test_cluster_bins_include_exact_boundaries_without_width_normalization(self):
        sizes = (11, 12, 49, 50, 199, 200, 999, 1000, 3999, 4000, 9000)
        distribution = [dict(common_species="A", cluster_size=size, cluster_count=1) for size in sizes]
        distribution.append(dict(common_species="B", cluster_size=12, cluster_count=99))
        tables = derive_tables(self.rows, {code: distribution for code in PATH_IDS}, [5])
        bins = [row for row in tables["fig05_clusters.csv"] if row["code"] == BINARY]
        self.assertEqual([(row["bin_min"], row["bin_max"]) for row in bins],
                         [(12, 49), (50, 199), (200, 999), (1000, 3999), (4000, None)])
        self.assertEqual([row["cluster_count"] for row in bins], [2, 2, 2, 2, 2])

    def test_cutoff12_uses_A_mass_below_twelve_and_B_plus_small_A_denominator(self):
        distribution = [dict(common_species=species, cluster_size=size, cluster_count=count)
                        for species, size, count in (("A", 1, 2), ("A", 11, 3),
                                                     ("A", 12, 4), ("A", 13, 5), ("B", 1, 65))]
        self.assertEqual(cutoff12_concentration(distribution, 65), (35, .35))
        self.assertEqual(cutoff12_concentration(distribution[-1:], 65), (0, 0))

    def test_all_eleven_native_counter_mappings_preserve_path_specific_meanings(self):
        for code in PATH_IDS:
            with self.subTest(code=code):
                if code.startswith("spparks_"):
                    row = dict(spparks_naccept_native="17", spparks_nreject_native="20")
                    expected = 37 if code.endswith("sweep_random") else 17
                    source = "spparks_naccept_native+spparks_nreject_native" if expected == 37 else "spparks_naccept_native"
                elif code.startswith("nanokmc_"):
                    expected = 17 if code == "nanokmc_exact_class_optimized" else 37
                    row = dict(accepted_exchanges_total="17", attempted_exchanges_total=str(expected))
                    source = "attempted_exchanges_total;accepted_exchanges_total"
                else:
                    expected = 17
                    row = dict(accepted_exchanges_total="17", attempted_exchanges_total="")
                    source = "accepted_exchanges_total"
                candidates, executed, meaning, actual_source = event_counters(code, row)
                self.assertEqual((candidates, executed, actual_source), (expected, 17, source))
                self.assertIn("rejection-free" if expected == 17 else "proposals", meaning)

    def test_invalid_native_counters_fail_instead_of_becoming_zero(self):
        for candidates, executed in (("16", "17"), ("nan", "17"), ("18", "1.5"), ("18", "-1")):
            with self.subTest(candidates=candidates, executed=executed):
                with self.assertRaises(ValueError):
                    event_counters(BINARY, dict(attempted_exchanges_total=candidates, accepted_exchanges_total=executed))

    def test_correspondence_joins_requested_state_seed_points_without_interpolation(self):
        comparator = "spparks_diffusion_tree"
        rows = [dict(row) for row in self.rows if row["code"] in (BINARY, comparator) and row["k"] == 6]
        for row in rows:
            row["rho_AB"] = row["requested_mcs"] + 100000 * row["x_A_nominal"] + 1000 * row["T_star"]
            if row["code"] == comparator:
                row["rho_AB"] += 7
                row["realized_common_mcs"] += 99
        pairs = derive_tables(rows, {}, [6], partial=True)["fig06_correspondence.csv"]
        self.assertEqual(len(pairs), 4 * 18 * 3)
        self.assertEqual({row["requested_mcs"] for row in pairs}, set(POINTS[1:]))
        for row in pairs:
            self.assertEqual(row["comparator"], comparator)
            self.assertEqual(row["seed"], 1)
            self.assertEqual(row["comparator_realized_mcs"] - row["binary_realized_mcs"], 99)
            if row["observable"] == "rho_AB":
                self.assertEqual(row["comparator_value"] - row["binary_value"], 7)

    def test_correspondence_rejects_duplicates_and_incomplete_present_trajectories(self):
        representative = [row for row in self.rows if row["code"] == BINARY and row["k"] == 6
                          and row["x_A_nominal"] == .2 and row["T_star"] == .75]
        for partial in (False, True):
            with self.subTest(partial=partial):
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    derive_tables(representative + [representative[0]], {}, [6], partial=partial)
                missing = [row for row in representative if row["requested_mcs"] != 20]
                with self.assertRaisesRegex(ValueError, "Missing"):
                    derive_tables(missing, {}, [6], partial=partial)
        self.assertEqual(derive_tables(representative, {}, [6], partial=True)["fig06_correspondence.csv"], [])

    def test_horizon_fit_uses_exact_sixteen_points_and_known_power_law(self):
        rows = [dict(row) for row in self.rows]
        for row in rows:
            point = row["requested_mcs"]
            row["runtime_seconds"] = 7 * point ** 1.25 if point >= 30 else 123456
        tables = derive_tables(rows, {}, [8])
        horizon = tables["fig08_runtime_horizon.csv"]
        self.assertEqual(len(horizon), 209)
        self.assertEqual({row["requested_mcs"] for row in horizon if row["fit_included"]}, set(POINTS[3:]))
        self.assertEqual(sum(row["fit_included"] for row in horizon), 176)
        self.assertTrue(all(row["displayed"] == row["fit_included"] for row in horizon))
        for row in tables["fig08_gamma.csv"]:
            self.assertEqual(row["n_points"], 16)
            self.assertAlmostEqual(row["gamma"], 1.25, places=12)
            self.assertAlmostEqual(row["A_seconds"], 7, places=11)
            self.assertAlmostEqual(row["r_squared"], 1, places=12)

    def test_partial_size_means_sample_SD_SEM_and_unavailable_singleton(self):
        rows = [dict(row) for row in self.rows if row["code"] == BINARY and row["requested_mcs"] == 30000
                and row["x_A_nominal"] == .2 and row["T_star"] == .75
                and ((row["k"] == 3 and row["seed"] <= 2) or row["k"] == 6)]
        for row in rows:
            row["runtime_seconds"] = 2 * row["seed"] if row["k"] == 3 else 9
        tables = derive_tables(rows, {}, [10], partial=True)
        small, single = tables["fig10_size_scaling.csv"]
        self.assertEqual((small["n"], small["expected_n"], small["mean_seconds"]), (2, 512, 3))
        self.assertAlmostEqual(small["sample_sd_seconds"], math.sqrt(2))
        self.assertAlmostEqual(small["sem_seconds"], 1)
        self.assertEqual((single["n"], single["mean_seconds"]), (1, 9))
        self.assertIsNone(single["sample_sd_seconds"])
        self.assertIsNone(single["sem_seconds"])
        self.assertEqual(tables["fig10_alpha.csv"], [])

    def test_size_fit_uses_four_arithmetic_means_with_equal_weight(self):
        counts = {3: 512, 4: 64, 5: 8, 6: 1}
        rows = [dict(row) for row in self.rows]
        for row in rows:
            count = counts[row["k"]]
            # Symmetric nonzero within-size variation leaves arithmetic mean 5*N**1.5.
            row["runtime_seconds"] = 5 * row["N"] ** 1.5 + row["seed"] - (count + 1) / 2
        tables = derive_tables(rows, {}, [10])
        for row in tables["fig10_size_scaling.csv"]:
            self.assertEqual(row["n"], counts[row["k"]])
            self.assertAlmostEqual(row["mean_seconds"], 5 * row["N"] ** 1.5)
        for row in tables["fig10_alpha.csv"]:
            self.assertEqual(row["n_points"], 4)
            self.assertAlmostEqual(row["alpha"], 1.5, places=12)
            self.assertAlmostEqual(row["A_seconds"], 5, places=10)
        fit = log_fit([1, 4, 16, 64], [3, 24, 192, 1536])
        self.assertAlmostEqual(fit["slope"], 1.5, places=12)
        self.assertAlmostEqual(fit["A_seconds"], 3, places=12)

    def test_strict_coverage_rejects_missing_paths_and_ensemble_seeds(self):
        absent_path = [row for row in self.rows if row["code"] != "kmcos_otf"]
        for figure in range(5, 11):
            with self.subTest(figure=figure):
                with self.assertRaisesRegex(ValueError, "Missing"):
                    derive_tables(absent_path, self.clusters, [figure])
        absent_seed = [row for row in self.rows if not (row["code"] == BINARY and row["k"] == 3 and row["seed"] == 512)]
        with self.assertRaisesRegex(ValueError, "Missing"):
            derive_tables(absent_seed, {}, [10])


if __name__ == "__main__":
    unittest.main()
