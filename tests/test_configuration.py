"""Scientific configuration validation without solver execution."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from benchmark_core.runner import PATH_IDS
from benchmark_core.scenario import CHECKPOINTS, load_configuration

MANIFEST = Path(__file__).resolve().parents[1] / "config/manuscript.json"
REPRESENTATIVE = "representative_k6_x20_t075"


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def load_data(self, data):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manuscript.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return load_configuration(path)

    def test_exact_seven_states_and_shared_fields(self):
        configuration = load_configuration(MANIFEST)
        actual = {s.id: (s.nx, s.composition_A, s.kT, tuple(s.seeds)) for s in configuration.scenarios}
        expected = {
            REPRESENTATIVE: (6, .2, .75, (1,)),
            "screen_k6_x20_t125": (6, .2, 1.25, (1,)),
            "screen_k6_x40_t075": (6, .4, .75, (1,)),
            "screen_k6_x40_t125": (6, .4, 1.25, (1,)),
            "scaling_k3_x20_t075": (3, .2, .75, tuple(range(1, 513))),
            "scaling_k4_x20_t075": (4, .2, .75, tuple(range(1, 65))),
            "scaling_k5_x20_t075": (5, .2, .75, tuple(range(1, 9))),
        }
        self.assertEqual(actual, expected)
        for scenario in configuration.scenarios:
            self.assertRegex(scenario.id, r"^(representative|screen|scaling)_k[3-6]_x(20|40)_t(075|125)$")
            self.assertEqual((scenario.geometry, scenario.dimension, scenario.ny, scenario.nz, scenario.Ea),
                             ("fcc", 3, scenario.nx, scenario.nx, 1.0))
            self.assertEqual(tuple(scenario.mcs_points), CHECKPOINTS)
            self.assertEqual(tuple(scenario.codes), PATH_IDS)
            self.assertIs(scenario.save_snapshots, True)
            self.assertIs(scenario.postprocess_snapshots, True)
            self.assertEqual((scenario.spparks_loglinfreq_n, scenario.spparks_loglinfreq_factor), (5, 10.0))

    def test_unknown_and_missing_keys_rejected_at_every_level(self):
        locations = ((), ("benchmark",), ("benchmark", "model"), ("benchmark", "outputs"),
                     ("states",), ("states", REPRESENTATIVE), ("campaigns",))
        for location in locations:
            for operation in ("add", "remove"):
                with self.subTest(location=location, operation=operation):
                    data = deepcopy(self.data)
                    target = data
                    for key in location:
                        target = target[key]
                    if operation == "add":
                        target["unused_field"] = True
                    else:
                        target.pop(next(iter(target)))
                    with self.assertRaisesRegex(ValueError, "fields"):
                        self.load_data(data)

    def test_state_values_do_not_follow_identifier_substrings(self):
        for key, value in (("k", 5), ("composition_A", .4), ("kT", 1.25),
                           ("seed_range", [1, 2]), ("seed_range", [0, 1])):
            with self.subTest(key=key, value=value):
                data = deepcopy(self.data)
                data["states"][REPRESENTATIVE][key] = value
                with self.assertRaises(ValueError):
                    self.load_data(data)
        data = deepcopy(self.data)
        data["states"]["unrecognized_state"] = data["states"].pop(REPRESENTATIVE)
        with self.assertRaisesRegex(ValueError, "states"):
            self.load_data(data)

    def test_campaign_membership_and_duplicates_rejected(self):
        for campaign, value in (("A", []), ("A", ["screen_k6_x20_t125"]),
                                ("B", [REPRESENTATIVE]), ("C", [REPRESENTATIVE]),
                                ("A", [REPRESENTATIVE, REPRESENTATIVE]),
                                ("A", "representative"), ("A", [{}])):
            with self.subTest(campaign=campaign, value=value):
                data = deepcopy(self.data)
                data["campaigns"][campaign] = value
                with self.assertRaisesRegex(ValueError, "campaigns"):
                    self.load_data(data)

    def test_wrong_json_types_rejected(self):
        mutations = (
            (("schema_version",), True),
            (("benchmark",), []),
            (("benchmark", "model", "dimension"), 3.0),
            (("benchmark", "model", "Ea"), True),
            (("benchmark", "outputs", "save_snapshots"), 1),
            (("benchmark", "outputs", "spparks_loglinfreq_n"), 5.0),
            (("benchmark", "outputs", "spparks_loglinfreq_factor"), "10.0"),
            (("states", REPRESENTATIVE, "k"), "6"),
            (("states", REPRESENTATIVE, "composition_A"), "0.2"),
            (("states", REPRESENTATIVE, "kT"), float("nan")),
            (("states", REPRESENTATIVE, "seed_range"), [True, 1]),
            (("benchmark", "observation_targets"), [float(p) for p in CHECKPOINTS]),
            (("benchmark", "solvers"), ",".join(PATH_IDS)),
        )
        for location, value in mutations:
            with self.subTest(location=location, value=value):
                data = deepcopy(self.data)
                target = data
                for key in location[:-1]:
                    target = target[key]
                target[location[-1]] = value
                with self.assertRaises(ValueError):
                    self.load_data(data)

    def test_common_schedule_and_solver_set_locked(self):
        for key in ("observation_targets", "solvers"):
            for operation in ("remove", "duplicate", "reverse"):
                with self.subTest(key=key, operation=operation):
                    data = deepcopy(self.data)
                    values = data["benchmark"][key]
                    if operation == "remove":
                        values.pop()
                    elif operation == "duplicate":
                        values.append(values[-1])
                    else:
                        values.reverse()
                    with self.assertRaises(ValueError):
                        self.load_data(data)

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manuscript.json"
            path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate configuration field"):
                load_configuration(path)

    def test_expansion_does_not_share_mutable_state_lists(self):
        scenarios = load_configuration(MANIFEST).scenarios
        scenarios[0].mcs_points.append(99999)
        scenarios[0].codes.clear()
        self.assertEqual(tuple(scenarios[1].mcs_points), CHECKPOINTS)
        self.assertEqual(tuple(scenarios[1].codes), PATH_IDS)


if __name__ == "__main__":
    unittest.main()
