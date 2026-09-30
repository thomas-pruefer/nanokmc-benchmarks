from __future__ import annotations

from pathlib import Path
import csv
import json

from adapters.base_adapter import CodeAdapter
from benchmark_core.scenario import BenchmarkScenario
from benchmark_core.timing import run_timed, msys2_runtime_env, explain_windows_returncode
from benchmark_core.metrics import (
    analyze_snapshot,
    cluster_distribution_rows,
    snapshot_manifest_row,
    write_metrics_csv,
    write_rows_csv,
    write_timing_csv,
)
from benchmark_core.snapshots import read_kmc_lattice_bin, write_rasmol_xyz


class KMCLatticeAdapter(CodeAdapter):
    """Frozen KMC_Lattice v2.1.0 selective FCC manuscript comparator."""

    COMMON_MCS_PER_NATIVE_TIME = 6.0
    VALID_MODES = {"selective"}

    def __init__(self, paths: dict, mode: str = "selective"):
        super().__init__(paths)
        if mode not in self.VALID_MODES:
            raise ValueError(f"Unsupported KMC_Lattice mode: {mode}")
        self.mode = mode
        self.name = f"kmc_lattice_{mode}"
        self.repo_root = Path(__file__).resolve().parents[1]

    def _root(self) -> Path:
        return Path(self.paths.get("kmc_lattice_root", self.repo_root / "sources" / "kmc_lattice"))

    def _exe(self) -> Path:
        configured = self.paths.get("kmc_lattice_exe")
        if configured:
            return Path(configured)
        return self.repo_root / "build" / "kmc_lattice" / "kmc_lattice_fcc.exe"

    def _validate_runtime(self) -> None:
        missing = []
        root = self._root()
        exe = self._exe()
        if not (root / "src" / "Simulation.cpp").exists():
            missing.append(f"KMC_Lattice v2.1.0 source checkout: {root}")
        if not exe.exists():
            missing.append(f"benchmark KMC_Lattice executable: {exe}")
        if missing:
            raise FileNotFoundError(
                "KMC_Lattice runtime is not ready:\n  - " + "\n  - ".join(missing)
                + "\nRun scripts/windows/build_all.bat first."
            )

    def prepare(self, scenario: BenchmarkScenario, seed: int, run_dir: Path) -> None:
        if scenario.geometry.lower() != "fcc" or scenario.dimension != 3:
            raise ValueError("KMC_Lattice adapter currently supports only the 3D FCC benchmark")
        self._validate_runtime()
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "scenario_id": scenario.id,
            "seed": int(seed),
            "fcc_cells": int(scenario.spparks_fcc_cells),
            "composition_A": float(scenario.composition_A),
            "kT": float(scenario.kT),
            "Ea": float(scenario.Ea),
            "mcs_points": [int(x) for x in scenario.mcs_points],
            "common_mcs_per_native_time": self.COMMON_MCS_PER_NATIVE_TIME,
            "external_root": str(self._root()),
            "executable": str(self._exe()),
            "recalc_mode": self.mode,
            "physics_equivalence": "exact",
        }
        (run_dir / "kmc_lattice_run_config.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def run(self, scenario: BenchmarkScenario, seed: int, run_dir: Path) -> None:
        points = ",".join(str(int(x)) for x in scenario.mcs_points)
        cmd = [
            str(self._exe()),
            "--fcc-cells", str(int(scenario.spparks_fcc_cells)),
            "--composition-a", str(float(scenario.composition_A)),
            "--kt", str(float(scenario.kT)),
            "--ea", str(float(scenario.Ea)),
            "--seed", str(int(seed)),
            "--mcs-points", points,
            "--recalc-mode", self.mode,
            "--output-dir", str(run_dir),
        ]
        result = run_timed(cmd, cwd=run_dir, env=msys2_runtime_env(self.paths))
        (run_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8", errors="ignore")
        (run_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8", errors="ignore")
        external = {
            "returncode": result.returncode,
            "wall_seconds": result.wall_seconds,
            "command": cmd,
            "launch_mode": "native_cpp_subprocess",
        }
        (run_dir / "external_timing.json").write_text(json.dumps(external, indent=2), encoding="utf-8")
        if result.returncode != 0:
            hint = explain_windows_returncode(result.returncode)
            extra = f" ({hint})" if hint else ""
            raise RuntimeError(
                f"KMC_Lattice run failed with return code {result.returncode}{extra}. "
                f"See {run_dir / 'stderr.txt'} and {run_dir / 'stdout.txt'}"
            )
        if not (run_dir / "progress.csv").exists():
            raise RuntimeError("KMC_Lattice subprocess returned success but progress.csv is missing")

    def collect(self, scenario: BenchmarkScenario, seed: int, run_dir: Path, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        with (run_dir / "progress.csv").open(newline="", encoding="utf-8") as f:
            checkpoints = list(csv.DictReader(f))
        external = json.loads((run_dir / "external_timing.json").read_text(encoding="utf-8"))
        if len(checkpoints) != len(scenario.mcs_points):
            raise ValueError(
                f"KMC_Lattice checkpoint count mismatch: expected {len(scenario.mcs_points)}, got {len(checkpoints)}"
            )

        total_sites = 4 * scenario.spparks_fcc_cells ** 3
        eval_dir = run_dir / "evaluation" / "Rasmol"
        eval_dir.mkdir(parents=True, exist_ok=True)
        rows: list[dict] = []
        timing_rows: list[dict] = []
        manifest_rows: list[dict] = []
        distribution_rows: list[dict] = []
        reference_counts: tuple[int, int] | None = None

        for idx, (point, checkpoint) in enumerate(zip(scenario.mcs_points, checkpoints)):
            requested = float(checkpoint["requested_mcs"])
            if int(round(requested)) != int(point):
                raise ValueError(
                    f"KMC_Lattice checkpoint mismatch at index {idx}: scenario={point}, checkpoint={requested}"
                )
            native_last_event_time = float(checkpoint["native_last_event_time"])
            native_observation_time = float(checkpoint["requested_native_time"])
            native_events = int(checkpoint["executed_moves"])
            snap = read_kmc_lattice_bin(
                run_dir / checkpoint["snapshot"],
                fcc_cells=scenario.spparks_fcc_cells,
                requested_common_mcs=float(point),
                native_last_event_time=native_last_event_time,
                native_events=native_events,
            )
            analysis = analyze_snapshot(snap)
            post = analysis.metrics
            counts = (analysis.validation.count_A, analysis.validation.count_B)
            if reference_counts is None:
                reference_counts = counts
            elif counts != reference_counts:
                raise ValueError(
                    f"KMC_Lattice composition changed: initial A/B={reference_counts}, point {point}={counts}"
                )
            if counts[0] != int(checkpoint["N_A"]):
                raise ValueError(
                    f"KMC_Lattice snapshot/progress A-count mismatch at {point}: "
                    f"snapshot={counts[0]}, progress={checkpoint['N_A']}"
                )

            write_rasmol_xyz(snap, eval_dir, label_step=int(point))
            manifest = snapshot_manifest_row(snap, analysis)
            manifest.update({
                "scenario_id": scenario.id,
                "code": self.name,
                "seed": seed,
                "save_index": idx,
                "requested_common_mcs": point,
                "native_observation_time": native_observation_time,
                "native_last_event_time": native_last_event_time,
            })
            manifest_rows.append(manifest)
            distribution_rows.extend(cluster_distribution_rows(
                analysis,
                scenario_id=scenario.id,
                code=self.name,
                seed=seed,
                save_index=idx,
                native_step=native_events,
            ))

            evolution_wall = float(checkpoint["evolution_wall_seconds_cumulative"])
            active_bonds = int(checkpoint["active_bonds"])
            total_rate = float(checkpoint["total_rate"])
            rows.append({
                "scenario_id": scenario.id,
                "code": self.name,
                "method_label": {
                    "selective": "KMC_Lattice FRM scheduling + local BKL pathway selection + selective recalculation",
                }[self.mode],
                "seed": seed,
                "geometry": "fcc",
                "coordination": 12,
                "total_sites": total_sites,
                "composition_A": scenario.composition_A,
                "kT": scenario.kT,
                "Ea": scenario.Ea,
                "save_index": idx,
                "requested_mcs": point,
                "native_step": native_events,
                "native_mcs": "",
                "native_simulation_time": native_observation_time,
                "kmc_lattice_native_last_event_time": native_last_event_time,
                "common_mcs_equivalent": float(point),
                "physical_time_seconds": "",
                "attempted_exchanges_total": "",
                "accepted_exchanges_total": native_events,
                "accepted_exchanges_per_site": native_events / total_sites if total_sites else "",
                "code_kmc_time": native_observation_time,
                "kmc_lattice_active_bonds_native": active_bonds,
                "kmc_lattice_total_rate_native": total_rate,
                "interface_density_native": "",
                "cluster_count_native": "",
                "average_cluster_size_native": "",
                "largest_cluster_size_native": "",
                "largest_cluster_fraction_native": "",
                "calculation_cpu_seconds_cumulative": "",
                "evolution_wall_seconds_cumulative": evolution_wall,
                "diagnostic_runtime_seconds_cumulative": evolution_wall,
                "diagnostic_runtime_source": "KMC_Lattice C++ steady_clock around event evolution only (wall time)",
                "spparks_loop_wall_seconds_total_run": "",
                "calculation_wall_seconds_total_run": float(external.get("wall_seconds", 0.0)),
                "cumulative_total_seconds_native": evolution_wall,
                "cumulative_compute_seconds_native": evolution_wall,
                "cumulative_wall_seconds_external": "",
                **post,
            })
            timing_rows.append({
                "scenario_id": scenario.id,
                "code": self.name,
                "seed": seed,
                "save_index": idx,
                "progress_native_mcs": "",
                "progress_native_simulation_time": native_observation_time,
                "progress_native_last_event_time": native_last_event_time,
                "progress_common_mcs_equivalent": float(point),
                "progress_accepted_events_native": native_events,
                "progress_accepted_per_site": native_events / total_sites if total_sites else "",
                "external_wall_seconds_total_run": float(external.get("wall_seconds", 0.0)),
                "native_total_seconds_cumulative": evolution_wall,
                "native_compute_seconds_cumulative": evolution_wall,
                "diagnostic_runtime_seconds_cumulative": evolution_wall,
                "diagnostic_runtime_source": "KMC_Lattice C++ steady_clock around event evolution only (wall time)",
                "timing_source": (
                    "C++ steady_clock around KMC_Lattice event evolution only; initialization, checkpoint analysis, "
                    "and snapshot I/O excluded. Build identity and flags are recorded in build/build_manifest.json."
                ),
            })

        write_metrics_csv(rows, out_dir / "metrics_common.csv")
        write_timing_csv(timing_rows, out_dir / "timing_common.csv")
        write_rows_csv(manifest_rows, out_dir / "snapshot_manifest_common.csv")
        write_rows_csv(distribution_rows, out_dir / "cluster_distribution_common.csv")
        (out_dir / "run_metadata.json").write_text(json.dumps({
            "adapter": self.name,
            "upstream": "KMC_Lattice v2.1.0",
            "external_root": str(self._root()),
            "executable": str(self._exe()),
            "recalc_mode": self.mode,
            "physics_equivalence": "exact",
            "run_dir": str(run_dir),
            "external_timing": external,
            "algorithm": {
                "global_selection": "first-reaction absolute event times; upstream chooseNextEvent() min_element",
                "local_selection": "upstream determinePathway() BKL choice among one A object's B destinations",
                "recalculation": "upstream selective recalculation, cutoff 3 doubled-grid units",
                "representation": "A=Object, B=empty physical FCC parity site",
            },
            "common_time": {
                "common_mcs_per_native_time": self.COMMON_MCS_PER_NATIVE_TIME,
                "derivation": (
                    "Each undirected A-B FCC bond is represented exactly once, from its A endpoint, with rate P. "
                    "The corresponding classical bond is proposed at rate 1/6 per MCS, so common_MCS=6*t_native."
                ),
            },
            "common_snapshot": {
                "coordinate_convention": "integer doubled-FCC/parity-grid coordinates",
                "species_mapping": "A=occupied KMC_Lattice object, B=empty physical parity site",
                "periods": [2 * scenario.spparks_fcc_cells] * 3,
                "topology": "periodic FCC, 12 neighbors/site",
            },
            "timing_status": "native evolution timer; build identity attached by verified harness",
        }, indent=2), encoding="utf-8")

