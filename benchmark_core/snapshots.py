from __future__ import annotations

from pathlib import Path
import re

import numpy as np

from benchmark_core.common_snapshot import (
    COMMON_A,
    COMMON_B,
    CanonicalSnapshot,
    spparks_fcc_periods,
)


def read_spparks_sites_dump(
    path: Path,
    geometry: str = "fcc",
    coordination: int = 12,
    common_time_scale: float = 1.0,
    *,
    fcc_cells: int | tuple[int, int, int],
    phase_a: int = 2,
    phase_b: int = 1,
) -> list[CanonicalSnapshot]:
    """Read SPPARKS text dumps into the common A/B FCC representation.

    Current benchmark mapping is explicit here, not inferred from numeric order:
      common A = SPPARKS phase 2
      common B = SPPARKS phase 1
    """
    snapshots: list[CanonicalSnapshot] = []
    if not path.exists():
        return snapshots

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    i = 0
    ordinal = 0
    while i < len(lines):
        if not lines[i].startswith("ITEM: TIMESTEP"):
            i += 1
            continue
        step_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if not step_line:
            i += 2
            continue

        toks = step_line.split()
        if len(toks) != 2:
            raise ValueError(f"{path}: SPPARKS TIMESTEP must contain dump index and native time")
        # The pinned text writer records native time in the second token.
        native_time_from_dump = float(toks[1])
        step = int(round(native_time_from_dump * common_time_scale))
        i += 2

        nsites = None
        cols = None
        while i < len(lines):
            line = lines[i]
            if line.startswith("ITEM: NUMBER"):
                nsites = int(lines[i + 1].strip())
                i += 2
            elif line.startswith("ITEM: BOX BOUNDS"):
                i += 4
            elif line.startswith("ITEM: ATOMS"):
                cols = line.split()[2:]
                i += 1
                break
            else:
                i += 1
        if cols is None or nsites is None:
            continue

        col_index = {c: k for k, c in enumerate(cols)}
        for required in ("id", "type", "x", "y", "z"):
            if required not in col_index:
                raise ValueError(f"{path}: SPPARKS dump must contain id type x y z columns")

        sites: list[tuple[int, int, int, int, str]] = []
        for _ in range(nsites):
            parts = lines[i].split()
            i += 1
            sid = int(float(parts[col_index["id"]]))
            raw_phase = int(float(parts[col_index["type"]]))
            if raw_phase == phase_a:
                common_species = COMMON_A
            elif raw_phase == phase_b:
                common_species = COMMON_B
            else:
                raise ValueError(
                    f"{path}: unexpected SPPARKS phase {raw_phase}; "
                    f"expected {phase_a}=A or {phase_b}=B"
                )
            x = float(parts[col_index["x"]])
            y = float(parts[col_index["y"]])
            z = float(parts[col_index["z"]])
            x2, y2, z2 = int(round(2 * x)), int(round(2 * y)), int(round(2 * z))
            if abs(2 * x - x2) > 1e-6 or abs(2 * y - y2) > 1e-6 or abs(2 * z - z2) > 1e-6:
                raise ValueError(
                    f"{path}: SPPARKS FCC coordinate {(x, y, z)} is not on the expected half-grid"
                )
            sites.append((sid, x2, y2, z2, common_species))

        sites.sort(key=lambda row: row[0])
        snapshots.append(
            CanonicalSnapshot(
                step=step,
                source_code="spparks",
                geometry=geometry,
                coordination=coordination,
                periods=spparks_fcc_periods(fcc_cells),
                sites=tuple(sites),
                native_time=native_time_from_dump,
                native_counter=None,
                snapshot_ordinal=ordinal,
            )
        )
        ordinal += 1
    return snapshots



