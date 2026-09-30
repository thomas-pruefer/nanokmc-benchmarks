from __future__ import annotations

from pathlib import Path
from abc import ABC, abstractmethod
from benchmark_core.scenario import BenchmarkScenario


class CodeAdapter(ABC):
    name: str

    def __init__(self, paths: dict):
        self.paths = paths

    @abstractmethod
    def prepare(self, scenario: BenchmarkScenario, seed: int, run_dir: Path) -> None:
        pass

    @abstractmethod
    def run(self, scenario: BenchmarkScenario, seed: int, run_dir: Path) -> None:
        pass

    @abstractmethod
    def collect(self, scenario: BenchmarkScenario, seed: int, run_dir: Path, out_dir: Path) -> None:
        pass
