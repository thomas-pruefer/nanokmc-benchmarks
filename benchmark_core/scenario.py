"""Strict JSON configuration for the seven manuscript benchmark states."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math
from typing import Any

from benchmark_core.local_config import PATH_KEYS, read_local_config


CHECKPOINTS = (0, 10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 1000, 2000,
               3000, 4000, 5000, 10000, 20000, 30000)
_STATE_PARAMETERS = {
    "representative_k6_x20_t075": (6, .2, .75, 1),
    "screen_k6_x20_t125": (6, .2, 1.25, 1),
    "screen_k6_x40_t075": (6, .4, .75, 1),
    "screen_k6_x40_t125": (6, .4, 1.25, 1),
    "scaling_k3_x20_t075": (3, .2, .75, 512),
    "scaling_k4_x20_t075": (4, .2, .75, 64),
    "scaling_k5_x20_t075": (5, .2, .75, 8),
}


@dataclass
class BenchmarkScenario:
    id: str
    description: str
    geometry: str
    dimension: int
    nx: int
    ny: int
    nz: int
    composition_A: float
    kT: float
    Ea: float
    mcs_points: list[int]
    seeds: list[int]
    codes: list[str]
    save_snapshots: bool = True
    postprocess_snapshots: bool = True
    spparks_loglinfreq_n: int | None = None
    spparks_loglinfreq_factor: float | None = None

    @property
    def spparks_fcc_cells(self) -> int:
        return 2 ** (self.nx - 1)

    @property
    def nanokmc_total_sites_estimate(self) -> int:
        return (2 ** self.nx) * (2 ** self.ny) * (2 ** self.nz) // 2


@dataclass(frozen=True)
class ManuscriptConfiguration:
    scenarios: tuple[BenchmarkScenario, ...]
    campaigns: dict[str, tuple[str, ...]]


def _keys(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    missing, unknown = expected - value.keys(), value.keys() - expected
    if missing or unknown:
        raise ValueError(f"{label}: missing fields {sorted(missing)}; unknown fields {sorted(unknown)}")


def _number(value: Any, expected: float, label: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value != expected:
        raise ValueError(f"{label} must be {expected}")


def _integer(value: Any, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise ValueError(f"{label} must be the integer {expected}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate configuration field {key!r}")
        result[key] = value
    return result


def load_configuration(path: Path) -> ManuscriptConfiguration:
    """Validate the fixed scientific contract, then expand its shared fields."""
    # Import here because the adapters also import BenchmarkScenario.
    from benchmark_core.runner import PATH_IDS

    data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    _keys(data, {"schema_version", "benchmark", "states", "campaigns"}, "configuration")
    _integer(data["schema_version"], 1, "schema_version")
    benchmark = data["benchmark"]
    _keys(benchmark, {"model", "observation_targets", "solvers", "outputs"}, "benchmark")
    model = benchmark["model"]
    _keys(model, {"geometry", "dimension", "Ea"}, "benchmark.model")
    if model["geometry"] != "fcc":
        raise ValueError("benchmark.model.geometry must be fcc")
    _integer(model["dimension"], 3, "benchmark.model.dimension")
    _number(model["Ea"], 1.0, "benchmark.model.Ea")
    points = benchmark["observation_targets"]
    if not isinstance(points, list) or any(type(p) is not int for p in points) or tuple(points) != CHECKPOINTS:
        raise ValueError("benchmark.observation_targets must contain the exact 19 manuscript checkpoints")
    codes = benchmark["solvers"]
    if not isinstance(codes, list) or tuple(codes) != PATH_IDS:
        raise ValueError("benchmark.solvers must contain the exact eleven manuscript solver IDs in order")
    outputs = benchmark["outputs"]
    _keys(outputs, {"save_snapshots", "postprocess_snapshots", "spparks_loglinfreq_n",
                    "spparks_loglinfreq_factor"}, "benchmark.outputs")
    for key in ("save_snapshots", "postprocess_snapshots"):
        if outputs[key] is not True:
            raise ValueError(f"benchmark.outputs.{key} must be true")
    _integer(outputs["spparks_loglinfreq_n"], 5, "benchmark.outputs.spparks_loglinfreq_n")
    _number(outputs["spparks_loglinfreq_factor"], 10.0, "benchmark.outputs.spparks_loglinfreq_factor")
    states = data["states"]
    _keys(states, set(_STATE_PARAMETERS), "states")
    scenarios = []
    for state_id, (k, composition, temperature, seed_count) in _STATE_PARAMETERS.items():
        state = states[state_id]
        _keys(state, {"k", "composition_A", "kT", "seed_range"}, f"states.{state_id}")
        _integer(state["k"], k, f"states.{state_id}.k")
        _number(state["composition_A"], composition, f"states.{state_id}.composition_A")
        _number(state["kT"], temperature, f"states.{state_id}.kT")
        seeds = state["seed_range"]
        if (not isinstance(seeds, list) or len(seeds) != 2
                or any(type(seed) is not int for seed in seeds) or seeds != [1, seed_count]):
            raise ValueError(f"states.{state_id}.seed_range must be [1, {seed_count}] (inclusive)")
        scenarios.append(BenchmarkScenario(
            id=state_id, description=f"FCC Kawasaki: k={k}, x_A={composition}, T*={temperature}",
            geometry=model["geometry"], dimension=model["dimension"], nx=k, ny=k, nz=k,
            composition_A=state["composition_A"], kT=state["kT"], Ea=model["Ea"],
            mcs_points=list(points), seeds=list(range(seeds[0], seeds[1] + 1)), codes=list(codes), **outputs,
        ))
    campaigns = data["campaigns"]
    _keys(campaigns, {"A", "B", "C"}, "campaigns")
    expected_members = {
        "A": {s.id for s in scenarios if (s.nx, s.composition_A, s.kT) == (6, .2, .75)},
        "B": {s.id for s in scenarios if s.nx == 6},
        "C": {s.id for s in scenarios if (s.composition_A, s.kT) == (.2, .75)},
    }
    for name in "ABC":
        members = campaigns[name]
        if (not isinstance(members, list) or any(type(member) is not str for member in members)
                or len(members) != len(set(members)) or set(members) != expected_members[name]):
            raise ValueError(f"campaigns.{name} must contain exactly {sorted(expected_members[name])}")
    return ManuscriptConfiguration(tuple(scenarios), {name: tuple(campaigns[name]) for name in "ABC"})


def load_scenarios(path: Path) -> list[BenchmarkScenario]:
    return list(load_configuration(path).scenarios)


def load_paths(path: Path) -> dict[str, Any]:
    """Resolve repository-local paths without guessing another installed solver."""
    data = read_local_config(path)
    root = Path(__file__).resolve().parents[1]
    for key in PATH_KEYS.intersection(data):
        value = Path(data[key])
        data[key] = str(value if value.is_absolute() else (root / value).resolve())
    return data
