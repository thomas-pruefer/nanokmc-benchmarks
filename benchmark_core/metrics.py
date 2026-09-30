from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections import Counter, deque
from statistics import median
import math
import pandas as pd

from benchmark_core.common_snapshot import (
    COMMON_A,
    COMMON_B,
    CanonicalSnapshot,
    SnapshotValidation,
    build_fcc_neighbors,
    validate_canonical_snapshot,
)


@dataclass(frozen=True)
class SnapshotAnalysis:
    metrics: dict
    cluster_sizes: dict[str, tuple[int, ...]]
    validation: SnapshotValidation


def _cluster_sizes_for_species(
    species: list[str],
    neighbors: list[list[int]],
    target: str,
) -> list[int]:
    visited = [False] * len(species)
    sizes: list[int] = []
    for i, label in enumerate(species):
        if visited[i] or label != target:
            continue
        q = deque([i])
        visited[i] = True
        size = 0
        while q:
            u = q.popleft()
            size += 1
            for v in neighbors[u]:
                if not visited[v] and species[v] == target:
                    visited[v] = True
                    q.append(v)
        sizes.append(size)
    return sizes


def analyze_snapshot(snapshot: CanonicalSnapshot) -> SnapshotAnalysis:
    """Calculate all common binary-FCC morphology metrics from one snapshot."""
    if not snapshot.sites:
        return SnapshotAnalysis(metrics={}, cluster_sizes={COMMON_A: (), COMMON_B: ()}, validation=validate_canonical_snapshot(snapshot))

    neighbors = build_fcc_neighbors(snapshot)
    validation = validate_canonical_snapshot(snapshot, neighbors=neighbors)
    species = [label for *_, label in snapshot.sites]

    total_bonds = sum(len(nbs) for nbs in neighbors) // 2
    interface_bonds = 0
    surface = {COMMON_A: 0, COMMON_B: 0}
    for i, nbs in enumerate(neighbors):
        has_unlike = False
        for j in nbs:
            if species[i] != species[j]:
                has_unlike = True
                if j > i:
                    interface_bonds += 1
        if has_unlike:
            surface[species[i]] += 1

    interface_density = interface_bonds / total_bonds if total_bonds else math.nan
    clusters = {
        COMMON_A: _cluster_sizes_for_species(species, neighbors, COMMON_A),
        COMMON_B: _cluster_sizes_for_species(species, neighbors, COMMON_B),
    }

    metrics = {
        "N_A_common": validation.count_A,
        "N_B_common": validation.count_B,
        "x_A_common": validation.composition_A,
        "x_B_common": validation.composition_B,
        "interface_bonds_common": interface_bonds,
        "interface_density_common": interface_density,
        "total_undirected_bonds_common": total_bonds,
        "surface_sites_A_common": surface[COMMON_A],
        "surface_sites_B_common": surface[COMMON_B],
    }

    for label in (COMMON_A, COMMON_B):
        sizes = clusters[label]
        n_species = validation.count_A if label == COMMON_A else validation.count_B
        n_clusters = len(sizes)
        monomers = sum(size == 1 for size in sizes)
        mean_size = (sum(sizes) / n_clusters) if n_clusters else 0.0
        median_size = float(median(sizes)) if sizes else 0.0
        largest = max(sizes) if sizes else 0
        largest_fraction = largest / n_species if n_species else 0.0
        metrics.update({
            f"clusters_{label}_common": n_clusters,
            f"monomers_{label}_common": monomers,
            f"monomer_fraction_{label}_common": monomers / n_species if n_species else 0.0,
            f"mean_cluster_size_{label}_common": mean_size,
            f"median_cluster_size_{label}_common": median_size,
            f"largest_cluster_{label}_common": largest,
            f"largest_cluster_fraction_{label}_common": largest_fraction,
        })

    return SnapshotAnalysis(
        metrics=metrics,
        cluster_sizes={COMMON_A: tuple(clusters[COMMON_A]), COMMON_B: tuple(clusters[COMMON_B])},
        validation=validation,
    )


def snapshot_manifest_row(snapshot: CanonicalSnapshot, analysis: SnapshotAnalysis) -> dict:
    v = analysis.validation
    return {
        "source_code": snapshot.source_code,
        "snapshot_ordinal": snapshot.snapshot_ordinal,
        "native_step": snapshot.step,
        "native_time_from_snapshot": snapshot.native_time,
        "native_counter_from_snapshot": snapshot.native_counter,
        "geometry": snapshot.geometry,
        "coordination": snapshot.coordination,
        "period_x": snapshot.periods[0],
        "period_y": snapshot.periods[1],
        "period_z": snapshot.periods[2],
        "total_sites": v.total_sites,
        "N_A": v.count_A,
        "N_B": v.count_B,
        "x_A": v.composition_A,
        "x_B": v.composition_B,
        "topology_valid": int(v.valid),
        "parity_valid": int(v.parity_valid),
        "min_neighbors": v.min_neighbors,
        "max_neighbors": v.max_neighbors,
        "unique_site_ids": v.unique_site_ids,
        "unique_coordinates": v.unique_coordinates,
        "expected_sites_from_periods": v.expected_sites_from_periods,
        "validation_message": v.message,
    }


def cluster_distribution_rows(
    analysis: SnapshotAnalysis,
    *,
    scenario_id: str,
    code: str,
    seed: int,
    save_index: int,
    native_step: float,
) -> list[dict]:
    rows: list[dict] = []
    for label in (COMMON_A, COMMON_B):
        counts = Counter(analysis.cluster_sizes[label])
        for cluster_size in sorted(counts):
            count = counts[cluster_size]
            rows.append({
                "scenario_id": scenario_id,
                "code": code,
                "seed": seed,
                "save_index": save_index,
                "native_step": native_step,
                "common_species": label,
                "cluster_size": cluster_size,
                "cluster_count": count,
                "sites_in_clusters": cluster_size * count,
            })
    return rows


def write_rows_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


write_metrics_csv = write_rows_csv
write_timing_csv = write_rows_csv
