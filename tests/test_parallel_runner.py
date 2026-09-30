"""Offline scheduler/identity tests. Only synthetic processes and CSV fixtures."""
from contextlib import redirect_stdout
import io
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from benchmark_core.campaigns import enumerate_runs
from benchmark_core.run_store import (CompletionError, create_attempt, identity_for_jobs,
                                      seal_completed, verified_completed)
from scripts import run_manuscript_campaigns as runner
from test_run_store import fixture_spec, fill_attempt
from test_process_lifecycle import is_running


class FakeWorker:
    def __init__(self, item, complete=True):
        self.item, self.complete = item, complete
        self.killed, self.closed = False, False

    def poll(self):
        return 0 if self.complete else None

    def cancel(self):
        self.killed = True

    def close(self):
        self.closed = True


class ParallelRunnerTests(unittest.TestCase):
    def test_dispatch_bounded_deterministic_no_prequeue(self):
        started, done, workers, active = [], [], [], set()
        def start(item, active_count):
            self.assertLessEqual(len(active), 3)
            self.assertEqual(active_count, 1)  # prior synthetic workers already exited when sampled
            started.append(item)
            active.add(item)
            worker = FakeWorker(item)
            workers.append(worker)
            return worker
        def complete(item, worker, code):
            self.assertTrue(worker.closed)
            done.append(item)
            active.remove(item)
        runner.bounded_dispatch(range(19), 4, start, complete, lambda *_: self.fail("Unexpected cancellation"))
        self.assertEqual(started, list(range(19)))
        self.assertEqual(done, started)
        self.assertTrue(all(worker.closed and not worker.killed for worker in workers))

    def test_interrupt_stops_all_only_bounded_items_consumed(self):
        workers, consumed, cancelled = [], [], []
        def items():
            for item in range(100):
                consumed.append(item)
                yield item
        def start(item, active_count):
            worker = FakeWorker(item, False)
            workers.append(worker)
            return worker
        def interrupt(_):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            runner.bounded_dispatch(items(), 8, start, lambda *_: None,
                                    lambda item, worker: cancelled.append(item), pause=interrupt)
        self.assertEqual(consumed, list(range(8)))
        self.assertEqual(cancelled, consumed)
        self.assertTrue(all(worker.killed and worker.closed for worker in workers))

    def test_completion_failure_cancels_other_workers(self):
        workers = []
        def start(item, count):
            worker = FakeWorker(item, item == 0)
            workers.append(worker)
            return worker
        def fail(*_):
            raise ValueError("Synthetic invalid completion")
        with self.assertRaisesRegex(ValueError, "invalid completion"):
            runner.bounded_dispatch(range(10), 3, start, fail, lambda *_: None)
        self.assertEqual(len(workers), 3)
        self.assertTrue(all(worker.killed and worker.closed for worker in workers))

    def test_jobs_are_sealed_resume_identity_and_status_infers(self):
        with tempfile.TemporaryDirectory() as directory:
            root, spec = Path(directory), fixture_spec()
            one = identity_for_jobs({"runtime_fingerprint": "same", "timing_policy": "native"}, 1)
            eight = identity_for_jobs(one, 8)
            attempt = create_attempt(root, spec, eight, active_jobs_at_launch=4)
            fill_attempt(attempt, spec)
            completion = seal_completed(root, spec, eight, attempt)
            for record in (completion.record, json.loads((attempt / "processed/run_metadata.json").read_text())):
                self.assertEqual(record["requested_jobs"], 8)
                self.assertEqual(record["concurrency"], 8)
                self.assertEqual(record["active_jobs_at_launch"], 4)
            self.assertEqual(verified_completed(root, spec, eight).raw_dir.parent, attempt)
            with self.assertRaises(CompletionError) as failure:
                verified_completed(root, spec, one)
            self.assertEqual(failure.exception.status, "stale")
            inferred = runner._inspect([spec], {spec.code: one}, root=root)
            self.assertEqual(inferred["counts"], {"complete": 1})
            self.assertEqual(inferred["runs"][0]["requested_jobs"], 8)
            self.assertEqual(runner._inspect([spec], {spec.code: one}, jobs=1, root=root)["counts"], {"stale": 1})
            for invalid in (0, 33, True, 1.5):
                with self.assertRaises(ValueError):
                    identity_for_jobs(one, invalid)

    def test_worker_rejects_paths_changed_before_or_during_load(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "paths.json"
            path.write_text('{"solver": "verified.exe"}')
            original = path.read_bytes()
            identity = {"_verification_files": [{"path": str(path), "sha256": hashlib.sha256(original).hexdigest(),
                                                  "role": "path_configuration"}]}
            with self.assertRaisesRegex(RuntimeError, "not the file"):
                runner.load_guarded_worker_paths(root, root / "another.json", identity)
            path.write_text('{"solver": "other.exe"}')
            with patch.object(runner, "load_paths") as load, self.assertRaisesRegex(RuntimeError, "changed"):
                runner.load_guarded_worker_paths(root, path, identity)
            load.assert_not_called()
            path.write_bytes(original)
            def changed_load(_):
                path.write_text('{"solver": "other.exe"}')
                return {"solver": "other.exe"}
            with patch.object(runner, "load_paths", side_effect=changed_load), self.assertRaisesRegex(RuntimeError, "changed"):
                runner.load_guarded_worker_paths(root, path, identity)

    def test_dry_run_prints_every_identity_without_runtime_or_worker(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "ROOT", Path(directory)), \
                patch.object(runner, "current_identities", side_effect=AssertionError("Runtime must not be probed")), \
                patch.object(runner, "WorkerProcess", side_effect=AssertionError("No worker may launch")):
            output = io.StringIO()
            report = Path(directory) / "plan.json"
            with redirect_stdout(output):
                code = runner.main(["--campaign", "A", "B", "--solvers", "binary,kmcos", "--jobs", "8", "--run-set", "test", "--dry-run", "--json", str(report)])
            self.assertEqual(code, 0)
            selected = enumerate_runs(campaign=["A", "B"], solvers=["binary", "kmcos"])
            self.assertEqual(len(selected), 8)
            for spec in selected:
                self.assertEqual(output.getvalue().count(spec.run_id), 1)
            data = json.loads(report.read_text())
            self.assertEqual(len(data["runs"]), 8)
            self.assertTrue(all(row["requested_jobs"] == 8 and row["reuse"] == "not_verified" for row in data["runs"]))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(runner.main(["--list-solvers"]), 0)
            self.assertEqual(output.getvalue().splitlines(), list(runner.PATH_IDS))

    def test_worker_final_log_flush_cannot_mutate_sealed_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root, spec, identity = Path(directory), fixture_spec(), identity_for_jobs({}, 1)
            attempt = create_attempt(root, spec, identity)
            fill_attempt(attempt, spec)
            seal_completed(root, spec, identity, attempt)
            worker = runner.WorkerProcess([sys.executable, "-B", "-c", "print('buffered final worker message')"],
                                          root / "logs/worker", cwd=root)
            try:
                self.assertEqual(worker.process.wait(timeout=10), 0)
            finally:
                worker.close()
            self.assertIn("buffered final", (root / "logs/worker.stdout.log").read_text())
            self.assertEqual(verified_completed(root, spec, identity).raw_dir.parent, attempt)

    @unittest.skipUnless(os.name == "nt", "Windows suspended process ownership")
    def test_failed_job_attachment_kills_suspended_process_without_taskkill(self):
        process = subprocess.Popen([sys.executable, "-B", "-c", "import time;time.sleep(30)"],
                                   creationflags=subprocess.CREATE_NO_WINDOW | 0x4)
        tree = runner.ProcessTree()
        try:
            with patch.object(tree.api, "AssignProcessToJobObject", return_value=0), \
                    patch("subprocess.run", side_effect=AssertionError("No external taskkill dependency")):
                with self.assertRaisesRegex(RuntimeError, "Cannot secure"):
                    tree.attach(process)
            self.assertIsNotNone(process.poll())
        finally:
            tree.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    @unittest.skipUnless(os.name == "nt", "Nested Windows Job Objects")
    def test_interrupt_kills_two_workers_and_each_native_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workers, pids = [], []
            source = ("import os,sys,time\nfrom pathlib import Path\n"
                      "Path('native.pid').write_text(str(os.getpid()))\n"
                      "import subprocess\n"
                      "subprocess.Popen([sys.executable,'-B','-c',"
                      "\"import os,time;from pathlib import Path;Path('grandchild.pid').write_text(str(os.getpid()));time.sleep(30)\"])\n"
                      "time.sleep(30)\n")
            def start(item, count):
                folder = root / str(item)
                folder.mkdir()
                (folder / "native.py").write_text(source)
                driver = (f"import sys;from pathlib import Path;sys.path.insert(0,{str(runner.ROOT)!r});"
                          "from benchmark_core.timing import run_timed;"
                          "run_timed([sys.executable,'-B','native.py'],Path.cwd())")
                worker = runner.WorkerProcess([sys.executable, "-B", "-c", driver], folder / "worker", cwd=folder)
                workers.append(worker)
                return worker
            def interrupt(_):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    files = list(root.glob("*/grandchild.pid"))
                    if len(files) == 2 and all(p.read_text() for p in files):
                        pids.extend(int(p.read_text()) for p in root.glob("*/*.pid"))
                        raise KeyboardInterrupt()
                    time.sleep(.02)
                self.fail("Synthetic native processes did not initialize")
            with self.assertRaises(KeyboardInterrupt):
                runner.bounded_dispatch(range(10), 2, start, lambda *_: self.fail("Unexpected exit"), lambda *_: None, pause=interrupt)
            self.assertEqual(len(workers), 2)
            self.assertEqual(len(pids), 4)
            deadline = time.monotonic() + 5
            while any(is_running(pid) for pid in pids) and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertFalse(any(is_running(pid) for pid in pids))
            self.assertTrue(all(worker.poll() is not None for worker in workers))


if __name__ == "__main__":
    unittest.main()
