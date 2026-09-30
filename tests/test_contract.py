"""Small scientific contract tests; no simulations or manuscript campaigns."""
from pathlib import Path
import tempfile
import unittest

from benchmark_core.common_snapshot import CanonicalSnapshot
from benchmark_core.metrics import analyze_snapshot
from benchmark_core.runner import PATH_IDS, get_adapter
from benchmark_core.scenario import load_scenarios

ROOT = Path(__file__).resolve().parents[1]


class ManuscriptContract(unittest.TestCase):
    def test_exact_unique_campaign_definition(self):
        scenarios = load_scenarios(ROOT / "config" / "manuscript.json")
        self.assertEqual(len(scenarios), 7)
        self.assertEqual(len(PATH_IDS), 11)
        self.assertEqual(sum(len(s.seeds) * len(s.codes) for s in scenarios), 6468)
        for scenario in scenarios:
            self.assertEqual(tuple(scenario.codes), PATH_IDS)
            self.assertEqual(scenario.mcs_points, [0, 10, 20, 30, 40, 50, 100, 200, 300, 400,
                             500, 1000, 2000, 3000, 4000, 5000, 10000, 20000, 30000])
            self.assertEqual(scenario.seeds, list(range(1, {3: 512, 4: 64, 5: 8, 6: 1}[scenario.nx] + 1)))
        for obsolete in ("nanokmc_active", "spparks", "spparks_diffusion_group", "kmc_lattice_full"):
            with self.assertRaises(KeyError):
                get_adapter(obsolete, {})

    def test_input_contracts(self):
        scenario = load_scenarios(ROOT / "config" / "manuscript.json")[0]
        with tempfile.TemporaryDirectory() as directory:
            for code in PATH_IDS:
                if not code.startswith(("nanokmc_", "spparks_")):
                    continue
                adapter = get_adapter(code, {})
                target = Path(directory) / code
                adapter.prepare(scenario, 1, target)
                if code.startswith("nanokmc_"):
                    text = (target / "nanokmc.in").read_text()
                    self.assertIn('NSpecies=2;', text)
                    self.assertIn('clvl=0.2;', text)
                    self.assertIn('kT=0.75;', text)
                    self.assertIn('knx=6;', text)
                    if code == "nanokmc_bit_encoded":
                        self.assertIn('SystemID="KMCClassical";', text)
                    if code == "nanokmc_rate_category_optimized":
                        self.assertIn('RateCategoryCount=4;', text)
                else:
                    text = (target / "in.spparks").read_text()
                    self.assertIn('app_style diffusion linear hop', text)
                    self.assertIn('temperature 1.5', text)
                    self.assertIn('boundary p p p', text)
                    self.assertIn('loglinfreq 5 10', text)
                    self.assertEqual(adapter.common_mcs_per_native_time, 6.0)
                    run_time = float([line for line in text.splitlines() if line.startswith('run ')][0].split()[1])
                    self.assertAlmostEqual(run_time, 30001 / 6)

    def test_common_fcc_and_interface_normalization(self):
        sites = []
        for x in range(8):
            for y in range(8):
                for z in range(8):
                    if (x+y+z) % 2 == 0:
                        sites.append((len(sites)+1, x, y, z, "A" if not sites else "B"))
        snapshot = CanonicalSnapshot(0, "fixture", "fcc", 12, (8, 8, 8), tuple(sites))
        result = analyze_snapshot(snapshot)
        self.assertEqual(result.validation.total_sites, 256)
        self.assertEqual(result.metrics["total_undirected_bonds_common"], 1536)
        self.assertEqual(result.metrics["interface_bonds_common"], 12)
        self.assertEqual(result.metrics["interface_density_common"], 12 / 1536)
        self.assertEqual(result.cluster_sizes["A"], (1,))
        self.assertEqual(sum(result.cluster_sizes["B"]), 255)
        broken = CanonicalSnapshot(0, "fixture", "fcc", 12, (8, 8, 8), tuple(sites[:-1] + [sites[0]]))
        with self.assertRaises(ValueError):
            analyze_snapshot(broken)


if __name__ == "__main__":
    unittest.main()
