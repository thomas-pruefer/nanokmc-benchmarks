"""Strict reader of the committed manuscript publication products."""
from __future__ import annotations
import csv
import hashlib
import json
import math
from pathlib import Path
import numpy as np

BINARY = "nanokmc_active_filtered_binary_nn"
ORDER = (
    "nanokmc_bit_encoded", "spparks_diffusion_sweep_random",
    "nanokmc_active_filtered_generic", BINARY,
    "nanokmc_partial_filter_optimized", "nanokmc_rate_category_optimized",
    "nanokmc_exact_class_optimized", "spparks_diffusion_linear",
    "spparks_diffusion_tree", "kmcos_otf", "kmc_lattice_selective",
)
LABELS = dict(zip(ORDER, (
    "NanoKMC Classical", "SPPARKS sweep", "NanoKMC Generic", "NanoKMC Binary",
    "Partial-Filter", "Rate-Category", "Exact-Class", "SPPARKS linear",
    "SPPARKS tree", "kmcos OTF", "KMC_Lattice selective",
)))
COLORS = dict(zip(ORDER, (
    "#df7629", "#388b44", "#24a1ab", "#1675b5", "#b3ab29", "#7b7970",
    "#b77d44", "#8970af", "#b64845", "#78504f", "#cc78b8",
)))
MARKERS = dict(zip(ORDER, ("o", "s", "^", "o", "v", "P", "D", ">", "<", "h", "X")))
POINTS = (0, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 1000, 2000,
          3000, 4000, 5000, 10000, 20000, 30000)
FIT_POINTS = POINTS[3:]
STATES = ((0.2, 0.75), (0.2, 1.25), (0.4, 0.75), (0.4, 1.25))
SIZES = ((3, 256, 512), (4, 2048, 64), (5, 16384, 8), (6, 131072, 1))
FILES = {
    5: ("fig05_observables.csv", "fig05_clusters.csv"),
    6: ("fig06_correspondence.csv",),
    7: ("fig07_event_accounting.csv",),
    8: ("fig08_runtime_horizon.csv", "fig08_gamma.csv"),
    9: ("fig09_runtime_screen.csv",),
    10: ("fig10_size_scaling.csv", "fig10_alpha.csv"),
}


class PublicationError(ValueError):
    """Publication inputs are missing, stale, or scientifically incomplete."""


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finite(row, key, *, positive=False):
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as error:
        raise PublicationError(f"Missing/nonnumeric {key!r} in {row}") from error
    if not math.isfinite(value) or (positive and value <= 0):
        raise PublicationError(f"Invalid {key}={value}; expected finite {'positive ' if positive else ''}value")
    return value


def integer(row, key):
    value = finite(row, key)
    if value != int(value):
        raise PublicationError(f"Expected integer {key}, got {value}")
    return int(value)


def truth(row, key):
    value = str(row.get(key, "")).strip().lower()
    if value not in ("true", "false", "1", "0"):
        raise PublicationError(f"Missing/invalid Boolean {key}: {row.get(key)!r}")
    return value in ("true", "1")


def _exact(rows, keys, expected, name):
    actual = [tuple(row[key] for key in keys) for row in rows]
    if len(set(actual)) != len(actual):
        raise PublicationError(f"{name}: duplicate keys {keys}")
    if set(actual) != expected:
        raise PublicationError(f"{name}: exact coverage failed ({len(actual)} rows, expected {len(expected)})")


def _normalize(rows, ints=(), floats=()):
    for row in rows:
        for key in ints:
            row[key] = integer(row, key)
        for key in floats:
            row[key] = finite(row, key)


def _bounded(rows, key, low=0., high=1.):
    for row in rows:
        value = finite(row, key)
        if not low <= value <= high:
            raise PublicationError(f"{key}={value} is outside [{low}, {high}]")


def _fit_check(points, fit, x, y, exponent, n):
    if integer(fit, "n_points") != n or len(points) != n:
        raise PublicationError(f"{exponent}: expected exactly {n} equally weighted fit points")
    xv = np.log([finite(row, x, positive=True) for row in points])
    yv = np.log([finite(row, y, positive=True) for row in points])
    slope, intercept = np.polyfit(xv, yv, 1)
    if not math.isclose(finite(fit, exponent), slope, rel_tol=1e-8, abs_tol=1e-10):
        raise PublicationError(f"{exponent} does not reproduce unweighted log-space OLS")
    if not math.isclose(finite(fit, "A_seconds", positive=True), math.exp(intercept), rel_tol=1e-8):
        raise PublicationError(f"{exponent} prefactor does not reproduce unweighted log-space OLS")


