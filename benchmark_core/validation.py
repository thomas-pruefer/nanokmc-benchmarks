"""Strict native-output validation shared by smoke and manuscript completion."""
from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path

from benchmark_core.scenario import BenchmarkScenario


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def validate_collection(scenario: BenchmarkScenario, code: str, seed: int, out_dir: Path) -> dict:
    """Reject missing, duplicate, malformed or scientifically invalid observations."""
    rows = _rows(out_dir / "metrics_common.csv")
    snapshots = _rows(out_dir / "snapshot_manifest_common.csv")
    timings = _rows(out_dir / "timing_common.csv")
    expected = scenario.mcs_points
    observed = [float(row["requested_mcs"]) for row in rows]
    if observed != expected:
        raise ValueError(f"Checkpoint sequence mismatch: {observed} != {expected}")
    if len(snapshots) != len(expected) or len(timings) != len(expected):
        raise ValueError("Missing snapshot validation or native timer records")
    expected_sites = scenario.nanokmc_total_sites_estimate
    counts = None
    previous_executed = 0
    previous_candidates = 0
    previous_runtime = 0.0
    previous_realized = 0.0
    snapshot_ordinals = set()
    summary = []
    for index, (point, row, snapshot) in enumerate(zip(expected, rows, snapshots)):
        for record in (snapshot, timings[index]):
            if (record.get("code") != code or record.get("scenario_id") != scenario.id
                    or int(record.get("seed", -1)) != seed or int(record.get("save_index", -1)) != index):
                raise ValueError("Snapshot/timer identity or observation index mismatch")
        for error_field in ("postprocess_error", "postprocess_warning"):
            if row.get(error_field):
                raise ValueError(row[error_field])
        if row["code"] != code or int(row["seed"]) != seed or row["scenario_id"] != scenario.id:
            raise ValueError("Run identity mismatch")
        if int(row["save_index"]) != index or int(snapshot["save_index"]) != index:
            raise ValueError("Snapshot index mismatch")
        if int(row["total_sites"]) != expected_sites or int(snapshot["total_sites"]) != expected_sites:
            raise ValueError("Incorrect FCC system size")
        for field in ("topology_valid", "parity_valid"):
            if int(snapshot[field]) != 1:
                raise ValueError(f"Failed topology check: {field}")
        if int(snapshot["min_neighbors"]) != 12 or int(snapshot["max_neighbors"]) != 12:
            raise ValueError("FCC coordination is not twelve")
        if int(snapshot["unique_coordinates"]) != expected_sites or int(snapshot["unique_site_ids"]) != expected_sites:
            raise ValueError("Duplicate/missing sites")
        current_counts = (int(row["N_A_common"]), int(row["N_B_common"]))
        if sum(current_counts) != expected_sites or current_counts != (int(snapshot["N_A"]), int(snapshot["N_B"])):
            raise ValueError("Species/site count mismatch")
        if counts is None:
            counts = current_counts
        elif counts != current_counts:
            raise ValueError("Species conservation failure")
        if int(row["total_undirected_bonds_common"]) != 6 * expected_sites:
            raise ValueError("Wrong periodic FCC bond population")
        rho = float(row["interface_density_common"])
        if not math.isfinite(rho) or not 0 <= rho <= 1:
            raise ValueError("Invalid interface fraction")
        runtime = float(row["diagnostic_runtime_seconds_cumulative"])
        if float(timings[index]["diagnostic_runtime_seconds_cumulative"]) != runtime:
            raise ValueError("Timer and metric native cumulative runtime disagree")
        if not math.isfinite(runtime) or runtime < previous_runtime or (point > 0 and runtime <= 0):
            raise ValueError("Missing/nonpositive/nonmonotone native evolution runtime")
        if not row["diagnostic_runtime_source"]:
            raise ValueError("Native timer source missing")
        realized = float(row["common_mcs_equivalent"])
        if not math.isfinite(realized) or realized < previous_realized:
            raise ValueError("Invalid realized progress")
        if float(timings[index]["progress_common_mcs_equivalent"]) != realized:
            raise ValueError("Timer and metric realized progress disagree")
        if code.startswith("spparks_"):
            ordinal = int(snapshot["snapshot_ordinal"])
            if ordinal in snapshot_ordinals:
                raise ValueError("A native SPPARKS snapshot was reused for multiple observations")
            snapshot_ordinals.add(ordinal)
            # Both preserved fields come from the same native output formatting;
            # this compares association, not a new physical overshoot tolerance.
            if float(snapshot["native_time_from_snapshot"]) != float(row["native_simulation_time"]):
                raise ValueError("SPPARKS snapshot and native stats time do not match")
        elif float(snapshot["native_step"]) != point:
            raise ValueError("Snapshot requested checkpoint does not match its observation")
        if not code.startswith("nanokmc_"):
            native_observation = float(row["native_simulation_time"])
            if not math.isfinite(native_observation) or not math.isclose(
                6.0 * native_observation, realized, rel_tol=1e-12, abs_tol=1e-9
            ):
                raise ValueError("Native observation/common-MCS factor-six mapping mismatch")
        executed = float(row["spparks_naccept_native"] if code.startswith("spparks_") else row["accepted_exchanges_total"])
        candidates = float(row["attempted_exchanges_total"]) if row.get("attempted_exchanges_total") else executed
        if not all(math.isfinite(value) and value.is_integer() for value in (executed, candidates)):
            raise ValueError("Missing/noninteger event counters")
        if executed < previous_executed or candidates < previous_candidates or candidates < executed:
            raise ValueError("Invalid cumulative event counts")
        if point > 0 and executed <= 0:
            raise ValueError("No executed exchanges at positive observation")
        if point == 0 and (executed != 0 or candidates != 0 or realized != 0):
            raise ValueError("Initial observation must have zero evolution progress and events")
        if code in ("kmcos_otf", "kmc_lattice_selective"):
            native_last_field = "kmcos_native_last_event_time" if code == "kmcos_otf" else "kmc_lattice_native_last_event_time"
            if (float(snapshot["native_counter_from_snapshot"]) != executed
                    or float(snapshot["requested_common_mcs"]) != point
                    or float(snapshot["native_observation_time"]) != float(row["native_simulation_time"])
                    or float(snapshot["native_time_from_snapshot"]) != float(row[native_last_field])
                    or float(snapshot["native_last_event_time"]) != float(row[native_last_field])):
                raise ValueError("External event counter, requested target or last-event time mismatch")
        previous_realized = realized
        previous_runtime, previous_executed, previous_candidates = runtime, executed, candidates
        summary.append({"requested_mcs": point, "realized_common_mcs": realized,
                        "native_runtime_seconds": runtime, "executed_exchanges": int(executed),
                        "candidate_or_selected_events": int(candidates), "rho_AB": rho})
    distributions = _rows(out_dir / "cluster_distribution_common.csv")
    cluster_keys = set()
    for record in distributions:
        if (record.get("code") != code or record.get("scenario_id") != scenario.id
                or int(record.get("seed", -1)) != seed):
            raise ValueError("Cluster record identity mismatch")
        index, size, count = (float(record[key]) for key in ("save_index", "cluster_size", "cluster_count"))
        if not all(math.isfinite(value) and value.is_integer() for value in (index, size, count)):
            raise ValueError("Cluster index/size/count must be finite integers")
        if not 0 <= index < len(expected) or size < 1 or count < 1 or record["common_species"] not in ("A", "B"):
            raise ValueError("Invalid cluster distribution entry")
        key = (int(index), record["common_species"], int(size))
        if key in cluster_keys:
            raise ValueError("Duplicate cluster distribution entry")
        cluster_keys.add(key)
        if float(record["sites_in_clusters"]) != size * count:
            raise ValueError("Cluster mass field disagrees with size times count")
    for index in range(len(expected)):
        for species, number in zip(("A", "B"), counts):
            cluster_sites = sum(int(r["cluster_size"]) * int(r["cluster_count"]) for r in distributions
                                if int(r["save_index"]) == index and r["common_species"] == species)
            if cluster_sites != number:
                raise ValueError("Cluster distribution does not account for all conserved species")
    return {"status": "PASS", "total_sites": expected_sites, "N_A": counts[0], "N_B": counts[1],
            "observations": summary, "checks": ["all_requested_observations", "periodic_FCC_12_neighbors",
            "unique_sites", "species_conservation", "6N_bonds", "cluster_mass", "finite_native_timers",
            "preserved_native_counters", "positive_evolution"]}
