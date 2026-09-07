"""Analytic geometry validation with derivable ground truth (Milestone 3.25).

    /path/to/blender -b --factory-startup --python tests/test_analytic_validation.py

Three surfaces whose geodesics are known in closed form - a plane, a cylinder
and a sphere - measured through the production pipeline at several mesh
densities, and reported against the smooth answer.

What is being claimed, exactly
------------------------------
BSMT's backend is pygeodesic's MMP implementation, which is **exact on the
polyhedral surface it is given**. It is NOT exact on the smooth surface that
surface was sampled from, and nothing here says it is.

So the three surfaces separate into two different kinds of check:

* **The plane is a correctness test.** A triangulated plane IS the smooth
  plane - the sampling loses nothing - so the polyhedral geodesic and the
  smooth geodesic are the same number, and the straight-line distance too.
  Any disagreement beyond float error is a defect.

* **The cylinder and the sphere are discretisation-error measurements.** A
  triangulated cylinder or sphere is a chord approximation that lies inside
  the smooth surface, so its geodesics are SHORTER than the smooth ones. The
  gap is a property of the mesh, not an error in the solver, and it must
  shrink as the mesh is refined. What is validated is that it does shrink, at
  the rate a chordal approximation predicts (O(h^2)), and that the sign is
  always the same way round.

Reporting the cylinder's 0.1% gap as "solver error" would be wrong in both
directions: it would blame the solver for the mesh, and it would imply the
solver has an error budget that it does not have.
"""

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bpy                                        # noqa: F401
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_analytic_validation.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_analytic_validation.py")
    raise SystemExit(0)

import numpy as np  # noqa: E402

FAILURES = []
CHECKS = [0]
TABLES = {}


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def relative(measured, expected):
    return abs(measured - expected) / abs(expected) if expected else float("inf")


def table(name, header, rows):
    """Record a report table and print it."""
    TABLES[name] = (header, rows)
    widths = [max(len(str(header[i])), max(len(str(r[i])) for r in rows))
              for i in range(len(header))]
    line = "  | " + " | ".join(str(header[i]).ljust(widths[i])
                               for i in range(len(header))) + " |"
    print("\n" + line)
    print("  |-" + "-|-".join("-" * w for w in widths) + "-|")
    for row in rows:
        print("  | " + " | ".join(str(row[i]).ljust(widths[i])
                                  for i in range(len(row))) + " |")
    print("")


