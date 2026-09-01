"""Offline unit tests for BSMT Milestone 2.0.

Runs OUTSIDE Blender with plain python3 + numpy. Covers geodesic/topology.py
and geodesic/spaces.py, which import only numpy by design.

Not covered here (requires Blender): geodesic/extract.py, the operators, the
panel, and anything touching bpy.

    python3 tests/test_topology.py
"""

import importlib.util
import math
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    path = os.path.join(ROOT, "body_surface_measurement", "geodesic", name + ".py")
    spec = importlib.util.spec_from_file_location("bsmt_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


topology = _load("topology")
spaces = _load("spaces")

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def close(label, got, expected, tol=1e-9):
    check(label, abs(got - expected) <= tol, "got %r expected %r" % (got, expected))


# --------------------------------------------------------------------------
# mesh fixtures
# --------------------------------------------------------------------------

def cube():
    """Closed cube, 8 vertices, 12 triangles, 18 edges, genus 0."""
    v = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=np.float64)
    f = np.array([
        [0, 2, 1], [0, 3, 2],      # bottom
        [4, 5, 6], [4, 6, 7],      # top
        [0, 1, 5], [0, 5, 4],      # -y
        [1, 2, 6], [1, 6, 5],      # +x
        [2, 3, 7], [2, 7, 6],      # +y
        [3, 0, 4], [3, 4, 7],      # -x
    ], dtype=np.int32)
    return v, f


def tetrahedron(offset=0.0):
    v = np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
    ], dtype=np.float64) + np.array([offset, 0.0, 0.0])
    f = np.array([
        [0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3],
    ], dtype=np.int32)
    return v, f


def grid(nx, ny, spacing=1.0):
    """Open triangulated rectangle: 2*nx*ny triangles, 2*(nx+ny) boundary edges."""
    xs, ys = np.meshgrid(
        np.arange(nx + 1) * spacing, np.arange(ny + 1) * spacing, indexing="ij"
    )
    v = np.stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)], axis=1).astype(np.float64)

    def index(i, j):
        return i * (ny + 1) + j

    i, j = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    i = i.ravel()
    j = j.ravel()
    f = np.concatenate([
        np.stack([index(i, j), index(i + 1, j), index(i + 1, j + 1)], axis=1),
        np.stack([index(i, j), index(i + 1, j + 1), index(i, j + 1)], axis=1),
    ]).astype(np.int32)
    return v, f


def permute_vertices(mesh, seed):
    """Randomise vertex numbering without changing the surface.

    Real scan vertex order has no relation to spatial position. Regular
    generated meshes do, which is exactly why the original component
    labelling bug survived the first test suite: ordered numbering converged
    in 2 rounds, arbitrary numbering did not converge at all.
    """
    vertices, faces = mesh
    rng = np.random.default_rng(seed)
    order = rng.permutation(vertices.shape[0])
    permuted = np.empty_like(vertices)
    permuted[order] = vertices
    return permuted, order[faces].astype(np.int32)


def merge(a, b):
    va, fa = a
    vb, fb = b
    return np.vstack([va, vb]), np.vstack([fa, fb + len(va)]).astype(np.int32)


# --------------------------------------------------------------------------
# topology tests
# --------------------------------------------------------------------------

def test_cube():
    print("\ncube (closed, 8v/12t)")
    v, f = cube()
    r = topology.analyse(v, f)
    check("vertex count 8", r["vertex_count"] == 8)
    check("triangle count 12", r["triangle_count"] == 12)
    check("edge count 18 (Euler V-E+F=2)", r["edge_count"] == 18, r["edge_count"])
    check("1 component", r["component_count"] == 1)
    check("0 boundary edges", r["boundary_edge_count"] == 0)
    check("0 non-manifold edges", r["nonmanifold_edge_count"] == 0)
    check("0 duplicates", r["duplicate_vertex_count"] == 0)
    check("0 degenerate triangles", r["degenerate_triangle_count"] == 0)
    check("0 loose vertices", r["loose_vertex_count"] == 0)
    check("no warnings", topology.warnings_for(r) == [])
    close("bbox x", r["bbox_dimensions"][0], 1.0)