def safe_path(root, value):
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise PublicationError(f"Manifest path leaves data root: {value}")
    return path


def verify_guards(guards):
    """Detect edits while processing/plotting, including external non-harness edits."""
    for filename, expected in guards.items():
        path = Path(filename)
        if not path.is_file() or sha256(path) != expected:
            raise PublicationError(f"Input or implementation changed during figure generation: {path}")


def load_inputs(root, csv_dir, figures, *, allow_test_only=False,run_set=None):
    """Read a committed processed publication; test opt-in is never exposed by CLI."""
    figures = sorted(set(figures))
    manifest_path = csv_dir / "publication_manifest.json"
    if not manifest_path.is_file():
        raise PublicationError(f"Missing {manifest_path}. Run 05_process_results.bat after required runs finish.")
    original_hash = sha256(manifest_path)
    implementation_guards = {str(Path(__file__).resolve()): sha256(Path(__file__))}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if run_set is not None and manifest.get("run_set") != run_set:
        raise PublicationError("Processing manifest belongs to a different run-set")
    if manifest.get("schema_version") != 1 or manifest.get("scope") != "manuscript":
        raise PublicationError("Publication manifest is not the supported manuscript schema")
    if manifest.get("test_only") and not allow_test_only:
        raise PublicationError("TEST ONLY data cannot generate publication figures")
    if manifest.get("publication_complete") is not True:
        raise PublicationError("Processing is incomplete; publication_complete is not true")
    if not set(figures).issubset(set(manifest.get("selected_figures", []))):
        raise PublicationError("Requested figures are absent from processing manifest; process those figures first")
    entries = manifest.get("files", [])
    by_name = {}
    required_names = {name for figure in figures for name in FILES[figure]}
    for entry in entries:
        name = Path(entry["path"]).name
        if name not in required_names:
            continue
        if name in by_name:
            raise PublicationError(f"Duplicate manifest filename: {name}")
        by_name[name] = entry
    tables = {}
    for fig in figures:
        for name in FILES[fig]:
            entry = by_name.get(name)
            if entry is None:
                raise PublicationError(f"Manifest does not identify {name}")
            path = safe_path(root, entry["path"])
            if path.resolve().parent != csv_dir.resolve():
                raise PublicationError(f"{name} is outside selected CSV directory {csv_dir}")
            if not path.is_file() or sha256(path) != entry.get("sha256"):
                raise PublicationError(f"Missing or stale CSV: {path}; rerun processing")
            with path.open(newline="", encoding="utf-8-sig") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames != entry.get("columns"):
                    raise PublicationError(f"Column manifest mismatch for {name}")
                tables[name] = list(reader)
            if len(tables[name]) != entry.get("rows"):
                raise PublicationError(f"Row-count manifest mismatch for {name}")
    input_guards = {str(manifest_path.resolve()): original_hash}
    for entry in entries:
        input_guards[str(safe_path(root, entry["path"]))] = entry["sha256"]
    for item in manifest.get("configurations", []):
        for species in ("a", "b"):
            input_guards[str(safe_path(root, item[f"{species}_xyz"]))] = item[f"{species}_sha256"]
    data = {"root": root, "manifest": manifest, "manifest_sha256": original_hash,
            "manifest_path": manifest_path.resolve(), "input_guards": input_guards,
            "implementation_guards": implementation_guards,
            "tables": tables, "configurations": manifest.get("configurations", []), "figures": figures}
    validate_tables(data)
    if 5 in figures:
        _validate_configurations(data)
    verify_guards(input_guards)
    verify_guards(implementation_guards)
    return data


