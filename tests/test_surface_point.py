"""Offline tests for BSMT Milestone 2.1 (no Blender).

Covers geodesic/surface_point.py: barycentric maths, normalisation,
classification and scratch-mesh endpoint insertion.

Not covered here (requires Blender): the BVH, meshcache, picking and the
operators.

    python3 tests/test_surface_point.py
"""

import importlib.util
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    path = os.path.join(ROOT, "body_surface_measurement", "geodesic", name + ".py")
    spec = importlib.util.spec_from_file_location("bsmt_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sp = _load("surface_point")
topology = _load("topology")

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def close(label, got, expected, tol=1e-12):
    check(label, abs(got - expected) <= tol, "got %r expected %r" % (got, expected))


# --------------------------------------------------------------------------

def two_triangles():
    """Two triangles sharing edge (1, 2)."""
    vertices = np.array([
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [0.0, 10.0, 0.0],
        [10.0, 10.0, 0.0],
    ])
    triangles = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int64)
    return vertices, triangles


def grid_mesh(n=6, spacing=3.0):
    xs, ys = np.meshgrid(np.arange(n + 1) * spacing, np.arange(n + 1) * spacing,
                         indexing="ij")
    vertices = np.stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)], axis=1)
    index = lambda i, j: i * (n + 1) + j
    i, j = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    i = i.ravel()
    j = j.ravel()
    triangles = np.concatenate([
        np.stack([index(i, j), index(i + 1, j), index(i + 1, j + 1)], axis=1),
        np.stack([index(i, j), index(i + 1, j + 1), index(i, j + 1)], axis=1),
    ]).astype(np.int64)
    return vertices, triangles


def total_area(vertices, triangles):
    return float(topology.triangle_areas(vertices, triangles).sum())


def boundary_length(vertices, triangles):
    """Total length of boundary edges. Splitting an edge preserves this,
    while the boundary edge COUNT legitimately grows by one per split."""
    triangles = np.asarray(triangles, dtype=np.int64)
    edge_a, edge_b, counts = topology.unique_edges(
        triangles, int(triangles.max()) + 1)
    boundary = counts == 1
    if not np.any(boundary):
        return 0.0
    segments = vertices[edge_a[boundary]] - vertices[edge_b[boundary]]
    return float(np.linalg.norm(segments, axis=1).sum())


def is_conforming(triangles):
    """No edge may be shared by more than two triangles."""
    _a, _b, counts = topology.unique_edges(np.asarray(triangles, dtype=np.int64),
                                           int(np.max(triangles)) + 1)
    return int(counts.max()) <= 2


# --------------------------------------------------------------------------

def test_barycentric_roundtrip():
    print("\nbarycentric round trip")
    triangle = np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 3.0, 2.0]])
    for target in (
        (0.25, 0.25, 0.5), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
        (0.5, 0.5, 0.0), (0.0, 0.5, 0.5), (1e-9, 0.5, 0.5 - 1e-9),
    ):
        point = sp.reconstruct(triangle, target)
        bary = sp.barycentric(triangle, point)
        error = float(np.abs(bary - np.array(target)).max())
        check("bary %s recovered" % (target,), error < 1e-12, error)
        back = sp.reconstruct(triangle, bary)
        check("position %s reconstructed" % (target,),
              float(np.linalg.norm(back - point)) < 1e-12)

    # scale invariance: barycentrics do not depend on triangle size
    big = triangle * 1000.0
    point = sp.reconstruct(big, (0.2, 0.3, 0.5))
    bary = sp.barycentric(big, point)
    check("barycentrics are scale invariant",
          float(np.abs(bary - np.array([0.2, 0.3, 0.5])).max()) < 1e-12)