def test_open_cube():
    print("\ncube with one face removed (cropping analogue)")
    v, f = cube()
    f = f[2:]                                  # drop the two top triangles
    r = topology.analyse(v, f)
    check("10 triangles", r["triangle_count"] == 10)
    check("4 boundary edges", r["boundary_edge_count"] == 4, r["boundary_edge_count"])
    check("17 edges", r["edge_count"] == 17, r["edge_count"])
    check("still 1 component", r["component_count"] == 1)
    check("boundary warning raised", any("boundary" in w for w in topology.warnings_for(r)))


def test_disconnected():
    print("\ntwo separated tetrahedra (disconnected components)")
    v, f = merge(tetrahedron(0.0), tetrahedron(10.0))
    r = topology.analyse(v, f)
    check("2 components", r["component_count"] == 2, r["component_count"])
    check("component sizes 4,4", r["component_triangle_counts"] == [4, 4])
    check("0 boundary edges", r["boundary_edge_count"] == 0)
    check("component warning raised", any("component" in w for w in topology.warnings_for(r)))


def test_many_components():
    print("\nfive separated tetrahedra")
    mesh = tetrahedron(0.0)
    for k in range(1, 5):
        mesh = merge(mesh, tetrahedron(10.0 * k))
    r = topology.analyse(*mesh)
    check("5 components", r["component_count"] == 5, r["component_count"])
    check("sizes all 4", r["component_triangle_counts"] == [4] * 5)


def test_nonmanifold():
    print("\nthree triangles sharing one edge (non-manifold)")
    v = np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1],
    ], dtype=np.float64)
    f = np.array([[0, 1, 2], [0, 1, 3], [0, 1, 4]], dtype=np.int32)
    r = topology.analyse(v, f)
    check("1 non-manifold edge", r["nonmanifold_edge_count"] == 1, r["nonmanifold_edge_count"])
    check("1 component", r["component_count"] == 1)
    check("non-manifold warning raised",
          any("non-manifold" in w for w in topology.warnings_for(r)))


def test_duplicates_are_reported_not_welded():
    print("\ncoincident-but-distinct vertices (the welding safety case)")
    # Two triangles that touch geometrically at a shared position but use
    # different vertex indices: like arm-to-torso self contact.
    v = np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0],
        [0, 0, 0], [-1, 0, 0], [0, -1, 0],
    ], dtype=np.float64)
    f = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
    r = topology.analyse(v, f)
    check("1 duplicate vertex reported", r["duplicate_vertex_count"] == 1,
          r["duplicate_vertex_count"])
    check("1 duplicate group", r["duplicate_group_count"] == 1)
    check("STILL 2 components (not welded)", r["component_count"] == 2,
          r["component_count"])
    check("duplicate warning mentions welding",
          any("weld" in w for w in topology.warnings_for(r)))


def test_near_coincident_tolerance():
    print("\nnear-coincident detection honours the tolerance")
    v = np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0],
        [0.005, 0, 0], [-1, 0, 0], [0, -1, 0],
    ], dtype=np.float64)
    f = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
    loose = topology.analyse(v, f, near_tolerance=0.01)
    tight = topology.analyse(v, f, near_tolerance=0.001)
    check("detected at 0.01 mm", loose["near_coincident_count"] >= 1,
          loose["near_coincident_count"])
    check("not detected at 0.001 mm", tight["near_coincident_count"] == 0,
          tight["near_coincident_count"])
    check("exact duplicates still 0", loose["duplicate_vertex_count"] == 0)
    check("components unchanged by detection", loose["component_count"] == 2)


def test_degenerate_and_loose():
    print("\ndegenerate triangle and loose vertices")
    v = np.array([
        [0, 0, 0], [1, 0, 0], [2, 0, 0],       # collinear -> zero area
        [0, 5, 0], [1, 5, 0], [0, 6, 0],
        [99, 99, 99],                           # loose
    ], dtype=np.float64)
    f = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
    r = topology.analyse(v, f)
    check("1 degenerate triangle", r["degenerate_triangle_count"] == 1,
          r["degenerate_triangle_count"])
    check("1 zero-area triangle", r["zero_area_triangle_count"] == 1)
    check("1 loose vertex", r["loose_vertex_count"] == 1)
    check("loose vertex not a component", r["component_count"] == 2,
          r["component_count"])


