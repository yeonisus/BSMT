"""Synthetic proof-of-concept suite for the exact geodesic backend.

Milestone 2.2 only. This module exists to answer one question: **does the
chosen exact-geodesic backend work correctly and at a usable speed inside the
real Blender Python environment?** It therefore uses only synthetic meshes it
generates itself. It never reads BSMT's production Point A / Point B state,
never touches a CanonicalMesh, a SurfacePoint or the real scan, and imports
nothing from the rest of the add-on except the backend wrapper.

Pure numpy: runnable inside Blender through ``bsmt.run_backend_selftest`` and
outside it through ``tests/test_backend_exact.py``, so the numerical claims are
made by one implementation and not by two that can drift apart.

What is proved here
-------------------
1. path/distance consistency - the returned polyline's segment lengths sum to
   the reported distance;
2. plane - the exact backend reproduces the Euclidean distance on every
   triangulation, since a planar polyhedron IS the plane (sect. 9.4: "the
   strongest single test"). Edge-Dijkstra is run alongside as a diagnostic
   only, to display its triangulation-dependent positive bias;
3. cylinder - convergence to the analytically unrolled distance under mesh
   refinement;
4. sphere - convergence to the great-circle distance under refinement;
5. dense mesh - construction time, query time, repeat-query time and path size
   at roughly the triangle count of the real scan;
6. failure behaviour - every bad input raises a typed exception rather than
   crashing or returning a number.

What is deliberately NOT proved here
------------------------------------
Anything about production measurement. The sphere and cylinder differences are
**representation error** between a triangulated polyhedron and the smooth
surface it samples (sect. 4.1), not algorithmic error. Only the plane test can
isolate algorithmic error, because only there is the polyhedron exactly the
reference surface.
"""

import math
import time

import numpy as np

from . import exact_mmp

# ---------------------------------------------------------------------------
# tolerances, and why they are what they are
# ---------------------------------------------------------------------------
#
# A float64 polyline length over k segments accumulates relative rounding of
# order sqrt(k) * eps. For the largest path this suite produces (k ~ 1e4) that
# is about 100 * 2.2e-16 ~ 2e-14. Both tolerances below sit several orders of
# magnitude above that floor, so they never fire on rounding, and many orders
# below any real defect: a dropped or duplicated polyline segment perturbs the
# length by O(h/d), and an edge-graph method's metrication bias on a grid is
# ~8e-2. There is therefore a wide gap between "noise" and "signal", and these
# values sit inside it. They are not tuned to make anything pass.

PATH_CONSISTENCY_REL_TOL = 1e-9
PLANE_REL_TOL = 1e-12

#: Triangle count of the real reference scan 21_M_3400E, used to size the
#: dense benchmark. The scan itself is never loaded or modified.
REFERENCE_SCAN_TRIANGLES = 314086


# ---------------------------------------------------------------------------
# synthetic mesh generators (pure numpy, deterministic)
# ---------------------------------------------------------------------------

def plane_index(ny, i, j):
    """Vertex index of grid node (i, j) in a plane produced by plane_grid."""
    return i * (ny + 1) + j


def plane_grid(nx, ny, spacing_x=1.0, spacing_y=None, diagonal="forward"):
    """Triangulated planar patch in the z=0 plane.

    ``diagonal`` selects the triangulation of each quad, which is the whole
    point of the plane test: an exact polyhedral method must give the same
    answer for all of them.

        forward      every quad split along the (i,j)-(i+1,j+1) diagonal
        backward     every quad split along the (i+1,j)-(i,j+1) diagonal
        alternating  checkerboard of the two, so the mesh has no global
                     diagonal direction for a path to follow
        sliver       forward diagonals on a strongly anisotropic grid,
                     producing high-aspect-ratio triangles
    """
    if spacing_y is None:
        spacing_y = spacing_x
    if diagonal == "sliver":
        spacing_y = spacing_x / 40.0

    xs = np.arange(nx + 1, dtype=np.float64) * spacing_x
    ys = np.arange(ny + 1, dtype=np.float64) * spacing_y
    grid_x, grid_y = np.meshgrid(xs, ys, indexing="ij")
    vertices = np.stack(
        [grid_x.ravel(), grid_y.ravel(), np.zeros(grid_x.size)], axis=1
    ).astype(np.float64)

    faces = []
    for i in range(nx):
        for j in range(ny):
            v00 = plane_index(ny, i, j)
            v10 = plane_index(ny, i + 1, j)
            v11 = plane_index(ny, i + 1, j + 1)
            v01 = plane_index(ny, i, j + 1)
            if diagonal == "backward":
                forward = False
            elif diagonal == "alternating":
                forward = ((i + j) % 2) == 0
            else:
                forward = True
            if forward:
                faces.append([v00, v10, v11])
                faces.append([v00, v11, v01])
            else:
                faces.append([v00, v10, v01])
                faces.append([v10, v11, v01])
    return vertices, np.asarray(faces, dtype=np.int32)


