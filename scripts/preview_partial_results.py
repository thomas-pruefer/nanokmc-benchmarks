"""Provisional plots from sealed completed runs; never publication Figures 5–10."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark_core.publication import POINTS, normalize_run, required_runs
from benchmark_core.figure_data import BINS, derive_tables
from benchmark_core.run_sets import add_run_set_argument, check_run_set, data_root
from benchmark_core.run_store import CompletionError, checked_output_path, current_identities, verified_completed
from plotting.inputs import BINARY, COLORS, LABELS, ORDER, SIZES, STATES, sha256
from plotting.manuscript import _mpl


def key_for(spec):
    scenario = spec.scenario
    return spec.code, scenario.nx, scenario.composition_A, scenario.kT, spec.seed


def available_run_rows(root: Path, specs: list, identities: dict, *, need_clusters: bool) -> tuple[dict, dict, list]:
    """Only complete, current, fully validated runs contribute any points."""
    records, seals, counts = {}, [], Counter()
    cluster_index = POINTS.index(3000)
    for spec in specs:
        try:
            completion = verified_completed(root, spec, expected_identity=identities[spec.code])
            rows, clusters = normalize_run(spec, completion)
        except CompletionError as exc:
            counts[exc.status] += 1
            continue
        except (ValueError, OSError, KeyError, TypeError) as exc:
            counts["invalid_processed"] += 1
            continue
        key = key_for(spec)
        representative = key[1:4] == (6, 0.2, 0.75) and spec.seed == 1
        keep_all = representative or key[1] == 6
        records[key] = {
            "rows": rows if keep_all else [rows[-1]],
            "clusters": ([item for item in clusters if int(item["save_index"]) == cluster_index]
                         if need_clusters and representative else []),
        }
        seals.append({"run_id": spec.run_id, "completion_path": str(completion.completion_path),
                      "completion_sha256": sha256(completion.completion_path),
                      "identity_sha256": completion.record["identity_sha256"]})
        counts["complete"] += 1
    return records, dict(counts), seals


def _finish(fig, number, shown, expected):
    fig.text(.5, .986, "PROVISIONAL PREVIEW — incomplete data; not a manuscript figure",
             ha="center", va="top", color="#a12626", fontsize=11, fontweight="bold")
    fig.text(.5, .013, f"Figure {number} preview · coverage {shown}/{expected} · missing data omitted",
             ha="center", va="bottom", color="#8c2727", fontsize=8.5)
    return fig


def figure5(tables):
    plt = _mpl()
    clusters = tables["fig05_clusters.csv"]
    available = [code for code in ORDER if any(row["code"] == code for row in clusters)]
    if not available:
        return None
    fig, (ax, bar) = plt.subplots(1, 2, figsize=(13, 5.4))
    fig.subplots_adjust(top=.86, bottom=.25, left=.085, right=.97, wspace=.30)
    rows = tables["fig05_observables.csv"]
    if rows:
        ax.plot([r["requested_mcs"] for r in rows], [r["rho_AB"] for r in rows],
                color=COLORS[BINARY], label="Interface fraction")
        ax.plot([r["requested_mcs"] for r in rows], [r["c_A_B"] for r in rows],
                color="#d87015", label="Dissolved A")
        ax.set_xscale("symlog", linthresh=30)
        ax.legend(frameon=False)
    else:
        ax.text(.5, .5, "Binary run not yet complete", ha="center", transform=ax.transAxes)
    ax.set(xlabel="Requested common MCS", ylabel="Fraction", title="Binary observables")
    ax.grid(alpha=.25)
    width = .75 / max(1, len(available))
    counts = {(row["code"], row["bin_min"]): row["cluster_count"] for row in clusters}
    for index, code in enumerate(available):
        values = [counts[code, lo] for lo, _ in BINS]
        bar.bar([i - .375 + (index + .5) * width for i in range(5)], values, width,
                color=COLORS[code], label=LABELS[code])
    bar.set_xticks(range(5), ("12–49", "50–199", "200–999", "1000–3999", "≥4000"), rotation=30)
    bar.set(xlabel="A-cluster size at 3000 MCS", ylabel="Count", title="Available solvers")
    bar.legend(fontsize=7, frameon=False)
    return _finish(fig, 5, len(available), 11)


def figure6(tables):
    plt = _mpl()
    pairs = tables["fig06_correspondence.csv"]
    if not pairs:
        return None
    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    fig.subplots_adjust(top=.84, bottom=.25, left=.07, right=.98, wspace=.30)
    for ax, field, title in zip(axes, ("rho_AB", "c_A_B", "n_ex"),
                                ("Interface fraction", "Dissolved A", "Executed exchanges/site")):
        rows = [row for row in pairs if row["observable"] == field]
        for code in ORDER:
            selected = [(row["binary_value"], row["comparator_value"]) for row in rows
                        if row["comparator"] == code]
            if selected:
                ax.scatter([x for x, _ in selected], [y for _, y in selected],
                           s=11, color=COLORS[code], label=LABELS[code], alpha=.7)
        values = [value for row in rows for value in (row["binary_value"], row["comparator_value"]) if value > 0]
        if field == "n_ex" and values:
            ax.set_xscale("log"); ax.set_yscale("log")
            lo, hi = min(values) / 1.2, max(values) * 1.2
        else:
            lo, hi = 0, max((value for row in rows for value in (row["binary_value"], row["comparator_value"])), default=1) * 1.05
        if hi <= lo:
            hi = lo + 1
        ax.plot([lo, hi], [lo, hi], "--", color="#666666", lw=.8)
        ax.set(xlim=(lo, hi), ylim=(lo, hi), xlabel="Binary", ylabel="Comparator", title=title)
        ax.grid(alpha=.2)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, fontsize=8, frameon=False)
    return _finish(fig, 6, len(pairs) // 3, 720)


def figure7(tables):
    plt = _mpl()
    rows = {row["code"]: row for row in tables["fig07_event_accounting.csv"]}
    found = [(i, code, rows[code]) for i, code in enumerate(ORDER) if code in rows]
    if not found:
        return None
    fig, ax = plt.subplots(figsize=(12.5, 5.6))
    fig.subplots_adjust(top=.86, bottom=.33, left=.08, right=.98)
    for i, code, row in found:
        ax.bar(i, row["candidate_events_per_site"], .70, color="#c6cbd0")
        ax.bar(i, row["executed_events_per_site"], .40, color=COLORS[code])
    ax.set_xticks(range(11), [LABELS[c] for c in ORDER], rotation=43, ha="right")
    ax.set(yscale="log", ylabel="Cumulative events per site", title="Event accounting at 30,000 MCS")
    ax.grid(axis="y", alpha=.25)
    return _finish(fig, 7, len(found), 11)


def figure8(tables):
    plt = _mpl()
    rows = tables["fig08_runtime_horizon.csv"]
    fits = {row["code"]: row for row in tables["fig08_gamma.csv"]}
    found = [(i, code) for i, code in enumerate(ORDER) if code in fits]
    if not found:
        return None
    fig, (ax, bar) = plt.subplots(1, 2, figsize=(13.7, 6))
    fig.subplots_adjust(top=.85, bottom=.31, left=.08, right=.98, wspace=.25)
    for i, code in found:
        selected = [r for r in rows if r["code"] == code and r["displayed"]]
        ax.plot([r["requested_mcs"] for r in selected], [r["runtime_seconds"] for r in selected],
                color=COLORS[code], label=LABELS[code], marker=".")
        bar.bar(i, fits[code]["gamma"], color=COLORS[code])
    ax.set(xscale="log", yscale="log", xlabel="Requested common MCS", ylabel="Runtime (s)",
           title="Cumulative runtime for available solvers")
    ax.grid(alpha=.2)
    bar.set_xticks(range(11), [LABELS[c] for c in ORDER], rotation=43, ha="right")
    bar.set(ylabel="Provisional horizon exponent γ", title="Fit of complete available trajectories")
    bar.axhline(1, ls="--", lw=.8, color="#777777")
    return _finish(fig, 8, len(found), 11)


def figure9(tables):
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(12.8, 6))
    fig.subplots_adjust(top=.86, bottom=.31, left=.08, right=.98)
    colors = ("#35556b", "#7495a0", "#b8ae8a", "#e7c76d")
    rows = {(row["code"], row["x_A_nominal"], row["T_star"]): row
            for row in tables["fig09_runtime_screen.csv"]}
    found = 0
    labelled = set()
    for i, code in enumerate(ORDER):
        for j, ((x, temp), color) in enumerate(zip(STATES, colors)):
            row = rows.get((code, x, temp))
            if row:
                found += 1
                label = f"x={x:.2f}, T*={temp:.2f}"
                ax.bar(i + (j - 1.5) * .2, row["runtime_seconds"], .18,
                       color=color, label=label if label not in labelled else None)
                labelled.add(label)
    if not found:
        plt.close(fig)
        return None
    ax.set_xticks(range(11), [LABELS[c] for c in ORDER], rotation=43, ha="right")
    ax.set(yscale="log", ylabel="Runtime to 30,000 MCS (s)", title="Four-state runtime screen")
    ax.grid(axis="y", alpha=.2)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, fontsize=8, frameon=False)
    return _finish(fig, 9, found, 44)


def figure10(tables):
    plt = _mpl()
    stats = {(row["code"], row["k"]): row for row in tables["fig10_size_scaling.csv"]}
    if not stats:
        return None
    fig, (ax, coverage) = plt.subplots(1, 2, figsize=(13.7, 6), gridspec_kw={"width_ratios": (1.15, 1)})
    fig.subplots_adjust(top=.84, bottom=.30, left=.08, right=.98, wspace=.28)
    for code in ORDER:
        points = [stats[code, k] for k, _, _ in SIZES if (code, k) in stats]
        if points:
            ax.plot([p["N"] for p in points], [p["mean_seconds"] for p in points],
                    marker="o", color=COLORS[code], label=LABELS[code])
            repeated = [p for p in points if p["sem_seconds"] is not None]
            if repeated:
                ax.errorbar([p["N"] for p in repeated], [p["mean_seconds"] for p in repeated],
                            yerr=[p["sem_seconds"] for p in repeated], fmt="none", ecolor=COLORS[code], capsize=2)
    ax.set(xscale="log", yscale="log", xlabel="N sites", ylabel="Available-seed mean runtime (s)",
           title="Provisional size scaling; no α fit")
    ax.grid(alpha=.2)
    matrix = [[stats.get((code, k), {}).get("n", 0) / expected for k, _, expected in SIZES] for code in ORDER]
    coverage.imshow(matrix, vmin=0, vmax=1, aspect="auto", cmap="Blues")
    coverage.set_xticks(range(4), [f"k={k}" for k, _, _ in SIZES])
    coverage.set_yticks(range(11), [LABELS[c] for c in ORDER], fontsize=8)
    coverage.set_title("Verified complete seeds / prescribed seeds")
    for i, code in enumerate(ORDER):
        for j, (k, _, expected) in enumerate(SIZES):
            count = stats.get((code, k), {}).get("n", 0)
            coverage.text(j, i, f"{count}/{expected}", ha="center", va="center", fontsize=7,
                          color="white" if count / expected > .55 else "black")
    return _finish(fig, 10, sum(p["n"] for p in stats.values()), 6435)


PLOTS = {5: figure5, 6: figure6, 7: figure7, 8: figure8, 9: figure9, 10: figure10}


def available_run_sets(repository: Path) -> list[str]:
    base = Path(repository).resolve() / "run-sets"
    if not base.is_dir():
        return []
    return sorted(child.name for child in base.iterdir()
                  if child.is_dir() and (child / "run_set.json").is_file())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_run_set_argument(parser)
    parser.add_argument("--paths", type=Path, default=ROOT / "config/paths.local.json")
    parser.add_argument("--figures", nargs="+", type=int, choices=range(5, 11), default=list(range(5, 11)))
    parser.add_argument("--format", choices=("png", "pdf", "both"), default="png")
    parser.add_argument("--dry-run", action="store_true", help="Validate available sealed runs and report coverage; write nothing")
    args = parser.parse_args(argv)
    figures = sorted(set(args.figures))
    try:
        dataset = data_root(ROOT, args.run_set)
        marker = check_run_set(ROOT, args.run_set)
        if marker is None:
            names = available_run_sets(ROOT)
            detail = (" Registered run-set(s): " + ", ".join(names) + "."
                      if names else " No registered run-sets were found in this repository.")
            raise ValueError(f"Run-set {args.run_set!r} is not registered.{detail}")
        identities = current_identities(ROOT, args.paths, jobs=marker["requested_jobs"], run_set=args.run_set)
        specs = required_runs(ROOT, figures)
        records, counts, seals = available_run_rows(dataset, specs, identities, need_clusters=5 in figures)
        expected_by_code = Counter(spec.code for spec in specs)
        spec_by_id = {spec.run_id: spec for spec in specs}
        complete_by_code = Counter(spec_by_id[item["run_id"]].code for item in seals)
        coverage_by_solver = {code: {"complete": complete_by_code[code], "required": expected_by_code[code]}
                              for code in ORDER}
        print(f"Verified complete: {counts.get('complete', 0)}/{len(specs)}; "
              f"missing={counts.get('missing', 0)}, incomplete={counts.get('incomplete', 0)}, "
              f"stale={counts.get('stale', 0)}, invalid={counts.get('corrupt', 0) + counts.get('invalid_processed', 0)}")
        print("Per solver: " + ", ".join(f"{LABELS[code]} {item['complete']}/{item['required']}"
                                         for code, item in coverage_by_solver.items()))
        if args.dry_run:
            print("DRY RUN: no figures written and no solver launched.")
            return 0
        if not records:
            raise ValueError("No verified complete trajectories are available for the selected figures")
        tables = derive_tables(
            [row for record in records.values() for row in record["rows"]],
            {code: record["clusters"] for (code, k, x, temp, seed), record in records.items()
             if (k, x, temp, seed) == (6, .2, .75, 1)},
            figures, partial=True)
        preview_root = checked_output_path(dataset, dataset / "figures/previews")
        preview_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:8]
        working = checked_output_path(dataset, preview_root / ("_building_" + stamp))
        final = checked_output_path(dataset, preview_root / ("preview_" + stamp))
        working.mkdir()
        plt = _mpl()
        formats = ("png", "pdf") if args.format == "both" else (args.format,)
        files, skipped = [], []
        for number in figures:
            fig = PLOTS[number](tables)
            if fig is None:
                skipped.append(number)
                continue
            try:
                for fmt in formats:
                    output = working / f"preview_fig{number:02d}.{fmt}"
                    fig.savefig(output, dpi=180, format=fmt,
                                metadata={"Creator": "nanokmc-benchmarks provisional preview"})
                    files.append({"figure": number, "name": output.name, "sha256": sha256(output)})
            finally:
                plt.close(fig)
        if not files:
            raise ValueError("No selected figure has enough paired completed data to preview")
        # The campaign may have been running throughout this read. Recheck every
        # used seal after rendering before publishing a preview identity record.
        for item in seals:
            completion = verified_completed(dataset, spec_by_id[item["run_id"]],
                                            expected_identity=identities[spec_by_id[item["run_id"]].code])
            if sha256(completion.completion_path) != item["completion_sha256"]:
                raise ValueError(f"Completion changed while rendering: {item['run_id']}")
        report = {"schema_version": 1, "scope": "provisional_preview", "publication_complete": False,
                  "run_set": args.run_set, "requested_jobs": marker["requested_jobs"],
                  "created_utc": datetime.now(timezone.utc).isoformat(), "selected_figures": figures,
                  "skipped_figures": skipped, "required_unique_runs": len(specs), "coverage": counts,
                  "coverage_by_solver": coverage_by_solver,
                  "verified_completed_runs": seals, "files": files,
                  "warning": "Incomplete exploratory plots. Never use as manuscript Figures 5–10 or publication CSVs."}
        (working / "preview_manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        working.rename(final)
        print(f"PREVIEW ONLY: {len(files)} file(s) in {final}; skipped figures {skipped}")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError) as exc:
        print(f"PREVIEW FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