# ---------------------------------------------------------------------------


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import geodesic
    unavailable = geodesic.ensure_loaded()
    if unavailable:
        print("SKIP  the exact backend is unavailable: %s" % unavailable)
        raise SystemExit(0)

    from body_surface_measurement.geodesic import solve
    from body_surface_measurement.geodesic.backends import selftest

    def spec(triangle, bary):
        """A PointSpec, the same shape a picked SurfacePoint produces."""
        return solve.PointSpec(int(triangle),
                               np.asarray(bary, dtype=np.float64),
                               component_id=1, source_object="Fixture",
                               geometry_hash="fixture", status="VALID",
                               valid=True)

    def at_vertex(faces, vertex_index):
        """A SurfaceLocation sitting exactly on a mesh vertex.

        Barycentric weight 1 on one corner, so the analytic reference is
        unambiguous - but still routed through the production insertion path,
        so this is the same code a researcher's click reaches.
        """
        rows = np.where(faces == vertex_index)
        triangle = int(rows[0][0])
        corner = int(rows[1][0])
        bary = np.zeros(3)
        bary[corner] = 1.0
        return spec(triangle, bary)

    def measure(vertices, faces, a, b):
        result = solve.surface_distance(vertices, faces, a, b)
        return float(result.distance_mm)

    # ------------------------------------------------------------------ A --
    print("\nA. PLANE - the sampling loses nothing, so this is exactness")
    #
    # A triangulated plane is the plane. Both the straight distance and the
    # polyhedral geodesic must equal the closed-form answer to float error,
    # at every density, and the two must equal each other.
    plane_rows = []
    worst_plane = 0.0
    for nx, ny, spacing in ((5, 5, 25.0), (11, 11, 10.0), (21, 21, 5.0),
                            (41, 41, 2.5), (81, 81, 1.25)):
        vertices, faces = selftest.plane_grid(nx, ny, spacing)
        source = selftest.plane_index(ny, 0, 0)
        target = selftest.plane_index(ny, nx - 1, ny - 1)
        expected = selftest.euclidean(vertices[source], vertices[target])
        measured = measure(vertices, faces,
                           at_vertex(faces, source), at_vertex(faces, target))
        straight = selftest.euclidean(vertices[source], vertices[target])
        error = abs(measured - expected)
        worst_plane = max(worst_plane, relative(measured, expected))
        plane_rows.append((
            "%d x %d" % (nx, ny), len(faces),
            "%.4f" % selftest.mean_edge_length(vertices, faces),
            "%.6f" % expected, "%.6f" % measured,
            "%.2e" % error, "%.2e" % relative(measured, expected)))
        check("plane %dx%d: straight distance is the analytic diagonal"
              % (nx, ny), abs(straight - expected) < 1e-9)
        check("plane %dx%d: surface distance equals it to float error"
              % (nx, ny), relative(measured, expected) < 1e-9,
              "%.3e" % relative(measured, expected))
    table("plane",
          ("grid", "triangles", "mean edge h", "expected mm", "measured mm",
           "abs err", "rel err"), plane_rows)
    check("PLANE: exact at every density (worst rel err %.2e)" % worst_plane,
          worst_plane < 1e-9)

    # A path that has to cross triangles diagonally, not along edges: the
    # case an edge-following algorithm would get wrong and MMP does not.
    vertices, faces = selftest.plane_grid(21, 21, 5.0)
    source = selftest.plane_index(21, 0, 0)
    target = selftest.plane_index(21, 20, 7)
    expected = selftest.euclidean(vertices[source], vertices[target])
    measured = measure(vertices, faces,
                       at_vertex(faces, source), at_vertex(faces, target))
    dijkstra = selftest.edge_dijkstra(vertices, faces, source, target)
    check("plane: an oblique path is still exact (%.3e)"
          % relative(measured, expected), relative(measured, expected) < 1e-9)
    check("  and edge-following Dijkstra is measurably WORSE (%.4f vs %.4f), "
          "which is why it is a diagnostic and never a backend"
          % (dijkstra, expected), dijkstra > expected * (1.0 + 1e-6))

    # ------------------------------------------------------------------ B --
    print("\nB. CYLINDER - a chord approximation, converging from below")
    radius, height = 50.0, 200.0
    cylinder_rows = []
    previous_error = None
    ratios = []
    for n_theta, n_z in ((24, 12), (48, 24), (96, 48), (192, 96)):
        vertices, faces = selftest.cylinder_mesh(radius, height, n_theta, n_z)
        i_a, j_a = 0, n_z // 4
        i_b, j_b = n_theta // 4, (3 * n_z) // 4
        source = selftest.cylinder_index(n_theta, i_a, j_a)
        target = selftest.cylinder_index(n_theta, i_b, j_b)
        expected = selftest.cylinder_unrolled(
            radius,
            i_a * 2.0 * math.pi / n_theta, j_a * height / n_z,
            i_b * 2.0 * math.pi / n_theta, j_b * height / n_z)
        measured = measure(vertices, faces,
                           at_vertex(faces, source), at_vertex(faces, target))
        straight = selftest.euclidean(vertices[source], vertices[target])
        h = selftest.mean_edge_length(vertices, faces)
        error = expected - measured
        cylinder_rows.append((
            "%dx%d" % (n_theta, n_z), len(faces), "%.4f" % h,
            "%.6f" % expected, "%.6f" % measured, "%.6f" % straight,
            "%+.2e" % error, "%.3e" % relative(measured, expected)))
        check("cylinder %dx%d: the polyhedral geodesic is SHORTER than the "
              "smooth one, as a chord approximation must be" % (n_theta, n_z),
              measured <= expected + 1e-9,
              "%.9f vs %.9f" % (measured, expected))
        check("cylinder %dx%d: and never shorter than the straight line"
              % (n_theta, n_z), measured >= straight - 1e-9)
        if previous_error is not None and error > 0:
            ratios.append(previous_error / error)
        previous_error = error
    table("cylinder",
          ("mesh", "triangles", "mean edge h", "smooth mm", "polyhedral mm",
           "straight mm", "smooth - poly", "rel gap"), cylinder_rows)
    check("CYLINDER: the gap to the smooth surface shrinks at every "
          "refinement", all(r > 1.0 for r in ratios), ratios)
    check("  and it shrinks roughly as h^2 (halving h -> %s x smaller)"
          % ", ".join("%.1f" % r for r in ratios),
          all(2.5 < r < 6.0 for r in ratios), ratios)

    # ------------------------------------------------------------------ C --
    print("\nC. SPHERE - the same, on a surface with curvature in both "
          "directions")
    sphere_radius = 100.0
    sphere_rows = []
    previous_error = None
    ratios = []
    for subdivisions in (1, 2, 3, 4):
        vertices, faces = selftest.icosphere(sphere_radius, subdivisions)
        # Two vertices roughly a quarter-turn apart, chosen from the mesh so
        # both lie exactly on the sphere and the great circle is exact.
        source = 0
        target = int(np.argmin(np.abs(
            vertices @ vertices[0] / (sphere_radius ** 2))))
        expected = selftest.great_circle(sphere_radius, vertices[source],
                                         vertices[target])
        measured = measure(vertices, faces,
                           at_vertex(faces, source), at_vertex(faces, target))
        straight = selftest.euclidean(vertices[source], vertices[target])
        h = selftest.mean_edge_length(vertices, faces)
        error = expected - measured
        sphere_rows.append((
            subdivisions, len(faces), "%.4f" % h,
            "%.6f" % expected, "%.6f" % measured, "%.6f" % straight,
            "%+.2e" % error, "%.3e" % relative(measured, expected)))
        check("sphere sub%d: shorter than the great circle" % subdivisions,
              measured <= expected + 1e-9,
              "%.9f vs %.9f" % (measured, expected))
        check("sphere sub%d: longer than the chord" % subdivisions,
              measured >= straight - 1e-9)
        if previous_error is not None and error > 0:
            ratios.append(previous_error / error)
        previous_error = error
    table("sphere",
          ("subdivisions", "triangles", "mean edge h", "great circle mm",
           "polyhedral mm", "chord mm", "gc - poly", "rel gap"), sphere_rows)
    check("SPHERE: the gap shrinks at every refinement",
          all(r > 1.0 for r in ratios), ratios)
    check("  roughly as h^2 (%s x smaller per halving)"
          % ", ".join("%.1f" % r for r in ratios),
          all(2.5 < r < 6.0 for r in ratios), ratios)

    # ------------------------------------------------------------------ D --
    print("\nD. the properties that must hold on ANY surface")
    vertices, faces = selftest.icosphere(sphere_radius, 3)
    picks = [0, 5, 17, 61, 140]
    worst_symmetry = 0.0
    for index, a in enumerate(picks):
        for b in picks[index + 1:]:
            forward = measure(vertices, faces,
                              at_vertex(faces, a), at_vertex(faces, b))
            backward = measure(vertices, faces,
                               at_vertex(faces, b), at_vertex(faces, a))
            worst_symmetry = max(worst_symmetry, abs(forward - backward))
            check("d(%d,%d) >= the straight line" % (a, b),
                  forward >= selftest.euclidean(vertices[a], vertices[b]) - 1e-9)
    check("d(a,b) == d(b,a) for every pair (worst %.2e)" % worst_symmetry,
          worst_symmetry < 1e-9)
    for a in picks:
        check("d(%d,%d) == 0" % (a, a),
              abs(measure(vertices, faces,
                          at_vertex(faces, a), at_vertex(faces, a))) < 1e-9)

    # Triangle inequality on the surface metric.
    a, b, c = picks[0], picks[1], picks[2]
    ab = measure(vertices, faces, at_vertex(faces, a), at_vertex(faces, b))
    bc = measure(vertices, faces, at_vertex(faces, b), at_vertex(faces, c))
    ac = measure(vertices, faces, at_vertex(faces, a), at_vertex(faces, c))
    check("the triangle inequality holds (%.4f <= %.4f + %.4f)"
          % (ac, ab, bc), ac <= ab + bc + 1e-9)

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for failure in FAILURES:
        print("  FAILED: %s" % failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        sys.stdout.flush()
        print("BSMT_ANALYTIC_RESULT=%d" % code)
    raise SystemExit(code)