def test_affine_invariance():
    print("\nbarycentrics are affine invariant (transform semantics)")
    triangle = np.array([[1.0, 2.0, 3.0], [7.0, 2.0, 3.5], [1.5, 9.0, 1.0]])
    target = np.array([0.3, 0.45, 0.25])
    point = sp.reconstruct(triangle, target)

    angle = 0.7231
    rotation = np.array([
        [np.cos(angle), -np.sin(angle), 0.0],
        [np.sin(angle), np.cos(angle), 0.0],
        [0.0, 0.0, 1.0],
    ])
    for label, linear, offset in (
        ("translation", np.eye(3), np.array([123.0, -45.0, 6.0])),
        ("rotation", rotation, np.zeros(3)),
        ("uniform scale", np.eye(3) * 7.5, np.zeros(3)),
        ("non-uniform scale", np.diag([2.0, 0.5, 3.0]), np.zeros(3)),
        ("rotation+scale+translation", rotation @ np.diag([2.0, 0.5, 3.0]),
         np.array([9.0, 9.0, 9.0])),
    ):
        moved_triangle = triangle @ linear.T + offset
        moved_point = point @ linear.T + offset
        bary = sp.barycentric(moved_triangle, moved_point)
        check("%s: barycentrics unchanged" % label,
              float(np.abs(bary - target).max()) < 1e-11,
              float(np.abs(bary - target).max()))


def test_normalisation_and_classification():
    print("\nnormalisation and classification")
    bary, ok, dev = sp.normalize_barycentric((0.5, 0.5, 0.5))
    check("sum far from 1 is rejected", not ok, dev)

    bary, ok, dev = sp.normalize_barycentric((0.3333333333, 0.3333333333, 0.3333333333))
    check("tiny sum error accepted", ok, dev)
    close("normalised sum is exactly 1", float(bary.sum()), 1.0, 1e-15)

    bary, ok, dev = sp.normalize_barycentric((-1e-12, 0.5, 0.5 + 1e-12))
    check("tiny negative accepted", ok, dev)
    check("negative clamped to zero", bary[0] >= 0.0)

    bary, ok, dev = sp.normalize_barycentric((-0.2, 0.6, 0.6))
    check("point outside the triangle is rejected", not ok, dev)

    check("interior classified FACE",
          sp.classify((0.3, 0.3, 0.4))[0] == sp.KIND_FACE)
    kind, index = sp.classify((0.0, 0.5, 0.5))
    check("edge classified EDGE", kind == sp.KIND_EDGE and index == 0, (kind, index))
    kind, index = sp.classify((1.0, 0.0, 0.0))
    check("vertex classified VERTEX", kind == sp.KIND_VERTEX and index == 0,
          (kind, index))
    check("near-edge below tolerance classified EDGE",
          sp.classify((1e-9, 0.5, 0.5))[0] == sp.KIND_EDGE)
    check("near-edge above tolerance stays FACE",
          sp.classify((1e-5, 0.5, 0.5))[0] == sp.KIND_FACE)

    snapped, kind, index = sp.snap((1e-9, 0.4, 0.6))
    close("snap zeroes the small coordinate", float(snapped[0]), 0.0)
    close("snap keeps the sum at 1", float(snapped.sum()), 1.0, 1e-15)


# --------------------------------------------------------------------------
# scratch mesh insertion
# --------------------------------------------------------------------------

def assert_insertion(label, vertices, triangles, points, expect_added,
                     expect_kinds=None):
    before_v = vertices.copy()
    before_f = triangles.copy()
    result = sp.insert_points(vertices, triangles, points)

    check("%s: canonical vertices untouched" % label,
          np.array_equal(vertices, before_v))
    check("%s: canonical triangles untouched" % label,
          np.array_equal(triangles, before_f))
    check("%s: added %d vertices" % (label, expect_added),
          result.added_vertex_count == expect_added, result.added_vertex_count)
    if expect_kinds is not None:
        check("%s: kinds %s" % (label, expect_kinds),
              result.point_kinds == expect_kinds, result.point_kinds)

    area_before = total_area(vertices, triangles)
    area_after = total_area(result.vertices, result.triangles)
    check("%s: area preserved" % label,
          abs(area_after - area_before) < 1e-9 * max(1.0, area_before),
          "%r vs %r" % (area_after, area_before))

    areas = topology.triangle_areas(result.vertices, result.triangles)
    check("%s: no zero-area triangles" % label, float(areas.min()) > 1e-12,
          float(areas.min()))
    check("%s: indices in range" % label,
          int(result.triangles.max()) < result.vertices.shape[0]
          and int(result.triangles.min()) >= 0)
    check("%s: mesh stays conforming" % label, is_conforming(result.triangles))

    for point, index in zip(points, result.point_vertex_indices):
        triangle_index, bary = point
        expected = sp.reconstruct(vertices[triangles[triangle_index]], bary)
        actual = result.vertices[index]
        check("%s: inserted vertex sits at the point" % label,
              float(np.linalg.norm(actual - expected)) < 1e-9,
              float(np.linalg.norm(actual - expected)))
    return result


