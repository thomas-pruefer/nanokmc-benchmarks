"""Serialize public build commands with campaign and publication writers."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def build_lock():
    sys.path.insert(0, str(ROOT))
    from benchmark_core.run_store import CampaignLock
    return CampaignLock(ROOT)
