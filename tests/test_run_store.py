"""Strict attempt lifecycle using synthetic records only; never runs an upstream solver."""
import csv
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from benchmark_core.campaigns import RunSpec
from benchmark_core.scenario import BenchmarkScenario
from benchmark_core.run_store import (CampaignLock, CompletionError, create_attempt, identity_hash,
                                     identity_for_jobs, seal_completed, verified_completed)
from scripts.run_manuscript_campaigns import execute_attempt


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def fixture_spec():
    code = "spparks_diffusion_tree"
    scenario = BenchmarkScenario("synthetic_fixture_only", "No simulation; validation fixture", "fcc", 3,
                                 3, 3, 3, .2, .75, 1., [0, 10], [1], [code])
    return RunSpec(scenario, code, 1)


def fill_attempt(attempt, spec):
    raw, out = attempt / "native", attempt / "processed"
    (raw / "external_timing.json").write_text(json.dumps({"returncode": 0}))
    (raw / "synthetic_native.txt").write_text("native bytes, not simulation data")
    (out / "run_metadata.json").write_text("{}")
    metrics, snapshots, clusters, timings = [], [], [], []
    for index, point in enumerate(spec.scenario.mcs_points):
        metrics.append(dict(requested_mcs=point, code=spec.code, seed=spec.seed, scenario_id=spec.scenario.id,
            save_index=index, total_sites=256, N_A_common=51, N_B_common=205,
            total_undirected_bonds_common=1536, interface_density_common=.2,
            diagnostic_runtime_seconds_cumulative=.001 * index, diagnostic_runtime_source="native CPU",
            common_mcs_equivalent=point, native_simulation_time=point/6, spparks_naccept_native=100*index,
            attempted_exchanges_total="", accepted_exchanges_total=""))
        snapshots.append(dict(save_index=index, total_sites=256, topology_valid=1, parity_valid=1,
            min_neighbors=12, max_neighbors=12, unique_coordinates=256, unique_site_ids=256, N_A=51, N_B=205,
            code=spec.code, scenario_id=spec.scenario.id, seed=spec.seed, requested_common_mcs=point,
            native_step=point, native_time_from_snapshot=point/6, snapshot_ordinal=index))
        timings.append(dict(save_index=index, code=spec.code, scenario_id=spec.scenario.id, seed=spec.seed,
                            requested_common_mcs=point, requested_mcs=point,
                            progress_common_mcs_equivalent=point,
                            diagnostic_runtime_seconds_cumulative=.001*index,
                            diagnostic_runtime_source="native CPU"))
        clusters.extend([dict(save_index=index, common_species="A", cluster_size=51, cluster_count=1,
                              code=spec.code, scenario_id=spec.scenario.id, seed=spec.seed, sites_in_clusters=51),
                         dict(save_index=index, common_species="B", cluster_size=205, cluster_count=1,
                              code=spec.code, scenario_id=spec.scenario.id, seed=spec.seed, sites_in_clusters=205)])
    for name, rows in (("metrics_common.csv", metrics), ("snapshot_manifest_common.csv", snapshots),
                       ("timing_common.csv", timings), ("cluster_distribution_common.csv", clusters)):
        write_csv(out / name, rows)