def test_grid_counts():
    print("\nopen grid: analytic edge/boundary counts")
    for nx, ny in ((1, 1), (4, 3), (17, 11)):
        v, f = grid(nx, ny)
        r = topology.analyse(v, f)
        expected_edges = 3 * nx * ny + nx + ny
        expected_boundary = 2 * (nx + ny)
        check("grid %dx%d triangles" % (nx, ny), r["triangle_count"] == 2 * nx * ny)
        check("grid %dx%d edges = 3nm+n+m" % (nx, ny),
              r["edge_count"] == expected_edges,
              "%d vs %d" % (r["edge_count"], expected_edges))
        check("grid %dx%d boundary = 2(n+m)" % (nx, ny),
              r["boundary_edge_count"] == expected_boundary,
              "%d vs %d" % (r["boundary_edge_count"], expected_boundary))
        check("grid %dx%d single component" % (nx, ny), r["component_count"] == 1)
        close("grid %dx%d median edge length" % (nx, ny),
              r["edge_length_median"], 1.0, 1e-12)


def test_component_labels():
    print("\ncomponent_labels: ordering, counts, coverage")
    # sizes 4, 4, 4 would be ambiguous, so build unequal components
    mesh = merge(grid(6, 6), tetrahedron(100.0))          # 72 tris, then 4
    mesh = merge(mesh, tetrahedron(200.0))                # + 4
    v, f = mesh
    tri_labels, vert_labels, tri_counts, vert_counts = topology.component_labels(v, f)

    check("three components found", len(tri_counts) == 3, tri_counts)
    check("largest first", tri_counts == sorted(tri_counts, reverse=True), tri_counts)
    check("largest is the grid", tri_counts[0] == 72, tri_counts[0])
    check("triangle labels cover every triangle", tri_labels.shape[0] == f.shape[0])
    check("labels are 0..k-1",
          int(tri_labels.min()) == 0 and int(tri_labels.max()) == 2)
    check("per-label counts match reported counts",
          [int((tri_labels == i).sum()) for i in range(3)] == tri_counts)
    check("vertex counts sum to used vertices",
          sum(vert_counts) == int(np.unique(f).size),
          "%d vs %d" % (sum(vert_counts), int(np.unique(f).size)))
    check("grid vertex count correct", vert_counts[0] == 49, vert_counts[0])
    check("unused vertices labelled -1",
          int((vert_labels == -1).sum()) == v.shape[0] - int(np.unique(f).size))

    # agreement with analyse()
    r = topology.analyse(v, f)
    check("component_labels agrees with analyse",
          r["component_triangle_counts"] == tri_counts)

    # a single-component mesh
    v2, f2 = cube()
    t2, _vl2, tc2, vc2 = topology.component_labels(v2, f2)
    check("cube: one component", tc2 == [12], tc2)
    check("cube: all triangles label 0", int(t2.max()) == 0)
    check("cube: 8 vertices in component", vc2 == [8], vc2)

    # empty mesh
    t3, vl3, tc3, vc3 = topology.component_labels(
        np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32))
    check("empty mesh: no components", tc3 == [] and vc3 == [])


def test_multi_component_warning_is_advisory():
    print("\nmulti-component warning states measurement is still allowed")
    v, f = merge(tetrahedron(0.0), tetrahedron(10.0))
    messages = topology.warnings_for(topology.analyse(v, f))
    text = " ".join(messages)
    check("warning says it does not invalidate measurement",
          "does NOT invalidate" in text, text)
    check("warning explains the same-component rule",
          "same component" in text, text)
    check("warning names DISCONNECTED for cross-component pairs",
          "DISCONNECTED" in text, text)
    check("warning states components are never welded",
          "never welded" in text, text)


