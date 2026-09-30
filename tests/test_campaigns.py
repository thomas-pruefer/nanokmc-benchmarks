"""Manifest selection checks; none of these tests launch a simulation."""
import unittest

from benchmark_core.campaigns import CHECKPOINTS, STATE_ALIASES, enumerate_runs, parse_seeds
from benchmark_core.runner import PATH_IDS


class CampaignManifestTests(unittest.TestCase):
    def test_exact_union_and_reuse(self):
        all_runs = enumerate_runs()
        self.assertEqual(len(all_runs), 6468)
        self.assertEqual(len({run.run_id for run in all_runs}), 6468)
        self.assertEqual(sum(len(run.scenario.mcs_points) for run in all_runs), 122892)
        a, b, c = [enumerate_runs(campaign=name) for name in "ABC"]
        self.assertEqual([len(a), len(b), len(c)], [11, 44, 6435])
        a_ids = {run.run_id for run in a}
        self.assertTrue(all(run.campaigns == ("A", "B", "C") for run in a))
        self.assertTrue(a_ids <= {run.run_id for run in b})
        self.assertTrue(a_ids <= {run.run_id for run in c})
        self.assertEqual(a_ids, {run.run_id for run in b} & {run.run_id for run in c})
        for k, count in ((3, 512), (4, 64), (5, 8), (6, 1)):
            selected = enumerate_runs(campaign="C", solvers=["binary"], sizes=[k])
            self.assertEqual([run.seed for run in selected], list(range(1, count + 1)))
            self.assertTrue(all(tuple(run.scenario.mcs_points) == CHECKPOINTS for run in selected))

    def test_exact_scientific_union(self):
        expected_states = ((6, .2, .75, 1), (6, .2, 1.25, 1), (6, .4, .75, 1),
                           (6, .4, 1.25, 1), (3, .2, .75, 512), (4, .2, .75, 64), (5, .2, .75, 8))
        expected = {(k, composition, temperature, code, seed)
                    for k, composition, temperature, seed_count in expected_states
                    for code in PATH_IDS for seed in range(1, seed_count + 1)}
        actual = {(run.scenario.nx, run.scenario.composition_A, run.scenario.kT, run.code, run.seed)
                  for run in enumerate_runs()}
        self.assertEqual(actual, expected)

    def test_current_state_aliases_and_explicit_membership(self):
        for alias, state_id in STATE_ALIASES.items():
            with self.subTest(alias=alias):
                selected = enumerate_runs(states=alias, solvers="binary")
                self.assertEqual({run.scenario.id for run in selected}, {state_id})
                self.assertEqual([run.run_id for run in selected],
                                 [run.run_id for run in enumerate_runs(states=state_id, solvers="binary")])
        for campaign, expected_states in (
                ("A", {"representative_k6_x20_t075"}),
                ("B", {"representative_k6_x20_t075", "screen_k6_x20_t125",
                       "screen_k6_x40_t075", "screen_k6_x40_t125"}),
                ("C", {"representative_k6_x20_t075", "scaling_k3_x20_t075",
                       "scaling_k4_x20_t075", "scaling_k5_x20_t075"})):
            self.assertEqual({run.scenario.id for run in enumerate_runs(campaign=campaign)}, expected_states)
        with self.assertRaisesRegex(ValueError, "Unknown state"):
            enumerate_runs(states="retired_development_name")

    def test_filters_do_not_invent_seed_labels_or_new_run_ids(self):
        runs = enumerate_runs(campaign="C", solvers=["binary", "kmcos"], sizes=[3], seeds=["2-4", "8"])
        self.assertEqual(len(runs), 8)
        self.assertEqual({run.seed for run in runs}, {2, 3, 4, 8})
        self.assertEqual(parse_seeds("1:3,5"), {1, 2, 3, 5})
        self.assertEqual(enumerate_runs(campaign="A", solvers="binary")[0].run_id,
                         enumerate_runs(campaign="C", solvers="binary", sizes=6)[0].run_id)
        with self.assertRaises(ValueError):
            enumerate_runs(campaign="A", seeds="2")
        for selection in ({"solvers": "homogenous"}, {"states": "guess"}, {"sizes": [7]},
                          {"seeds": "0"}, {"campaign": "all,invalid"}):
            with self.assertRaises(ValueError):
                enumerate_runs(**selection)


if __name__ == "__main__":
    unittest.main()
