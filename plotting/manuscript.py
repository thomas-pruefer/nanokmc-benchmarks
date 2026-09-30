"""Python-only current Figures 5--10; inputs are verified CSVs/configurations."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from plotting.inputs import (BINARY, ORDER, LABELS, COLORS, MARKERS, STATES, SIZES,
                             FILES, finite, truth, safe_path, read_xyz, sha256, verify_guards)

OUTPUT_NAMES = {5: "fig05_phase1_morphology_observables", 6: "fig06_correspondence",
    7: "fig07_event_accounting", 8: "fig08_horizon_runtime",
    9: "fig09_runtime_screen", 10: "fig10_phase2_size_scaling"}


def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 11,
        "axes.labelsize": 10, "xtick.labelsize": 9, "ytick.labelsize": 9,
        "legend.fontsize": 8.5, "axes.spines.top": False, "axes.spines.right": False,
        "axes.axisbelow": True, "grid.alpha": .24, "pdf.fonttype": 42, "ps.fonttype": 42,
        "savefig.facecolor": "white"})
    return plt


def _panel(ax, letter, title):
    ax.set_title(title, loc="left", pad=12, fontweight="medium")
    ax.text(-.09, 1.07, f"{letter})", transform=ax.transAxes, fontweight="bold", fontsize=12)


def _line(ax, code, x, y, **kwargs):
    return ax.plot(x, y, color=COLORS[code], marker=MARKERS[code], markersize=4,
                   linewidth=1.35, label=LABELS[code], **kwargs)


def _solver_ticks(ax):
    ax.set_xticks(np.arange(len(ORDER)), [LABELS[c] for c in ORDER], rotation=42,
                  ha="right", rotation_mode="anchor")


def _legend(fig, handles=None, labels=None, y=.01, ncol=4):
    if handles is None:
        handles, labels = fig.axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(.5, y), ncol=ncol,
               frameon=False, handlelength=2, columnspacing=1.6)


def figure5(data):
    plt = _mpl()
    from matplotlib.lines import Line2D
    fig = plt.figure(figsize=(13, 8.7))
    gs = fig.add_gridspec(2, 6, height_ratios=(1.0, .88), hspace=.39, wspace=.62,
                         left=.065, right=.91, top=.95, bottom=.20)
    configs = {(item["code"], int(item["requested_mcs"])): item for item in data["configurations"]}
    for i, t in enumerate((0, 3000, 30000)):
        ax = fig.add_subplot(gs[0, 2*i:2*i+2], projection="3d")
        item = configs[(BINARY, t)]
        coords = read_xyz(safe_path(data["root"], item["a_xyz"]))
        ax.scatter(*coords.T, s=1.9, color=COLORS[BINARY], alpha=.85, linewidths=0,
                   depthshade=True, rasterized=True)
        length = item["periods"][0]
        ax.set(xlim=(0, length), ylim=(0, length), zlim=(0, length))
        ax.set_box_aspect((1, 1, 1)); ax.view_init(elev=18, azim=-62)
        ax.set_xticks([]); ax.set_yticks([]); ax.set_zticks([])
        for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
            axis.pane.set_facecolor((.97, .98, 1., 1.))
            axis.line.set_color("#b0b8c0")
        ax.set_title(f"{chr(97+i)})  Binary: {t:,} MCS", pad=2, fontweight="medium")
    ax = fig.add_subplot(gs[1, :4]); _panel(ax, "d", "A-cluster counts at 3,000 MCS")
    rows = data["tables"][FILES[5][1]]
    lows = (12, 50, 200, 1000, 4000)
    lookup = {(r["code"], r["bin_min"]): r["cluster_count"] for r in rows}
    width = .075
    for j, code in enumerate(ORDER):
        values = np.array([lookup[(code, lo)] for lo in lows])
        positions = np.arange(5)+(j-5)*width
        positive = values > 0
        ax.bar(positions[positive], values[positive], width, color=COLORS[code], label=LABELS[code],
               linewidth=.3, edgecolor="#4a4a4a")
        # Zero has no positive log-scale height. A baseline marker is placed in
        # axis coordinates, not a fabricated positive count in data coordinates.
        ax.plot(positions[~positive], np.full(np.count_nonzero(~positive), .015), "x", color=COLORS[code],
                ms=3, transform=ax.get_xaxis_transform(), clip_on=False)
    ax.set_yscale("log"); ax.set_ylim(bottom=.65)
    ax.set_xticks(np.arange(5), ("12–49", "50–199", "200–999", "1,000–3,999", "≥4,000"))
    ax.set_xlabel("Cluster size (sites)"); ax.set_ylabel("Number of clusters (raw counts)")
    ax.grid(axis="y", which="both")
    ax.text(0, -.25, "× at baseline denotes zero clusters; no pseudocounts added.", transform=ax.transAxes, fontsize=8)
    ax2 = fig.add_subplot(gs[1, 4:]); _panel(ax2, "e", "Binary interface and dissolved A")
    rows = sorted(data["tables"][FILES[5][0]], key=lambda r: r["requested_mcs"])
    t = np.array([r["requested_mcs"] for r in rows], dtype=float)
    # Continuous linear-to-log transform; tick labels remain common MCS.
    def forward(values):
        values = np.asarray(values, dtype=float)
        return np.where(values <= 30, values / 30, 1 + np.log(np.maximum(values, 30) / 30))
    def inverse(values):
        values = np.asarray(values, dtype=float)
        return np.where(values <= 1, values * 30, 30 * np.exp(values-1))
    ax2.set_xscale("function", functions=(forward, inverse))
    ax2.plot(t, [r["rho_AB"] for r in rows], color=COLORS[BINARY], linewidth=1.7)
    right = ax2.twinx(); right.spines["right"].set_visible(True)
    right.plot(t, [r["c_A_B"] for r in rows], color="#d87015", linewidth=1.7)
    ax2.set_xlim(0, 30000)
    ax2.set_xticks([0, 30, 300, 3000, 30000], ["0", "30", "300", "3,000", "30,000"])
    ax2.tick_params(axis="x", rotation=35)
    ax2.set_xlabel("Requested common MCS\n(linear to 30, then logarithmic)")
    ax2.set_ylabel(r"Interface-bond fraction $\rho_{AB}$", color=COLORS[BINARY])
    right.set_ylabel(r"Dissolved concentration $c_A^B$", color="#d87015")
    ax2.tick_params(axis="y", colors=COLORS[BINARY]); right.tick_params(axis="y", colors="#d87015")
    ax2.grid(alpha=.2)
    _legend(fig, [Line2D([], [], color=COLORS[c], lw=4) for c in ORDER],
            [LABELS[c] for c in ORDER], y=.025, ncol=4)
    return fig


def figure6(data):
    plt = _mpl()
    fig, axes = plt.subplots(1, 3, figsize=(14.1, 5.0))
    fig.subplots_adjust(left=.065, right=.985, bottom=.30, top=.89, wspace=.31)
    titles = (r"Interface-bond fraction $\rho_{AB}$", r"Dissolved A concentration $c_A^B$", r"Executed exchanges/site $n_{ex}$")
    rows = data["tables"][FILES[6][0]]
    for i, (ax, obs, title) in enumerate(zip(axes, ("rho_AB", "c_A_B", "n_ex"), titles)):
        _panel(ax, chr(97+i), title)
        values = []
        for code in ORDER:
            if code == BINARY:
                continue
            selected = [r for r in rows if r["comparator"] == code and r["observable"] == obs]
            x, y = [r["binary_value"] for r in selected], [r["comparator_value"] for r in selected]
            ax.scatter(x, y, c=COLORS[code], marker=MARKERS[code], s=13, alpha=.7, linewidths=.3,
                       label=LABELS[code], rasterized=True)
            values.extend(x+y)
        if obs == "n_ex":
            ax.set_xscale("log"); ax.set_yscale("log")
            lo, hi = min(values) / 1.2, max(values) * 1.2
        else:
            lo, hi = min(0, min(values)), max(values) * 1.06
        ax.plot([lo, hi], [lo, hi], "--", color="#4b4b4b", linewidth=.8, zorder=0)
        ax.set(xlim=(lo, hi), ylim=(lo, hi), xlabel="NanoKMC Binary", ylabel="Comparator")
        ax.set_box_aspect(1); ax.grid(which="both", linewidth=.5)
    _legend(fig, y=.03, ncol=5)
    fig.text(.5, .005, "72 paired observations per comparator/panel; four trajectories matched by requested checkpoint.",
             ha="center", fontsize=8.5)
    return fig


def figure7(data):
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(12.5, 5.6))
    fig.subplots_adjust(left=.085, right=.985, bottom=.31, top=.88)
    rows = {r["code"]: r for r in data["tables"][FILES[7][0]]}
    x = np.arange(len(ORDER))
    ax.bar(x, [finite(rows[c], "candidate_events_per_site") for c in ORDER], .72,
           color="#c6cbd0", edgecolor="#555d65", lw=.7, label="Proposed / selected (native meaning)")
    ax.bar(x, [finite(rows[c], "executed_events_per_site") for c in ORDER], .40,
           color="#414a53", label="Executed unlike exchanges")
    _solver_ticks(ax); ax.set_yscale("log"); ax.set_ylabel("Cumulative events per site")
    ax.set_title("Event accounting at 30,000 common MCS", loc="left", pad=16)
    ax.grid(axis="y", which="both", linewidth=.5); ax.legend(loc="upper right", frameon=True)
    fig.text(.09, .015, "Native counter semantics: Classical 30,000 proposals/site; SPPARKS sweep 60,000 selections/site.", fontsize=9)
    return fig


def _fit_bars(ax, rows, exponent):
    lookup = {row["code"]: row for row in rows}
    values = [finite(lookup[c], exponent) for c in ORDER]
    ax.bar(np.arange(len(ORDER)), values, color=[COLORS[c] for c in ORDER], edgecolor="#555d65", linewidth=.5)
    for i, value in enumerate(values):
        ax.text(i, value+.025, f"{value:.2f}", ha="center", va="bottom", fontsize=8)
    _solver_ticks(ax)
    ax.axhline(1, linestyle="--", color="#626b72", lw=.8)
    ax.grid(axis="y", linewidth=.5)
    ax.set_ylim(min(0, min(values)*1.1), max(1.1, max(values)*1.15))


def figure8(data):
    plt = _mpl()
    fig, axes = plt.subplots(1, 2, figsize=(13.7, 6.7), gridspec_kw={"width_ratios": (1, 1.15)})
    fig.subplots_adjust(left=.065, right=.99, bottom=.33, top=.9, wspace=.22)
    ax, bar = axes
    _panel(ax, "a", "Cumulative evolution runtime"); _panel(bar, "b", "Empirical horizon exponent")
    rows = data["tables"][FILES[8][0]]
    for code in ORDER:
        selected = sorted((r for r in rows if r["code"] == code and truth(r, "displayed")), key=lambda r: r["requested_mcs"])
        _line(ax, code, [r["requested_mcs"] for r in selected], [finite(r, "runtime_seconds") for r in selected])
    ax.set(xscale="log", yscale="log", xlabel="Requested common MCS", ylabel="Cumulative solver runtime (s)")
    ax.grid(which="both", linewidth=.45)
    _fit_bars(bar, data["tables"][FILES[8][1]], "gamma")
    bar.set_ylabel(r"$\gamma$ in $t_{runtime}=A_t\,t^\gamma$")
    _legend(fig, y=.035, ncol=4)
    fig.text(.5, .009, "Unweighted log-space fits use exactly 16 requested checkpoints, 30–30,000 MCS.", ha="center", fontsize=9)
    return fig


def figure9(data):
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(12.8, 6.0))
    fig.subplots_adjust(left=.08, right=.985, bottom=.33, top=.88)
    rows = data["tables"][FILES[9][0]]
    lookup = {(r["code"], r["x_A_nominal"], r["T_star"]): r for r in rows}
    colors = ("#35556b", "#7495a0", "#b8ae8a", "#e7c76d")
    for j, ((x, temp), color) in enumerate(zip(STATES, colors)):
        ax.bar(np.arange(len(ORDER))+(j-1.5)*.2,
            [finite(lookup[(c, x, temp)], "runtime_seconds") for c in ORDER], .18,
            color=color, edgecolor="#414a53", linewidth=.5, label=fr"$x_A={x:.2f},\ T^*={temp:.2f}$")
    _solver_ticks(ax); ax.set_yscale("log"); ax.set_ylabel("Runtime to 30,000 common MCS (s)")
    ax.set_title("Four-state endpoint runtime, N = 131,072", loc="left", pad=16)
    ax.grid(axis="y", which="both", linewidth=.5); ax.legend(loc="upper left", ncol=2, frameon=True)
    fig.text(.08, .02, "One native realization per path/state (seed label 1); wall-clock timings depend on the recorded execution environment.", fontsize=9)
    return fig


def figure10(data):
    plt = _mpl()
    fig, axes = plt.subplots(1, 2, figsize=(13.7, 6.9), gridspec_kw={"width_ratios": (1, 1.15)})
    fig.subplots_adjust(left=.065, right=.99, bottom=.35, top=.9, wspace=.22)
    ax, bar = axes
    _panel(ax, "a", "Size-scaling ensemble means"); _panel(bar, "b", "Empirical size exponent")
    rows = data["tables"][FILES[10][0]]
    for code in ORDER:
        selected = sorted((r for r in rows if r["code"] == code), key=lambda r: r["N"])
        _line(ax, code, [r["N"] for r in selected], [finite(r, "mean_seconds") for r in selected])
        # Do not invent an error bar for the n=1 realization.
        repeated = [r for r in selected if r["n"] > 1]
        ax.errorbar([r["N"] for r in repeated], [finite(r, "mean_seconds") for r in repeated],
            yerr=[finite(r, "sem_seconds") for r in repeated], fmt="none", ecolor=COLORS[code], capsize=3, lw=1)
    ax.set(xscale="log", yscale="log", xlabel="System size N (sites)", ylabel="Mean solver runtime (s)")
    ax.set_xticks([n for _, n, _ in SIZES], [f"{n:,}\nn = {seeds}" for _, n, seeds in SIZES])
    ax.minorticks_off(); ax.grid(which="major", linewidth=.45)
    _fit_bars(bar, data["tables"][FILES[10][1]], "alpha")
    bar.set_ylabel(r"$\alpha$ in $\langle t_{runtime}\rangle=A_N\,N^\alpha$")
    _legend(fig, y=.055, ncol=4)
    fig.text(.5, .016, "Arithmetic means ± sample SEM (n > 1); no SEM at n = 1. Fits weight the four means equally in log space.", ha="center", fontsize=9)
    return fig


def render_figures(data, output_dir, *, formats=("pdf", "png"), dpi=220):
    """Render requested figures; atomically commit their hashes and provenance."""
    renderer = Path(__file__).resolve()
    reader = renderer.with_name("inputs.py")
    implementation_guards = dict(data.get("implementation_guards", {}))
    # Saved before rendering; never attribute loaded code to bytes first observed
    # after a long render. Inputs are also checked before use and before commit.
    implementation_guards.setdefault(str(renderer), sha256(renderer))
    implementation_guards.setdefault(str(reader), sha256(reader))
    verify_guards(implementation_guards)
    verify_guards(data["input_guards"])
    plt = _mpl()
    output_dir.mkdir(parents=True, exist_ok=True)
    methods = {5: figure5, 6: figure6, 7: figure7, 8: figure8, 9: figure9, 10: figure10}
    output_files = []
    for number in data["figures"]:
        fig = methods[number](data)
        if data["manifest"].get("test_only"):
            fig.text(.5, .985, "SYNTHETIC TEST ONLY — NOT MANUSCRIPT RESULTS", ha="center", va="top", color="#a03030", fontsize=11)
        try:
            for fmt in formats:
                destination = output_dir / f"{OUTPUT_NAMES[number]}.{fmt}"
                temporary = destination.with_name(destination.stem + ".partial." + fmt)
                fig.savefig(temporary, dpi=dpi, format=fmt, metadata={"Creator": "nanokmc-benchmarks Python plotting"})
                temporary.replace(destination)
                output_files.append({"figure": number, "path": destination.name, "sha256": sha256(destination)})
        finally:
            plt.close(fig)
    import matplotlib
    result = {"schema_version": 1, "test_only": bool(data["manifest"].get("test_only")),
        "requested_jobs": data["manifest"].get("requested_jobs"),
        "run_set": data["manifest"].get("run_set"),
        "execution_policy": data["manifest"].get("execution_policy"),
        "timing_policy": data["manifest"].get("timing_policy"),
        "publication_manifest_sha256": data["manifest_sha256"], "matplotlib_version": matplotlib.__version__,
        "numpy_version": np.__version__, "figures": data["figures"], "files": output_files,
        "plotting_source_sha256": implementation_guards[str(renderer)],
        "reader_source_sha256": implementation_guards[str(reader)],
        "implementation_guards": implementation_guards,
        "notes": "Python-only figures; every A coordinate shown; native data and CSV fitted values unchanged."}
    temporary = output_dir / "figure_manifest.json.partial"
    temporary.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    verify_guards(implementation_guards)
    verify_guards(data["input_guards"])
    temporary.replace(output_dir / "figure_manifest.json")
    return result
