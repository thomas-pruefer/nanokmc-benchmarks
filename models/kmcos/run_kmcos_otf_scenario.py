#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

COMMON_MCS_PER_NATIVE_TIME = 6.0
MAX_EVENTS_PER_INTERVAL = 1_000_000_000_000
_DLL_DIRECTORY_HANDLES: list[object] = []


def _configure_runtime(kmcos_root: Path, compiled_src: Path, msys2_ucrt_bin: Path) -> tuple[Path, Path]:
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(msys2_ucrt_bin.resolve())))
    os.environ["PATH"] = str(msys2_ucrt_bin.resolve()) + os.pathsep + os.environ.get("PATH", "")

    search_dirs = [compiled_src.resolve(), compiled_src.resolve().parent]
    pyd_dir = next((d for d in search_dirs if list(d.glob("kmc_model*.pyd"))), None)
    settings_dir = next((d for d in search_dirs if (d / "kmc_settings.py").exists()), None)
    if pyd_dir is None:
        raise FileNotFoundError(
            "No compiled kmc_model*.pyd found under " + " or ".join(str(d) for d in search_dirs)
        )
    if settings_dir is None:
        raise FileNotFoundError(
            "kmc_settings.py not found under " + " or ".join(str(d) for d in search_dirs)
        )

    sys.path.insert(0, str(pyd_dir))
    if settings_dir != pyd_dir:
        sys.path.insert(0, str(settings_dir))
    sys.path.insert(0, str(kmcos_root.resolve()))
    return pyd_dir, settings_dir


def _write_snapshot(path: Path, config: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, configuration=np.asarray(config, dtype=np.int8))


def _make_initial_configuration(L: int, A: int, B: int, composition_A: float, seed: int) -> np.ndarray:
    total_sites = 4 * L**3
    n_a = int(round(float(composition_A) * total_sites))
    if not 0 <= n_a <= total_sites:
        raise ValueError(f"Invalid requested composition_A={composition_A}")
    flat = np.full(total_sites, B, dtype=np.int8)
    rng = np.random.default_rng(int(seed))
    if n_a:
        selected = rng.choice(total_sites, size=n_a, replace=False)
        flat[selected] = A
    return flat.reshape((L, L, L, 4))