def validate_tables(data):
    """Independent checks of the figure-specific scientific contract."""
    tables = data["tables"]
    manifest = data.get("manifest")
    if manifest is not None and not manifest.get("test_only"):
        jobs = manifest.get("requested_jobs")
        execution,timing = manifest.get("execution_policy"),manifest.get("timing_policy")
        if type(jobs) is not int or not 1 <= jobs <= 32 or execution != "independent_single_thread_jobs" or not timing:
            raise PublicationError("Missing/invalid uniform publication concurrency and timing policy")
        for name,rows in tables.items():
            for row in rows:
                if integer(row,"requested_jobs") != jobs or row.get("execution_policy") != execution or row.get("timing_policy") != timing:
                    raise PublicationError(f"{name}: CSV concurrency/timing policy differs from the publication manifest")
                if manifest.get("run_set") is not None and row.get("run_set") != manifest["run_set"]:
                    raise PublicationError(f"{name}: CSV belongs to another run-set")
    for fig in data["figures"]:
        if fig == 5:
            rows = tables[FILES[5][0]]
            _normalize(rows, ("requested_mcs", "cutoff", "N_A_lt12", "N_B"), ("rho_AB", "c_A_B"))
            _exact(rows, ("code", "requested_mcs"), {(BINARY, t) for t in POINTS}, "Figure 5 observations")
            _bounded(rows, "rho_AB"); _bounded(rows, "c_A_B")
            for row in rows:
                if row["cutoff"] != 12 or row["N_A_lt12"] < 0 or row["N_B"] <= 0:
                    raise PublicationError("Figure 5 requires strict cutoff 12 and valid site counts")
                expected = row["N_A_lt12"] / (row["N_B"] + row["N_A_lt12"])
                if not math.isclose(row["c_A_B"], expected, rel_tol=1e-9, abs_tol=1e-12):
                    raise PublicationError("Figure 5 dissolved concentration must be NA_lt12/(NB+NA_lt12)")
            rows = tables[FILES[5][1]]
            _normalize(rows, ("requested_mcs", "bin_min", "cluster_count"))
            bins = {12: 49, 50: 199, 200: 999, 1000: 3999, 4000: None}
            _exact(rows, ("code", "requested_mcs", "bin_min"),
                   {(code, 3000, lo) for code in ORDER for lo in bins}, "Figure 5 cluster bins")
            for row in rows:
                hi = None if row["bin_max"] in ("", None) else integer(row, "bin_max")
                if hi != bins[row["bin_min"]] or row["cluster_count"] < 0:
                    raise PublicationError("Figure 5 cluster bins/counts are invalid")
        elif fig == 6:
            rows = tables[FILES[6][0]]
            _normalize(rows, ("requested_mcs", "seed"), ("x_A_nominal", "T_star", "binary_value", "comparator_value"))
            _exact(rows, ("comparator", "x_A_nominal", "T_star", "seed", "requested_mcs", "observable"),
                {(code, x, temp, 1, t, obs) for code in ORDER if code != BINARY
                 for x, temp in STATES for t in POINTS[1:] for obs in ("rho_AB", "c_A_B", "n_ex")},
                "Figure 6 correspondence")
            for row in rows:
                if row["observable"] == "n_ex":
                    finite(row, "binary_value", positive=True); finite(row, "comparator_value", positive=True)
                else:
                    _bounded([row], "binary_value"); _bounded([row], "comparator_value")
        elif fig == 7:
            rows = tables[FILES[7][0]]
            _normalize(rows, ("requested_mcs", "N", "candidate_events", "executed_events"))
            _exact(rows, ("code", "requested_mcs"), {(code, 30000) for code in ORDER}, "Figure 7 events")
            for row in rows:
                if row["candidate_events"] < row["executed_events"] or not row.get("counter_meaning"):
                    raise PublicationError("Figure 7 requires native counter meaning and selections >= executions")
                for raw, norm in (("candidate_events", "candidate_events_per_site"), ("executed_events", "executed_events_per_site")):
                    if not math.isclose(finite(row, norm, positive=True), row[raw] / row["N"], rel_tol=1e-10):
                        raise PublicationError(f"Figure 7 incorrect per-site normalization: {norm}")
        elif fig == 8:
            rows, fits = (tables[name] for name in FILES[8])
            _normalize(rows, ("requested_mcs",))
            _exact(rows, ("code", "requested_mcs"), {(code, t) for code in ORDER for t in POINTS}, "Figure 8 runtimes")
            _exact(fits, ("code",), {(code,) for code in ORDER}, "Figure 8 fits")
            for row in rows:
                expected = row["requested_mcs"] in FIT_POINTS
                if truth(row, "fit_included") != expected or truth(row, "displayed") != expected:
                    raise PublicationError("Figure 8 must display/fit exactly the 16 requested targets 30..30000")
                value = finite(row, "runtime_seconds", positive=row["requested_mcs"] > 0)
                if value < 0:
                    raise PublicationError("Negative cumulative evolution runtime")
            for fit in fits:
                if finite(fit, "fit_mcs_min") != 30 or finite(fit, "fit_mcs_max") != 30000:
                    raise PublicationError("Figure 8 fit interval must be 30..30000")
                selected = [r for r in rows if r["code"] == fit["code"] and truth(r, "fit_included")]
                _fit_check(selected, fit, "requested_mcs", "runtime_seconds", "gamma", 16)
        elif fig == 9:
            rows = tables[FILES[9][0]]
            _normalize(rows, ("requested_mcs", "seed"), ("x_A_nominal", "T_star"))
            _exact(rows, ("code", "x_A_nominal", "T_star", "seed", "requested_mcs"),
                {(code, x, temp, 1, 30000) for code in ORDER for x, temp in STATES}, "Figure 9 runtime screen")
            for row in rows:
                finite(row, "runtime_seconds", positive=True)
        elif fig == 10:
            rows, fits = (tables[name] for name in FILES[10])
            _normalize(rows, ("k", "N", "n", "expected_n", "requested_mcs"))
            _exact(rows, ("code", "k", "N", "n", "expected_n", "requested_mcs"),
                {(code, k, nsites, n, n, 30000) for code in ORDER for k, nsites, n in SIZES}, "Figure 10 ensembles")
            _exact(fits, ("code",), {(code,) for code in ORDER}, "Figure 10 fits")
            for row in rows:
                finite(row, "mean_seconds", positive=True)
                if row["n"] == 1:
                    if row.get("sem_seconds") not in ("", None) or row.get("sample_sd_seconds") not in ("", None):
                        raise PublicationError("Figure 10 n=1 SEM and sample SD must be unavailable, not zero")
                else:
                    sd, sem = finite(row, "sample_sd_seconds"), finite(row, "sem_seconds")
                    if min(sd, sem) < 0 or not math.isclose(sem, sd / math.sqrt(row["n"]), rel_tol=1e-9, abs_tol=1e-12):
                        raise PublicationError("Figure 10 requires sample SEM=sample SD/sqrt(n)")
            for fit in fits:
                _fit_check([r for r in rows if r["code"] == fit["code"]], fit, "N", "mean_seconds", "alpha", 4)


