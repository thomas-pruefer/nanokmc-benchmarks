"""Machine-local installation paths, kept separate from scientific and source locks."""
from __future__ import annotations

import json
from pathlib import Path

PATH_KEYS = frozenset({
    "nanokmc_exe", "spparks_exe", "kmc_lattice_root", "kmc_lattice_exe",
    "kmcos_root", "kmcos_python", "kmcos_compiled_src", "kmcos_msys2_ucrt_bin",
    "msys2_root", "msys2_bash",
})
SOURCE_NAMES = frozenset({"nanokmc", "spparks", "kmcos", "kmc_lattice"})
MSYS_PATH_PREFIX = "/ucrt64/bin:/usr/bin"


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate local configuration key: {key}")
        result[key] = value
    return result


def read_local_config(path: Path, *, missing_ok: bool = False) -> dict:
    path = Path(path)
    if missing_ok and not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_keys)
    if not isinstance(data, dict):
        raise ValueError("Local configuration must be a JSON object")
    unknown = data.keys() - PATH_KEYS - {"msys2_path_prefix", "source_repositories"}
    if unknown:
        raise ValueError(f"Unknown local configuration fields: {sorted(unknown)}")
    for key in PATH_KEYS.intersection(data):
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"Local configuration {key} must be a non-empty path string")
    if data.get("msys2_path_prefix", MSYS_PATH_PREFIX) != MSYS_PATH_PREFIX:
        raise ValueError(f"msys2_path_prefix must be {MSYS_PATH_PREFIX}")
    repositories = data.get("source_repositories", {})
    if not isinstance(repositories, dict) or repositories.keys() - SOURCE_NAMES:
        raise ValueError("source_repositories must map only the four dependency names to local paths")
    for key, value in repositories.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"source_repositories.{key} must be a non-empty path string")
    return data
