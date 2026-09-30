from __future__ import annotations

from dataclasses import dataclass
COMMON_A = "A"
COMMON_B = "B"
COMMON_SPECIES = (COMMON_A, COMMON_B)

# Canonical FCC representation used by every adapter:
# coordinates are integer coordinates on a doubled conventional-cell grid.
# A conventional FCC site at (0.0, 0.5, 0.5) is therefore stored as (0, 1, 1).
# NanoKMC's homogeneous RasMol output already uses this parity-grid convention.
FCC_NEIGHBOR_OFFSETS: tuple[tuple[int, int, int], ...] = tuple(
    offset
    for sx in (-1, 1)
    for sy in (-1, 1)
    for offset in ((sx, sy, 0), (sx, 0, sy), (0, sx, sy))
)


@dataclass(frozen=True)
class CanonicalSnapshot:
    """Code-independent binary FCC lattice snapshot.

    ``sites`` entries are ``(site_id, x2, y2, z2, common_species)`` where
    x2/y2/z2 are integer doubled-FCC coordinates and common_species is ``A``
    or ``B``. ``periods`` are expressed in the same integer coordinate units.

    Native progress/time fields deliberately remain code-native. They are not
    assumed to be physically equivalent across KMC implementations.
    """

    step: float
    source_code: str
    geometry: str
    coordination: int
    periods: tuple[int, int, int]
    sites: tuple[tuple[int, int, int, int, str], ...]
    native_time: float | None = None
    native_counter: float | None = None
    snapshot_ordinal: int | None = None

    @property
    def total_sites(self) -> int:
        return len(self.sites)

    @property
    def count_A(self) -> int:
        return sum(1 for *_, species in self.sites if species == COMMON_A)

    @property
    def count_B(self) -> int:
        return sum(1 for *_, species in self.sites if species == COMMON_B)

    @property
    def composition_A(self) -> float:
        return self.count_A / self.total_sites if self.total_sites else 0.0

    @property
    def composition_B(self) -> float:
        return self.count_B / self.total_sites if self.total_sites else 0.0


@dataclass(frozen=True)
class SnapshotValidation:
    valid: bool
    total_sites: int
    unique_site_ids: int
    unique_coordinates: int
    count_A: int
    count_B: int
    composition_A: float
    composition_B: float
    min_neighbors: int
    max_neighbors: int
    expected_sites_from_periods: int | None
    parity_valid: bool
    message: str = ""


def nanokmc_fcc_periods(nx: int, ny: int, nz: int) -> tuple[int, int, int]:
    """Return NanoKMC parity-grid periods for homogeneous FCC exponents."""
    return (2 ** int(nx), 2 ** int(ny), 2 ** int(nz))


def spparks_fcc_periods(cells: int | tuple[int, int, int]) -> tuple[int, int, int]:
    """Return canonical doubled-coordinate periods for SPPARKS FCC cells."""
    if isinstance(cells, tuple):
        cx, cy, cz = cells
    else:
        cx = cy = cz = int(cells)
    return (2 * int(cx), 2 * int(cy), 2 * int(cz))


def _coordinate_index(snapshot: CanonicalSnapshot) -> dict[tuple[int, int, int], int]:
    index: dict[tuple[int, int, int], int] = {}
    for i, (_, x, y, z, _) in enumerate(snapshot.sites):
        key = (int(x), int(y), int(z))
        if key in index:
            raise ValueError(f"Duplicate canonical FCC coordinate {key}")
        index[key] = i
    return index