def cylinder_index(n_theta, i, j):
    """Vertex index of ring position i on ring j of a cylinder_mesh."""
    return j * n_theta + (i % n_theta)


def cylinder_mesh(radius, height, n_theta, n_z):
    """Open (uncapped) lateral surface of a cylinder about the z axis.

    Vertices lie exactly on the analytical cylinder, so the polyhedron is
    inscribed and its geodesics are expected to *underestimate* the smooth
    reference: the chords cut corners. That bias must shrink under refinement.
    """
    thetas = np.arange(n_theta, dtype=np.float64) * (2.0 * math.pi / n_theta)
    zs = np.arange(n_z + 1, dtype=np.float64) * (height / n_z)
    ring = np.stack(
        [radius * np.cos(thetas), radius * np.sin(thetas), np.zeros(n_theta)], axis=1
    )
    vertices = np.concatenate(
        [ring + np.array([0.0, 0.0, z]) for z in zs], axis=0
    ).astype(np.float64)

    faces = []
    for j in range(n_z):
        for i in range(n_theta):
            v00 = cylinder_index(n_theta, i, j)
            v10 = cylinder_index(n_theta, i + 1, j)
            v11 = cylinder_index(n_theta, i + 1, j + 1)
            v01 = cylinder_index(n_theta, i, j + 1)
            faces.append([v00, v10, v11])
            faces.append([v00, v11, v01])
    return vertices, np.asarray(faces, dtype=np.int32)