def test_insert_case_a_interior():
    print("\ninsertion A: point strictly inside a triangle")
    vertices, triangles = two_triangles()
    result = assert_insertion("interior", vertices, triangles,
                              [(0, (0.25, 0.35, 0.40))], 1, [sp.KIND_FACE])
    check("interior: 1 triangle became 3",
          result.triangles.shape[0] == triangles.shape[0] + 2,
          result.triangles.shape[0])


def test_insert_case_b_edge():
    print("\ninsertion B: point on a shared edge")
    vertices, triangles = two_triangles()
    # edge (1,2) is shared by both triangles; it is corner 0 of triangle 0
    result = assert_insertion("shared edge", vertices, triangles,
                              [(0, (0.0, 0.5, 0.5))], 1, [sp.KIND_EDGE])
    check("shared edge: BOTH incident triangles split",
          result.triangles.shape[0] == 4, result.triangles.shape[0])
    check("shared edge: both originals replaced",
          result.replaced_triangles == {0, 1}, result.replaced_triangles)

    # a boundary edge splits only its single triangle
    result = assert_insertion("boundary edge", vertices, triangles,
                              [(0, (0.5, 0.5, 0.0))], 1, [sp.KIND_EDGE])
    check("boundary edge: only one triangle split",
          result.triangles.shape[0] == 3, result.triangles.shape[0])


def test_insert_case_c_vertex():
    print("\ninsertion C: point exactly on an existing vertex")
    vertices, triangles = two_triangles()
    result = sp.insert_points(vertices, triangles, [(0, (1.0, 0.0, 0.0))])
    check("vertex: no vertex added", result.added_vertex_count == 0)
    check("vertex: existing index reused",
          result.point_vertex_indices == [0], result.point_vertex_indices)
    check("vertex: kind is VERTEX", result.point_kinds == [sp.KIND_VERTEX])
    check("vertex: triangulation unchanged",
          np.array_equal(result.triangles, triangles))

    for corner, expected_index in ((0, 0), (1, 1), (2, 2)):
        bary = [0.0, 0.0, 0.0]
        bary[corner] = 1.0
        outcome = sp.insert_points(vertices, triangles, [(0, tuple(bary))])
        check("vertex corner %d reused" % corner,
              outcome.point_vertex_indices == [expected_index],
              outcome.point_vertex_indices)


def test_insert_case_d_different_triangles():
    print("\ninsertion D: A and B inside different triangles")
    vertices, triangles = grid_mesh()
    points = [(0, (0.3, 0.4, 0.3)), (25, (0.2, 0.5, 0.3))]
    result = assert_insertion("two triangles", vertices, triangles, points, 2,
                              [sp.KIND_FACE, sp.KIND_FACE])
    check("two triangles: 2 originals replaced",
          result.replaced_triangles == {0, 25}, result.replaced_triangles)
    check("two triangles: +4 triangles",
          result.triangles.shape[0] == triangles.shape[0] + 4,
          result.triangles.shape[0])
    check("two triangles: distinct inserted vertices",
          result.point_vertex_indices[0] != result.point_vertex_indices[1])


def test_insert_case_e_same_triangle():
    print("\ninsertion E: A and B inside the SAME triangle")
    vertices, triangles = two_triangles()
    points = [(0, (0.6, 0.2, 0.2)), (0, (0.2, 0.6, 0.2))]
    result = assert_insertion("same triangle", vertices, triangles, points, 2,
                              [sp.KIND_FACE, sp.KIND_FACE])
    check("same triangle: both vertices present",
          len(set(result.point_vertex_indices)) == 2)
    check("same triangle: 1 triangle became 5",
          result.triangles.shape[0] == triangles.shape[0] + 4,
          result.triangles.shape[0])

    # the second point must NOT be resolved against a renumbered triangle
    for index, (triangle_index, bary) in enumerate(points):
        expected = sp.reconstruct(vertices[triangles[triangle_index]], bary)
        actual = result.vertices[result.point_vertex_indices[index]]
        check("same triangle: point %d placed from ORIGINAL topology" % index,
              float(np.linalg.norm(actual - expected)) < 1e-12)