class RunStoreTests(unittest.TestCase):
    def test_resume_only_accepts_unchanged_complete_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, spec, identity = Path(temporary), fixture_spec(), identity_for_jobs({"fixture_build": "frozen"}, 1)
            with self.assertRaises(CompletionError) as failure:
                verified_completed(root, spec, identity)
            self.assertEqual(failure.exception.status, "missing")
            attempt = create_attempt(root, spec, identity)
            fill_attempt(attempt, spec)
            with self.assertRaises(CompletionError) as failure:
                verified_completed(root, spec, identity)
            self.assertEqual(failure.exception.status, "incomplete")
            seal_completed(root, spec, identity, attempt)
            self.assertEqual(verified_completed(root, spec, identity).raw_dir, attempt / "native")
            with self.assertRaises(CompletionError) as failure:
                verified_completed(root, spec, {"fixture_build": "changed"})
            self.assertEqual(failure.exception.status, "stale")
            changed_spec = replace(spec, scenario=replace(spec.scenario, kT=1.25))
            with self.assertRaises(CompletionError):
                verified_completed(root, changed_spec, identity)
            (attempt / "native/synthetic_native.txt").write_text("corrupt")
            with self.assertRaises(CompletionError) as failure:
                verified_completed(root, spec, identity)
            self.assertEqual(failure.exception.status, "corrupt")
            second = create_attempt(root, spec, identity)
            fill_attempt(second, spec)
            seal_completed(root, spec, identity, second)
            self.assertTrue(attempt.is_dir())
            self.assertNotEqual(attempt, second)
            self.assertEqual(verified_completed(root, spec, identity).raw_dir, second / "native")
            (second / "processed/unexpected.csv").write_text("not sealed")
            with self.assertRaises(CompletionError):
                verified_completed(root, spec, identity)

    def test_missing_checkpoint_cannot_be_sealed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, spec = Path(temporary), fixture_spec()
            identity = identity_for_jobs({}, 1)
            attempt = create_attempt(root, spec, identity)
            fill_attempt(attempt, spec)
            path = attempt / "processed/metrics_common.csv"
            lines = path.read_text().splitlines()
            path.write_text("\n".join(lines[:-1]) + "\n")
            with self.assertRaises(ValueError):
                seal_completed(root, spec, identity, attempt)
            with self.assertRaises(CompletionError):
                verified_completed(root, spec, identity)

    def test_malformed_completion_objects_fail_with_controlled_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, spec, identity = Path(temporary), fixture_spec(), identity_for_jobs({}, 1)
            attempt = create_attempt(root, spec, identity)
            fill_attempt(attempt, spec)
            complete = seal_completed(root, spec, identity, attempt)
            for malformed in (None, [], "invalid", {**complete.record, "execution_identity": None},
                              {**complete.record, "execution_identity": []}):
                complete.completion_path.write_text(json.dumps(malformed))
                with self.assertRaises(CompletionError):
                    verified_completed(root, spec, identity)

    def test_os_lock_rejects_concurrent_owner_then_releases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with CampaignLock(root):
                with self.assertRaises(RuntimeError):
                    with CampaignLock(root):
                        self.fail("Second simultaneous owner must not acquire the lock")
            with CampaignLock(root):
                pass

    def test_guard_path_relocation_does_not_change_scientific_identity(self):
        spec = fixture_spec()
        before = {"runtime_fingerprint": "same bytes", "_verification_files": [{"path": "C:/old/x", "sha256": "abc"}]}
        after = {"runtime_fingerprint": "same bytes", "_verification_files": [{"path": "D:/new/x", "sha256": "abc"}]}
        self.assertEqual(identity_hash(spec, before), identity_hash(spec, after))

    def test_interruption_is_not_complete_and_next_attempt_is_separate(self):
        class InterruptedAdapter:
            def prepare(self, scenario, seed, directory):
                (directory / "started.txt").write_text("synthetic initialization only")

            def run(self, scenario, seed, directory):
                raise KeyboardInterrupt()

        class SuccessfulAdapter(InterruptedAdapter):
            def run(self, scenario, seed, directory):
                pass

            def collect(self, scenario, seed, raw, processed):
                fill_attempt(raw.parent, RunSpec(scenario, "spparks_diffusion_tree", seed))

        with tempfile.TemporaryDirectory() as temporary:
            root, spec = Path(temporary), fixture_spec()
            identity = identity_for_jobs({}, 1)
            interrupted_attempt = create_attempt(root, spec, identity)
            with self.assertRaises(KeyboardInterrupt):
                execute_attempt(root, spec, {}, identity, interrupted_attempt,
                                adapter_factory=lambda *args: InterruptedAdapter())
            self.assertEqual(
                json.loads((interrupted_attempt / "attempt_status.json").read_text())["status"],
                "interrupted",
            )
            with self.assertRaises(CompletionError):
                verified_completed(root, spec, identity)
            successful_attempt = create_attempt(root, spec, identity)
            complete = execute_attempt(root, spec, {}, identity, successful_attempt,
                                       adapter_factory=lambda *args: SuccessfulAdapter())
            self.assertNotEqual(complete.raw_dir.parent, interrupted_attempt)
            self.assertTrue(interrupted_attempt.is_dir())
            self.assertEqual(verified_completed(root, spec, identity).raw_dir, complete.raw_dir)


if __name__ == "__main__":
    unittest.main()
