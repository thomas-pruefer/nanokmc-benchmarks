from __future__ import annotations

from dataclasses import dataclass

# Conventional cubic FCC cell represented in the benchmark's doubled-coordinate
# convention. Physical positions are BASIS2 / 2 in conventional-cell units.
FCC_BASIS2: dict[int, tuple[int, int, int]] = {
    0: (0, 0, 0),
    1: (0, 1, 1),
    2: (1, 0, 1),
    3: (1, 1, 0),
}
FCC_BASIS_INDEX = {coord: index for index, coord in FCC_BASIS2.items()}

FCC_NEIGHBOR_OFFSETS2: tuple[tuple[int, int, int], ...] = tuple(
    offset
    for sx in (-1, 1)
    for sy in (-1, 1)
    for offset in ((sx, sy, 0), (sx, 0, sy), (0, sx, sy))
)


def _add(a: tuple[int, int, int], b: tuple[int, int, int]) -> tuple[int, int, int]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def decompose_doubled_coordinate(
    q: tuple[int, int, int],
) -> tuple[tuple[int, int, int], int]:
    """Map a doubled FCC coordinate to conventional-cell offset + basis index."""
    cell = (q[0] // 2, q[1] // 2, q[2] // 2)
    remainder = (
        q[0] - 2 * cell[0],
        q[1] - 2 * cell[1],
        q[2] - 2 * cell[2],
    )
    try:
        basis = FCC_BASIS_INDEX[remainder]
    except KeyError as exc:
        raise ValueError(f"Coordinate {q} is not an FCC parity-grid site") from exc
    return cell, basis


@dataclass(frozen=True)
class DirectedFCCBondTemplate:
    index: int
    source_basis: int
    target_basis: int
    target_cell_offset: tuple[int, int, int]
    source_q2: tuple[int, int, int]
    target_q2: tuple[int, int, int]
    initial_unique_neighbors_q2: tuple[tuple[int, int, int], ...]
    final_unique_neighbors_q2: tuple[tuple[int, int, int], ...]
    shared_neighbors_q2: tuple[tuple[int, int, int], ...]


def _environment_partition(
    source_q2: tuple[int, int, int],
    target_q2: tuple[int, int, int],
) -> tuple[
    tuple[tuple[int, int, int], ...],
    tuple[tuple[int, int, int], ...],
    tuple[tuple[int, int, int], ...],
]:
    source_neighbors = {_add(source_q2, offset) for offset in FCC_NEIGHBOR_OFFSETS2}
    target_neighbors = {_add(target_q2, offset) for offset in FCC_NEIGHBOR_OFFSETS2}

    if target_q2 not in source_neighbors or source_q2 not in target_neighbors:
        raise AssertionError("Template endpoints are not reciprocal FCC nearest neighbors")

    source_external = source_neighbors - {target_q2}
    target_external = target_neighbors - {source_q2}
    shared = source_external & target_external
    initial_unique = source_external - shared
    final_unique = target_external - shared

    # Adjacent FCC sites have four common nearest neighbors. After excluding
    # the exchange partner, each endpoint therefore has 7 unique + 4 shared
    # external neighbors. The four shared sites cancel exactly from n_i - n_f.
    if len(shared) != 4 or len(initial_unique) != 7 or len(final_unique) != 7:
        raise AssertionError(
            f"Unexpected FCC environment partition: shared={len(shared)}, "
            f"initial_unique={len(initial_unique)}, final_unique={len(final_unique)}"
        )

    return tuple(sorted(initial_unique)), tuple(sorted(final_unique)), tuple(sorted(shared))


def directed_fcc_bond_templates() -> tuple[DirectedFCCBondTemplate, ...]:
    """Return the 48 directed nearest-neighbor templates of a 4-site FCC cell.

    There are four basis sites and twelve directed nearest-neighbor directions
    from each basis site, hence 4*12 = 48 templates. With the process condition
    A(source)+B(target), every physical unlike undirected bond is active in
    exactly one of the two directions.
    """
    templates: list[DirectedFCCBondTemplate] = []
    for source_basis, source_q2 in FCC_BASIS2.items():
        for offset in FCC_NEIGHBOR_OFFSETS2:
            target_q2 = _add(source_q2, offset)
            target_cell_offset, target_basis = decompose_doubled_coordinate(target_q2)
            ini, fin, shared = _environment_partition(source_q2, target_q2)
            templates.append(
                DirectedFCCBondTemplate(
                    index=len(templates),
                    source_basis=source_basis,
                    target_basis=target_basis,
                    target_cell_offset=target_cell_offset,
                    source_q2=source_q2,
                    target_q2=target_q2,
                    initial_unique_neighbors_q2=ini,
                    final_unique_neighbors_q2=fin,
                    shared_neighbors_q2=shared,
                )
            )

    if len(templates) != 48:
        raise AssertionError(f"Expected 48 directed FCC templates, got {len(templates)}")
    return tuple(templates)