def test_insert_mixed_and_degenerate_cases():
    print("\ninsertion: mixed and awkward combinations")
    vertices, triangles = two_triangles()

    assert_insertion("interior + shared edge", vertices, triangles,
                     [(0, (0.5, 0.3, 0.2)), (0, (0.0, 0.5, 0.5))], 2,
                     [sp.KIND_FACE, sp.KIND_EDGE])

    assert_insertion("two points on the same edge", vertices, triangles,
                     [(0, (0.0, 0.7, 0.3)), (0, (0.0, 0.3, 0.7))], 2,
                     [sp.KIND_EDGE, sp.KIND_EDGE])

    assert_insertion("interior + vertex", vertices, triangles,
                     [(0, (0.4, 0.3, 0.3)), (0, (0.0, 1.0, 0.0))], 1,
                     [sp.KIND_FACE, sp.KIND_VERTEX])

    # coincident points share one inserted vertex
    result = sp.insert_points(vertices, triangles,
                              [(0, (0.3, 0.4, 0.3)), (0, (0.3, 0.4, 0.3))])
    check("coincident points share a vertex",
          result.point_vertex_indices[0] == result.point_vertex_indices[1],
          result.point_vertex_indices)
    check("coincident points add only one vertex", result.added_vertex_count == 1)

    result = sp.insert_points(vertices, triangles, [])
    check("no points: triangles unchanged",
          np.array_equal(result.triangles, triangles))

    try:
        sp.insert_points(vertices, triangles, [(99, (0.3, 0.3, 0.4))])
        check("out-of-range triangle rejected", False)
    except sp.InsertionError:
        check("out-of-range triangle rejected", True)

    try:
        sp.insert_points(vertices, triangles, [(0, (0.9, 0.9, -0.8))])
        check("off-triangle barycentrics rejected", False)
    except sp.InsertionError:
        check("off-triangle barycentrics rejected", True)


def test_insertion_preserves_components():
    print("\ninsertion preserves component structure")
    vertices, triangles = grid_mesh(8)
    before = topology.analyse(vertices, triangles)
    result = sp.insert_points(vertices, triangles,
                              [(3, (0.3, 0.3, 0.4)), (60, (0.0, 0.5, 0.5))])
    after = topology.analyse(result.vertices, result.triangles)
    check("component count unchanged",
          after["component_count"] == before["component_count"])
    # Splitting a boundary edge adds one boundary edge but no boundary length.
    length_before = boundary_length(vertices, triangles)
    length_after = boundary_length(result.vertices, result.triangles)
    check("boundary length preserved",
          abs(length_after - length_before) < 1e-9,
          "%r vs %r" % (length_after, length_before))
    check("boundary edge count grows only by splits",
          0 <= after["boundary_edge_count"] - before["boundary_edge_count"] <= 2,
          "%d vs %d" % (after["boundary_edge_count"], before["boundary_edge_count"]))
    check("no non-manifold edges introduced",
          after["nonmanifold_edge_count"] == 0)


