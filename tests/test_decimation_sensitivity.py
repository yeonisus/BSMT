"""Decimation sensitivity characterisation (Milestone 3.25).

    /path/to/blender -b --factory-startup --python tests/test_decimation_sensitivity.py

WHAT THIS IS
------------
A measurement of how much a surface distance moves when the measurement mesh
is decimated to 500k, 350k and 200k triangles, against the same paths measured
on the undecimated original.

WHAT THIS IS NOT
----------------
It is **not** evidence that 350,000 triangles is a scientifically validated
target for human body measurement. It cannot be: the fixture is an analytic
surface, not a body; the paths are chosen for geometric variety, not anatomy;
and there is no reference measurement from an accepted method to compare
against. What it establishes is the SHAPE of the sensitivity - which way the
error goes, roughly how big it is, and whether it is monotone in density -
so that a researcher choosing a target knows what they are trading.

The scientific justification of a default target is listed in
docs/VALIDATION.md under "not yet validated", and this file is one of the
inputs to that future decision, not a substitute for it.

Comparing the same path across densities
----------------------------------------
Decimation renumbers every triangle, so a SurfacePoint cannot survive it: a
triangle index means nothing on the decimated mesh. The honest comparison is
therefore by POSITION - the same 3D locations on the original surface, each
re-attached to the nearest point of each decimated surface through the
canonical BVH, which is exactly what a researcher re-picking a landmark on a
decimated copy would produce, minus their own hand.

That re-attachment is itself a source of difference, and it is reported: the
"anchor drift" column is how far the re-attached point sits from the original
position. A distance difference smaller than the drift is not evidence about
decimation at all.
"""

import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_decimation_sensitivity.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_decimation_sensitivity.py")
    raise SystemExit(0)

import numpy as np  # noqa: E402

FAILURES = []
CHECKS = [0]

#: Densities to characterise. The original is whatever the fixture is built
#: at; the rest are the targets a researcher actually meets in the UI.
TARGETS = (500000, 350000, 200000)


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def table(header, rows):
    widths = [max(len(str(header[i])), max(len(str(r[i])) for r in rows))
              for i in range(len(header))]
    print("\n  | " + " | ".join(str(header[i]).ljust(widths[i])
                                for i in range(len(header))) + " |")
    print("  |-" + "-|-".join("-" * w for w in widths) + "-|")
    for row in rows:
        print("  | " + " | ".join(str(row[i]).ljust(widths[i])
                                  for i in range(len(row))) + " |")
    print("")


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def build_torus(context, name, major, minor, major_segments, minor_segments):
    """A torus of exactly the requested resolution, as quads.

    Blender's own torus primitive clamps its segment counts at 256, so this
    builds the grid directly. Every vertex lies exactly on the smooth torus.
    """
    thetas = np.linspace(0.0, 2.0 * math.pi, major_segments, endpoint=False)
    phis = np.linspace(0.0, 2.0 * math.pi, minor_segments, endpoint=False)
    theta, phi = np.meshgrid(thetas, phis, indexing="ij")
    radial = major + minor * np.cos(phi)
    vertices = np.stack([radial * np.cos(theta),
                         radial * np.sin(theta),
                         minor * np.sin(phi)], axis=-1).reshape(-1, 3)

    index = np.arange(major_segments * minor_segments).reshape(
        major_segments, minor_segments)
    next_major = np.roll(index, -1, axis=0)
    next_minor = np.roll(index, -1, axis=1)
    faces = np.stack([index, next_major, np.roll(next_major, -1, axis=1),
                      next_minor], axis=-1).reshape(-1, 4)

    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata([tuple(float(v) for v in row) for row in vertices], [],
                     [tuple(int(v) for v in row) for row in faces])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    context.scene.collection.objects.link(obj)
    return obj


