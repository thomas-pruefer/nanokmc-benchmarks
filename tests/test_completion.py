"""Scientific completion rejects plausible-looking but incomplete native records."""
import csv
from pathlib import Path
import tempfile
import unittest

from benchmark_core.scenario import BenchmarkScenario
from benchmark_core.validation import validate_collection


def write_rows(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class Completion(unittest.TestCase):
    def test_complete_and_corrupted_records(self):
        code = "spparks_diffusion_tree"
        scenario = BenchmarkScenario("fixture", "validation fixture", "fcc", 3, 3, 3, 3,
                                     0.2, 0.75, 1.0, [0, 10], [1], [code])
        metrics = []
        snapshots = []
        clusters = []
        timings = []
        for index, point in enumerate([0, 10]):
            metrics.append(dict(requested_mcs=point, code=code, seed=1, scenario_id="fixture",
                save_index=index, total_sites=256, N_A_common=51, N_B_common=205,
                total_undirected_bonds_common=1536, interface_density_common=0.2,
                diagnostic_runtime_seconds_cumulative=0.001*index, diagnostic_runtime_source="native CPU",
                common_mcs_equivalent=point, native_simulation_time=point/6, spparks_naccept_native=100*index,
                attempted_exchanges_total="", accepted_exchanges_total=""))
            snapshots.append(dict(save_index=index, total_sites=256, topology_valid=1, parity_valid=1,
                min_neighbors=12, max_neighbors=12, unique_coordinates=256, unique_site_ids=256, N_A=51, N_B=205,
                code=code, scenario_id="fixture", seed=1, native_step=point,
                native_time_from_snapshot=point/6, snapshot_ordinal=index))
            timings.append(dict(save_index=index, code=code, scenario_id="fixture", seed=1,
                                diagnostic_runtime_seconds_cumulative=0.001*index, progress_common_mcs_equivalent=point))
            clusters.extend([dict(save_index=index, common_species=species, cluster_size=size, cluster_count=1,
                                  sites_in_clusters=size, code=code, scenario_id="fixture", seed=1)
                             for species,size in (("A",51),("B",205))])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_rows(output / "metrics_common.csv", metrics)
            write_rows(output / "snapshot_manifest_common.csv", snapshots)
            write_rows(output / "timing_common.csv", timings)
            write_rows(output / "cluster_distribution_common.csv", clusters)
            result = validate_collection(scenario, code, 1, output)
            self.assertEqual(result["observations"][-1]["executed_exchanges"], 100)
            self.assertEqual(result["observations"][-1]["candidate_or_selected_events"], 100)
            # A present endpoint alone cannot mark a two-observation run complete.
            write_rows(output / "metrics_common.csv", metrics[1:])
            with self.assertRaises(ValueError):
                validate_collection(scenario, code, 1, output)
            metrics[1]["diagnostic_runtime_seconds_cumulative"] = "nan"
            write_rows(output / "metrics_common.csv", metrics)
            with self.assertRaises(ValueError):
                validate_collection(scenario, code, 1, output)
            metrics[1]["diagnostic_runtime_seconds_cumulative"] = 0.001
            metrics[1]["spparks_naccept_native"] = ""
            write_rows(output / "metrics_common.csv", metrics)
            with self.assertRaises(ValueError):
                validate_collection(scenario, code, 1, output)


if __name__ == "__main__":
    unittest.main()