def test_reconstruction_tolerance_grid():
    print("\nreconstruction accuracy across a triangle")
    vertices, triangles = grid_mesh(4)
    worst = 0.0
    samples = 0
    for triangle_index in range(triangles.shape[0]):
        corners = vertices[triangles[triangle_index]]
        for target in (
            (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
            (0.5, 0.5, 0.0), (0.0, 0.5, 0.5), (0.5, 0.0, 0.5),
            (1e-8, 0.5, 0.5 - 1e-8), (1 - 2e-8, 1e-8, 1e-8),
            (1 / 3.0, 1 / 3.0, 1 / 3.0), (0.05, 0.9, 0.05),
        ):
            point = sp.reconstruct(corners, target)
            bary = sp.barycentric(corners, point)
            back = sp.reconstruct(corners, bary)
            worst = max(worst, float(np.linalg.norm(back - point)))
            samples += 1
    print("        %d samples, worst reconstruction error %.3e" % (samples, worst))
    check("interior/edge/vertex reconstruction under 1e-9", worst < 1e-9, worst)


# ---------------------------------------------------------------------------
# Accepting a BVH hit (Milestone 3.12)
# ---------------------------------------------------------------------------

#: A triangle with an awkward shape and off-origin position, so nothing here
#: passes by accident on a tidy unit triangle.
AWKWARD = np.array([
    [0.29182687, -0.00341, 0.95509612],
    [0.30119, 0.01204, 0.93871],
    [0.27455, 0.00087, 0.94120],
], dtype=np.float64)


def as_float32(point):
    """What a mathutils Vector does to a coordinate: single precision."""
    return np.asarray(np.asarray(point, dtype=np.float32), dtype=np.float64)


def point_at(triangle, bary):
    return sp.reconstruct(triangle, np.asarray(bary, dtype=np.float64))


def test_hit_on_a_triangle():
    print("\n[hit] a hit is seated on the triangle the BVH named")
    cases = (
        ("interior", (0.3, 0.35, 0.35)),
        ("near an edge", (1e-9, 0.5, 0.5 - 1e-9)),
        ("exactly on an edge", (0.0, 0.5, 0.5)),
        ("near a vertex", (1.0 - 2e-9, 1e-9, 1e-9)),
        ("exactly on a vertex", (1.0, 0.0, 0.0)),
        ("on the second vertex", (0.0, 1.0, 0.0)),
        ("on the third vertex", (0.0, 0.0, 1.0)),
    )
    for label, bary in cases:
        exact = point_at(AWKWARD, bary)
        result = sp.locate_hit(AWKWARD, exact)
        check("%s is accepted" % label, result["ok"],
              "off_plane %.3e residual %.3e tol %.3e"
              % (result["off_plane"], result["residual"], result["tolerance"]))
        check("  its coordinates sum to exactly 1",
              abs(float(result["bary"].sum()) - 1.0) < 1e-15,
              float(result["bary"].sum()) - 1.0)
        check("  and none is outside [0, 1]",
              result["bary"].min() >= 0.0 and result["bary"].max() <= 1.0,
              result["bary"])
        rebuilt = sp.reconstruct(AWKWARD, result["bary"])
        check("  the reconstruction matches the hit",
              float(np.linalg.norm(rebuilt - exact)) < 1e-12,
              float(np.linalg.norm(rebuilt - exact)))

    # The same points after float32 rounding - which is what a BVH returns.
    for label, bary in cases:
        rounded = as_float32(point_at(AWKWARD, bary))
        result = sp.locate_hit(AWKWARD, rounded)
        check("%s survives float32 rounding" % label, result["ok"],
              "off_plane %.3e residual %.3e tol %.3e"
              % (result["off_plane"], result["residual"], result["tolerance"]))
        check("  and is still a convex combination",
              abs(float(result["bary"].sum()) - 1.0) < 1e-15
              and result["bary"].min() >= 0.0)


def test_the_reproduced_failure():
    print("\n[hit] the failure this milestone exists to fix")
    # Measured on a decimated body scan: a hit whose float32 position is a
    # fraction OUTSIDE the triangle the BVH named, because the true
    # intersection lay on a shared edge. The old barycentric rule saw an
    # excursion of 3.7e-06 and refused the pick; geometrically the point was
    # 5.8e-08 units - 58 nanometres - from the triangle.
    edge_point = point_at(AWKWARD, (0.0, 0.5, 0.5))
    normal = np.cross(AWKWARD[1] - AWKWARD[0], AWKWARD[2] - AWKWARD[0])
    normal = normal / np.linalg.norm(normal)
    outward = np.cross(normal, AWKWARD[2] - AWKWARD[1])
    outward = outward / np.linalg.norm(outward)
    if float(np.dot(outward, AWKWARD[0] - edge_point)) > 0:
        outward = -outward

    # The ray was cast from 5 units away, and a hit is computed as
    # origin + t*direction, so the float32 spacing that produced the error is
    # the RAY's, not the triangle's. That is exactly why the barycentric
    # excursion was large on a triangle only ~20 mm across.
    ray_scale = 5.0
    ulp = sp.FLOAT32_EPS * ray_scale
    just_outside = edge_point + outward * (0.2 * ulp) + normal * (2.3 * ulp)

    old_bary = sp.barycentric(AWKWARD, just_outside)
    _clean, old_ok, old_deviation = sp.normalize_barycentric(old_bary)
    check("the old barycentric rule refuses it", not old_ok,
          "deviation %.3e against SUM_TOLERANCE %.1e"
          % (old_deviation, sp.SUM_TOLERANCE))
    check("  and the excursion it saw is ~1e-6, as measured in Blender",
          1e-6 < old_deviation < 1e-4, old_deviation)

    result = sp.locate_hit(AWKWARD, just_outside, ray_scale=ray_scale)
    check("the geometric rule accepts it", result["ok"],
          "off_plane %.3e residual %.3e tol %.3e"
          % (result["off_plane"], result["residual"], result["tolerance"]))
    check("  because it only has to move a fraction of a float32 ulp",
          result["residual"] < ulp, "%.3e vs ulp %.3e" % (result["residual"], ulp))
    check("  a displacement of nanometres, not micrometres",
          result["residual"] < 1e-6, result["residual"])
    check("  and the result is a valid convex combination",
          abs(float(result["bary"].sum()) - 1.0) < 1e-15
          and result["bary"].min() >= 0.0 and result["bary"].max() <= 1.0,
          result["bary"])


def test_the_old_rule_was_blind_off_the_plane():
    print("\n[hit] the old rule measured the wrong thing in BOTH directions")
    # `barycentric` computes the coordinates of the ORTHOGONAL PROJECTION onto
    # the triangle plane - so a point arbitrarily far off the surface, whose
    # projection lands inside the triangle, produced perfectly clean
    # coordinates and passed. The old rule had no off-plane check at all.
    flat = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    centre = flat.mean(axis=0)
    for distance in (1e-6, 1e-3, 1.0, 1000.0):
        point = centre + np.array([0.0, 0.0, distance])
        _clean, old_ok, old_deviation = sp.normalize_barycentric(
            sp.barycentric(flat, point))
        check("the OLD rule accepted a point %g off the plane" % distance,
              old_ok, "deviation %.1e" % old_deviation)
        check("  the new rule refuses it",
              not sp.locate_hit(flat, point)["ok"])
    check("and both still accept a point actually on the plane",
          sp.normalize_barycentric(sp.barycentric(flat, centre))[1]
          and sp.locate_hit(flat, centre)["ok"])

    # So the change is not "a looser tolerance". It is stricter off the plane
    # and looser only within float32 noise in it.
    tolerance = sp.hit_tolerance(flat, centre)
    check("the off-plane bound is a few float32 ulps",
          tolerance < 1e-6, tolerance)


def test_genuinely_invalid_points_are_still_refused():
    print("\n[hit] nothing that is really off the triangle gets through")
    centre = point_at(AWKWARD, (1 / 3.0, 1 / 3.0, 1 / 3.0))
    normal = np.cross(AWKWARD[1] - AWKWARD[0], AWKWARD[2] - AWKWARD[0])
    normal = normal / np.linalg.norm(normal)
    tolerance = sp.hit_tolerance(AWKWARD, centre)
    check("the tolerance is sub-micron on a metre-scale mesh",
          tolerance < 1e-6, tolerance)

    for label, distance in (("1 mm", 1e-3), ("0.1 mm", 1e-4), ("1 um", 1e-6),
                            ("10x tolerance", tolerance * 10.0),
                            ("just over tolerance", tolerance * 1.01)):
        result = sp.locate_hit(AWKWARD, centre + normal * distance)
        check("%s off the plane is refused" % label, not result["ok"],
              "off_plane %.3e tol %.3e" % (result["off_plane"], tolerance))
    result = sp.locate_hit(AWKWARD, centre + normal * tolerance * 0.5)
    check("half a tolerance off the plane is accepted", result["ok"])

    # Beside the triangle, in its own plane.
    direction = AWKWARD[0] - AWKWARD[1]
    direction = direction / np.linalg.norm(direction)
    for label, distance in (("1 mm", 1e-3), ("1 um", 1e-6),
                            ("10x tolerance", tolerance * 10.0)):
        result = sp.locate_hit(AWKWARD, AWKWARD[0] + direction * distance)
        check("%s beside the triangle is refused" % label, not result["ok"],
              "residual %.3e tol %.3e" % (result["residual"], tolerance))

    check("a non-finite point is refused",
          not sp.locate_hit(AWKWARD, [np.nan, 0.0, 0.0])["ok"])
    check("  and reports an infinite residual",
          not np.isfinite(
              sp.locate_hit(AWKWARD, [np.inf, 0.0, 0.0])["residual"]))


def test_tolerance_is_scale_aware():
    print("\n[hit] the tolerance follows the coordinates, not a constant")
    small = AWKWARD.copy()
    metres = sp.hit_tolerance(small, small.mean(axis=0))
    millimetres = sp.hit_tolerance(small * 1000.0,
                                              small.mean(axis=0) * 1000.0)
    check("a mesh in millimetres gets a 1000x larger tolerance",
          abs(millimetres / metres - 1000.0) < 1e-9,
          millimetres / metres)
    check("  which is the SAME physical fraction",
          abs((millimetres / 1000.0) / metres - 1.0) < 1e-9)

    # Sect. 6: large mm-scale scanner coordinates, the real case.
    scanner = AWKWARD * 1000.0 + np.array([1200.0, -450.0, 1700.0])
    for label, bary in (("interior", (0.3, 0.35, 0.35)),
                        ("on an edge", (0.0, 0.5, 0.5)),
                        ("on a vertex", (0.0, 0.0, 1.0))):
        rounded = as_float32(point_at(scanner, bary))
        result = sp.locate_hit(scanner, rounded)
        check("mm-scale scanner coordinates, %s" % label, result["ok"],
              "off_plane %.3e residual %.3e tol %.3e"
              % (result["off_plane"], result["residual"], result["tolerance"]))
    tolerance = sp.hit_tolerance(scanner, scanner.mean(axis=0))
    check("the mm-scale tolerance is a few microns, not a few mm",
          1e-4 < tolerance < 1e-2, tolerance)

    # The ray's own magnitude counts: a hit computed as origin + t*direction
    # from far away is intrinsically less precise.
    near = sp.hit_tolerance(AWKWARD, AWKWARD[0], ray_scale=1.0)
    far = sp.hit_tolerance(AWKWARD, AWKWARD[0], ray_scale=1000.0)
    check("a ray cast from far away gets a proportionally larger tolerance",
          abs(far / near - 1000.0) < 1e-9, far / near)
    check("and a ray from close by does not shrink it below the mesh scale",
          sp.hit_tolerance(AWKWARD, AWKWARD[0], ray_scale=0.0) > 0.0)

    check("the ulp count is a named, derived constant",
          sp.HIT_TOLERANCE_ULPS == 16)
    check("and float32 epsilon is the real one",
          abs(sp.FLOAT32_EPS - 5.9604644775390625e-08) < 1e-20)


def test_transformed_object():
    print("\n[hit] an object transform changes nothing about the answer")
    rng = np.random.default_rng(31)
    for seed in range(6):
        rotation, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        if np.linalg.det(rotation) < 0:
            rotation[:, 0] *= -1
        offset = rng.normal(size=3) * 10.0
        moved = AWKWARD @ rotation.T + offset

        for label, bary in (("interior", (0.3, 0.35, 0.35)),
                            ("on an edge", (0.0, 0.5, 0.5)),
                            ("on a vertex", (1.0, 0.0, 0.0))):
            here = sp.locate_hit(
                AWKWARD, as_float32(point_at(AWKWARD, bary)))
            there = sp.locate_hit(
                moved, as_float32(point_at(moved, bary)))
            check("seed %d, %s: accepted in both frames" % (seed, label),
                  here["ok"] and there["ok"])
            # The float32 rounding differs between frames, so the barycentric
            # DIGITS differ slightly. What must hold is that each frame seats
            # the point where that frame's hit actually was, within that
            # frame's own tolerance - which is the property that matters.
            check("  each frame seats its own hit within tolerance",
                  here["residual"] <= here["tolerance"]
                  and there["residual"] <= there["tolerance"])
            check("  and both land on the same place on the triangle",
                  np.allclose(here["bary"], there["bary"], atol=1e-4),
                  "%s vs %s" % (here["bary"], there["bary"]))
            mapped = rotation @ here["point"] + offset
            check("  the transformed answer IS the answer transformed",
                  float(np.linalg.norm(mapped - there["point"]))
                  <= there["tolerance"] * 2.0,
                  "%.3e vs tol %.3e"
                  % (float(np.linalg.norm(mapped - there["point"])),
                     there["tolerance"]))


def test_the_triangle_is_never_reconsidered():
    print("\n[hit] no neighbour search, no vertex snap, no other face")
    source = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "body_surface_measurement", "geodesic", "surface_point.py")).read()
    body = source.split("def locate_hit")[1].split("\ndef ")[0]
    # Strip the docstring: it SAYS "no neighbour is searched", which is the
    # claim, not a violation of it.
    code = body.split('"""')[-1]
    for forbidden in ("find_nearest", "neighbour", "neighbor", "argmin",
                      "argmax", "for triangle", "triangles[", "vertices["):
        check("locate_hit never uses %s" % forbidden, forbidden not in code)

    cache = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "body_surface_measurement", "geodesic", "meshcache.py")).read()
    caster = cache.split("def ray_cast_local")[1].split("\ndef ")[0]
    check("ray_cast_local still uses the BVH's own triangle index",
          "triangle_index = int(hit[2])" in caster)
    check("  and never looks for another",
          "find_nearest" not in caster)
    check("  and refuses rather than substituting",
          'if not seated["ok"]:' in caster and "return None" in caster)

    # An interior hit must come back exactly as it went in - the projection
    # and clamp are no-ops there, not a quiet nudge.
    exact = point_at(AWKWARD, (0.3, 0.35, 0.35))
    result = sp.locate_hit(AWKWARD, exact)
    check("an interior hit is not moved at all",
          float(np.linalg.norm(result["point"] - exact)) < 1e-15,
          float(np.linalg.norm(result["point"] - exact)))
    check("  and its coordinates are the plain barycentric ones",
          np.allclose(result["bary"], (0.3, 0.35, 0.35), atol=1e-12),
          result["bary"])