def icosphere(radius, subdivisions):
    """Icosphere with every vertex exactly on the sphere of the given radius."""
    phi = (1.0 + math.sqrt(5.0)) / 2.0
    vertices = np.array([
        [-1, phi, 0], [1, phi, 0], [-1, -phi, 0], [1, -phi, 0],
        [0, -1, phi], [0, 1, phi], [0, -1, -phi], [0, 1, -phi],
        [phi, 0, -1], [phi, 0, 1], [-phi, 0, -1], [-phi, 0, 1],
    ], dtype=np.float64)
    faces = np.array([
        [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
        [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
        [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
        [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1],
    ], dtype=np.int64)

    for _ in range(subdivisions):
        midpoint = {}
        vertex_list = list(vertices)
        new_faces = []

        def middle(a, b):
            key = (a, b) if a < b else (b, a)
            if key not in midpoint:
                vertex_list.append((vertex_list[a] + vertex_list[b]) * 0.5)
                midpoint[key] = len(vertex_list) - 1
            return midpoint[key]

        for i0, i1, i2 in faces:
            a = middle(int(i0), int(i1))
            b = middle(int(i1), int(i2))
            c = middle(int(i2), int(i0))
            new_faces += [[i0, a, c], [i1, b, a], [i2, c, b], [a, b, c]]
        vertices = np.asarray(vertex_list, dtype=np.float64)
        faces = np.asarray(new_faces, dtype=np.int64)

    norms = np.linalg.norm(vertices, axis=1, keepdims=True)
    return np.ascontiguousarray(vertices / norms * radius), \
        np.ascontiguousarray(faces.astype(np.int32))


def mean_edge_length(vertices, faces):
    """Mean edge length h, the refinement parameter of sect. 4.4."""
    pairs = np.concatenate(
        (faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]), axis=0
    )
    segments = vertices[pairs[:, 0]] - vertices[pairs[:, 1]]
    return float(np.linalg.norm(segments, axis=1).mean())


# ---------------------------------------------------------------------------
# analytic references
# ---------------------------------------------------------------------------

def euclidean(a, b):
    return float(np.linalg.norm(np.asarray(b, dtype=np.float64)
                                - np.asarray(a, dtype=np.float64)))


def cylinder_unrolled(radius, theta_a, z_a, theta_b, z_b):
    """Geodesic on a smooth open cylinder, by unrolling it into a plane.

    The lateral surface is developable, so cutting it along a generator and
    flattening it is an isometry. The geodesic is the straight segment in that
    flattened strip, and the shorter way around is taken - hence the wrap of
    the angular difference into [0, pi].
    """
    delta = abs(theta_b - theta_a) % (2.0 * math.pi)
    delta = min(delta, 2.0 * math.pi - delta)
    return math.hypot(radius * delta, z_b - z_a)


def great_circle(radius, a, b):
    """Geodesic on a smooth sphere of the given radius."""
    cosine = float(np.dot(a, b)) / (radius * radius)
    return radius * math.acos(max(-1.0, min(1.0, cosine)))


# ---------------------------------------------------------------------------
# edge-graph Dijkstra - DIAGNOSTIC ONLY, never a measurement backend
# ---------------------------------------------------------------------------

def edge_dijkstra(vertices, faces, source_index, target_index):
    """Shortest path length along mesh EDGES only.

    Present solely to display the metrication bias that motivates the exact
    backend (sect. 5.3). Its result is positive-biased, anisotropic and does
    **not** vanish under mesh refinement. It must never be shown as, exported
    as, or stored as a BSMT surface measurement.
    """
    import heapq

    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces)
    pairs = np.concatenate(
        (faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]), axis=0
    )
    lengths = np.linalg.norm(vertices[pairs[:, 0]] - vertices[pairs[:, 1]], axis=1)

    adjacency = [[] for _ in range(vertices.shape[0])]
    for (a, b), w in zip(pairs, lengths):
        adjacency[int(a)].append((int(b), float(w)))
        adjacency[int(b)].append((int(a), float(w)))

    best = np.full(vertices.shape[0], np.inf)
    best[source_index] = 0.0
    queue = [(0.0, int(source_index))]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance > best[node]:
            continue
        if node == target_index:
            return float(distance)
        for neighbour, weight in adjacency[node]:
            candidate = distance + weight
            if candidate < best[neighbour]:
                best[neighbour] = candidate
                heapq.heappush(queue, (candidate, neighbour))
    return float("inf")


# ---------------------------------------------------------------------------
# individual suites
# ---------------------------------------------------------------------------

def _relative(value, reference):
    return abs(value - reference) / abs(reference) if reference else float("nan")


def run_path_consistency():
    """sum(|segment|) of the returned polyline == the reported distance."""
    rows = []
    cases = (
        ("plane 40x40", plane_grid(40, 40, 5.0)),
        ("cylinder R=50 mm", cylinder_mesh(50.0, 200.0, 48, 40)),
        ("icosphere R=100 mm", icosphere(100.0, 3)),
    )
    for label, (vertices, faces) in cases:
        source = int(np.argmin(vertices[:, 2] + vertices[:, 0]))
        target = int(np.argmax(vertices[:, 2] + vertices[:, 0]))
        distance, path = exact_mmp.compute_distance_and_path(
            vertices, faces, source, target
        )
        length = exact_mmp.path_length(path)
        rows.append({
            "mesh": label,
            "triangles": int(faces.shape[0]),
            "distance": distance,
            "path_points": int(path.shape[0]),
            "path_length": length,
            "rel_error": _relative(length, distance),
            "endpoint_error_mm": max(
                euclidean(path[0], vertices[source]),
                euclidean(path[-1], vertices[target]),
            ),
            "pass": _relative(length, distance) <= PATH_CONSISTENCY_REL_TOL,
        })
    return {"name": "path consistency", "tolerance": PATH_CONSISTENCY_REL_TOL,
            "rows": rows, "pass": all(row["pass"] for row in rows)}


