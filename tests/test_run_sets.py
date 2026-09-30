"""Offline namespace, seal isolation and non-executing CLI regression tests."""
import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from benchmark_core.run_sets import (validate_name, data_root, check_run_set, unsealed_status,
                                    campaign_lock_held, process_token)
from benchmark_core.run_store import (CampaignLock, CompletionError, create_attempt, identity_for_jobs,
                                     seal_completed, verified_completed, mark_attempt,
                                     run_directory, check_native_path_length)
from scripts import run_manuscript_campaigns as runner
from scripts import process_results, make_figures
from test_run_store import fixture_spec, fill_attempt


class RunSetTests(unittest.TestCase):
    def test_names_reject_windows_aliases_traversal_and_case(self):
        for name in ("", ".", "..", "../x", "a/b", "a\\b", "c:x", "A", "name.", "name ",
                     "con", "aux", "nul", "prn", "com1", "lpt9", "a"*65, "ümlaut", "x:$data"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_name(name)
        self.assertEqual(validate_name("manuscript-jobs8"), "manuscript-jobs8")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"run-sets/Mixed").mkdir(parents=True)
            with self.assertRaisesRegex(ValueError,"case alias"):
                data_root(root,"mixed")

    def test_dataset_registration_is_read_only_until_execution_and_locks_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertIsNone(check_run_set(root,"parallel",8))
            self.assertFalse((root/"run-sets").exists())
            record = check_run_set(root,"parallel",8,create=True)
            self.assertEqual(check_run_set(root,"parallel"),record)
            with self.assertRaisesRegex(ValueError,"another --run-set"):
                check_run_set(root,"parallel",1,create=True)
            check_run_set(root,"sequential",1,create=True)
            self.assertNotEqual(data_root(root,"parallel"),data_root(root,"sequential"))

    def test_corrupt_registration_and_jobs_change_fail_before_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            check_run_set(root,"test",8,create=True)
            marker = data_root(root,"test")/"run_set.json"
            original = marker.read_text()
            for value in (None,[],{"schema_version":1,"run_set":"other","requested_jobs":8},
                          {**json.loads(original),"requested_jobs":True},
                          {**json.loads(original),"execution_policy":"unknown"}):
                marker.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,"Invalid run_set.json"):
                    check_run_set(root,"test")
            marker.write_text(original)
            with patch.object(runner,"ROOT",root),patch.object(runner,"current_identities") as identities, \
                 patch.object(runner,"WorkerProcess") as worker,contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(runner.main(["--campaign","A","--run-set","test","--jobs","1"]),2)
                identities.assert_not_called()
                worker.assert_not_called()

    def test_redirected_digest_and_attempt_paths_fail_before_any_write_or_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root,spec = Path(folder),fixture_spec()
            dataset = data_root(root,"test")
            identity = identity_for_jobs({"run_set":"test"},1)
            attempt = create_attempt(dataset,spec,identity)
            directory = attempt.parent
            before = {p:p.read_bytes() for p in root.rglob("*") if p.is_file()}
            for target in (directory,attempt):
                for method in ("is_junction","is_symlink"):
                    with self.subTest(target=target.name,method=method), \
                         patch.object(Path,method,autospec=True,side_effect=lambda p,target=target:p==target), \
                         patch.object(runner,"get_adapter") as adapter:
                        for action in (lambda:create_attempt(dataset,spec,identity),
                                       lambda:verified_completed(dataset,spec,identity),
                                       lambda:unsealed_status(directory,root),
                                       lambda:runner.execute_attempt(dataset,spec,{},identity,attempt,adapter_factory=adapter)):
                            with self.assertRaisesRegex(CompletionError,"links or junctions"):
                                action()
                        adapter.assert_not_called()
                    self.assertEqual(before,{p:p.read_bytes() for p in root.rglob("*") if p.is_file()})
            original_resolve = Path.resolve
            def redirected(path,*args,**kwargs):
                return root/"outside" if path==directory else original_resolve(path,*args,**kwargs)
            with patch.object(Path,"resolve",autospec=True,side_effect=redirected):
                with self.assertRaisesRegex(CompletionError,"canonical location"):
                    create_attempt(dataset,spec,identity)
            self.assertEqual(before,{p:p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_redirected_completed_attempt_is_rejected_before_native_or_processed_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root,spec = Path(folder),fixture_spec()
            dataset = data_root(root,"test")
            identity = identity_for_jobs({"run_set":"test"},1)
            attempt = create_attempt(dataset,spec,identity)
            fill_attempt(attempt,spec)
            seal_completed(dataset,spec,identity,attempt)
            before = {p:p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with patch.object(Path,"is_junction",autospec=True,side_effect=lambda p:p==attempt/"native"):
                with self.assertRaisesRegex(CompletionError,"links or junctions"):
                    verified_completed(dataset,spec,identity)
                with self.assertRaisesRegex(CompletionError,"links or junctions"):
                    seal_completed(dataset,spec,identity,attempt)
            self.assertEqual(before,{p:p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_complete_results_coexist_and_cannot_be_relabelled(self):
        with tempfile.TemporaryDirectory() as folder:
            root, spec = Path(folder), fixture_spec()
            outputs = {}
            for name,jobs in (("sequential",1),("parallel",8)):
                check_run_set(root,name,jobs,create=True)
                dataset = data_root(root,name)
                identity = identity_for_jobs({"run_set":name},jobs)
                attempt = create_attempt(dataset,spec,identity)
                fill_attempt(attempt,spec)
                seal_completed(dataset,spec,identity,attempt)
                outputs[name] = (dataset,identity,verified_completed(dataset,spec,identity))
            seq,parallel = outputs["sequential"],outputs["parallel"]
            self.assertNotEqual(seq[2].completion_path,parallel[2].completion_path)
            with self.assertRaises(CompletionError):
                verified_completed(seq[0],spec,parallel[1])
            # Copying a valid seal/attempt to another namespace never makes it reusable.
            other = data_root(root,"copied")
            shutil.copytree(seq[0],other)
            with self.assertRaises(CompletionError):
                verified_completed(other,spec,identity_for_jobs({"run_set":"copied"},1))

    def test_status_states_and_live_owner_checks_do_not_mutate_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root,spec = Path(folder),fixture_spec()
            dataset = data_root(root,"test")
            directory = run_directory(dataset,spec)
            self.assertEqual(unsealed_status(directory,root)[0],"pending")
            attempt = create_attempt(dataset,spec,identity_for_jobs({"run_set":"test"},1))
            self.assertEqual(unsealed_status(directory,root)[0],"stale-running")
            with CampaignLock(root):
                before = {p:p.read_bytes() for p in dataset.rglob("*") if p.is_file()}
                self.assertTrue(campaign_lock_held(root))
                self.assertEqual(unsealed_status(directory,root)[0],"running")
                self.assertEqual(before,{p:p.read_bytes() for p in dataset.rglob("*") if p.is_file()})
                status = json.loads((attempt/"attempt_status.json").read_text())
                status["process_token"] = "recycled-pid"
                (attempt/"attempt_status.json").write_text(json.dumps(status))
                self.assertEqual(unsealed_status(directory,root)[0],"stale-running")
            mark_attempt(attempt,"failed",error="synthetic")
            self.assertEqual(unsealed_status(directory,root), ("failed","synthetic"))
            mark_attempt(attempt,"interrupted")
            self.assertEqual(unsealed_status(directory,root)[0],"incomplete")

    def test_native_path_limit_is_checked_before_execution(self):
        spec = replace(fixture_spec(),code="nanokmc_active_filtered_binary_nn")
        root = Path(__file__).resolve().parents[1]
        attempt = run_directory(data_root(root,"manuscript-jobs8"),spec)/"attempt_123456789abc"
        check_native_path_length(attempt,spec,windows=True)
        with self.assertRaisesRegex(ValueError,"shorter clone"):
            check_native_path_length(Path(tempfile.gettempdir())/("x"*230)/"attempt",spec,windows=True)

    def test_cli_requires_namespace_and_dry_run_status_write_no_execution_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            # Enumeration reads only the authoritative scientific manifest.
            with patch.object(runner,"ROOT",root), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(["--list-solvers"]),0)
                for mode in ("--dry-run","--status"):
                    self.assertEqual(runner.main(["--campaign","A","--solvers","binary",mode,"--run-set","test"]),0)
                self.assertEqual(list(root.iterdir()),[])
            for module,args in ((runner,["--dry-run"]),(process_results,["--dry-run"]),(make_figures,["--dry-run"])):
                with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as failure:
                    module.main(args)
                self.assertEqual(failure.exception.code,2)

    def test_processing_and_plot_reader_keep_namespaces_separate(self):
        from benchmark_core.campaigns import enumerate_runs
        from benchmark_core.publication import _process_locked, load_publication
        from plotting.inputs import load_inputs, PublicationError
        from test_publication import synthetic_rows
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            for name in ("benchmark_core/publication.py","benchmark_core/figure_data.py","scripts/process_results.py","config/manuscript.json",
                         "dependencies.lock.json","benchmark_core/runtime_identity.py"):
                target = source/name
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(repository/name,target)
            specs = enumerate_runs(campaign="A")
            for namespace,jobs in (("sequential",1),("parallel",8)):
                check_run_set(source,namespace,jobs,create=True)
                dataset = data_root(source,namespace)
                identity = identity_for_jobs({"run_set":namespace,"harness_sha256":{},"timing_policy":"synthetic_native"},jobs)
                completions = {}
                rows,_ = synthetic_rows(jobs)
                for spec in specs:
                    directory = run_directory(dataset,spec)
                    directory.mkdir(parents=True)
                    record = dict(scope="manuscript",status="complete",run_set=namespace,run_id=spec.run_id,
                                  requested_jobs=jobs,concurrency=jobs,execution_identity=identity,identity_sha256="TEST_ONLY")
                    marker = directory/"complete.json"
                    marker.write_text(json.dumps(record))
                    completions[spec.run_id] = SimpleNamespace(record=record,completion_path=marker,
                                                               raw_dir=directory,processed_dir=directory)
                def normalize(spec,completion):
                    return ([dict(row,run_set=namespace,timing_policy="synthetic_native") for row in rows
                             if row["run_id"] == "SYNTHETIC_TEST_ONLY_"+spec.run_id],[])
                with patch("benchmark_core.publication.normalize_run",side_effect=normalize),contextlib.redirect_stdout(io.StringIO()):
                    report = _process_locked(dataset,[7],source/"unused.json",False,
                        lambda _,spec,**kw:completions[spec.run_id],lambda *a,**kw:{spec.code:identity for spec in specs},
                        source_root=source,run_set=namespace)
                self.assertEqual(report["run_set"],namespace)
                load_publication(dataset,[7],source_root=source,run_set=namespace)
                loaded = load_inputs(dataset,dataset/"results/csv",[7],run_set=namespace)
                self.assertEqual({row["run_set"] for row in loaded["tables"]["fig07_event_accounting.csv"]},{namespace})
                with self.assertRaisesRegex(PublicationError,"different run-set"):
                    load_inputs(dataset,dataset/"results/csv",[7],run_set="another")
            self.assertTrue((data_root(source,"sequential")/"results/csv/publication_manifest.json").is_file())
            self.assertTrue((data_root(source,"parallel")/"results/csv/publication_manifest.json").is_file())

    def test_absent_raw_root_preview_keeps_full_coverage_without_per_run_path_scans(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/"repository"
            root.mkdir()
            for registered in (False,True):
                if registered:
                    check_run_set(root,"test",8,create=True)
                before = {p:p.read_bytes() for p in root.rglob("*") if p.is_file()}
                for mode in ("--dry-run","--status"):
                    report = Path(folder)/"preview.json"
                    output = io.StringIO()
                    with patch.object(runner,"ROOT",root), \
                         patch.object(runner,"run_directory",side_effect=AssertionError("No per-run filesystem scan needed")), \
                         patch.object(runner,"current_identities",side_effect=AssertionError("No runtime probe needed")), \
                         patch.object(runner,"WorkerProcess",side_effect=AssertionError("No solver may start")), \
                         contextlib.redirect_stdout(output):
                        self.assertEqual(runner.main(["--campaign","all","--run-set","test","--jobs","8",
                                                      mode,"--verbose","--json",str(report)]),0)
                    result = json.loads(report.read_text())
                    self.assertEqual(result["selected_unique_runs"],6468)
                    self.assertEqual(result["expected_checkpoint_rows"],122892)
                    self.assertEqual(len(result["runs"]),6468)
                    self.assertEqual(len({item["run_id"] for item in result["runs"]}),6468)
                    self.assertTrue(all(item["requested_jobs"]==8 for item in result["runs"]))
                    if mode == "--status":
                        self.assertEqual(result["counts"],{"pending":6468})
                        self.assertEqual(output.getvalue().count("PENDING    "),6468)
                    else:
                        self.assertTrue(all(item["existing_completion"]=="no_sealed_result" for item in result["runs"]))
                    self.assertEqual(before,{p:p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_existing_raw_root_retains_strict_per_run_inspection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            check_run_set(root,"test",1,create=True)
            (data_root(root,"test")/"results/raw").mkdir(parents=True)
            for mode in ("--dry-run","--status"):
                with patch.object(runner,"ROOT",root), \
                     patch.object(runner,"run_directory",side_effect=CompletionError("synthetic redirect rejected")) as inspect, \
                     contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(runner.main(["--campaign","A","--solvers","binary","--run-set","test",mode]),2)
                    inspect.assert_called()


if __name__ == "__main__":
    unittest.main()
