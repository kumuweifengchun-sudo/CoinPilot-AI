import math

from coinpilot_ai.charts.regions import enclosed_region


def edges(points):
    return [(a, b, 0, 1) for a, b in zip(points, points[1:]+points[:1])]


def test_bounded_face_from_crossing_lines_and_subdivision():
    lines = [((0, 0), (1, 0), -math.inf, math.inf),
             ((0, 10), (1, 10), -math.inf, math.inf),
             ((0, -5), (20, 15), 0, 1), ((20, -5), (0, 15), 0, 1)]
    face = enclosed_region(lines, (10, 2))
    assert set(face) == {(5, 0), (15, 0), (10, 5)}
    assert enclosed_region(lines, (2, 2)) is None


def test_open_chain_ray_direction_and_no_viewport_boundary():
    chain = [((0, 0), (10, 0), 0, 1), ((10, 0), (10, 10), 0, 1)]
    assert enclosed_region(chain, (8, 2)) is None
    assert enclosed_region(chain+[((10, 10), (0, 0), 0, math.inf)], (8, 2))
    assert enclosed_region(chain+[((0, 0), (-10, -10), 0, math.inf)], (8, 2)) is None


def test_overlapping_edges_dangling_lines_and_nested_contours():
    square = edges([(0, 0), (10, 0), (10, 10), (0, 10)])
    square += [((0, 0), (5, 0), 0, 1), ((10, 10), (15, 15), 0, 1)]
    assert enclosed_region(square, (5, 5))
    assert enclosed_region(square, (12, 12)) is None
    square += edges([(3, 3), (7, 3), (7, 7), (3, 7)])
    assert set(enclosed_region(square, (5, 5))) == {(3, 3), (7, 3), (7, 7), (3, 7)}