def run_plane(include_dijkstra=True):
    """Ground truth: on a plane the geodesic distance IS the Euclidean one."""
    rows = []
    configurations = (
        ("forward", 20, 20, 5.0),
        ("backward", 20, 20, 5.0),
        ("alternating", 20, 20, 5.0),
        ("sliver", 20, 20, 5.0),
        ("forward", 8, 8, 12.5),
        ("forward", 60, 60, 5.0 / 3.0),
    )
    for diagonal, nx, ny, spacing in configurations:
        vertices, faces = plane_grid(nx, ny, spacing, diagonal=diagonal)
        source = plane_index(ny, 0, 0)
        target = plane_index(ny, nx, ny)
        distance, path = exact_mmp.compute_distance_and_path(
            vertices, faces, source, target
        )
        reference = euclidean(vertices[source], vertices[target])
        relative = _relative(distance, reference)
        row = {
            "triangulation": diagonal,
            "grid": "%dx%d" % (nx, ny),
            "triangles": int(faces.shape[0]),
            "h_mm": mean_edge_length(vertices, faces),
            "exact_mm": distance,
            "reference_mm": reference,
            "abs_error_mm": abs(distance - reference),
            "rel_error": relative,
            "path_points": int(path.shape[0]),
            "pass": relative <= PLANE_REL_TOL,
        }
        if include_dijkstra:
            edge = edge_dijkstra(vertices, faces, source, target)
            row["dijkstra_mm"] = edge
            row["dijkstra_rel_error"] = _relative(edge, reference)
        rows.append(row)
    return {"name": "plane (exactness)", "tolerance": PLANE_REL_TOL,
            "rows": rows, "pass": all(row["pass"] for row in rows)}


def run_cylinder():
    """Convergence to the analytically unrolled cylinder geodesic."""
    radius = 50.0
    height = 200.0
    rows = []
    previous = None
    for n_theta, n_z in ((24, 10), (48, 20), (96, 40), (192, 80)):
        vertices, faces = cylinder_mesh(radius, height, n_theta, n_z)
        # A quarter turn apart and half the height apart, both exactly on mesh
        # vertices so no endpoint insertion is needed at this milestone.
        i_a, j_a = 0, n_z // 4
        i_b, j_b = n_theta // 4, (3 * n_z) // 4
        source = cylinder_index(n_theta, i_a, j_a)
        target = cylinder_index(n_theta, i_b, j_b)
        distance, path = exact_mmp.compute_distance_and_path(
            vertices, faces, source, target
        )
        reference = cylinder_unrolled(
            radius,
            i_a * 2.0 * math.pi / n_theta, j_a * height / n_z,
            i_b * 2.0 * math.pi / n_theta, j_b * height / n_z,
        )
        h = mean_edge_length(vertices, faces)
        relative = _relative(distance, reference)
        row = {
            "n_theta": n_theta, "n_z": n_z,
            "triangles": int(faces.shape[0]),
            "h_mm": h,
            "polyhedral_mm": distance,
            "smooth_reference_mm": reference,
            "abs_error_mm": abs(distance - reference),
            "rel_error": relative,
            "rel_error_percent": relative * 100.0,
            "signed_error_mm": distance - reference,
            "path_points": int(path.shape[0]),
            "observed_order": float("nan"),
        }
        if previous is not None and relative > 0.0:
            ratio = previous[1] / relative
            if ratio > 0.0 and h > 0.0 and previous[0] != h:
                row["observed_order"] = math.log(ratio) / math.log(previous[0] / h)
        previous = (h, relative)
        rows.append(row)
    return {"name": "cylinder (convergence)", "rows": rows, "pass": None}