def test_stored_point_validation_is_unchanged():
    print("\n[hit] the STORED-point check stays exactly as strict")
    check("SUM_TOLERANCE is untouched", sp.SUM_TOLERANCE == 1e-6)
    _c, ok, _d = sp.normalize_barycentric((0.5, 0.5, 0.0))
    check("a clean triple is still accepted", ok)
    _c, ok, deviation = sp.normalize_barycentric((0.5, 0.5, 0.1))
    check("a triple that does not sum to 1 is still refused", not ok, deviation)
    _c, ok, _d = sp.normalize_barycentric((-0.01, 0.5, 0.51))
    check("a negative coordinate is still refused", not ok)
    _c, ok, _d = sp.normalize_barycentric((np.nan, 0.5, 0.5))
    check("a non-finite triple is still refused", not ok)

    # And what locate_hit produces always passes it, including after the
    # float32 round trip a stored SurfacePoint goes through.
    for bary in ((0.3, 0.35, 0.35), (0.0, 0.5, 0.5), (1.0, 0.0, 0.0),
                 (1e-9, 1 - 2e-9, 1e-9)):
        result = sp.locate_hit(
            AWKWARD, as_float32(point_at(AWKWARD, bary)))
        stored = np.asarray(np.asarray(result["bary"], dtype=np.float32),
                            dtype=np.float64)
        _c, ok, deviation = sp.normalize_barycentric(stored)
        check("what locate_hit produces survives float32 storage %s"
              % (bary,), ok, "deviation %.3e" % deviation)


def main():
    print("BSMT Milestone 2.1 - SurfacePoint tests (no Blender)")
    for test in (
        test_barycentric_roundtrip,
        test_affine_invariance,
        test_normalisation_and_classification,
        test_insert_case_a_interior,
        test_insert_case_b_edge,
        test_insert_case_c_vertex,
        test_insert_case_d_different_triangles,
        test_insert_case_e_same_triangle,
        test_insert_mixed_and_degenerate_cases,
        test_insertion_preserves_components,
        test_reconstruction_tolerance_grid,
        test_hit_on_a_triangle,
        test_the_reproduced_failure,
        test_the_old_rule_was_blind_off_the_plane,
        test_genuinely_invalid_points_are_still_refused,
        test_tolerance_is_scale_aware,
        test_transformed_object,
        test_the_triangle_is_never_reconsidered,
        test_stored_point_validation_is_unchanged,
    ):
        test()

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