def _checkpoint_record(
    *,
    save_index: int,
    requested_mcs: float,
    requested_native_time: float,
    native_last_event_time: float,
    native_kmc_step: int,
    events_since_previous: int,
    segment_wall_seconds: float,
    cumulative_wall_seconds: float,
    snapshot_relpath: str,
    config: np.ndarray,
    A: int,
) -> dict:
    return {
        "save_index": int(save_index),
        "requested_mcs": float(requested_mcs),
        "requested_native_observation_time": float(requested_native_time),
        "native_last_event_time": float(native_last_event_time),
        "native_kmc_step": int(native_kmc_step),
        "events_since_previous": int(events_since_previous),
        "evolution_wall_seconds_segment": float(segment_wall_seconds),
        "evolution_wall_seconds_cumulative": float(cumulative_wall_seconds),
        "snapshot": snapshot_relpath,
        "N_A": int(np.count_nonzero(config == A)),
        "N_total": int(config.size),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--kmcos-root", required=True)
    parser.add_argument("--compiled-src", required=True)
    parser.add_argument("--msys2-ucrt-bin", required=True)
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    run_dir = config_path.parent
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    kmcos_root = Path(args.kmcos_root)
    compiled_src = Path(args.compiled_src)
    msys2_ucrt_bin = Path(args.msys2_ucrt_bin)
    if not kmcos_root.exists():
        raise FileNotFoundError(f"kmcos checkout not found: {kmcos_root}")
    if not msys2_ucrt_bin.exists():
        raise FileNotFoundError(f"MSYS2 UCRT64 bin not found: {msys2_ucrt_bin}")

    pyd_dir, settings_dir = _configure_runtime(kmcos_root, compiled_src, msys2_ucrt_bin)
    from kmcos.run import KMC_Model

    L = int(payload["fcc_cells"])
    seed = int(payload["seed"])
    composition_A = float(payload["composition_A"])
    Ea = float(payload["Ea"])
    kT = float(payload["kT"])
    mcs_points = [float(x) for x in payload["mcs_points"]]
    if not mcs_points or mcs_points[0] != 0:
        raise ValueError("kmcos benchmark checkpoints must start at common MCS 0")
    if mcs_points != sorted(mcs_points):
        raise ValueError("kmcos benchmark checkpoints must be sorted")

    checkpoints: list[dict] = []
    cumulative_evolution_wall = 0.0

    with KMC_Model(size=[L, L, L], random_seed=seed, banner=False, print_rates=False) as model:
        if int(model.lattice.spuck) != 4:
            raise AssertionError(f"Expected 4 FCC basis sites/cell, got {model.lattice.spuck}")
        if list(map(int, model.lattice.system_size)) != [L, L, L]:
            raise AssertionError(f"Unexpected kmcos system size {model.lattice.system_size}")
        if int(model.proclist.nr_of_proc) != 48:
            raise AssertionError(f"Expected 48 OTF processes, got {model.proclist.nr_of_proc}")

        A = int(model.proclist.a)
        B = int(model.proclist.b)
        if (A, B) != (0, 1):
            raise AssertionError(f"Unexpected kmcos species constants A={A}, B={B}")

        # Set physical benchmark parameters before installing the initial state.
        model.parameters.Ea = Ea
        model.parameters.kT = kT

        initial = _make_initial_configuration(L, A, B, composition_A, seed)
        model._set_configuration(initial)
        initial_count_a = int(np.count_nonzero(initial == A))
        initial_active_events = sum(
            int(model.base.get_nrofsites(proc))
            for proc in range(1, int(model.proclist.nr_of_proc) + 1)
        )
        initial_total_rate = float(model.base.get_accum_rate(0))

        # Observation at t=0.
        snapshot0 = Path("snapshots") / "state_00000000.npz"
        _write_snapshot(run_dir / snapshot0, initial)
        checkpoints.append(_checkpoint_record(
            save_index=0,
            requested_mcs=0.0,
            requested_native_time=0.0,
            native_last_event_time=float(model.base.get_kmc_time()),
            native_kmc_step=int(model.base.get_kmc_step()),
            events_since_previous=0,
            segment_wall_seconds=0.0,
            cumulative_wall_seconds=0.0,
            snapshot_relpath=snapshot0.as_posix(),
            config=initial,
            A=A,
        ))

        previous_step = int(model.base.get_kmc_step())
        for save_index, requested_mcs in enumerate(mcs_points[1:], start=1):
            target_native_time = requested_mcs / COMMON_MCS_PER_NATIVE_TIME
            current_native_time = float(model.base.get_kmc_time())
            remaining = target_native_time - current_native_time
            if remaining < -1.0e-12:
                raise RuntimeError(
                    f"kmcos clock already passed requested observation time: current={current_native_time}, "
                    f"target={target_native_time}"
                )

            segment_wall = 0.0
            executed = 0
            if remaining > 0.0:
                t0 = time.perf_counter()
                executed = int(model.do_steps_time(float(remaining), MAX_EVENTS_PER_INTERVAL))
                segment_wall = time.perf_counter() - t0
                cumulative_evolution_wall += segment_wall
                if executed >= MAX_EVENTS_PER_INTERVAL:
                    raise RuntimeError(
                        "kmcos do_steps_time hit the event upper limit before reaching the observation time"
                    )

            current_native_time = float(model.base.get_kmc_time())
            # do_kmc_steps_time normally stops before the next event would cross the target.
            # It always executes at least one event, however, so explicitly reject the rare
            # first-event overshoot instead of silently mislabelling a snapshot.
            tolerance = max(1.0e-12, 1.0e-12 * max(1.0, target_native_time))
            if current_native_time > target_native_time + tolerance:
                raise RuntimeError(
                    "kmcos do_steps_time overshot the requested observation time. "
                    f"last_event={current_native_time:.17g}, target={target_native_time:.17g}. "
                    "Use a larger benchmark system/checkpoint spacing or a dedicated exact-observation runner."
                )

            current = model._get_configuration()
            count_a = int(np.count_nonzero(current == A))
            if count_a != initial_count_a:
                raise AssertionError(f"A count changed during kmcos run: {initial_count_a} -> {count_a}")
            current_step = int(model.base.get_kmc_step())
            events_from_counter = current_step - previous_step
            if events_from_counter != executed:
                raise AssertionError(
                    f"kmcos step counter mismatch: do_steps_time returned {executed}, counter advanced {events_from_counter}"
                )
            previous_step = current_step

            label = int(round(requested_mcs))
            snapshot_rel = Path("snapshots") / f"state_{label:08d}.npz"
            _write_snapshot(run_dir / snapshot_rel, current)
            checkpoints.append(_checkpoint_record(
                save_index=save_index,
                requested_mcs=requested_mcs,
                requested_native_time=target_native_time,
                native_last_event_time=current_native_time,
                native_kmc_step=current_step,
                events_since_previous=executed,
                segment_wall_seconds=segment_wall,
                cumulative_wall_seconds=cumulative_evolution_wall,
                snapshot_relpath=snapshot_rel.as_posix(),
                config=current,
                A=A,
            ))

            print(
                f"checkpoint common_MCS={requested_mcs:g}: events={current_step}, "
                f"last_event_time={current_native_time:.12g}, evolution_wall={cumulative_evolution_wall:.6f}s",
                flush=True,
            )

    result = {
        "schema_version": 1,
        "adapter_code": "kmcos_otf",
        "scenario_id": payload["scenario_id"],
        "seed": seed,
        "fcc_cells": L,
        "total_sites": 4 * L**3,
        "composition_A_requested": composition_A,
        "N_A_initial": initial_count_a,
        "x_A_initial": initial_count_a / (4 * L**3),
        "Ea": Ea,
        "kT": kT,
        "common_mcs_per_native_time": COMMON_MCS_PER_NATIVE_TIME,
        "kmcos_root": str(kmcos_root.resolve()),
        "compiled_pyd_dir": str(pyd_dir),
        "settings_dir": str(settings_dir),
        "python": sys.version,
        "numpy": np.__version__,
        "initial_active_events": initial_active_events,
        "initial_total_rate": initial_total_rate,
        "checkpoints": checkpoints,
        "timing_note": (
            "evolution_wall_seconds_* uses Python time.perf_counter only around kmcos do_steps_time; "
            "model initialization, configuration installation, and snapshot I/O are excluded. "
            "The manuscript kmcos build uses its disclosed O3 Fortran route; C++ paths use O1."
        ),
        "observation_note": (
            "Each requested common-MCS checkpoint is mapped to native observation time t=MCS/6. "
            "kmcos do_steps_time stops after the last event not exceeding that observation time and restores the RNG "
            "when the next event would cross it; therefore the stored lattice is the CTMC state at the requested observation time."
        ),
    }
    (run_dir / "kmcos_progress.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("kmcos OTF scenario run: PASS", flush=True)


if __name__ == "__main__":
    main()