def run_sphere():
    """Convergence to the smooth great-circle distance."""
    radius = 100.0
    rows = []
    previous = None
    for subdivisions in (2, 3, 4, 5):
        vertices, faces = icosphere(radius, subdivisions)
        source = int(np.argmax(vertices[:, 2]))
        # A well-posed target: nearest vertex to 90 deg from the source, so the
        # geodesic is unique. An antipodal pair has infinitely many.
        cosines = vertices @ vertices[source] / (radius * radius)
        target = int(np.argmin(np.abs(cosines)))
        distance, path = exact_mmp.compute_distance_and_path(
            vertices, faces, source, target
        )
        reference = great_circle(radius, vertices[source], vertices[target])
        h = mean_edge_length(vertices, faces)
        relative = _relative(distance, reference)
        row = {
            "subdivisions": subdivisions,
            "vertices": int(vertices.shape[0]),
            "triangles": int(faces.shape[0]),
            "h_mm": h,
            "polyhedral_mm": distance,
            "smooth_reference_mm": reference,
            "abs_error_mm": abs(distance - reference),
            "rel_error": relative,
            "rel_error_percent": relative * 100.0,
            "signed_error_mm": distance - reference,
            "path_points": int(path.shape[0]),
            "observed_order": float("nan"),
        }
        if previous is not None and relative > 0.0:
            ratio = previous[1] / relative
            if ratio > 0.0 and h > 0.0 and previous[0] != h:
                row["observed_order"] = math.log(ratio) / math.log(previous[0] / h)
        previous = (h, relative)
        rows.append(row)
    return {"name": "sphere (convergence)", "rows": rows, "pass": None}


def _max_rss_bytes():
    """Peak resident set size, or None where it is not observable."""
    try:
        import resource
        import sys
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:  # noqa: BLE001
        return None
    # ru_maxrss is bytes on macOS and kilobytes on Linux.
    return int(value) if sys.platform == "darwin" else int(value) * 1024


def run_benchmark(target_triangles=REFERENCE_SCAN_TRIANGLES, repeats=3):
    """Timing on a synthetic mesh of roughly the real scan's triangle count.

    The real scan is never loaded, opened or modified: the mesh is generated
    here. Construction, first query and repeated queries are timed separately
    because Milestone 2.3 needs to know which of them dominates.
    """
    # Choose the icosphere subdivision whose triangle count is closest to the
    # requested size. 20*4^n triangles: n=5 -> 20480, 6 -> 81920, 7 -> 327680.
    best_n, best_gap = 5, None
    for n in range(2, 8):
        gap = abs(20 * (4 ** n) - target_triangles)
        if best_gap is None or gap < best_gap:
            best_n, best_gap = n, gap

    rss_before = _max_rss_bytes()
    build_started = time.perf_counter()
    vertices, faces = icosphere(100.0, best_n)
    mesh_build = time.perf_counter() - build_started

    source = int(np.argmax(vertices[:, 2]))
    cosines = vertices @ vertices[source] / (100.0 * 100.0)
    target = int(np.argmin(np.abs(cosines)))

    started = time.perf_counter()
    solver = exact_mmp.ExactSolver(vertices, faces)
    construction = time.perf_counter() - started

    started = time.perf_counter()
    distance, path = solver.distance_and_path(source, target)
    first_query = time.perf_counter() - started

    repeat_times = []
    for _ in range(max(0, repeats - 1)):
        started = time.perf_counter()
        solver.distance_and_path(source, target)
        repeat_times.append(time.perf_counter() - started)

    rss_after = _max_rss_bytes()
    return {
        "name": "dense-mesh benchmark",
        "rows": [{
            "requested_triangles": int(target_triangles),
            "subdivisions": best_n,
            "vertices": int(vertices.shape[0]),
            "triangles": int(faces.shape[0]),
            "mesh_generation_s": mesh_build,
            "solver_construction_s": construction,
            "first_query_s": first_query,
            "repeat_query_s_mean": (
                sum(repeat_times) / len(repeat_times) if repeat_times else float("nan")
            ),
            "repeat_query_count": len(repeat_times),
            "distance_mm": distance,
            "path_points": int(path.shape[0]),
            "peak_rss_before_bytes": rss_before,
            "peak_rss_after_bytes": rss_after,
            "peak_rss_delta_bytes": (
                None if rss_before is None or rss_after is None
                else rss_after - rss_before
            ),
        }],
        "pass": None,
    }


