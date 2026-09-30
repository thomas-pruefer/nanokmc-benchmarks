"""Offline checks for the benchmark-owned kmcos FCC model geometry."""
from __future__ import annotations

import unittest

from models.kmcos.fcc_kawasaki_geometry import FCC_BASIS2, directed_fcc_bond_templates


REFERENCE_NEIGHBOR_OFFSETS = {
    (sx, sy, 0)
    for sx in (-1, 1)
    for sy in (-1, 1)
} | {
    (sx, 0, sz)
    for sx in (-1, 1)
    for sz in (-1, 1)
} | {
    (0, sy, sz)
    for sy in (-1, 1)
    for sz in (-1, 1)
}


def translated(point: tuple[int, int, int], offsets: set[tuple[int, int, int]]) -> set[tuple[int, int, int]]:
    return {
        (point[0] + offset[0], point[1] + offset[1], point[2] + offset[2])
        for offset in offsets
    }


class KmcosGeometryTests(unittest.TestCase):
    def test_directed_templates_match_independent_neighbor_enumeration(self) -> None:
        templates = directed_fcc_bond_templates()
        expected_endpoints = {
            (basis, tuple(source[i] + offset[i] for i in range(3)))
            for basis, source in FCC_BASIS2.items()
            for offset in REFERENCE_NEIGHBOR_OFFSETS
        }
        observed_endpoints = {(template.source_basis, template.target_q2) for template in templates}

        self.assertEqual(len(templates), 48)
        self.assertEqual(observed_endpoints, expected_endpoints)

        for template in templates:
            source_neighbors = translated(template.source_q2, REFERENCE_NEIGHBOR_OFFSETS)
            target_neighbors = translated(template.target_q2, REFERENCE_NEIGHBOR_OFFSETS)
            source_external = source_neighbors - {template.target_q2}
            target_external = target_neighbors - {template.source_q2}
            expected_shared = source_external & target_external

            self.assertEqual(set(template.shared_neighbors_q2), expected_shared)
            self.assertEqual(set(template.initial_unique_neighbors_q2), source_external - expected_shared)
            self.assertEqual(set(template.final_unique_neighbors_q2), target_external - expected_shared)
            self.assertEqual((len(template.initial_unique_neighbors_q2),
                              len(template.final_unique_neighbors_q2),
                              len(template.shared_neighbors_q2)), (7, 7, 4))


if __name__ == "__main__":
    unittest.main()