def test_connectivity_is_numbering_independent():
    """The regression test for the component labelling defect.

    A connected surface must report exactly one component no matter how its
    vertices are numbered. The previous min-label hooking propagated labels
    one edge per round, needed O(diameter) rounds, silently stopped at an
    iteration cap and split single components into spatially disconnected
    pieces - the "same colour on head and forearm" symptom.
    """
    print("\nconnectivity is independent of vertex numbering")
    base = grid(120, 120)
    reference = topology.analyse(*base)
    check("ordered numbering: 1 component", reference["component_count"] == 1)

    for seed in (1, 2, 3, 17, 99):
        vertices, faces = permute_vertices(base, seed)
        result = topology.analyse(vertices, faces)
        check("permuted seed=%d: still 1 component" % seed,
              result["component_count"] == 1, result["component_count"])
        check("permuted seed=%d: same triangle count" % seed,
              result["triangle_count"] == reference["triangle_count"])
        check("permuted seed=%d: same edge count" % seed,
              result["edge_count"] == reference["edge_count"])
        check("permuted seed=%d: same boundary count" % seed,
              result["boundary_edge_count"] == reference["boundary_edge_count"])

    # multi-component mesh under permutation
    multi = merge(merge(grid(40, 40), tetrahedron(500.0)), tetrahedron(900.0))
    ordered = topology.analyse(*multi)
    for seed in (5, 6):
        vertices, faces = permute_vertices(multi, seed)
        result = topology.analyse(vertices, faces)
        check("multi permuted seed=%d: same component count" % seed,
              result["component_count"] == ordered["component_count"],
              result["component_count"])
        check("multi permuted seed=%d: same component sizes" % seed,
              sorted(result["component_triangle_counts"])
              == sorted(ordered["component_triangle_counts"]))


def test_labelling_certificate():
    print("\nlabelling carries a correctness certificate")
    vertices, faces = permute_vertices(grid(80, 80), 4)
    faces64 = faces.astype(np.int64)
    edge_a, edge_b, _ = topology.unique_edges(faces64, vertices.shape[0])
    labels = topology.connected_components(vertices.shape[0], edge_a, edge_b)
    check("every edge joins equal labels",
          topology.labels_are_consistent(labels, edge_a, edge_b))

    # A deliberately wrong labelling must be rejected by the certificate.
    broken = labels.copy()
    broken[int(edge_a[0])] = -1
    check("certificate rejects a corrupted labelling",
          not topology.labels_are_consistent(broken, edge_a, edge_b))

    check("TopologyError exists for refusal",
          issubclass(topology.TopologyError, Exception))


def test_component_verification():
    print("\nindependent per-component verification")
    multi = merge(merge(grid(30, 30), tetrahedron(500.0)), tetrahedron(900.0))
    vertices, faces = permute_vertices(multi, 11)
    faces64 = faces.astype(np.int64)
    labels, _vl, counts, _vc = topology.component_labels(vertices, faces64)
    rows, problems = topology.verify_component_labels(
        vertices, faces64, labels, counts)

    check("verification reports no problems", problems == [], problems)
    check("one row per component", len(rows) == len(counts))
    for row in rows:
        check("component %d holds exactly 1 connected piece" % row["component"],
              row["independent_components"] == 1, row["independent_components"])
        check("component %d actual == expected" % row["component"],
              row["actual_triangles"] == row["expected_triangles"])
    check("rows sum to the canonical triangle count",
          sum(r["actual_triangles"] for r in rows) == faces64.shape[0])

    # Inject a deliberately wrong labelling: move triangles of component 2
    # into component 1, making component 1 disconnected.
    corrupted = labels.copy()
    victim = np.flatnonzero(corrupted == 1)
    corrupted[victim] = 0
    bad_counts = list(counts)
    bad_counts[0] += len(victim)
    bad_counts[1] = 0
    rows2, problems2 = topology.verify_component_labels(
        vertices, faces64, corrupted, bad_counts)
    check("corrupted labelling is detected", problems2 != [])
    check("failure names the disconnected component",
          any("not connected" in p for p in problems2), problems2)
    check("formatting works with problems",
          any("INTEGRITY FAILURES" in line
              for line in topology.format_verification(rows2, problems2)))


def test_empty():
    print("\nempty mesh does not crash")
    r = topology.analyse(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32))
    check("0 vertices", r["vertex_count"] == 0)
    check("0 components", r["component_count"] == 0)
    check("formats without error", len(topology.format_report(r)) > 0)