def run_failure_behaviour():
    """Every bad input must raise a typed exception, not crash or guess."""
    vertices, faces = plane_grid(4, 4, 1.0)
    cases = []

    def expect(label, exception_type, call):
        try:
            call()
        except exception_type as exc:
            cases.append({"case": label, "raised": type(exc).__name__,
                          "message": str(exc), "pass": True})
        except Exception as exc:  # noqa: BLE001
            cases.append({"case": label, "raised": type(exc).__name__,
                          "message": str(exc), "pass": False})
        else:
            cases.append({"case": label, "raised": "(nothing)",
                          "message": "returned a value", "pass": False})

    expect("empty vertex array", exact_mmp.InvalidMeshError,
           lambda: exact_mmp.compute_distance_and_path(
               np.zeros((0, 3)), faces, 0, 1))
    expect("empty triangle array", exact_mmp.InvalidMeshError,
           lambda: exact_mmp.compute_distance_and_path(
               vertices, np.zeros((0, 3), dtype=np.int32), 0, 1))
    expect("wrong vertex shape", exact_mmp.InvalidMeshError,
           lambda: exact_mmp.compute_distance_and_path(
               np.zeros((10, 2)), faces, 0, 1))
    expect("non-finite vertex", exact_mmp.InvalidMeshError,
           lambda: exact_mmp.compute_distance_and_path(
               _with_nan(vertices), faces, 0, 1))
    expect("float triangle indices", exact_mmp.InvalidMeshError,
           lambda: exact_mmp.compute_distance_and_path(
               vertices, faces.astype(np.float64), 0, 1))
    expect("triangle index out of range", exact_mmp.InvalidMeshError,
           lambda: exact_mmp.compute_distance_and_path(
               vertices, _with_bad_index(faces, vertices.shape[0] + 5), 0, 1))
    expect("negative triangle index", exact_mmp.InvalidMeshError,
           lambda: exact_mmp.compute_distance_and_path(
               vertices, _with_bad_index(faces, -1), 0, 1))
    expect("degenerate triangle (repeated index)", exact_mmp.InvalidMeshError,
           lambda: exact_mmp.compute_distance_and_path(
               vertices, _with_repeat(faces), 0, 1))
    expect("source index out of range", exact_mmp.InvalidMeshError,
           lambda: exact_mmp.compute_distance_and_path(
               vertices, faces, vertices.shape[0], 1))
    expect("target index out of range", exact_mmp.InvalidMeshError,
           lambda: exact_mmp.compute_distance_and_path(
               vertices, faces, 0, -1))
    expect("path_length on a 1-point path", exact_mmp.InvalidMeshError,
           lambda: exact_mmp.path_length(np.zeros((1, 3))))

    # A -> A is not an error: it is exactly zero, with no solver call.
    distance, path = exact_mmp.compute_distance_and_path(vertices, faces, 3, 3)
    cases.append({"case": "source == target returns exactly 0.0",
                  "raised": "(none)", "message": "distance = %r" % distance,
                  "pass": distance == 0.0 and path.shape[0] >= 2})

    return {"name": "failure behaviour", "rows": cases,
            "pass": all(case["pass"] for case in cases)}


def _with_nan(vertices):
    bad = vertices.copy()
    bad[2, 1] = float("nan")
    return bad


def _with_bad_index(faces, value):
    bad = faces.astype(np.int64).copy()
    bad[0, 0] = value
    return bad


def _with_repeat(faces):
    bad = faces.copy()
    bad[1, 1] = bad[1, 0]
    return bad


# ---------------------------------------------------------------------------
# runner and report formatting
# ---------------------------------------------------------------------------

