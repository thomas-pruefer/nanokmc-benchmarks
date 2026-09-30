"""Shared scientific transformations for strict Figure 5–10 data and previews."""
from __future__ import annotations

import math
import statistics
from benchmark_core.runner import PATH_IDS
from benchmark_core.scenario import CHECKPOINTS as POINTS

BINARY = "nanokmc_active_filtered_binary_nn"
BINS = ((12,49),(50,199),(200,999),(1000,3999),(4000,None))
STATES = ((0.2,0.75),(0.2,1.25),(0.4,0.75),(0.4,1.25))
SIZES = ((3,512),(4,64),(5,8),(6,1))
FIGURE_FILES = {5:("fig05_observables.csv","fig05_clusters.csv"),6:("fig06_correspondence.csv",),
    7:("fig07_event_accounting.csv",),8:("fig08_runtime_horizon.csv","fig08_gamma.csv"),
    9:("fig09_runtime_screen.csv",),10:("fig10_size_scaling.csv","fig10_alpha.csv")}
COMMON = ("run_id","code","scenario_id","k","N","seed","x_A_nominal","T_star","requested_mcs",
          "realized_common_mcs","runtime_seconds","runtime_source","source_identity_sha256",
          "requested_jobs","active_jobs_at_launch","execution_policy","timing_policy")


def number(value, label, *, integer=False, minimum=0):
    result = float(value)
    if not math.isfinite(result) or result < minimum or (integer and not result.is_integer()):
        raise ValueError(f"Invalid {label}: {value!r}")
    return int(result) if integer else result


def cutoff12_concentration(distribution, n_b):
    small = sum(c["cluster_size"]*c["cluster_count"] for c in distribution
                if c["common_species"] == "A" and c["cluster_size"] < 12)
    return small, small/(n_b+small)


def event_counters(code, row):
    """Keep native proposal/selection meanings and executed exchanges distinct."""
    if code.startswith("spparks_"):
        executed = number(row["spparks_naccept_native"], "Naccept", integer=True)
        rejected = number(row["spparks_nreject_native"], "Nreject", integer=True)
        sweep = code.endswith("sweep_random")
        candidates = executed+rejected if sweep else executed
        meaning = "native Naccept+Nreject sweep proposals" if sweep else "native Naccept; rejection-free selections=executions"
        source = "spparks_naccept_native+spparks_nreject_native" if sweep else "spparks_naccept_native"
    else:
        executed = number(row["accepted_exchanges_total"], "executed exchanges", integer=True)
        candidates = number(row["attempted_exchanges_total"], "candidate events", integer=True) if row.get("attempted_exchanges_total") else executed
        meaning = "native proposals including nulls/rejections" if code.startswith("nanokmc_") and code != "nanokmc_exact_class_optimized" else "native rejection-free selections=executions"
        source = "attempted_exchanges_total;accepted_exchanges_total" if row.get("attempted_exchanges_total") else "accepted_exchanges_total"
    if candidates < executed:
        raise ValueError(f"{code}: fewer candidates than executions")
    return candidates, executed, meaning, source


def log_fit(xs: list[float], ys: list[float]) -> dict:
    """Unweighted OLS in log space, never pooled or inverse-variance weighted."""
    if len(xs) != len(ys) or len(xs) < 2 or len(set(xs)) != len(xs):
        raise ValueError("A fit needs distinct paired abscissae")
    if not all(math.isfinite(v) and v > 0 for v in xs+ys):
        raise ValueError("Log fits require finite positive values")
    lx,ly = [math.log(v) for v in xs],[math.log(v) for v in ys]
    mx,my = statistics.mean(lx),statistics.mean(ly)
    slope = sum((x-mx)*(y-my) for x,y in zip(lx,ly))/sum((x-mx)**2 for x in lx)
    intercept = my-slope*mx
    residual = sum((y-intercept-slope*x)**2 for x,y in zip(lx,ly))
    total = sum((y-my)**2 for y in ly)
    return dict(slope=slope,A_seconds=math.exp(intercept),r_squared=1-residual/total if total else 1.0,n_points=len(xs))


def common(row):
    result = {key:row[key] for key in COMMON}
    if row.get("run_set") is not None:
        result["run_set"] = row["run_set"]
    return result


