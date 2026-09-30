"""The exploratory plotter accepts sealed runs but never fabricates missing data."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import preview_partial_results as preview
from benchmark_core.run_store import CompletionError
from plotting.inputs import BINARY


def trajectory(code=BINARY, multiplier=1, *, k=6, seed=1, runtime=None):
    """Normalized-shaped synthetic observations, without any solver calls."""
    n = 2 ** (3 * k - 1)
    n_b = n - round(.2 * n)
    return [dict(
        run_id=f"SYNTHETIC_{code}_{k}_{seed}", code=code, scenario_id=f"SYNTHETIC_k{k}",
        k=k, N=n, seed=seed, x_A_nominal=.2, T_star=.75,
        requested_mcs=t, realized_common_mcs=t, runtime_seconds=(runtime if runtime is not None else multiplier * (1 + t / 100)),
        runtime_source="SYNTHETIC", source_identity_sha256="SYNTHETIC",
        requested_jobs=8, active_jobs_at_launch="", execution_policy="independent_single_thread_jobs",
        timing_policy="native_evolution_intervals_no_overhead_subtraction_v1",
        rho_AB=.2, N_A_lt12=13, N_B=n_b, c_A_B=13 / (n_b + 13), cutoff=12,
        n_ex=max(1, t), candidate_events=2 * max(1, t) * n, executed_events=max(1, t) * n,
        candidate_events_per_site=2 * max(1, t), executed_events_per_site=max(1, t),
        counter_meaning="SYNTHETIC", native_counter_source="SYNTHETIC",
    ) for t in (preview.POINTS if k == 6 else (30000,))]


class PartialPreviewTests(unittest.TestCase):
    def test_available_run_sets_lists_only_registered_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("zeta", "alpha"):
                directory = root / "run-sets" / name
                directory.mkdir(parents=True)
                (directory / "run_set.json").write_text("{}", encoding="utf-8")
            (root / "run-sets/unregistered").mkdir()
            self.assertEqual(preview.available_run_sets(root), ["alpha", "zeta"])

    def test_only_verified_complete_runs_enter_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            seal = Path(temp) / "complete.json"
            seal.write_text('{"status":"complete"}', encoding="utf-8")
            scenario = SimpleNamespace(nx=6, composition_A=.2, kT=.75)
            good = SimpleNamespace(code=BINARY, scenario=scenario, seed=1, run_id="good")
            missing = SimpleNamespace(code="spparks_diffusion_tree", scenario=scenario,
                                      seed=1, run_id="missing")
            completion = SimpleNamespace(completion_path=seal, record={"identity_sha256": "identity"})

            def verify(_root, spec, expected_identity):
                if spec is missing:
                    raise CompletionError("No sealed complete attempt", "incomplete")
                self.assertEqual(expected_identity, "expected")
                return completion

            with patch.object(preview, "verified_completed", side_effect=verify), patch.object(
                    preview, "normalize_run", return_value=([{"requested_mcs": 30000}], [])):
                records, counts, seals = preview.available_run_rows(Path(temp), [good, missing],
                    {good.code: "expected", missing.code: "expected"}, need_clusters=False)
            self.assertEqual(set(records), {(BINARY, 6, .2, .75, 1)})
            self.assertEqual(counts, {"complete": 1, "incomplete": 1})
            self.assertEqual([item["run_id"] for item in seals], ["good"])

    def test_partial_size_mean_reports_actual_n_and_withholds_single_seed_sem(self):
        rows = (trajectory(k=3, runtime=10.0) + trajectory(k=3, seed=2, runtime=20.0)
                + trajectory(runtime=100.0))
        tables = preview.derive_tables(rows, {}, [10], partial=True)
        result = {(row["code"], row["k"]): row for row in tables["fig10_size_scaling.csv"]}
        self.assertEqual(result[BINARY, 3]["n"], 2)
        self.assertEqual(result[BINARY, 3]["expected_n"], 512)
        self.assertEqual(result[BINARY, 3]["mean_seconds"], 15.0)
        self.assertAlmostEqual(result[BINARY, 3]["sem_seconds"], 5.0)
        self.assertEqual(result[BINARY, 6]["n"], 1)
        self.assertIsNone(result[BINARY, 6]["sem_seconds"])
        self.assertNotIn((BINARY, 4), result)
        self.assertEqual(tables["fig10_alpha.csv"], [])

    def test_missing_solver_remains_absent_and_plot_is_watermarked(self):
        tables = {"fig07_event_accounting.csv": [{
            "code": BINARY, "candidate_events_per_site": 100.0,
            "executed_events_per_site": 25.0,
        }]}
        figure = preview.figure7(tables)
        try:
            self.assertTrue(any("PROVISIONAL PREVIEW" in text.get_text() for text in figure.texts))
            self.assertEqual(len(figure.axes[0].patches), 2)
            self.assertEqual(len(figure.axes[0].get_xticklabels()), 11)
        finally:
            preview._mpl().close(figure)

    def test_all_preview_figures_render_with_missing_solvers_and_seeds(self):
        comparator = "spparks_diffusion_tree"
        rows = trajectory() + trajectory(comparator, 2) + trajectory(k=3)
        clusters = {BINARY: [{"common_species": "A", "cluster_size": 20, "cluster_count": 2}], comparator: []}
        tables = preview.derive_tables(rows, clusters, list(preview.PLOTS), partial=True)
        with tempfile.TemporaryDirectory() as temp:
            for number, plot in preview.PLOTS.items():
                with self.subTest(figure=number):
                    figure = plot(tables)
                    self.assertIsNotNone(figure)
                    try:
                        self.assertTrue(any("PROVISIONAL PREVIEW" in item.get_text()
                                            for item in figure.texts))
                        output = Path(temp) / f"preview_fig{number:02d}.png"
                        figure.savefig(output, dpi=72)
                        self.assertGreater(output.stat().st_size, 1000)
                    finally:
                        preview._mpl().close(figure)

    def test_cli_writes_only_a_provisional_namespaced_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = root / "run-sets/demo"
            dataset.mkdir(parents=True)
            (dataset / "run_set.json").write_text(json.dumps({
                "schema_version": 1, "run_set": "demo", "requested_jobs": 8,
                "execution_policy": "independent_single_thread_jobs"}), encoding="utf-8")
            seal = dataset / "complete.json"
            seal.write_text("sealed synthetic completion", encoding="utf-8")
            scenario = SimpleNamespace(nx=6, composition_A=.2, kT=.75)
            spec = SimpleNamespace(code=BINARY, scenario=scenario, seed=1, run_id="synthetic")
            records = {(BINARY, 6, .2, .75, 1): {"rows": trajectory(), "clusters": []}}
            seals = [{"run_id": "synthetic", "completion_path": str(seal),
                      "completion_sha256": preview.sha256(seal), "identity_sha256": "synthetic"}]
            completion = SimpleNamespace(completion_path=seal)
            with patch.object(preview, "ROOT", root), patch.object(preview, "current_identities",
                    return_value={BINARY: "identity"}), patch.object(preview, "required_runs",
                    return_value=[spec]), patch.object(preview, "available_run_rows",
                    return_value=(records, {"complete": 1}, seals)), patch.object(preview,
                    "verified_completed", return_value=completion):
                self.assertEqual(preview.main(["--run-set", "demo", "--figures", "7", "--dry-run"]), 0)
                self.assertFalse((dataset / "figures").exists())
                code = preview.main(["--run-set", "demo", "--figures", "7"])
            self.assertEqual(code, 0)
            previews = list((dataset / "figures/previews").glob("preview_*"))
            self.assertEqual(len(previews), 1)
            manifest = json.loads((previews[0] / "preview_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["scope"], "provisional_preview")
            self.assertFalse(manifest["publication_complete"])
            self.assertEqual(manifest["coverage_by_solver"][BINARY], {"complete": 1, "required": 1})
            self.assertTrue((previews[0] / "preview_fig07.png").is_file())
            self.assertFalse((dataset / "results/csv/publication_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