def _validate_configurations(data):
    configs = data["configurations"]
    expected = {(code, 3000) for code in ORDER} | {(BINARY, 0), (BINARY, 30000)}
    actual = [(item["code"], integer(item, "requested_mcs")) for item in configs]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise PublicationError("Figure 5 requires exactly thirteen distinct configuration instances")
    for item in configs:
        if len(item.get("periods", [])) != 3 or len(set(item["periods"])) != 1:
            raise PublicationError("Figure 5 requires an explicit cubic periodic FCC box")
        if not data["manifest"].get("test_only") and item["periods"] != [64, 64, 64]:
            raise PublicationError("Figure 5 requires k=6 half-cell periods [64,64,64]")
        for species in ("a", "b"):
            path = safe_path(data["root"], item[f"{species}_xyz"])
            if not path.is_file() or sha256(path) != item.get(f"{species}_sha256"):
                raise PublicationError(f"Missing/stale morphology configuration: {path}")
            coords = read_xyz(path)
            if len(coords) != integer(item, f"N_{species.upper()}"):
                raise PublicationError(f"Configuration species count mismatch: {path}")
            if np.any(coords < 0) or np.any(coords >= np.array(item["periods"])):
                raise PublicationError(f"Coordinates outside canonical box: {path}")


def read_xyz(path):
    with path.open(encoding="utf-8") as stream:
        try:
            count = int(next(stream).strip())
            next(stream)
            coords = np.asarray([[float(x) for x in line.split()[1:4]] for line in stream if line.strip()], dtype=float)
        except (ValueError, StopIteration) as error:
            raise PublicationError(f"Invalid XYZ configuration: {path}") from error
    if coords.shape != (count, 3) or not np.all(np.isfinite(coords)):
        raise PublicationError(f"Invalid XYZ dimensions or coordinates: {path}")
    return coords