def derive_tables(rows: list[dict], a_clusters: dict, figures: list[int], *, partial=False) -> dict:
    """Exact manuscript formulas; previews allow absent runs and omit alpha fits.

    Inputs come from normalized, sealed whole trajectories. A missing observation
    in a present trajectory is an error even when other trajectories are absent.
    Partial tables are used in memory by previews, never committed as publication.
    """
    if partial and not rows:
        return {}
    policies = {(r["requested_jobs"],r["execution_policy"],r["timing_policy"]) for r in rows}
    if len(policies) != 1:
        raise ValueError("Mixed execution/timing policies cannot be silently combined in publication tables")
    jobs,execution_policy,timing_policy = next(iter(policies))
    if type(jobs) is not int or not 1 <= jobs <= 32 or execution_policy != "independent_single_thread_jobs" or not timing_policy:
        raise ValueError("Invalid execution/timing policy for publication tables")
    policy = dict(requested_jobs=jobs,execution_policy=execution_policy,timing_policy=timing_policy)
    namespaces = {r.get("run_set") for r in rows}
    if len(namespaces) != 1:
        raise ValueError("Different run-sets cannot be combined into publication tables")
    if next(iter(namespaces)) is not None:
        policy["run_set"] = next(iter(namespaces))
    lookup = {}
    for r in rows:
        key = r["code"],r["k"],r["x_A_nominal"],r["T_star"],r["seed"],r["requested_mcs"]
        if key in lookup:
            raise ValueError(f"Duplicate standardized observation: {key}")
        lookup[key] = r
    present = {key[:-1] for key in lookup}

    def get(code,point,*,k=6,x=0.2,t=0.75,seed=1):
        key = code,k,x,t,seed,point
        if key not in lookup and (not partial or key[:-1] in present):
            raise ValueError(f"Missing required standardized observation: {key}")
        return lookup.get(key)

    tables = {}
    if 5 in figures:
        observations,clusters = [],[]
        for point in POINTS:
            r = get(BINARY,point)
            if r is not None:
                observations.append(dict(common(r),**{key:r[key] for key in ("rho_AB","N_A_lt12","N_B","c_A_B","cutoff")}))
        for code in PATH_IDS:
            r = get(code,3000)
            if r is None:
                continue
            if code not in a_clusters:
                raise ValueError(f"Missing A cluster distribution: {code}")
            for lower,upper in BINS:
                count = sum(int(c["cluster_count"]) for c in a_clusters[code] if c["common_species"] == "A" and lower <= int(c["cluster_size"]) and (upper is None or int(c["cluster_size"]) <= upper))
                clusters.append(dict(common(r),bin_min=lower,bin_max=upper,bin_label=f"{lower}–{upper}" if upper else f"≥{lower}",cluster_count=count))
        tables["fig05_observables.csv"],tables["fig05_clusters.csv"] = observations,clusters
    if 6 in figures:
        pairs = []
        for code in PATH_IDS:
            if code == BINARY:
                continue
            for x,t in STATES:
                for point in POINTS[1:]:
                    b,c = get(BINARY,point,x=x,t=t),get(code,point,x=x,t=t)
                    if b is None or c is None:
                        continue
                    for observable in ("rho_AB","c_A_B","n_ex"):
                        pairs.append(dict(policy,comparator=code,scenario_id=c["scenario_id"],seed=1,x_A_nominal=x,T_star=t,
                            requested_mcs=point,observable=observable,binary_value=b[observable],comparator_value=c[observable],
                            binary_run_id=b["run_id"],comparator_run_id=c["run_id"],binary_realized_mcs=b["realized_common_mcs"],comparator_realized_mcs=c["realized_common_mcs"]))
        tables["fig06_correspondence.csv"] = pairs
    if 7 in figures:
        tables["fig07_event_accounting.csv"] = [dict(common(r),**{key:r[key] for key in
            ("candidate_events","executed_events","candidate_events_per_site","executed_events_per_site","counter_meaning","native_counter_source")})
            for code in PATH_IDS if (r := get(code,30000)) is not None]
    if 8 in figures:
        horizons,fits = [],[]
        for code in PATH_IDS:
            records = [r for point in POINTS if (r := get(code,point)) is not None]
            if not records:
                continue
            horizons.extend(dict(common(r),displayed=int(r["requested_mcs"]>=30),fit_included=int(r["requested_mcs"]>=30)) for r in records)
            selected = [r for r in records if r["requested_mcs"]>=30]
            fit = log_fit([r["requested_mcs"] for r in selected],[r["runtime_seconds"] for r in selected])
            fits.append(dict(policy,code=code,gamma=fit.pop("slope"),**fit,fit_mcs_min=30,fit_mcs_max=30000,fit_rule="unweighted OLS log(runtime) on log(requested MCS)"))
        tables["fig08_runtime_horizon.csv"],tables["fig08_gamma.csv"] = horizons,fits
    if 9 in figures:
        tables["fig09_runtime_screen.csv"] = [common(r) for code in PATH_IDS for x,t in STATES
                                              if (r := get(code,30000,x=x,t=t)) is not None]
    if 10 in figures:
        means,fits = [],[]
        for code in PATH_IDS:
            code_means = []
            for k,count in SIZES:
                values = [r["runtime_seconds"] for seed in range(1,count+1) if (r := get(code,30000,k=k,seed=seed)) is not None]
                if not values:
                    continue
                sd = statistics.stdev(values) if len(values)>1 else None
                mean = dict(policy,code=code,k=k,N=2**(3*k-1),n=len(values),expected_n=count,mean_seconds=statistics.mean(values),
                    sample_sd_seconds=sd,sem_seconds=sd/math.sqrt(len(values)) if sd is not None else None,requested_mcs=30000)
                means.append(mean)
                code_means.append(mean)
            if not partial:
                fit = log_fit([r["N"] for r in code_means],[r["mean_seconds"] for r in code_means])
                fits.append(dict(policy,code=code,alpha=fit.pop("slope"),**fit,fit_rule="unweighted OLS log(arithmetic mean runtime) on log(N), four equal-weight sizes"))
        tables["fig10_size_scaling.csv"],tables["fig10_alpha.csv"] = means,fits
    if not partial:
        expected = {"fig05_observables.csv":19,"fig05_clusters.csv":55,"fig06_correspondence.csv":2160,
            "fig07_event_accounting.csv":11,"fig08_runtime_horizon.csv":209,"fig08_gamma.csv":11,
            "fig09_runtime_screen.csv":44,"fig10_size_scaling.csv":44,"fig10_alpha.csv":11}
        for name,table in tables.items():
            if len(table) != expected[name]:
                raise ValueError(f"Incorrect {name} row count")
    return tables
