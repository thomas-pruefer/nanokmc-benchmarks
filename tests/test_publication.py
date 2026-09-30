"""Offline synthetic arithmetic/coverage tests; no solver and no publication results."""
import csv
import json
import math
from pathlib import Path
import statistics
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchmark_core.campaigns import enumerate_runs
from benchmark_core.publication import (POINTS,BINARY,derive_tables,log_fit,normalize_run,
                                        required_runs,write_csv,load_publication,verify_implementation_guards,
                                        infer_publication_jobs,record_requested_jobs)
from benchmark_core.runner import PATH_IDS
from benchmark_core.validation import sha256

ROOT = Path(__file__).resolve().parents[1]


def synthetic_rows(jobs=1):
    """All required figure keys, fabricated values clearly confined to tests."""
    records = []
    for spec in enumerate_runs():
        s = spec.scenario
        n = s.nanokmc_total_sites_estimate
        # Full histories only where figures consume them, endpoints for size ensembles.
        for point in POINTS if s.nx == 6 else (30000,):
            records.append(dict(run_id="SYNTHETIC_TEST_ONLY_"+spec.run_id,code=spec.code,scenario_id=s.id,
                k=s.nx,N=n,seed=spec.seed,x_A_nominal=s.composition_A,T_star=s.kT,requested_mcs=point,
                realized_common_mcs=point+.25 if point else 0,runtime_seconds=n*point**.75*(1+spec.seed/1000),
                runtime_source="fabricated test value",source_identity_sha256="TEST_ONLY",rho_AB=.15,N_A_lt12=13,
                requested_jobs=jobs,active_jobs_at_launch='',execution_policy='independent_single_thread_jobs',
                timing_policy='native_evolution_intervals_no_overhead_subtraction_v1',
                N_B=n-round(n*s.composition_A),c_A_B=13/(n-round(n*s.composition_A)+13),cutoff=12,n_ex=point/100,
                candidate_events=point*n,executed_events=point*n/100,candidate_events_per_site=point,
                executed_events_per_site=point/100,counter_meaning="fabricated fixture",native_counter_source="TEST_ONLY"))
    clusters = {code:[dict(common_species="A",cluster_size=12,cluster_count=3),
                      dict(common_species="A",cluster_size=4000,cluster_count=1)] for code in PATH_IDS}
    return records,clusters


class PublicationArithmetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows,cls.clusters = synthetic_rows()
        cls.tables = derive_tables(cls.rows,cls.clusters,list(range(5,11)))

    def test_exact_coverage_and_requested_pairing(self):
        self.assertEqual(len(required_runs(ROOT,list(range(5,11)))),6468)
        self.assertEqual(len(required_runs(ROOT,[5,7,8])),11)
        self.assertEqual(len(required_runs(ROOT,[6,9])),44)
        self.assertEqual(len(required_runs(ROOT,[10])),6435)
        self.assertEqual(len(self.tables["fig06_correspondence.csv"]),2160)
        pairs = self.tables["fig06_correspondence.csv"]
        self.assertEqual(len({(r['comparator'],r['scenario_id'],r['seed'],r['requested_mcs'],r['observable']) for r in pairs}),2160)
        self.assertTrue(all(r['requested_mcs'] != r['binary_realized_mcs'] for r in pairs))
        for observable in ("rho_AB","c_A_B","n_ex"):
            self.assertEqual(sum(r['observable']==observable for r in pairs),720)
        self.assertEqual(len(self.tables['fig09_runtime_screen.csv']),44)

    def test_unweighted_horizon_and_four_mean_fits(self):
        horizons = self.tables['fig08_runtime_horizon.csv']
        self.assertEqual(len(horizons),209)
        self.assertEqual(sum(r['fit_included'] for r in horizons),176)
        self.assertTrue(all(r['displayed']==r['fit_included']==int(r['requested_mcs']>=30) for r in horizons))
        for fit in self.tables['fig08_gamma.csv']:
            self.assertAlmostEqual(fit['gamma'],.75,places=12)
            self.assertEqual(fit['n_points'],16)
        means = [r for r in self.tables['fig10_size_scaling.csv'] if r['code']==BINARY]
        self.assertEqual([r['n'] for r in means],[512,64,8,1])
        self.assertIsNone(means[-1]['sem_seconds'])
        self.assertIsNone(means[-1]['sample_sd_seconds'])
        vals = [256*30000**.75*(1+seed/1000) for seed in range(1,513)]
        self.assertAlmostEqual(means[0]['mean_seconds'],statistics.mean(vals))
        self.assertAlmostEqual(means[0]['sem_seconds'],statistics.stdev(vals)/math.sqrt(512))
        expected = log_fit([r['N'] for r in means],[r['mean_seconds'] for r in means])['slope']
        actual = next(r['alpha'] for r in self.tables['fig10_alpha.csv'] if r['code']==BINARY)
        self.assertAlmostEqual(actual,expected)

    def test_zero_bins_are_retained_and_missing_or_duplicate_points_rejected(self):
        clusters = self.tables['fig05_clusters.csv']
        self.assertEqual(len(clusters),55)
        self.assertEqual(sum(r['cluster_count']==0 for r in clusters),33)
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            derive_tables(self.rows+[self.rows[0]],self.clusters,[5])
        absent = [r for r in self.rows if not (r['code']==BINARY and r['k']==6 and r['x_A_nominal']==.2 and r['T_star']==.75 and r['requested_mcs']==20)]
        with self.assertRaisesRegex(ValueError,'Missing'):
            derive_tables(absent,self.clusters,[6])
        absent_seed = [r for r in self.rows if not (r['code']==BINARY and r['k']==3 and r['seed']==512)]
        with self.assertRaisesRegex(ValueError,'Missing'):
            derive_tables(absent_seed,self.clusters,[10])

    def test_invalid_log_values_rejected(self):
        for ys in ([1,0],[1,float('nan')],[1,-1]):
            with self.assertRaises(ValueError):
                log_fit([1,2],ys)

    def test_exported_csv_schemas_pass_independent_plot_validation(self):
        from benchmark_core.publication import read_csv
        from plotting.inputs import validate_tables
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            loaded = {}
            for name,rows in self.tables.items():
                write_csv(path/name,rows)
                loaded[name] = read_csv(path/name)
            validate_tables({'tables':loaded,'figures':list(range(5,11))})

    def test_uniform_jobs_eight_retained_and_mixed_policies_rejected(self):
        from copy import deepcopy
        from plotting.inputs import validate_tables,PublicationError
        rows,clusters = synthetic_rows(jobs=8)
        tables = derive_tables(rows,clusters,list(range(5,11)))
        for name,table in tables.items():
            self.assertEqual({row['requested_jobs'] for row in table},{8},name)
        self.assertEqual(tables['fig08_gamma.csv'][0]['gamma'],self.tables['fig08_gamma.csv'][0]['gamma'])
        manifest = dict(requested_jobs=8,execution_policy='independent_single_thread_jobs',
                        timing_policy='native_evolution_intervals_no_overhead_subtraction_v1')
        data = dict(tables=deepcopy(tables),figures=list(range(5,11)),manifest=manifest)
        validate_tables(data)
        data['tables']['fig08_gamma.csv'][0]['requested_jobs'] = 1
        with self.assertRaisesRegex(PublicationError,'policy differs'):
            validate_tables(data)
        rows[0]['requested_jobs'] = 1
        with self.assertRaisesRegex(ValueError,'Mixed execution/timing'):
            derive_tables(rows,clusters,[5,7,8])