def test_component_labels_chain():
    """A long thin strip forces many hooking iterations - convergence check."""
    print("\nlong strip: component labelling convergence")
    v, f = grid(2000, 1)
    r = topology.analyse(v, f)
    check("long strip is one component", r["component_count"] == 1,
          r["component_count"])


# --------------------------------------------------------------------------
# solver space / cache identity tests  (PROJECT_SPEC sect. 6.2-6.4)
# --------------------------------------------------------------------------

def rotation_matrix(axis, angle):
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / np.linalg.norm(axis)
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    return np.array([
        [c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
        [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
        [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)],
    ])


def matrix(linear=None, translation=(0.0, 0.0, 0.0)):
    m = np.eye(4)
    if linear is not None:
        m[:3, :3] = linear
    m[:3, 3] = translation
    return m


def pairwise(v):
    """A transform-sensitive summary of the geometry: all pair distances."""
    diff = v[:, None, :] - v[None, :, :]
    return np.linalg.norm(diff, axis=2)


def test_solver_space_pipeline():
    print("\nsolver space: unit conversion, centering, float64")
    v, f = cube()
    v = v * 100.0                                  # 100-unit cube

    solver, center = spaces.to_solver_space(v, matrix(), 1.0)
    check("float64 output", solver.dtype == np.float64)
    close("centered on origin", float(np.abs(solver.mean(axis=0)).max()), 0.0, 1e-12)
    close("edge stays 100 mm at unit=mm", float(pairwise(solver)[0, 1]), 100.0, 1e-9)

    solver_cm, _ = spaces.to_solver_space(v, matrix(), 10.0)
    close("edge is 1000 mm at unit=cm", float(pairwise(solver_cm)[0, 1]), 1000.0, 1e-9)

    solver_m, _ = spaces.to_solver_space(v, matrix(), 1000.0)
    close("edge is 100000 mm at unit=m", float(pairwise(solver_m)[0, 1]), 100000.0, 1e-6)

    back = spaces.solver_to_world(solver, center, 1.0)
    close("round trip to world", float(np.abs(back - v).max()), 0.0, 1e-9)


def test_transform_semantics():
    print("\ntransform semantics (PROJECT_SPEC sect. 6.3)")
    v, f = cube()
    v = v * 100.0
    prep = {"weld": "off"}

    base_solver, _ = spaces.to_solver_space(v, matrix(), 1.0)
    base_geometry = spaces.geometry_hash(v, f, prep)
    base_metric = spaces.metric_key(np.eye(3), 1.0)
    base_pairs = pairwise(base_solver)

    # --- translation ---
    m = matrix(translation=(1234.5, -99.0, 7.0))
    solver, _ = spaces.to_solver_space(v, m, 1.0)
    close("translation: distances unchanged",
          float(np.abs(pairwise(solver) - base_pairs).max()), 0.0, 1e-9)
    check("translation: geometry_hash unchanged",
          spaces.geometry_hash(v, f, prep) == base_geometry)
    check("translation: metric_key unchanged",
          spaces.metric_key(m[:3, :3], 1.0) == base_metric)

    # --- rotation ---
    r = rotation_matrix((0.3, -0.7, 0.5), 0.9123)
    m = matrix(r, translation=(5.0, 6.0, 7.0))
    solver, _ = spaces.to_solver_space(v, m, 1.0)
    close("rotation: distances unchanged",
          float(np.abs(pairwise(solver) - base_pairs).max()), 0.0, 1e-9)
    check("rotation: geometry_hash unchanged",
          spaces.geometry_hash(v, f, prep) == base_geometry)
    check("rotation: metric_key unchanged (L^T L kills R)",
          spaces.metric_key(m[:3, :3], 1.0) == base_metric)

    # --- uniform scale ---
    for k in (0.5, 2.0, 10.0):
        m = matrix(np.eye(3) * k, translation=(3.0, 0.0, 0.0))
        solver, _ = spaces.to_solver_space(v, m, 1.0)
        close("uniform scale k=%g: distances scale by k" % k,
              float(np.abs(pairwise(solver) - k * base_pairs).max()), 0.0, 1e-8)
        check("uniform scale k=%g: metric_key CHANGES" % k,
              spaces.metric_key(m[:3, :3], 1.0) != base_metric)
        check("uniform scale k=%g: geometry_hash unchanged" % k,
              spaces.geometry_hash(v, f, prep) == base_geometry)

    # --- non-uniform scale ---
    s = np.diag([2.0, 0.5, 3.0])
    m = matrix(s)
    solver, _ = spaces.to_solver_space(v, m, 1.0)
    pairs = pairwise(solver)
    check("non-uniform scale: metric_key CHANGES",
          spaces.metric_key(m[:3, :3], 1.0) != base_metric)
    check("non-uniform scale: not a uniform rescale of the base",
          not np.allclose(pairs / np.maximum(base_pairs, 1e-12),
                          (pairs / np.maximum(base_pairs, 1e-12))[0, 1],
                          equal_nan=True))
    # metamorphic: transform via matrix == transform baked into the data
    baked, _ = spaces.to_solver_space(v @ s.T, matrix(), 1.0)
    close("non-uniform scale: matrix == baked-in vertex data",
          float(np.abs(pairwise(baked) - pairs).max()), 0.0, 1e-9)

    # rotation AFTER non-uniform scale must not change the metric key
    m2 = matrix(r @ s)
    check("rotate(non-uniform scale): metric_key equals unrotated",
          spaces.metric_key(m2[:3, :3], 1.0) == spaces.metric_key(s, 1.0))

    # --- coordinate unit ---
    check("unit change: metric_key CHANGES",
          spaces.metric_key(np.eye(3), 10.0) != base_metric)
    check("unit x10 with scale /10 is the same physical metric",
          spaces.metric_key(np.eye(3) * 0.1, 10.0) == base_metric)
    check("unit change: geometry_hash unchanged",
          spaces.geometry_hash(v, f, prep) == base_geometry)

    # --- geometry edit ---
    edited = v.copy()
    edited[0, 0] += 0.001
    check("mesh edit: geometry_hash CHANGES",
          spaces.geometry_hash(edited, f, prep) != base_geometry)
    check("mesh edit: metric_key unchanged",
          spaces.metric_key(np.eye(3), 1.0) == base_metric)

    # --- preprocessing settings participate in geometry identity ---
    check("preprocessing change: geometry_hash CHANGES",
          spaces.geometry_hash(v, f, {"weld": "on:0.001"}) != base_geometry)

    # --- quantisation absorbs float noise ---
    noisy = np.eye(3) * (1.0 + 1e-14)
    check("float noise does not change metric_key",
          spaces.metric_key(noisy, 1.0) == base_metric)


def test_performance():
    print("\nperformance on a scan-sized mesh")
    v, f = permute_vertices(grid(700, 700, spacing=2.0), 3)   # 980,000 triangles
    started = time.perf_counter()
    r = topology.analyse(v, f, near_tolerance=0.01)
    elapsed = time.perf_counter() - started
    print("        %d vertices, %d triangles analysed in %.2f s"
          % (r["vertex_count"], r["triangle_count"], elapsed))
    check("large grid single component", r["component_count"] == 1)
    check("large grid boundary = 2(n+m)",
          r["boundary_edge_count"] == 2 * (700 + 700))
    check("analyse under 30 s on ~1M triangles", elapsed < 30.0, "%.2f s" % elapsed)


def main():
    print("BSMT Milestone 2.0 - offline tests (no Blender)")
    for test in (
        test_cube,
        test_open_cube,
        test_disconnected,
        test_many_components,
        test_nonmanifold,
        test_duplicates_are_reported_not_welded,
        test_near_coincident_tolerance,
        test_degenerate_and_loose,
        test_grid_counts,
        test_component_labels,
        test_connectivity_is_numbering_independent,
        test_labelling_certificate,
        test_component_verification,
        test_multi_component_warning_is_advisory,
        test_empty,
        test_component_labels_chain,
        test_solver_space_pipeline,
        test_transform_semantics,
        test_performance,
    ):
        test()

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
