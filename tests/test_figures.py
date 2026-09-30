"""Small synthetic figure workflow, not simulation or historical manuscript data."""
from __future__ import annotations
import copy
import csv
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import numpy as np

from plotting.inputs import (BINARY, ORDER, POINTS, FIT_POINTS, STATES, SIZES, FILES,
                             sha256, load_inputs, validate_tables, PublicationError)


def create_fixture(root):
    """Create 2,564 small synthetic CSV records and thirteen k3 configurations.

    Explicit TEST ONLY manifest prevents the public CLI from consuming them.
    The fixture uses publication-shaped tables but performs no solver calls.
    """
    root = Path(root)
    csv_dir = root / "results/csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    tables = {name: [] for names in FILES.values() for name in names}
    def common(code, t, x=.2, temp=.75, k=6):
        return dict(run_id=f"synthetic_{code}_{x}_{temp}_{k}", code=code, scenario_id=f"synthetic_{x}_{temp}_{k}",
            k=k,N=2**(3*k-1),seed=1,x_A_nominal=x,T_star=temp,requested_mcs=t,
            realized_common_mcs=float(t),runtime_seconds=(ORDER.index(code)+1)*max(t,1)**.75/100,
            source_identity_sha256="synthetic_test_only")
    for t in POINTS:
        na = round(9000/(1+t/20))
        tables[FILES[5][0]].append(dict(common(BINARY,t),rho_AB=.30/(1+math.log1p(t)/5),
            N_A_lt12=na,N_B=104858,c_A_B=na/(104858+na),cutoff=12))
    for i,code in enumerate(ORDER):
        for j,(lo,hi,label) in enumerate(((12,49,"12–49"),(50,199,"50–199"),(200,999,"200–999"),
                                        (1000,3999,"1000–3999"),(4000,"","≥4000"))):
            tables[FILES[5][1]].append(dict(common(code,3000),bin_min=lo,bin_max=hi,bin_label=label,
                cluster_count=(i+j)%5 if j in (0,4) else 10+(i*3+j)%23))
        candidates = [30000,60000,2057,2057,6134,266,177,177,177,177,177][i]
        tables[FILES[7][0]].append(dict(common(code,30000),candidate_events=candidates*131072,
            executed_events=177*131072,candidate_events_per_site=candidates,executed_events_per_site=177,
            counter_meaning="synthetic native counter for plotting test only"))
        gamma,A = .5+i*.035,.02*(i+1)
        for t in POINTS:
            row = common(code,t); row["runtime_seconds"] = A*t**gamma if t else 0
            tables[FILES[8][0]].append(dict(row,fit_included=t in FIT_POINTS,displayed=t in FIT_POINTS))
        tables[FILES[8][1]].append(dict(code=code,gamma=gamma,A_seconds=A,r_squared=1,n_points=16,
            fit_mcs_min=30,fit_mcs_max=30000))
        for x,temp in STATES:
            row=common(code,30000,x,temp); row["runtime_seconds"] *= (x*10+temp)
            tables[FILES[9][0]].append(row)
        alpha,A = .95+i*.035,.0001*(i+1)
        for k,N,n in SIZES:
            mean = A*N**alpha; sd = mean*.12 if n>1 else ""
            tables[FILES[10][0]].append(dict(code=code,k=k,N=N,n=n,expected_n=n,
                mean_seconds=mean,sample_sd_seconds=sd,sem_seconds=sd/math.sqrt(n) if n>1 else "",requested_mcs=30000))
        tables[FILES[10][1]].append(dict(code=code,alpha=alpha,A_seconds=A,r_squared=1,n_points=4))
        if code==BINARY:
            continue
        for x,temp in STATES:
            for t in POINTS[1:]:
                for obs in ("rho_AB","c_A_B","n_ex"):
                    value={"rho_AB":.32/(1+math.log1p(t)/3),"c_A_B":.018/(1+math.log1p(t)),"n_ex":t**.75/4}[obs]
                    tables[FILES[6][0]].append(dict(comparator=code,scenario_id=f"synthetic_{x}_{temp}",seed=1,
                        x_A_nominal=x,T_star=temp,requested_mcs=t,observable=obs,binary_value=value,
                        comparator_value=value*(1+.008*(i-5)),binary_run_id=f"synthetic_{BINARY}_{x}_{temp}",
                        comparator_run_id=f"synthetic_{code}_{x}_{temp}",binary_realized_mcs=t,comparator_realized_mcs=t))
    entries=[]
    for name,rows in tables.items():
        path=csv_dir/name
        with path.open("w",newline="",encoding="utf-8") as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        entries.append(dict(path=path.relative_to(root).as_posix(),sha256=sha256(path),rows=len(rows),columns=list(rows[0])))
    coordinates=np.array([(x,y,z) for x in range(8) for y in range(8) for z in range(8) if (x+y+z)%2==0])
    configs=[]
    for code,t in [(code,3000) for code in ORDER]+[(BINARY,0),(BINARY,30000)]:
        rng=np.random.default_rng(t+5)
        selected = rng.choice(len(coordinates),51,replace=False)
        if t==30000:
            selected=np.argsort(np.sum((coordinates-np.array([4,4,4]))**2,axis=1))[:51]
        mask=np.zeros(len(coordinates),dtype=bool);mask[selected]=True
        config=dict(run_id=f"synthetic_{code}",code=code,requested_mcs=t,realized_common_mcs=t,periods=[8,8,8],N_A=51,N_B=205)
        for species,part in (("a",coordinates[mask]),("b",coordinates[~mask])):
            path=root/"results/rasmol"/code/f"{t}_{species}.xyz";path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(str(len(part))+"\nSYNTHETIC TEST ONLY\n"+"\n".join("C "+" ".join(map(str,coord)) for coord in part)+"\n",encoding="utf-8")
            config[f"{species}_xyz"]=path.relative_to(root).as_posix();config[f"{species}_sha256"]=sha256(path)
        configs.append(config)
    manifest=dict(schema_version=1,scope="manuscript",test_only=True,publication_complete=True,
                  selected_figures=list(FILES),files=entries,configurations=configs)
    (csv_dir/"publication_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return root


class FigureContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="nanokmc_figure_test_")
        self.root=create_fixture(Path(self.temp.name))
        self.data=load_inputs(self.root,self.root/"results/csv",FILES,allow_test_only=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_coverage_and_test_isolation(self):
        self.assertEqual(len(self.data["tables"][FILES[6][0]]),2160)
        self.assertEqual(len(self.data["configurations"]),13)
        with self.assertRaisesRegex(PublicationError,"TEST ONLY"):
            load_inputs(self.root,self.root/"results/csv",FILES)

    def test_missing_pair_wrong_fit_and_false_sem_are_rejected(self):
        bad=copy.deepcopy(self.data);bad["tables"][FILES[6][0]].pop()
        with self.assertRaisesRegex(PublicationError,"coverage"):
            validate_tables(bad)
        bad=copy.deepcopy(self.data);bad["tables"][FILES[8][1]][0]["gamma"]="1.99"
        with self.assertRaisesRegex(PublicationError,"OLS"):
            validate_tables(bad)
        bad=copy.deepcopy(self.data)
        row=next(r for r in bad["tables"][FILES[10][0]] if r["n"]==1);row["sem_seconds"]="0"
        with self.assertRaisesRegex(PublicationError,"unavailable"):
            validate_tables(bad)

    def test_tampering_and_wrong_membership_are_rejected(self):
        path=self.root/"results/csv"/FILES[7][0];path.write_text(path.read_text()+"\n")
        with self.assertRaisesRegex(PublicationError,"stale CSV"):
            load_inputs(self.root,self.root/"results/csv",[7],allow_test_only=True)
        bad=copy.deepcopy(self.data)
        row=next(r for r in bad["tables"][FILES[8][0]] if r["requested_mcs"]==20);row["fit_included"]="true"
        with self.assertRaisesRegex(PublicationError,"16 requested"):
            validate_tables(bad)

    def test_all_six_figures_render_pdf_and_png(self):
        from plotting.manuscript import render_figures
        report=render_figures(self.data,self.root/"figures",dpi=90)
        self.assertTrue(report["test_only"])
        self.assertEqual(len(report["files"]),12)
        for item in report["files"]:
            path=self.root/"figures"/item["path"]
            self.assertGreater(path.stat().st_size,1000)
            self.assertEqual(sha256(path),item["sha256"])

    def test_external_edit_during_render_prevents_manifest_commit(self):
        from plotting import manuscript
        data=load_inputs(self.root,self.root/"results/csv",[7],allow_test_only=True)
        original=manuscript.figure7
        def edit_after_reading(value):
            fig=original(value)
            path=self.root/"results/csv"/FILES[7][0]
            path.write_text(path.read_text()+"\n",encoding="utf-8")
            return fig
        with mock.patch.object(manuscript,"figure7",side_effect=edit_after_reading):
            with self.assertRaisesRegex(PublicationError,"changed during figure generation"):
                manuscript.render_figures(data,self.root/"figures",formats=("png",),dpi=72)
        self.assertFalse((self.root/"figures/figure_manifest.json").exists())


if __name__=="__main__":
    unittest.main()
