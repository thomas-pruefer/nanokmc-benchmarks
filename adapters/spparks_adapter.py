from __future__ import annotations

from pathlib import Path
import json
import math
import re

from adapters.base_adapter import CodeAdapter
from benchmark_core.scenario import BenchmarkScenario
from benchmark_core.timing import (
    run_timed,
    msys2_runtime_env,
    explain_windows_returncode,
    windows_path_to_msys,
    msys2_bash_command,
)
from benchmark_core.metrics import (
    analyze_snapshot,
    cluster_distribution_rows,
    snapshot_manifest_row,
    write_metrics_csv,
    write_rows_csv,
    write_timing_csv,
)
from benchmark_core.snapshots import read_spparks_sites_dump, write_rasmol_xyz


class SPPARKSAdapter(CodeAdapter):
    """Three unmodified diffusion linear-hop manuscript selections."""

    def __init__(self, paths: dict, mode: str = "diffusion_tree"):
        super().__init__(paths)
        if mode not in {"diffusion_sweep_random", "diffusion_linear", "diffusion_tree"}:
            raise ValueError(f"Unsupported SPPARKS mode: {mode}")
        self.mode = mode
        self.name = "spparks_" + mode

    @property
    def method_label(self) -> str:
        return {
            "diffusion_sweep_random": "SPPARKS diffusion linear-hop sweep random (binary Kawasaki rejection KMC)",
            "diffusion_linear": "SPPARKS diffusion linear-hop linear solver (binary Kawasaki rejection-free KMC)",
            "diffusion_tree": "SPPARKS diffusion linear-hop tree solver (binary Kawasaki rejection-free KMC)",
        }[self.mode]

    @property
    def is_sweep_mode(self) -> bool:
        return self.mode == "diffusion_sweep_random"

    @property
    def spparks_application(self) -> str:
        return "diffusion"

    @property
    def common_mcs_per_native_time(self) -> float:
        return 6.0

    def spparks_temperature(self, scenario: BenchmarkScenario) -> float:
        if float(scenario.Ea) <= 0.0:
            raise ValueError("SPPARKS diffusion mapping requires Ea > 0")
        return 2.0 * float(scenario.kT) / float(scenario.Ea)

    # Run one additional common MCS beyond the final requested comparison point.
    # This is output orchestration only: it ensures SPPARKS crosses the final dump
    # target so the final morphology snapshot is emitted. Collected metrics
    # still stop at mcs_points.
    RUN_TAIL_COMMON_MCS = 1.0

    def _dump_interval(self, scenario: BenchmarkScenario) -> int:
        positive_points = [int(x) for x in scenario.mcs_points if int(x) > 0]
        if not positive_points:
            return 1
        # For loglinfreq output the base interval is the first positive target,
        # not the GCD.  Using the GCD would defeat the purpose and generate a
        # dense dump stream.
        if scenario.spparks_loglinfreq_n is not None:
            return max(1, positive_points[0])
        import math
        dump_interval = positive_points[0]
        for point in positive_points[1:]:
            dump_interval = math.gcd(dump_interval, point)
        return max(1, dump_interval)

    @staticmethod
    def _validate_loglinfreq_schedule(scenario: BenchmarkScenario) -> None:
        if scenario.spparks_loglinfreq_n is None:
            return
        nrepeat = int(scenario.spparks_loglinfreq_n)
        scale = float(scenario.spparks_loglinfreq_factor or 0.0)
        positive = [float(x) for x in scenario.mcs_points if float(x) > 0.0]
        if not positive:
            raise ValueError("SPPARKS loglinfreq scenario requires positive mcs_points")
        if nrepeat <= 0 or scale <= 1.0:
            raise ValueError(
                "SPPARKS loglinfreq requires spparks_loglinfreq_n > 0 "
                "and spparks_loglinfreq_factor > 1"
            )

        delta = positive[0]
        limit = positive[-1]
        generated: list[float] = []
        current = 0.0
        # Replicate SPPARKS Output::next_time(logfreq=2) in common-MCS units.
        while True:
            start = delta
            while current >= start * scale - 1.0e-12:
                start *= scale
            next_time = math.ceil((current - 1.0e-12) / start) * start
            if abs(next_time - current) <= 1.0e-12:
                next_time = current + start
            if int(round(next_time / start)) > nrepeat:
                next_time = start * scale
            if next_time > limit + 1.0e-9:
                break
            generated.append(next_time)
            current = next_time

        if len(generated) != len(positive) or any(
            abs(a - b) > 1.0e-8 for a, b in zip(generated, positive)
        ):
            raise ValueError(
                "Scenario mcs_points do not match the requested SPPARKS loglinfreq schedule. "
                f"Expected positive points {generated}, got {positive}."
            )

    def prepare(self, scenario: BenchmarkScenario, seed: int, run_dir: Path) -> None:
        self._validate_loglinfreq_schedule(scenario)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "snapshots").mkdir(parents=True, exist_ok=True)
        ncell = scenario.spparks_fcc_cells
        positive_points = [int(x) for x in scenario.mcs_points if int(x) > 0]
        max_common_mcs = max(positive_points) if positive_points else 0
        common_dump_interval = self._dump_interval(scenario)
        time_scale = self.common_mcs_per_native_time
        native_run_time = (max_common_mcs + self.RUN_TAIL_COMMON_MCS) / time_scale
        native_dump_interval = common_dump_interval / time_scale

        combined_dump = "snapshots/state_all.dump"
        lines = [
            "# Generated by nanokmc-benchmarks",
            "# SPPARKS 3D FCC two-component phase-separation benchmark",
            "# common A = SPPARKS site value 2; common B = SPPARKS site value 1",
            f"seed {seed}",
            "",
        ]
        lines.append("app_style diffusion linear hop")
        lines += [
            "",
            "dimension 3",
            "boundary p p p",
            "",
            "lattice fcc 1.0",
            f"region box block 0 {ncell} 0 {ncell} 0 {ncell}",
            "create_box box",
            "create_sites box value site 1",
            "",
            f"set site value 2 fraction {scenario.composition_A}",
            "",
        ]
        lines.append(f"temperature {self.spparks_temperature(scenario):.17g}")
        if self.mode == "diffusion_sweep_random":
            lines.append("sweep random")
        elif self.mode == "diffusion_linear":
            lines.append("solve_style linear")
        elif self.mode == "diffusion_tree":
            lines.append("solve_style tree")
        lines.append("diag_style energy")

        # Morphology-heavy campaigns can request many early snapshots and
        # progressively fewer late snapshots.  A fixed interval equal to the
        # GCD of all requested points would make SPPARKS write hundreds or
        # thousands of unnecessary full-lattice dumps.  SPPARKS supports the
        # same logarithmic-linear schedule natively for both stats and dumps.
        # The scenario still lists every requested common-MCS checkpoint, so
        # all adapters are collected on an identical comparison grid.
        if scenario.spparks_loglinfreq_n is not None:
            nrepeat = int(scenario.spparks_loglinfreq_n)
            factor = float(scenario.spparks_loglinfreq_factor or 0.0)
            if nrepeat <= 0 or factor <= 1.0:
                raise ValueError(
                    "SPPARKS loglinfreq requires spparks_loglinfreq_n > 0 "
                    "and spparks_loglinfreq_factor > 1"
                )
            lines += [
                f"stats {native_dump_interval:.15g} loglinfreq {nrepeat} {factor:.15g} tol 1.0e-8",
                f"dump d1 text {native_dump_interval:.15g} {combined_dump} id site x y z",
                f"dump_modify d1 loglinfreq {nrepeat} {factor:.15g} sort id tol 1.0e-8",
                f"run {native_run_time:.15g}",
            ]
        else:
            lines += [
                f"stats {native_dump_interval:.15g} tol 1.0e-8",
                f"dump d1 text {native_dump_interval:.15g} {combined_dump} id site x y z",
                "dump_modify d1 sort id",
                f"run {native_run_time:.15g}",
            ]
        (run_dir / "in.spparks").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def run(self, scenario: BenchmarkScenario, seed: int, run_dir: Path) -> None:
        exe = Path(self.paths["spparks_exe"])
        run_dir_msys = windows_path_to_msys(run_dir)
        exe_msys = windows_path_to_msys(exe)
        shell_cmd = f"cd '{run_dir_msys}' && '{exe_msys}' < in.spparks"
        cmd = msys2_bash_command(self.paths, shell_cmd)

        result = run_timed(cmd, cwd=run_dir, env=msys2_runtime_env(self.paths))
        (run_dir / "log.wrapper.stdout.txt").write_text(result.stdout, encoding="utf-8", errors="ignore")
        (run_dir / "log.wrapper.stderr.txt").write_text(result.stderr, encoding="utf-8", errors="ignore")
        (run_dir / "external_timing.json").write_text(json.dumps({
            "returncode": result.returncode,
            "wall_seconds": result.wall_seconds,
            "launch_mode": "msys2_bash",
            "command": cmd,
        }, indent=2), encoding="utf-8")

        if result.returncode != 0:
            hint = explain_windows_returncode(result.returncode)
            extra = f" ({hint})" if hint else ""
            raise RuntimeError(f"SPPARKS failed with return code {result.returncode}{extra}. See {run_dir}")

    def _parse_log_timing(self, run_dir: Path) -> dict:
        """Parse native stats by header; CPU records cumulative iteration wall time.

        This manuscript timer includes previous in-loop observations and is sampled
        after energy diagnostics, before the same-checkpoint dump. Final tail Loop
        time remains a separate diagnostic; no output overhead is subtracted.
        """
        log_path = run_dir / "log.spparks"
        out = {"stats_rows": []}
        if not log_path.exists():
            return out

        text = log_path.read_text(encoding="utf-8", errors="ignore")
        out["log_spparks_exists"] = True
        m = re.findall(r"Loop time of\s+([0-9.eE+-]+)", text)
        if m:
            vals = [float(x) for x in m]
            out["loop_times"] = vals
            out["loop_time_final"] = vals[-1]

        lines = text.splitlines()
        header_index = None
        header = []
        for idx, line in enumerate(lines):
            cols = line.split()
            if "Time" in cols and "CPU" in cols and "Naccept" in cols:
                header_index = idx
                header = cols
                break

        if header_index is None:
            out["stats_parse_warning"] = "SPPARKS stats header not found"
            return out

        out["stats_header"] = header
        for line in lines[header_index + 1:]:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("Loop time of"):
                break
            parts = stripped.split()
            if len(parts) < len(header):
                continue
            try:
                values = [float(x) for x in parts[:len(header)]]
            except ValueError:
                continue
            record = dict(zip(header, values))
            out["stats_rows"].append({
                "native_simulation_time": record.get("Time", ""),
                "accepted_exchanges_native": record.get("Naccept", ""),
                "rejected_events_native": record.get("Nreject", ""),
                "sweeps_native": record.get("Nsweeps", ""),
                "cpu_seconds": record.get("CPU", ""),
                "energy_native": record.get("Energy", ""),
                "raw": stripped,
            })
        return out

    def _nearest_stats_row(self, stats_rows: list[dict], point: float, tol: float | None = None) -> dict | None:
        if not stats_rows:
            return None
        best = min(stats_rows, key=lambda r: abs(float(r.get("native_simulation_time", 0.0)) - float(point)))
        if tol is not None and abs(float(best.get("native_simulation_time", 0.0)) - float(point)) > tol:
            return None
        return best

    def collect(self, scenario: BenchmarkScenario, seed: int, run_dir: Path, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        timing = json.loads((run_dir / "external_timing.json").read_text(encoding="utf-8"))
        log_timing = self._parse_log_timing(run_dir)
        rows: list[dict] = []
        timing_rows: list[dict] = []
        manifest_rows: list[dict] = []
        distribution_rows: list[dict] = []
        total_sites = 4 * scenario.spparks_fcc_cells ** 3
        eval_dir = run_dir / "evaluation" / "Rasmol"
        eval_dir.mkdir(parents=True, exist_ok=True)

        combined_dump = run_dir / "snapshots" / "state_all.dump"
        snapshot_by_step = {}
        parsed_snapshots = []
        if combined_dump.exists():
            parsed_snapshots = read_spparks_sites_dump(
                combined_dump,
                geometry="fcc",
                coordination=12,
                common_time_scale=self.common_mcs_per_native_time,
                fcc_cells=scenario.spparks_fcc_cells,
                phase_a=2,
                phase_b=1,
            )
            for snap in parsed_snapshots:
                snapshot_by_step[int(snap.step)] = snap

        max_point = max(scenario.mcs_points) if scenario.mcs_points else 0
        loop_time_final = float(log_timing.get("loop_time_final", timing.get("wall_seconds", 0.0)))
        stats_rows = log_timing.get("stats_rows", [])
        dump_interval_common_mcs = float(self._dump_interval(scenario))
        native_dump_interval = dump_interval_common_mcs / self.common_mcs_per_native_time
        snapshot_tol_common = max(1.0, 0.51 * dump_interval_common_mcs)
        stats_tol = max(1.0e-6, 0.51 * native_dump_interval)
        reference_counts: tuple[int, int] | None = None

        for idx, point in enumerate(scenario.mcs_points):
            snap = snapshot_by_step.get(int(point))
            # SPPARKS rKMC/sweep output can cross a floating-time dump target
            # by a small amount.  Do not require the rounded common-MCS key to
            # be bit-exact: if the exact requested key is absent, accept the
            # nearest rounded dump key within 0.51 of the configured base dump
            # interval (at least one common MCS). The native time is retained;
            # strict collection validation rejects reused snapshots and requires
            # the snapshot and stats timestamps to agree.
            if snap is None and parsed_snapshots:
                candidate = min(parsed_snapshots, key=lambda s: abs(float(s.step) - float(point)))
                if abs(float(candidate.step) - float(point)) <= snapshot_tol_common:
                    snap = candidate
            post: dict = {}
            native_target_time = float(point) / self.common_mcs_per_native_time
            stats_row = self._nearest_stats_row(stats_rows, native_target_time, tol=stats_tol)
            native_simulation_time = ""
            spparks_naccept = ""
            spparks_nreject = ""
            spparks_nsweeps = ""
            spparks_energy = ""
            native_cpu_from_stats = ""
            if stats_row is not None:
                native_simulation_time = stats_row.get("native_simulation_time", "")
                spparks_naccept = stats_row.get("accepted_exchanges_native", "")
                spparks_nreject = stats_row.get("rejected_events_native", "")
                spparks_nsweeps = stats_row.get("sweeps_native", "")
                spparks_energy = stats_row.get("energy_native", "")
                native_cpu_from_stats = stats_row.get("cpu_seconds", "")

            if snap is not None:
                try:
                    analysis = analyze_snapshot(snap)
                    post = analysis.metrics
                    counts = (analysis.validation.count_A, analysis.validation.count_B)
                    if reference_counts is None:
                        reference_counts = counts
                    elif counts != reference_counts:
                        raise ValueError(
                            f"SPPARKS composition changed across snapshots: "
                            f"initial A/B={reference_counts}, point {point}={counts}"
                        )
                    write_rasmol_xyz(snap, eval_dir, label_step=int(point))

                    manifest = snapshot_manifest_row(snap, analysis)
                    manifest.update({
                        "scenario_id": scenario.id,
                        "code": self.name,
                        "seed": seed,
                        "save_index": idx,
                    })
                    manifest_rows.append(manifest)
                    distribution_rows.extend(cluster_distribution_rows(
                        analysis,
                        scenario_id=scenario.id,
                        code=self.name,
                        seed=seed,
                        save_index=idx,
                        native_step=point,
                    ))
                except Exception as exc:
                    raise ValueError(f"Invalid SPPARKS snapshot at requested point {point}") from exc
                if stats_row is None and snap.native_time is not None:
                    native_simulation_time = snap.native_time
            else:
                raise FileNotFoundError(f"SPPARKS snapshot missing at requested point {point}")

            if native_cpu_from_stats != "":
                native_loop = native_cpu_from_stats
                timing_source = "SPPARKS stats CPU column matched to native simulation time"
            else:
                native_loop = ""
                timing_source = "No exact SPPARKS stats CPU row matched this snapshot"

            common_mcs = (
                float(native_simulation_time) * self.common_mcs_per_native_time
                if native_simulation_time != "" else float(point)
            )

            rows.append({
                "scenario_id": scenario.id,
                "code": self.name,
                "method_label": self.method_label,
                "seed": seed,
                "geometry": "fcc",
                "coordination": 12,
                "total_sites": total_sites,
                "composition_A": scenario.composition_A,
                "kT": scenario.kT,
                "Ea": scenario.Ea,
                "save_index": idx,
                "requested_mcs": point,
                "native_step": point,
                "native_mcs": "",
                "native_simulation_time": native_simulation_time,
                "common_mcs_equivalent": common_mcs,
                "physical_time_seconds": "",
                "spparks_naccept_native": spparks_naccept,
                "spparks_nreject_native": spparks_nreject,
                "spparks_nsweeps_native": spparks_nsweeps,
                "spparks_energy_native": spparks_energy,
                "attempted_exchanges_total": (
                    float(spparks_naccept) + float(spparks_nreject)
                    if self.is_sweep_mode and spparks_naccept != "" and spparks_nreject != ""
                    else ""
                ),
                "accepted_exchanges_total": (
                    spparks_naccept if self.is_sweep_mode else ""
                ),
                "accepted_exchanges_per_site": (
                    float(spparks_naccept) / total_sites
                    if self.is_sweep_mode and spparks_naccept != "" else ""
                ),
                "code_kmc_time": native_simulation_time,
                "interface_density_native": "",
                "cluster_count_native": "",
                "average_cluster_size_native": "",
                "largest_cluster_size_native": "",
                "largest_cluster_fraction_native": "",
                "calculation_cpu_seconds_cumulative": native_loop,
                "evolution_wall_seconds_cumulative": "",
                "diagnostic_runtime_seconds_cumulative": native_loop,
                "diagnostic_runtime_source": "SPPARKS cumulative stats CPU timer matched to native simulation time",
                "spparks_loop_wall_seconds_total_run": loop_time_final,
                "calculation_wall_seconds_total_run": float(timing.get("wall_seconds", 0.0)),
                "cumulative_total_seconds_native": native_loop,
                "cumulative_compute_seconds_native": native_loop,
                "cumulative_wall_seconds_external": "",
                **post,
            })
            timing_rows.append({
                "scenario_id": scenario.id,
                "code": self.name,
                "seed": seed,
                "save_index": idx,
                "progress_native_mcs": "",
                "progress_native_simulation_time": native_simulation_time,
                "progress_common_mcs_equivalent": common_mcs,
                "progress_spparks_naccept_native": spparks_naccept,
                "progress_accepted_per_site": "",
                "external_wall_seconds_total_run": float(timing.get("wall_seconds", 0.0)),
                "native_total_seconds_cumulative": native_loop,
                "native_compute_seconds_cumulative": native_loop,
                "diagnostic_runtime_seconds_cumulative": native_loop,
                "diagnostic_runtime_source": "SPPARKS cumulative stats CPU timer matched to native simulation time",
                "timing_source": timing_source,
            })

        write_metrics_csv(rows, out_dir / "metrics_common.csv")
        write_timing_csv(timing_rows, out_dir / "timing_common.csv")
        write_rows_csv(manifest_rows, out_dir / "snapshot_manifest_common.csv")
        write_rows_csv(distribution_rows, out_dir / "cluster_distribution_common.csv")
        (out_dir / "run_metadata.json").write_text(json.dumps({
            "adapter": self.name,
            "spparks_mode": self.mode,
            "spparks_application": self.spparks_application,
            "spparks_temperature_input": self.spparks_temperature(scenario),
            "common_mcs_per_native_time": self.common_mcs_per_native_time,
            "run_dir": str(run_dir),
            "external_timing": timing,
            "log_timing": log_timing,
            "common_snapshot": {
                "coordinate_convention": "integer doubled-FCC/parity-grid coordinates",
                "species_mapping": "A=SPPARKS phase 2, B=SPPARKS phase 1",
                "topology": "periodic FCC, 12 neighbors/site",
            },
            "notes": (
                "A=OCCUPIED=2, B=VACANT=1; T_app=2*kT/Ea; common MCS=6*native Time. "
                "Native CPU stats are accumulated iteration wall time and the manuscript runtime. "
                "Final tail Loop time is retained separately. No overhead subtraction."
            ),
        }, indent=2), encoding="utf-8")