class NativeNormalization(unittest.TestCase):
    def test_cutoff_twelve_and_spparks_counter_fallback(self):
        spec = enumerate_runs(campaign='A',solvers=['spparks-tree'])[0]
        n,n_a = spec.scenario.nanokmc_total_sites_estimate,26214
        n_b = n-n_a
        metrics,snapshots,timings,clusters = [],[],[],[]
        for index,point in enumerate(POINTS):
            identity = dict(code=spec.code,scenario_id=spec.scenario.id,seed=1,save_index=index)
            metrics.append(dict(identity,requested_mcs=point,total_sites=n,N_A_common=n_a,N_B_common=n_b,
                total_undirected_bonds_common=6*n,interface_bonds_common=100000,interface_density_common=100000/(6*n),
                diagnostic_runtime_seconds_cumulative=point*.1,diagnostic_runtime_source='synthetic timer',
                common_mcs_equivalent=point,native_simulation_time=point/6,spparks_naccept_native=100*point,
                spparks_nreject_native=999,attempted_exchanges_total='',accepted_exchanges_total=''))
            snapshots.append(dict(identity,total_sites=n,N_A=n_a,N_B=n_b,topology_valid=1,parity_valid=1,
                min_neighbors=12,max_neighbors=12,unique_coordinates=n,unique_site_ids=n,
                native_step=point,native_time_from_snapshot=point/6,snapshot_ordinal=index))
            timings.append(dict(identity,diagnostic_runtime_seconds_cumulative=point*.1,progress_common_mcs_equivalent=point))
            for species,size,count in [('A',1,2),('A',11,1),('A',12,1),('A',n_a-25,1),('B',n_b,1)]:
                clusters.append(dict(identity,common_species=species,cluster_size=size,cluster_count=count,sites_in_clusters=size*count))
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            for name,rows in [('metrics_common.csv',metrics),('snapshot_manifest_common.csv',snapshots),
                              ('timing_common.csv',timings),('cluster_distribution_common.csv',clusters)]:
                write_csv(out/name,rows)
            completion = SimpleNamespace(processed_dir=out,record={'identity_sha256':'TEST_ONLY',
                'requested_jobs':8,'concurrency':8,'execution_identity':{'requested_jobs':8,'concurrency':8,
                    'execution_policy':'independent_single_thread_jobs','timing_policy':'native_evolution_intervals_no_overhead_subtraction_v1'}})
            rows,_ = normalize_run(spec,completion)
            self.assertEqual(rows[-1]['requested_jobs'],8)
            self.assertEqual(rows[-1]['active_jobs_at_launch'],'')
            self.assertEqual(rows[-1]['N_A_lt12'],13)
            self.assertEqual(rows[-1]['c_A_B'],13/(n_b+13))
            self.assertEqual(rows[-1]['candidate_events'],3000000)
            self.assertEqual(rows[-1]['executed_events'],3000000)
            self.assertNotEqual(rows[-1]['c_A_B'],13/n)
            write_csv(out/'cluster_distribution_common.csv',clusters+[clusters[0]])
            with self.assertRaisesRegex(ValueError,'Duplicate'):
                normalize_run(spec,completion)

    def test_manifest_rejects_test_data_and_changed_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root/'results/csv/fig07_event_accounting.csv'
            write_csv(path,[{'test':'only'}])
            manifest = dict(schema_version=1,scope='manuscript',publication_complete=True,test_only=True,selected_figures=[7],
                files=[dict(path='results/csv/fig07_event_accounting.csv',sha256=sha256(path))])
            marker = root/'results/csv/publication_manifest.json'
            marker.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'Synthetic'):
                load_publication(root,[7])
            load_publication(root,[7],allow_test_only=True)
            path.write_text('edited')
            with self.assertRaisesRegex(ValueError,'Changed'):
                load_publication(root,[7],allow_test_only=True)

    def test_changed_dependency_or_harness_guard_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root/'dependencies.lock.json'
            path.write_text('{"synthetic":"test only"}')
            guards = [dict(path=path.name,sha256=sha256(path),repository_relative=True)]
            verify_implementation_guards(root,guards)
            path.write_text('{"synthetic":"different"}')
            with self.assertRaisesRegex(ValueError,'changed'):
                verify_implementation_guards(root,guards)

    def test_processing_rejects_files_changed_after_start_even_validate_only(self):
        from benchmark_core.publication import _process_locked
        # A zero-run mocked lifecycle isolates provenance; it is never exposed
        # through the public campaign manifest or written as publication data.
        for changed in ('benchmark_core/publication.py','benchmark_core/figure_data.py','scripts/process_results.py','config/manuscript.json'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for name in ('benchmark_core/publication.py','benchmark_core/figure_data.py','scripts/process_results.py','config/manuscript.json',
                             'dependencies.lock.json','benchmark_core/runtime_identity.py'):
                    path = root/name; path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_text('original synthetic bytes')
                def mutate(*args):
                    (root/changed).write_text('changed during processing')
                    return {}
                with patch('benchmark_core.publication.required_runs',return_value=[]), \
                     patch('benchmark_core.publication.infer_publication_jobs',return_value=1), \
                     patch('benchmark_core.publication.derive_tables',side_effect=mutate):
                    with self.assertRaisesRegex(ValueError,'changed'):
                        _process_locked(root,[7],root/'paths.json',True,None,lambda *a,**kw:{})
                self.assertFalse((root/'results/csv/publication_manifest.json').exists())

    def test_metadata_preflight_infers_uniform_jobs_and_rejects_mixed_missing(self):
        from benchmark_core.run_store import run_directory
        specs = enumerate_runs(campaign='A',solvers=['binary','generic'])
        def metadata(spec,jobs):
            return dict(scope='manuscript',status='complete',run_id=spec.run_id,requested_jobs=jobs,concurrency=jobs,
                execution_identity=dict(requested_jobs=jobs,concurrency=jobs,execution_policy='independent_single_thread_jobs',
                    timing_policy='native_evolution_intervals_no_overhead_subtraction_v1'))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for spec in specs:
                path = run_directory(root,spec)/'complete.json'; path.parent.mkdir(parents=True)
                path.write_text(json.dumps(metadata(spec,8))); paths.append(path)
            self.assertEqual(infer_publication_jobs(root,specs),8)
            paths[0].write_text(json.dumps(metadata(specs[0],1)))
            with self.assertRaisesRegex(ValueError,'Mixed requested job limits'):
                infer_publication_jobs(root,specs)
            paths[0].unlink()
            with self.assertRaisesRegex(ValueError,'absent'):
                infer_publication_jobs(root,specs)
        for jobs in (0,33,True,'8',None):
            with self.assertRaisesRegex(ValueError,'metadata'):
                record_requested_jobs(metadata(specs[0],jobs))
        for malformed in (None,[],{},dict(execution_identity=None)):
            with self.assertRaisesRegex(ValueError,'metadata'):
                record_requested_jobs(malformed)


if __name__ == '__main__':
    unittest.main()