def build_fcc_neighbors(snapshot: CanonicalSnapshot) -> list[list[int]]:
    """Build the 12-neighbor FCC graph in O(N) using direct coordinate lookup.

    There is intentionally no all-pairs distance fallback. A malformed or
    unsupported snapshot should fail visibly instead of turning a large paper
    run into an accidental O(N^2) postprocessing job.
    """
    if snapshot.geometry.lower() != "fcc":
        raise NotImplementedError(
            f"Common snapshot topology currently supports FCC only, got {snapshot.geometry!r}"
        )
    if snapshot.coordination != 12:
        raise ValueError(
            f"FCC common snapshot requires coordination=12, got {snapshot.coordination}"
        )
    lx, ly, lz = snapshot.periods
    if lx <= 0 or ly <= 0 or lz <= 0:
        raise ValueError(f"Invalid periodic lengths: {snapshot.periods}")

    index = _coordinate_index(snapshot)
    neighbors: list[list[int]] = [[] for _ in snapshot.sites]
    for i, (_, x, y, z, _) in enumerate(snapshot.sites):
        for dx, dy, dz in FCC_NEIGHBOR_OFFSETS:
            key = ((x + dx) % lx, (y + dy) % ly, (z + dz) % lz)
            j = index.get(key)
            if j is None:
                raise ValueError(
                    "FCC topology validation failed: "
                    f"site {(x, y, z)} is missing nearest neighbor {key} "
                    f"for periods {snapshot.periods}"
                )
            neighbors[i].append(j)
    return neighbors


def validate_canonical_snapshot(
    snapshot: CanonicalSnapshot,
    neighbors: list[list[int]] | None = None,
) -> SnapshotValidation:
    if not snapshot.sites:
        return SnapshotValidation(
            valid=False,
            total_sites=0,
            unique_site_ids=0,
            unique_coordinates=0,
            count_A=0,
            count_B=0,
            composition_A=0.0,
            composition_B=0.0,
            min_neighbors=0,
            max_neighbors=0,
            expected_sites_from_periods=None,
            parity_valid=False,
            message="snapshot contains no sites",
        )

    ids = [site_id for site_id, *_ in snapshot.sites]
    coords = [(x, y, z) for _, x, y, z, _ in snapshot.sites]
    labels = [species for *_, species in snapshot.sites]
    unknown = sorted(set(labels) - set(COMMON_SPECIES))
    if unknown:
        raise ValueError(f"Unknown common species labels: {unknown}")

    unique_site_ids = len(set(ids))
    unique_coordinates = len(set(coords))
    if unique_site_ids != len(ids):
        raise ValueError("Canonical snapshot contains duplicate site IDs")
    if unique_coordinates != len(coords):
        raise ValueError("Canonical snapshot contains duplicate coordinates")

    lx, ly, lz = snapshot.periods
    parity_valid = all((x + y + z) % 2 == 0 for x, y, z in coords)
    expected_sites = None
    if lx % 2 == 0 and ly % 2 == 0 and lz % 2 == 0:
        expected_sites = (lx * ly * lz) // 2
        if expected_sites != len(coords):
            raise ValueError(
                f"FCC site-count mismatch: periods {snapshot.periods} imply "
                f"{expected_sites} sites, snapshot contains {len(coords)}"
            )
    if not parity_valid:
        raise ValueError("Canonical FCC coordinates violate x+y+z even parity")

    if neighbors is None:
        neighbors = build_fcc_neighbors(snapshot)
    counts = [len(nbs) for nbs in neighbors]
    min_neighbors = min(counts)
    max_neighbors = max(counts)
    if min_neighbors != 12 or max_neighbors != 12:
        raise ValueError(
            f"FCC topology mismatch: neighbor counts range {min_neighbors}..{max_neighbors}, expected 12"
        )

    # Reciprocal-neighbor validation is O(12N), still linear in system size.
    neighbor_sets = [set(nbs) for nbs in neighbors]
    for i, nbs in enumerate(neighbors):
        for j in nbs:
            if i not in neighbor_sets[j]:
                raise ValueError(f"Non-reciprocal FCC neighbor relation {i}->{j}")

    n_a = labels.count(COMMON_A)
    n_b = labels.count(COMMON_B)
    n = len(labels)
    return SnapshotValidation(
        valid=True,
        total_sites=n,
        unique_site_ids=unique_site_ids,
        unique_coordinates=unique_coordinates,
        count_A=n_a,
        count_B=n_b,
        composition_A=n_a / n,
        composition_B=n_b / n,
        min_neighbors=min_neighbors,
        max_neighbors=max_neighbors,
        expected_sites_from_periods=expected_sites,
        parity_valid=parity_valid,
        message="PASS",
    )
