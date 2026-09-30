from __future__ import annotations

from pathlib import Path
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
from benchmark_core.snapshots import read_kmcos_otf_npz, write_rasmol_xyz


class KmcosOTFAdapter(CodeAdapter):
    """Adapter for the validated kmcos OTF FCC Kawasaki model.

    kmcos runs in a dedicated Python 3.10 environment because the frozen
    comparator release uses NumPy F2PY/distutils machinery. The main benchmark
    harness remains on its normal Python environment and communicates through
    JSON + compressed configuration snapshots.
    """

    name = "kmcos_otf"
    COMMON_MCS_PER_NATIVE_TIME = 6.0

    def __init__(self, paths: dict):
        super().__init__(paths)
        self.repo_root = Path(__file__).resolve().parents[1]

    def _python(self) -> Path:
        return Path(self.paths["kmcos_python"])

    def _kmcos_root(self) -> Path:
        return Path(self.paths.get("kmcos_root", self.repo_root / "sources" / "kmcos"))

    def _compiled_src(self) -> Path:
        configured = self.paths.get("kmcos_compiled_src")
        if configured:
            return Path(configured)
        return self.repo_root / "build" / "kmcos" / "fcc_kawasaki_otf_otf" / "src"

    def _msys2_ucrt_bin(self) -> Path:
        configured = self.paths.get("kmcos_msys2_ucrt_bin")
        if configured:
            return Path(configured)
        return Path(self.paths.get("msys2_root", "C:/msys64")) / "ucrt64" / "bin"

    def _validate_runtime(self) -> None:
        python = self._python()
        kmcos_root = self._kmcos_root()
        compiled_src = self._compiled_src()
        msys_bin = self._msys2_ucrt_bin()
        missing = []
        if not python.exists():
            missing.append(f"dedicated kmcos Python 3.10: {python}")
        if not (kmcos_root / "kmcos" / "__init__.py").exists():
            missing.append(f"kmcos checkout: {kmcos_root}")
        if not msys_bin.exists():
            missing.append(f"MSYS2 UCRT64 bin: {msys_bin}")
        search_dirs = [compiled_src, compiled_src.parent]
        if not any(list(d.glob("kmc_model*.pyd")) for d in search_dirs if d.exists()):
            missing.append(
                "compiled kmcos model kmc_model*.pyd under "
                + " or ".join(str(d) for d in search_dirs)
            )
        if not any((d / "kmc_settings.py").exists() for d in search_dirs if d.exists()):
            missing.append(
                "generated kmc_settings.py under "
                + " or ".join(str(d) for d in search_dirs)
            )
        if missing:
            raise FileNotFoundError(
                "kmcos OTF runtime is not ready:\n  - " + "\n  - ".join(missing)
                + "\nRun 03_build_all.bat first."
            )

    def prepare(self, scenario: BenchmarkScenario, seed: int, run_dir: Path) -> None:
        if scenario.geometry.lower() != "fcc" or scenario.dimension != 3:
            raise ValueError("kmcos OTF adapter currently supports only the 3D FCC benchmark")
        self._validate_runtime()
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "snapshots").mkdir(parents=True, exist_ok=True)
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
        }
        (run_dir / "kmcos_run_config.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def run(self, scenario: BenchmarkScenario, seed: int, run_dir: Path) -> None:
        runner = self.repo_root / "models" / "kmcos" / "run_kmcos_otf_scenario.py"
        cmd = [
            str(self._python()),
            str(runner),
            "--config", str(run_dir / "kmcos_run_config.json"),
            "--kmcos-root", str(self._kmcos_root()),
            "--compiled-src", str(self._compiled_src()),
            "--msys2-ucrt-bin", str(self._msys2_ucrt_bin()),
        ]
        result = run_timed(cmd, cwd=run_dir, env=msys2_runtime_env(self.paths))
        (run_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8", errors="ignore")
        (run_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8", errors="ignore")
        external = {
            "returncode": result.returncode,
            "wall_seconds": result.wall_seconds,
            "launch_mode": "dedicated_python310_subprocess",
            "command": cmd,
        }
        (run_dir / "external_timing.json").write_text(json.dumps(external, indent=2), encoding="utf-8")
        if result.returncode != 0:
            hint = explain_windows_returncode(result.returncode)
            extra = f" ({hint})" if hint else ""
            raise RuntimeError(
                f"kmcos OTF run failed with return code {result.returncode}{extra}. "
                f"See {run_dir / 'stderr.txt'} and {run_dir / 'stdout.txt'}"
            )
        if not (run_dir / "kmcos_progress.json").exists():
            raise RuntimeError("kmcos subprocess returned success but kmcos_progress.json is missing")

    def collect(self, scenario: BenchmarkScenario, seed: int, run_dir: Path, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        progress = json.loads((run_dir / "kmcos_progress.json").read_text(encoding="utf-8"))
        external = json.loads((run_dir / "external_timing.json").read_text(encoding="utf-8"))
        checkpoints = progress.get("checkpoints", [])
        if len(checkpoints) != len(scenario.mcs_points):
            raise ValueError(
                f"kmcos checkpoint count mismatch: expected {len(scenario.mcs_points)}, got {len(checkpoints)}"
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
            if int(round(float(checkpoint["requested_mcs"]))) != int(point):
                raise ValueError(
                    f"kmcos checkpoint mismatch at index {idx}: scenario={point}, checkpoint={checkpoint['requested_mcs']}"
                )
            snap_path = run_dir / checkpoint["snapshot"]
            snap = read_kmcos_otf_npz(
                snap_path,
                fcc_cells=scenario.spparks_fcc_cells,
                requested_common_mcs=float(point),
                native_last_event_time=float(checkpoint["native_last_event_time"]),
                native_kmc_step=int(checkpoint["native_kmc_step"]),
            )
            analysis = analyze_snapshot(snap)
            post = analysis.metrics
            counts = (analysis.validation.count_A, analysis.validation.count_B)
            if reference_counts is None:
                reference_counts = counts
            elif counts != reference_counts:
                raise ValueError(
                    f"kmcos composition changed across snapshots: initial A/B={reference_counts}, point {point}={counts}"
                )
            if counts[0] != int(checkpoint["N_A"]):
                raise ValueError(
                    f"kmcos snapshot/progress A-count mismatch at {point}: snapshot={counts[0]}, progress={checkpoint['N_A']}"
                )

            write_rasmol_xyz(snap, eval_dir, label_step=int(point))
            manifest = snapshot_manifest_row(snap, analysis)
            manifest.update({
                "scenario_id": scenario.id,
                "code": self.name,
                "seed": seed,
                "save_index": idx,
                "requested_common_mcs": point,
                "native_observation_time": float(checkpoint["requested_native_observation_time"]),
                "native_last_event_time": float(checkpoint["native_last_event_time"]),
            })
            manifest_rows.append(manifest)
            distribution_rows.extend(cluster_distribution_rows(
                analysis,
                scenario_id=scenario.id,
                code=self.name,
                seed=seed,
                save_index=idx,
                native_step=float(checkpoint["native_kmc_step"]),
            ))

            native_observation_time = float(checkpoint["requested_native_observation_time"])
            native_last_event_time = float(checkpoint["native_last_event_time"])
            native_step = int(checkpoint["native_kmc_step"])
            evolution_wall = float(checkpoint["evolution_wall_seconds_cumulative"])

            rows.append({
                "scenario_id": scenario.id,
                "code": self.name,
                "method_label": "kmcos OTF rejection-free active-event KMC",
                "seed": seed,
                "geometry": "fcc",
                "coordination": 12,
                "total_sites": total_sites,
                "composition_A": scenario.composition_A,
                "kT": scenario.kT,
                "Ea": scenario.Ea,
                "save_index": idx,
                "requested_mcs": point,
                "native_step": native_step,
                "native_mcs": "",
                "native_simulation_time": native_observation_time,
                "kmcos_native_last_event_time": native_last_event_time,
                "common_mcs_equivalent": float(point),
                "physical_time_seconds": "",
                "attempted_exchanges_total": "",
                "accepted_exchanges_total": native_step,
                "accepted_exchanges_per_site": native_step / total_sites if total_sites else "",
                "code_kmc_time": native_observation_time,
                "interface_density_native": "",
                "cluster_count_native": "",
                "average_cluster_size_native": "",
                "largest_cluster_size_native": "",
                "largest_cluster_fraction_native": "",
                "calculation_cpu_seconds_cumulative": "",
                "evolution_wall_seconds_cumulative": evolution_wall,
                "diagnostic_runtime_seconds_cumulative": evolution_wall,
                "diagnostic_runtime_source": "kmcos Python perf_counter around do_steps_time only (evolution wall time)",
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
                "progress_accepted_events_native": native_step,
                "progress_accepted_per_site": native_step / total_sites if total_sites else "",
                "external_wall_seconds_total_run": float(external.get("wall_seconds", 0.0)),
                "native_total_seconds_cumulative": evolution_wall,
                "native_compute_seconds_cumulative": evolution_wall,
                "diagnostic_runtime_seconds_cumulative": evolution_wall,
                "diagnostic_runtime_source": "kmcos Python perf_counter around do_steps_time only (evolution wall time)",
                "timing_source": (
                    "kmcos Python perf_counter around do_steps_time only; "
                    "Fortran O3 compiler/build identity recorded in build/build_manifest.json"
                ),
            })

        write_metrics_csv(rows, out_dir / "metrics_common.csv")
        write_timing_csv(timing_rows, out_dir / "timing_common.csv")
        write_rows_csv(manifest_rows, out_dir / "snapshot_manifest_common.csv")
        write_rows_csv(distribution_rows, out_dir / "cluster_distribution_common.csv")
        (out_dir / "run_metadata.json").write_text(json.dumps({
            "adapter": self.name,
            "backend": "otf",
            "run_dir": str(run_dir),
            "external_timing": external,
            "kmcos_progress": progress,
            "common_time": {
                "common_mcs_per_native_time": self.COMMON_MCS_PER_NATIVE_TIME,
                "derivation": (
                    "Each active undirected A-B bond has kmcos OTF rate P. In the classical FCC proposal, the same "
                    "undirected bond is proposed at rate 1/6 per MCS, so accepted rate is P/6 and common_MCS=6*t_kmcos."
                ),
            },
            "common_snapshot": {
                "coordinate_convention": "integer doubled-FCC/parity-grid coordinates",
                "species_mapping": "A=kmcos species integer 0, B=kmcos species integer 1",
                "periods": [2 * scenario.spparks_fcc_cells] * 3,
                "topology": "periodic FCC, 12 neighbors/site",
            },
            "timing_status": "native evolution timer; build identity attached by verified harness",
        }, indent=2), encoding="utf-8")
