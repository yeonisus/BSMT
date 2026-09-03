"""The degenerate-triangle policy, enforced end to end (Milestone 3.18).

    /path/to/blender -b --factory-startup --python tests/test_degenerate_policy.py

The pure rules are in tests/test_preprocess.py. What needs real Blender is the
claim that matters: that a mesh the UI calls NOT READY never reaches
pygeodesic. That is proved by counting constructions of
``PyGeodesicAlgorithmExact`` - the only door to the native solver - across a
real surface-distance call and a real surface-path call.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_degenerate_policy.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_degenerate_policy.py")
    raise SystemExit(0)

import numpy as np  # noqa: E402

FAILURES = []
CHECKS = [0]
SOLVER_CALLS = {"construct": 0}


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def instrument(exact_mmp):
    """Count every construction of the native solver."""
    module = getattr(exact_mmp, "_geodesic", None)
    if module is None:
        return False
    original = module.PyGeodesicAlgorithmExact

    class Counted(object):
        def __init__(self, *args, **kwargs):
            SOLVER_CALLS["construct"] += 1
            self._inner = original(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    module.PyGeodesicAlgorithmExact = Counted
    return True


class NoSolver(object):
    """Assert a block of work never constructed the native solver."""

    def __init__(self, label):
        self.label = label

    def __enter__(self):
        self.before = SOLVER_CALLS["construct"]
        return self

    def __exit__(self, *exc):
        check("%s: pygeodesic was never constructed" % self.label,
              SOLVER_CALLS["construct"] == self.before,
              "%d construction(s)" % (SOLVER_CALLS["construct"] - self.before))
        return False


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def build_scan(name, degenerate=False, segments=48, rings=24):
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings)
    obj = bpy.context.object
    obj.name = name
    if degenerate:
        # Collapse vertices onto a neighbour: exact coincident vertices and
        # zero-area triangles, with the edge/face topology untouched - so the
        # mesh stays manifold, closed and single-component.
        for index in range(1, 15):
            obj.data.vertices[index].co = obj.data.vertices[0].co
        obj.data.update()
    bpy.context.view_layer.objects.active = obj
    return obj


def add_landmarks(context, canonical, obj, triangles):
    collection = context.scene.bsmt_landmarks
    collection.clear()
    for index, triangle in enumerate(triangles, start=1):
        item = collection.add()
        item.stable_id = index
        item.name = "L%d" % index
        item.protocol_id = "P%02d" % index
        # The landmark's OWN status is what measurement readiness reads; the
        # SurfacePoint's status is a different field.
        item.status = "VALID"
        point = item.surface_point
        point.triangle_index = int(triangle)
        point.barycentric = (1 / 3.0, 1 / 3.0, 1 / 3.0)
        point.source_object = obj.name
        point.geometry_hash = canonical.geometry_hash
        point.component_id = canonical.component_of(triangle)
        point.status = "VALID"
        point.valid = True
        bary = np.array(point.barycentric, dtype=np.float64)
        point.local_xyz = tuple(
            float(v) for v in canonical.local_from(triangle, bary))
        point.world_xyz = tuple(float(v) for v in canonical.world_from(
            triangle, bary, obj.matrix_world))
    return collection


def add_measurement(context, source_id, target_id):
    collection = context.scene.bsmt_measurements
    collection.clear()
    item = collection.add()
    item.stable_id = 1
    item.name = "M1"
    item.protocol_id = "M01"
    item.source_stable_id = source_id
    item.target_stable_id = target_id
    context.scene.bsmt.measurement_index = 0
    return item


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import geodesic, preprocess, state

    context = bpy.context
    props = context.scene.bsmt
    check("pygeodesic is instrumented", instrument(geodesic.registry.exact_mmp))

    # ------------------------------------------------------------------ A --
    print("\nA. a degenerate mesh is refused by the UI and by the solver")
    bad = build_scan("DegenerateScan", degenerate=True)
    canonical = geodesic.meshcache.get(context, bad, props.unit, rebuild=True)
    report = canonical.topology
    check("1 connected component", report["component_count"] == 1,
          report["component_count"])
    check("0 boundary edges", report["boundary_edge_count"] == 0,
          report["boundary_edge_count"])
    check("0 non-manifold edges", report["nonmanifold_edge_count"] == 0,
          report["nonmanifold_edge_count"])
    check("degenerate triangles present",
          report["degenerate_triangle_count"] > 0,
          report["degenerate_triangle_count"])

    verdict = state.mesh_verdict(context, props, bad)
    check("the UI verdict is NOT READY",
          verdict["state"] == preprocess.MEASUREMENT_NOT_READY, verdict["state"])
    gate = preprocess.preflight(report, props.dense_threshold_triangles,
                                props.guard_dense_solve)
    check("and the solver gate refuses the same mesh", not gate["allowed"],
          gate["refusals"])

    add_landmarks(context, canonical, bad, (10, canonical.triangle_count // 2))
    item = add_measurement(context, 1, 2)

    print("\n   the real surface-distance operator")
    # A reported ERROR surfaces as RuntimeError when an operator is called
    # from Python. Refusing IS the expected outcome here.
    refused = ""
    with NoSolver("surface distance on a degenerate mesh"):
        try:
            result = bpy.ops.bsmt.calculate_measurement()
        except RuntimeError as exc:
            result, refused = {'CANCELLED'}, str(exc)
    check("the operator did not report success", result != {'FINISHED'},
          str(result))
    check("and the refusal reached the user",
          "degenerate" in refused.lower(), refused)
    check("naming the Mesh Repair panel", "Mesh Repair" in refused, refused)
    check("and asking for a re-analysis", "re-analyze" in refused, refused)
    check("no surface distance was stored", not item.surface_valid)
    check("and the failure names the degenerate triangles",
          "degenerate" in item.status_detail.lower(), item.status_detail)
    check("naming the measurement mesh, not a 'copy'",
          "measurement copy" not in item.status_detail.lower(),
          item.status_detail)

    print("\n   the real surface-path operator")
    # The path operator requires a stored surface distance first, so the
    # refusal it must give is the earlier one - there is no route to a path.
    with NoSolver("surface path on a degenerate mesh"):
        try:
            bpy.ops.bsmt.compute_surface_path()
        except RuntimeError:
            pass
    check("no path was cached", not item.path_valid)

    print("\n   forcing the path route with a surface distance in place")
    # Give the measurement a stored distance so compute_surface_path's own
    # precondition passes and the ONLY thing left to stop it is the gate.
    item.surface_valid = True
    item.surface_mm = 42.0
    item.result_object = bad.name
    item.result_geometry_hash = canonical.geometry_hash
    with NoSolver("surface path, gate is the only thing left"):
        try:
            bpy.ops.bsmt.compute_surface_path()
        except RuntimeError:
            pass
    check("the path was still refused", not item.path_valid)
    check("and the status names the refusal",
          "degenerate" in props.viz_status.lower(), props.viz_status)

    print("\n   the A-to-B surface distance route")
    for slot, triangle in (('A', 10), ('B', canonical.triangle_count // 2)):
        point = state.surface_point(props, slot)
        point.triangle_index = triangle
        point.barycentric = (1 / 3.0, 1 / 3.0, 1 / 3.0)
        point.source_object = bad.name
        point.geometry_hash = canonical.geometry_hash
        point.component_id = canonical.component_of(triangle)
        point.status = "VALID"
        point.valid = True
    props.surface_object = bad.name
    with NoSolver("A/B surface distance on a degenerate mesh"):
        try:
            bpy.ops.bsmt.calculate_surface_distance()
        except RuntimeError:
            pass
    check("no A/B surface distance was produced", not props.surface_valid)
    check("and it says why",
          "degenerate" in props.surface_status.lower(), props.surface_status)

    # ------------------------------------------------------------------ B --
    print("\nB. coincident vertices alone refuse nothing")
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
    coincident = bpy.context.object
    coincident.name = "CoincidentScan"
    # A LOOSE duplicate vertex: coincident with another, but used by no face,
    # so it produces no zero-area triangle.
    mesh = coincident.data
    mesh.vertices.add(1)
    mesh.vertices[-1].co = mesh.vertices[0].co
    mesh.update()
    bpy.context.view_layer.objects.active = coincident
    canonical = geodesic.meshcache.get(context, coincident, props.unit,
                                       rebuild=True)
    report = canonical.topology
    check("the fixture has coincident vertices",
          report["duplicate_vertex_count"] > 0,
          report["duplicate_vertex_count"])
    check("and no degenerate triangles",
          report["degenerate_triangle_count"] == 0,
          report["degenerate_triangle_count"])
    verdict = state.mesh_verdict(context, props, coincident)
    check("the UI does not call it NOT READY",
          verdict["state"] != preprocess.MEASUREMENT_NOT_READY,
          (verdict["state"], verdict["reasons"]))
    gate = preprocess.preflight(report, props.dense_threshold_triangles,
                                props.guard_dense_solve)
    check("and the solver is not refused",
          gate["allowed"] or "coincident" not in " ".join(gate["refusals"]),
          gate["refusals"])
    check("nothing is refused BECAUSE of coincidence",
          not any("coincident" in line.lower() for line in gate["refusals"]),
          gate["refusals"])

    # ---------------------------------------------------------------- C/E --
    print("\nC/E. a clean mesh still solves, and the gate lets it through")
    good = build_scan("CleanScan")
    canonical = geodesic.meshcache.get(context, good, props.unit, rebuild=True)
    check("the clean fixture has no degenerate triangles",
          canonical.topology["degenerate_triangle_count"] == 0)
    verdict = state.mesh_verdict(context, props, good)
    check("the UI calls it READY",
          verdict["state"] == preprocess.MEASUREMENT_READY, verdict)

    add_landmarks(context, canonical, good, (10, canonical.triangle_count // 2))
    item = add_measurement(context, 1, 2)
    before = SOLVER_CALLS["construct"]
    result = bpy.ops.bsmt.calculate_measurement()
    check("the surface distance is calculated", result == {'FINISHED'},
          "%s / %s" % (result, item.status_detail))
    check("a real solve ran", SOLVER_CALLS["construct"] > before,
          SOLVER_CALLS["construct"] - before)
    check("and a distance was stored", item.surface_valid and item.surface_mm > 0,
          item.surface_mm)

    print("\n   the same-component rule is untouched")
    check("both landmarks are on component 1",
          context.scene.bsmt_landmarks[0].surface_point.component_id
          == context.scene.bsmt_landmarks[1].surface_point.component_id)
    solve = geodesic.solve
    spec_a = solve.PointSpec(10, np.array([1 / 3.0] * 3), component_id=1)
    spec_b = solve.PointSpec(20, np.array([1 / 3.0] * 3), component_id=2)
    try:
        solve.validate_points(spec_a, spec_b, canonical.triangle_count)
        check("a cross-component pair is refused", False, "no error raised")
    except solve.MeasurementError as exc:
        check("a cross-component pair is still refused by DISCONNECTED",
              exc.code == 'DISCONNECTED', exc.code)

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for label in FAILURES:
        print("  FAILED: %s" % label)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        sys.stdout.flush()
        print("BSMT_DEGENERATE_POLICY_RESULT=%d" % code)
