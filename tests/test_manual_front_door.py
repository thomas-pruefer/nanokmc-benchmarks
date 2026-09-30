"""Interface-only checks: no builds, solvers or publication processing."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reproduce_manuscript


class ManualFrontDoor(unittest.TestCase):
    def test_dry_run_forwards_campaign_jobs_without_building(self):
        with patch.object(sys, "argv", ["reproduce_manuscript.py", "--dry-run", "--jobs", "8", "--build-jobs", "4", "--run-set", "test"]), \
             patch.object(reproduce_manuscript.subprocess, "run") as launch:
            self.assertEqual(reproduce_manuscript.main(), 0)
        commands = [call.args[0] for call in launch.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertTrue(all("--dry-run" in command for command in commands))
        self.assertTrue(all(command[command.index("--run-set")+1] == "test" for command in commands))
        campaign = commands[0]
        self.assertEqual(campaign[campaign.index("--jobs") + 1], "8")
        self.assertFalse(any("build_all.py" in str(token) for command in commands for token in command))

    def test_help_and_invalid_jobs_never_launch_work(self):
        with patch.object(reproduce_manuscript.subprocess, "run") as launch:
            with patch.object(sys, "argv", ["reproduce_manuscript.py"]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(reproduce_manuscript.main(), 0)
            for jobs in ("0", "33", "-1"):
                with self.subTest(jobs=jobs), \
                     patch.object(sys, "argv", ["reproduce_manuscript.py", "--dry-run", "--jobs", jobs]), \
                     contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    reproduce_manuscript.main()
                self.assertEqual(error.exception.code, 2)
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