def run_all(include_dense=True, dense_triangles=REFERENCE_SCAN_TRIANGLES,
            include_dijkstra=True):
    """Run every suite. Returns a report dict; never raises for a test failure.

    A backend that is unavailable is reported as such rather than skipped
    silently, and an unexpected exception inside a suite is captured with its
    traceback so it reaches the panel instead of Blender's console alone.
    """
    import traceback as _traceback

    report = {
        "backend": exact_mmp.status(),
        "suites": [],
        "pass": False,
        "error": "",
        "elapsed_s": 0.0,
    }
    if not exact_mmp.availability():
        report["error"] = (
            "exact geodesic backend unavailable: %s"
            % (exact_mmp.IMPORT_ERROR or "pygeodesic not installed")
        )
        return report

    started = time.perf_counter()
    planned = [
        ("path consistency", run_path_consistency, {}),
        ("plane", run_plane, {"include_dijkstra": include_dijkstra}),
        ("cylinder", run_cylinder, {}),
        ("sphere", run_sphere, {}),
        ("failure behaviour", run_failure_behaviour, {}),
    ]
    if include_dense:
        planned.append(
            ("benchmark", run_benchmark, {"target_triangles": dense_triangles})
        )

    for label, function, kwargs in planned:
        try:
            report["suites"].append(function(**kwargs))
        except Exception as exc:  # noqa: BLE001
            report["suites"].append({
                "name": label,
                "rows": [],
                "pass": False,
                "exception": "%s: %s" % (type(exc).__name__, exc),
                "traceback": _traceback.format_exc(),
            })
    report["elapsed_s"] = time.perf_counter() - started
    report["pass"] = all(
        suite.get("pass") is not False for suite in report["suites"]
    )
    return report


def _fmt(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        if value != value:          # NaN
            return "n/a"
        if value == 0.0:
            return "0"
        if abs(value) < 1e-3 or abs(value) >= 1e6:
            return "%.3e" % value
        return "%.6f" % value
    return str(value)


def format_report(report):
    """Render run_all()'s dict as plain lines for the panel and the console."""
    lines = ["BSMT Milestone 2.2 - exact geodesic backend self-test"]
    backend = report.get("backend", {})
    lines.append("  Backend:   %s" % backend.get("backend_name", "?"))
    lines.append("  Available: %s" % ("yes" if backend.get("available") else "NO"))
    if backend.get("version"):
        lines.append("  Version:   %s" % backend["version"])
    if backend.get("module_path"):
        lines.append("  Path:      %s" % backend["module_path"])
    if report.get("error"):
        lines.append("")
        lines.append("  " + report["error"])
        if backend.get("import_error"):
            lines.append("  Original import error:")
            lines.append("    %s" % backend["import_error"])
        return lines

    for suite in report.get("suites", []):
        lines.append("")
        verdict = suite.get("pass")
        mark = "PASS" if verdict is True else ("FAIL" if verdict is False else "INFO")
        lines.append("[%s] %s" % (mark, suite.get("name", "?")))
        if suite.get("exception"):
            lines.append("  raised %s" % suite["exception"])
            lines.append("  (traceback in the system console)")
            continue
        if "tolerance" in suite:
            lines.append("  tolerance: %s" % _fmt(suite["tolerance"]))
        for row in suite.get("rows", []):
            parts = []
            for key, value in row.items():
                if key == "pass":
                    continue
                parts.append("%s=%s" % (key, _fmt(value)))
            prefix = "  "
            if "pass" in row:
                prefix = "  %s " % ("ok  " if row["pass"] else "FAIL")
            lines.append(prefix + ", ".join(parts))

    lines.append("")
    lines.append("Elapsed: %.2f s" % report.get("elapsed_s", 0.0))
    lines.append("Overall: %s" % ("PASS" if report.get("pass") else "FAIL"))
    lines.append("")
    lines.append("Note: cylinder and sphere differences are representation")
    lines.append("error between the triangulated polyhedron and the smooth")
    lines.append("surface, not algorithmic error. Only the plane test can")
    lines.append("isolate algorithmic error, and it must be ~1e-16 there.")
    return lines
