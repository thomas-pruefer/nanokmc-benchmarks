"""The exact, deduplicated manuscript A--C run manifest (never launches a solver)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import re

from benchmark_core.runner import PATH_IDS
from benchmark_core.scenario import BenchmarkScenario, CHECKPOINTS, load_configuration

ROOT = Path(__file__).resolve().parents[1]
SOLVER_ALIASES = dict(zip(("classical", "partial-filter", "generic", "binary", "rate-category",
                          "exact-class", "spparks-sweep", "spparks-linear", "spparks-tree",
                          "kmcos", "kmc-lattice"), PATH_IDS))
STATE_ALIASES = {
    "x20-t075": "representative_k6_x20_t075",
    "x20-t125": "screen_k6_x20_t125",
    "x40-t075": "screen_k6_x40_t075",
    "x40-t125": "screen_k6_x40_t125",
    **{f"k{k}": f"scaling_k{k}_x20_t075" for k in (3, 4, 5)},
}
REPRESENTATIVE = STATE_ALIASES["x20-t075"]
STATE_ALIASES.update({"representative": REPRESENTATIVE, "k6": REPRESENTATIVE})


@dataclass(frozen=True)
class RunSpec:
    scenario: BenchmarkScenario
    code: str
    seed: int
    campaign_names: tuple[str, ...] = ()

    @property
    def run_id(self) -> str:
        return f"{self.scenario.id}__{self.code}__seed_{self.seed:04d}"

    @property
    def campaigns(self) -> tuple[str, ...]:
        return self.campaign_names

    def scientific_identity(self) -> dict:
        data = asdict(self.scenario)
        # Labels, campaign membership and the other selected replicates do not alter this run.
        for key in ("description", "seeds", "codes"):
            data.pop(key)
        return {"run_id": self.run_id, "scenario": data, "code": self.code, "seed": self.seed}


def _tokens(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, int)):
        values = [values]
    return [piece.strip() for value in values for piece in str(value).split(",") if piece.strip()]


def parse_seeds(values) -> set[int] | None:
    """Accept '1-8', '1:8', '1,3,5' or several tokens; ranges are inclusive."""
    if values is None:
        return None
    selected = set()
    for token in _tokens(values):
        if re.fullmatch(r"\d+", token):
            start = end = int(token)
        else:
            match = re.fullmatch(r"(\d+)[-:](\d+)", token)
            if not match:
                raise ValueError(f"Invalid seed selection {token!r}; use 1-8 or 1,3,5")
            start, end = map(int, match.groups())
        if start < 1 or end < start or end > 512:
            raise ValueError("Manuscript seed selections must be increasing ranges within 1..512")
        selected.update(range(start, end + 1))
    if not selected:
        raise ValueError("Empty seed selection")
    return selected


def resolve_solvers(values=None) -> list[str]:
    if values is None or _tokens(values) == ["all"]:
        return list(PATH_IDS)
    selected = set()
    for value in _tokens(values):
        code = SOLVER_ALIASES.get(value.lower(), value)
        if code not in PATH_IDS:
            raise ValueError(f"Unknown solver {value!r}; aliases: {', '.join(SOLVER_ALIASES)}")
        selected.add(code)
    return [code for code in PATH_IDS if code in selected]


def enumerate_runs(manifest_path: Path | None = None, campaign="all", solvers=None,
                   states=None, sizes=None, seeds=None) -> list[RunSpec]:
    """Filters intersect; A is the same canonical run when requested through B or C."""
    configuration = load_configuration(Path(manifest_path or ROOT / "config/manuscript.json"))
    scenarios = configuration.scenarios
    selected_campaigns = {s.upper() for s in _tokens(campaign)}
    if selected_campaigns == {"ALL"}:
        selected_campaigns = {"A", "B", "C"}
    if not selected_campaigns or not selected_campaigns <= {"A", "B", "C"}:
        raise ValueError("--campaign must be A, B, C or all")
    selected_codes = set(resolve_solvers(solvers))
    selected_states = None
    if states is not None:
        selected_states = {STATE_ALIASES.get(t.lower(), t) for t in _tokens(states)}
        unknown = selected_states - {s.id for s in scenarios}
        if unknown:
            raise ValueError(f"Unknown state(s): {sorted(unknown)}; aliases: {', '.join(STATE_ALIASES)}")
    selected_sizes = {int(t.removeprefix("k")) for t in _tokens(sizes)} if sizes is not None else None
    if selected_sizes is not None and (not selected_sizes or not selected_sizes <= {3, 4, 5, 6}):
        raise ValueError("--sizes accepts only k indices 3, 4, 5, 6")
    selected_seeds = parse_seeds(seeds)
    results = []
    for scenario in scenarios:
        if selected_states is not None and scenario.id not in selected_states:
            continue
        if selected_sizes is not None and scenario.nx not in selected_sizes:
            continue
        memberships = tuple(name for name, members in configuration.campaigns.items()
                            if scenario.id in members)
        for code in scenario.codes:
            if code not in selected_codes:
                continue
            for seed in scenario.seeds:
                spec = RunSpec(scenario, code, seed, memberships)
                if selected_campaigns.intersection(spec.campaigns) and (selected_seeds is None or seed in selected_seeds):
                    results.append(spec)
    if not results:
        raise ValueError("These campaign/solver/state/size/seed filters select no manuscript runs")
    return results


def add_selection_arguments(parser) -> None:
    parser.add_argument("--campaign", nargs="+", default=["all"], help="A, B, C, space/comma combination, or all (default)")
    parser.add_argument("--solvers", nargs="+", help="Friendly aliases or full path IDs; accepts space/comma lists")
    parser.add_argument("--states", nargs="+", help="x20-t075, x20-t125, x40-t075, x40-t125, k3/k4/k5/k6 or full IDs")
    parser.add_argument("--sizes", nargs="+", help="Size indices 3 4 5 6; intersects other selections")
    parser.add_argument("--seeds", nargs="+", help="Exact seed labels, e.g. 1-8 or 1,3,5; intersects each state's prescribed seeds")