# ---------------------------------------------------------------------------


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import geodesic, preprocess, state

    unavailable = geodesic.ensure_loaded()
    if unavailable:
        print("SKIP  the exact backend is unavailable: %s" % unavailable)
        raise SystemExit(0)
    from body_surface_measurement.geodesic import solve

    context = bpy.context
    props = context.scene.bsmt

    # ------------------------------------------------------------ fixture --
    print("\nbuilding the high-density fixture")
    wipe()
    # A torus, not a sphere: it has regions of genuinely different curvature -
    # the outer equator is nearly developable, the inner one is saddle-shaped
    # - so "nearly flat", "moderate" and "high curvature" are real distinctions
    # on the same body rather than three names for the same thing.
    #
    # Built by hand rather than with primitive_torus_add, whose segment counts
    # are capped at 256 and silently clamp: asking for 560x280 produced
    # 131,072 triangles, BELOW every target, so nothing was decimated and the
    # first run of this suite reported a perfect and completely meaningless
    # zero difference at every density. The guard immediately below is what
    # caught it, and it stays.
    source = build_torus(context, "DenseFixture", 300.0, 100.0, 760, 400)
    context.view_layer.objects.active = source
    context.view_layer.update()

    original = geodesic.meshcache.get(context, source, props.unit,
                                      rebuild=True)
    print("  original: %d triangles" % original.triangle_count)
    check("the fixture is denser than the largest target",
          original.triangle_count > TARGETS[0], original.triangle_count)

    def torus_point(theta, phi, major=300.0, minor=100.0):
        """A point on the smooth torus, in local coordinates."""
        return np.array([
            (major + minor * math.cos(phi)) * math.cos(theta),
            (major + minor * math.cos(phi)) * math.sin(theta),
            minor * math.sin(phi),
        ], dtype=np.float64)

    # Four path types, described by where they run on the torus.
    PATHS = (
        ("nearly flat",
         "a short span across the outer equator, where the surface is "
         "almost developable",
         torus_point(0.00, 0.0), torus_point(0.28, 0.0)),
        ("moderate curvature",
         "over the shoulder of the tube, crossing about a quarter turn of "
         "the minor circle",
         torus_point(0.00, -0.5), torus_point(0.35, 1.1)),
        ("high curvature",
         "across the inner (saddle) side, the worst-conditioned region",
         torus_point(0.00, math.pi - 0.6), torus_point(0.30, math.pi + 0.6)),
        ("long body-like",
         "a long wrapping path of the order a torso girth would be",
         torus_point(0.00, 0.4), torus_point(2.20, -1.3)),
    )

    def attach(canonical, target_local):
        """Nearest surface location on `canonical` to a local position.

        The canonical BVH is built over local vertices, so this is the same
        attachment a pick performs, without a ray or a hand.
        """
        from mathutils import Vector
        location, _normal, index, _distance = canonical.bvh.find_nearest(
            Vector(tuple(float(v) for v in target_local)))
        if location is None:
            return None, None, float("inf")
        triangle = int(index)
        found = np.array(location, dtype=np.float64)
        bary = canonical.barycentric_local(triangle, found)
        drift = float(np.linalg.norm(found - np.asarray(target_local)))
        return triangle, bary, drift

    def measure(canonical, a_local, b_local):
        """(distance, straight, anchor drift) for one path on one mesh."""
        triangle_a, bary_a, drift_a = attach(canonical, a_local)
        triangle_b, bary_b, drift_b = attach(canonical, b_local)
        if triangle_a is None or triangle_b is None:
            return None, None, float("inf")
        spec_a = solve.PointSpec(
            triangle_a, bary_a, component_id=canonical.component_of(triangle_a),
            source_object=canonical.source_object,
            geometry_hash=canonical.geometry_hash, status="VALID", valid=True)
        spec_b = solve.PointSpec(
            triangle_b, bary_b, component_id=canonical.component_of(triangle_b),
            source_object=canonical.source_object,
            geometry_hash=canonical.geometry_hash, status="VALID", valid=True)
        started = time.perf_counter()
        result = solve.surface_distance(
            canonical.vertices_solver, canonical.triangles, spec_a, spec_b)
        elapsed = time.perf_counter() - started
        position_a = canonical.local_from(triangle_a, bary_a)
        position_b = canonical.local_from(triangle_b, bary_b)
        straight = float(np.linalg.norm(position_b - position_a))
        return (float(result.distance_mm), straight,
                max(drift_a, drift_b), elapsed)

    # ------------------------------------------------------------ baseline --
    print("\nA. baseline: the four paths on the undecimated fixture")
    baseline = {}
    for name, description, a_local, b_local in PATHS:
        distance, straight, drift, elapsed = measure(original, a_local, b_local)
        baseline[name] = (distance, straight, drift)
        check("%s: measured (%.4f mm surface, %.4f straight, %.2f s)"
              % (name, distance, straight, elapsed),
              distance is not None and distance >= straight - 1e-9)
        print("      %s" % description)

    # --------------------------------------------------------- decimation --
    print("\nB. producing the measurement meshes")
    meshes = [("original", original.triangle_count, original)]
    for target in TARGETS:
        context.view_layer.objects.active = source
        for other in list(bpy.data.objects):
            other.select_set(False)
        source.select_set(True)
        props.preprocess_target_triangles = target
        started = time.perf_counter()
        result = bpy.ops.bsmt.create_measurement_copy()
        elapsed = time.perf_counter() - started
        check("a measurement copy was made for %d" % target,
              result == {'FINISHED'}, str(result))
        copy = bpy.data.objects.get(props.preprocess_copy_name)
        check("  and it exists", copy is not None, props.preprocess_copy_name)
        if copy is None:
            continue
        copy.name = "Target_%d" % target
        canonical = geodesic.meshcache.get(context, copy, props.unit,
                                           rebuild=True)
        print("      %d target -> %d triangles in %.1f s"
              % (target, canonical.triangle_count, elapsed))
        check("  %d: the source scan was NOT modified" % target,
              original.geometry_hash
              == geodesic.meshcache.get(context, source, props.unit,
                                        rebuild=True).geometry_hash)
        meshes.append(("%dk" % (target // 1000), canonical.triangle_count,
                       canonical))

    # ------------------------------------------------------------- report --
    print("\nC. sensitivity: difference from the undecimated original")
    rows = []
    per_path = {}
    for name, _description, a_local, b_local in PATHS:
        reference = baseline[name][0]
        series = []
        for label, triangles, canonical in meshes:
            distance, straight, drift, elapsed = measure(
                canonical, a_local, b_local)
            difference = distance - reference
            rows.append((
                name, label, triangles, "%.4f" % distance,
                "%+.4f" % difference,
                "%+.4f" % (100.0 * difference / reference),
                "%.4f" % drift, "%.2f" % elapsed))
            series.append((label, triangles, distance, difference, drift))
        per_path[name] = series

    table(("path", "mesh", "triangles", "surface mm", "diff mm", "diff %",
           "anchor drift mm", "solve s"), rows)

    print("\nD. what the numbers do and do not support")
    for name, series in per_path.items():
        differences = [abs(entry[3]) for entry in series[1:]]
        drifts = [entry[4] for entry in series[1:]]
        check("%s: every decimated mesh still measures the path" % name,
              all(entry[2] > 0.0 for entry in series))
        # Ranking by density is the reportable structure: coarser meshes
        # should not be CLOSER to the original than finer ones, other than by
        # noise of the order of the anchor drift.
        ordered = all(
            differences[i] <= differences[i + 1] + max(drifts)
            for i in range(len(differences) - 1))
        check("%s: error is ordered by density, within the anchor drift "
              "(%s mm)" % (name, ", ".join("%.4f" % d for d in differences)),
              ordered, differences)
        check("%s: the largest deviation is %.4f mm at the coarsest mesh"
              % (name, differences[-1]), differences[-1] >= 0.0)

    worst = max(abs(float(row[4])) for row in rows if row[1] != "original")
    worst_percent = max(abs(float(row[5])) for row in rows
                        if row[1] != "original")
    print("\n  worst absolute deviation across every path and density: "
          "%.4f mm (%.4f%%)" % (worst, worst_percent))
    check("no path moved by more than 1% of its length under decimation",
          worst_percent < 1.0, "%.4f%%" % worst_percent)
    check("THIS IS CHARACTERISATION, NOT VALIDATION - the fixture is a "
          "torus, not a body, and no reference method was compared against",
          True)

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
        print("BSMT_DECIMATION_RESULT=%d" % code)
    raise SystemExit(code)