def read_kmcos_otf_npz(
    path: Path,
    *,
    fcc_cells: int,
    requested_common_mcs: float,
    native_last_event_time: float,
    native_kmc_step: int,
) -> CanonicalSnapshot:
    """Read one benchmark-owned kmcos OTF configuration snapshot.

    The subprocess stores only kmcos's compact ``[X,Y,Z,4]`` integer species
    array.  Coordinates are reconstructed deterministically from the standard
    four-site FCC conventional-cell basis.  The validated model uses
    kmcos species integer 0 = common A and 1 = common B.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    with np.load(path, allow_pickle=False) as data:
        if "configuration" not in data:
            raise ValueError(f"{path}: missing configuration array")
        config = np.asarray(data["configuration"], dtype=np.int8)

    L = int(fcc_cells)
    expected_shape = (L, L, L, 4)
    if tuple(config.shape) != expected_shape:
        raise ValueError(
            f"{path}: kmcos configuration shape {config.shape} != expected {expected_shape}"
        )
    raw_species = set(int(x) for x in np.unique(config))
    if not raw_species.issubset({0, 1}):
        raise ValueError(f"{path}: unexpected kmcos species integers {sorted(raw_species)}")

    basis2 = (
        (0, 0, 0),
        (0, 1, 1),
        (1, 0, 1),
        (1, 1, 0),
    )
    sites: list[tuple[int, int, int, int, str]] = []
    sid = 1
    for x in range(L):
        for y in range(L):
            for z in range(L):
                for basis, (bx, by, bz) in enumerate(basis2):
                    species = COMMON_A if int(config[x, y, z, basis]) == 0 else COMMON_B
                    sites.append((sid, 2*x + bx, 2*y + by, 2*z + bz, species))
                    sid += 1

    return CanonicalSnapshot(
        step=float(requested_common_mcs),
        source_code="kmcos_otf",
        geometry="fcc",
        coordination=12,
        periods=(2*L, 2*L, 2*L),
        sites=tuple(sites),
        native_time=float(native_last_event_time),
        native_counter=float(native_kmc_step),
        snapshot_ordinal=None,
    )


def read_kmc_lattice_bin(
    path: Path,
    *,
    fcc_cells: int,
    requested_common_mcs: float,
    native_last_event_time: float,
    native_events: int,
) -> CanonicalSnapshot:
    """Read the benchmark-owned binary snapshot emitted by the KMC_Lattice comparator."""
    import struct

    data = path.read_bytes()
    if len(data) < 20 or data[:8] != b"KMCLAT01":
        raise ValueError(f"{path}: invalid KMC_Lattice snapshot header")
    period = struct.unpack_from("<I", data, 8)[0]
    nsites = struct.unpack_from("<Q", data, 12)[0]
    species_raw = data[20:]
    expected_period = 2 * int(fcc_cells)
    expected_sites = 4 * int(fcc_cells) ** 3
    if period != expected_period:
        raise ValueError(f"{path}: period {period} != expected {expected_period}")
    if nsites != expected_sites or len(species_raw) != expected_sites:
        raise ValueError(
            f"{path}: site count/header mismatch nsites={nsites}, bytes={len(species_raw)}, expected={expected_sites}"
        )

    sites: list[tuple[int, int, int, int, str]] = []
    offset = 0
    sid = 1
    for x in range(period):
        for y in range(period):
            for z in range(period):
                if (x + y + z) % 2:
                    continue
                raw = species_raw[offset]
                offset += 1
                if raw == 0:
                    label = COMMON_A
                elif raw == 1:
                    label = COMMON_B
                else:
                    raise ValueError(f"{path}: unexpected species byte {raw}")
                sites.append((sid, x, y, z, label))
                sid += 1

    return CanonicalSnapshot(
        step=float(requested_common_mcs),
        source_code="kmc_lattice_selective",
        geometry="fcc",
        coordination=12,
        periods=(period, period, period),
        sites=tuple(sites),
        native_time=float(native_last_event_time),
        native_counter=float(native_events),
        snapshot_ordinal=None,
    )

def read_nanokmc_rasmol_xyz(
    run_dir: Path,
    step: int,
    geometry: str = "fcc",
    coordination: int = 12,
    *,
    periods: tuple[int, int, int],
) -> CanonicalSnapshot | None:
    """Read NanoKMC species-split RasMol files into common A/B coordinates.

    NanoKMC homogeneous output already writes integer parity-grid coordinates:
      S0 / raw species 0 -> common A
      S1 / raw species 1 -> common B
    """
    rdir = run_dir / "evaluation" / "Rasmol"
    if not rdir.exists():
        return None

    raw_sites: list[tuple[int, int, int, str]] = []
    for path in sorted(rdir.glob(f"{step:08d}_S*.xyz")):
        m = re.search(r"_S(\d+)\.xyz$", path.name)
        if not m:
            continue
        raw_species = int(m.group(1))
        if raw_species == 0:
            common_species = COMMON_A
        elif raw_species == 1:
            common_species = COMMON_B
        else:
            raise ValueError(
                f"{path}: homogeneous binary benchmark expected only S0/S1, got S{raw_species}"
            )
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in lines[2:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
            x2, y2, z2 = int(round(x)), int(round(y)), int(round(z))
            if abs(x - x2) > 1e-6 or abs(y - y2) > 1e-6 or abs(z - z2) > 1e-6:
                raise ValueError(
                    f"{path}: NanoKMC homogeneous RasMol coordinate {(x, y, z)} "
                    "is not an integer parity-grid coordinate"
                )
            raw_sites.append((x2, y2, z2, common_species))

    if not raw_sites:
        return None
    # Deterministic canonical site IDs are based on coordinates, not on the
    # species-file order. This makes A/B exchanges leave site identity stable.
    raw_sites.sort(key=lambda row: (row[0], row[1], row[2]))
    sites = tuple(
        (sid, x, y, z, species)
        for sid, (x, y, z, species) in enumerate(raw_sites, start=1)
    )
    return CanonicalSnapshot(
        step=step,
        source_code="nanokmc",
        geometry=geometry,
        coordination=coordination,
        periods=periods,
        sites=sites,
    )


def write_rasmol_xyz(
    snapshot: CanonicalSnapshot,
    out_dir: Path,
    label_step: int | None = None,
) -> list[Path]:
    """Write common A/B snapshots using NanoKMC-compatible split XYZ files.

    The suffix and colors follow COMMON species:
      common A -> S0 -> red
      common B -> S1 -> blue
    Coordinates are the canonical integer FCC parity-grid coordinates, making
    NanoKMC and converted SPPARKS snapshots directly comparable visually.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    step = int(snapshot.step) if label_step is None else int(label_step)
    grouped: dict[str, list[tuple[int, int, int]]] = {COMMON_A: [], COMMON_B: []}
    for _, x, y, z, species in snapshot.sites:
        if species not in grouped:
            raise ValueError(f"Unexpected common species {species!r}")
        grouped[species].append((x, y, z))

    written: list[Path] = []
    suffix_by_species = {COMMON_A: 0, COMMON_B: 1}
    atom_by_species = {COMMON_A: "A", COMMON_B: "B"}
    for species in (COMMON_A, COMMON_B):
        pts = grouped[species]
        suffix = suffix_by_species[species]
        xyz_name = f"{step:08d}_S{suffix}.xyz"
        path = out_dir / xyz_name
        with path.open("w", encoding="utf-8") as f:
            f.write(f"{len(pts)}\n\n")
            for x, y, z in pts:
                f.write(f"{atom_by_species[species]}\t{x}\t{y}\t{z}\t{suffix}\n")
        written.append(path)

    return written
