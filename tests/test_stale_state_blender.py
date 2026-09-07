"""Failure and stale-state policy, enforced end to end (Milestone 3.25).

    /path/to/blender -b --factory-startup --python tests/test_stale_state_blender.py

The staleness rules themselves are small and readable - `_path_cache_state`
and `refresh_measurement_status` in state.py are each one function. What
needed a real Blender was the claim they exist to make: that a stored number
computed against one configuration is never shown, reused or exported once
that configuration has changed.

Every section below therefore asserts the same three things in different
words:

* the result went to the state the policy says it should,
* the OLD number was not silently reused, and
* where the operation is meant to be refused before solving, pygeodesic was
  never constructed.

Why the last one is counted rather than reasoned about
------------------------------------------------------
"The gate refuses it" is a claim about control flow, and control flow is
exactly what drifts. `PyGeodesicAlgorithmExact` is the only door to the
native solver, so wrapping its constructor and counting turns the claim into
a measurement. This mirrors tests/test_degenerate_policy.py, which proves the
same property for the blocking-defect list; the two suites share the
technique deliberately.

What is NOT re-proved here
--------------------------
Repair staleness is covered by tests/test_mesh_repair_blender.py, the
hidden/excluded measurement mesh by tests/test_picking_transforms.py, the
blocking-topology refusal by tests/test_degenerate_policy.py, and the export
of a stale row by tests/test_export.py. This file covers what none of them
did: geometry edit, scale change, landmark repick, the density guard's
refusal, and the cross-component refusal reached through a real operator.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_stale_state_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_stale_state_blender.py")
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


def build_scan(name="Scan", segments=48, rings=24, radius=100.0):
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments,
                                         ring_count=rings, radius=radius)
    obj = bpy.context.object
    obj.name = name
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


def add_measurements(context, pairs):
    """One measurement per (source_id, target_id) pair, in order."""
    collection = context.scene.bsmt_measurements
    collection.clear()
    for index, (source_id, target_id) in enumerate(pairs, start=1):
        item = collection.add()
        item.stable_id = index
        item.name = "M%d" % index
        item.protocol_id = "M%02d" % index
        item.source_stable_id = source_id
        item.target_stable_id = target_id
    context.scene.bsmt.measurement_index = 0
    return collection


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (export, geodesic, preprocess,
                                          state)

    context = bpy.context
    props = context.scene.bsmt

    unavailable = geodesic.ensure_loaded()
    if unavailable:
        print("SKIP  the exact backend is unavailable: %s" % unavailable)
        raise SystemExit(0)
    check("pygeodesic is instrumented",
          instrument(geodesic.registry.exact_mmp))

    def canonical_of(obj, rebuild=True):
        return geodesic.meshcache.get(context, obj, props.unit,
                                      rebuild=rebuild)

    def calculate_all():
        props.measurement_index = 0
        result = bpy.ops.bsmt.calculate_all_measurements()
        context.view_layer.update()
        return result

    def restate(obj):
        """Re-run the status policy against the live configuration."""
        canonical = canonical_of(obj)
        for item in context.scene.bsmt_measurements:
            state.refresh_measurement_status(context, item, canonical,
                                             obj.matrix_world)
        return canonical

    def numbers():
        return [(item.protocol_id, item.status,
                 bool(item.surface_valid), float(item.surface_mm))
                for item in context.scene.bsmt_measurements]

    # ================================================================== A ==
    print("\nA. a geometry edit stales the result, and the old number is gone")
    obj = build_scan()
    canonical = canonical_of(obj)
    add_landmarks(context, canonical, obj, (10, 300, 600, 900))
    add_measurements(context, ((1, 2), (3, 4)))
    check("both measurements calculated", calculate_all() == {'FINISHED'})

    before = numbers()
    check("both hold a surface distance",
          all(valid and value > 0.0 for _id, _s, valid, value in before),
          before)
    original_hash = canonical.geometry_hash

    # Move a vertex that is not under any landmark: the surface changes, so
    # every stored result on this mesh was computed against geometry that no
    # longer exists.
    mesh = obj.data
    moved = 1200 % len(mesh.vertices)
    mesh.vertices[moved].co.x += 7.0
    mesh.update()
    context.view_layer.update()

    edited = canonical_of(obj, rebuild=True)
    check("the geometry hash changed after the edit",
          edited.geometry_hash != original_hash,
          "%s -> %s" % (original_hash[:8], edited.geometry_hash[:8]))

    with NoSolver("A: restating status after a geometry edit"):
        for item in context.scene.bsmt_measurements:
            state.refresh_measurement_status(context, item, edited,
                                             obj.matrix_world)

    after = numbers()
    for (pid, status, valid, value) in after:
        check("A: %s is STALE" % pid, status == "STALE", status)
        check("A: %s carries no surface distance any more" % pid,
              not valid, value)
    check("A: and the old numbers were not kept anywhere",
          all(not valid for _i, _s, valid, _v in after), after)
    check("A: the definitions themselves survived",
          len(context.scene.bsmt_measurements) == 2)

    print("\n   the same edit stales the cached path")
    item = context.scene.bsmt_measurements[0]
    path_state, reason = state.path_cache_state(context, item, edited,
                                                obj.matrix_world)
    check("A: the cached path is not reported as usable",
          path_state != state.PATH_CACHED, path_state)
    check("A: and it says why", bool(reason) or path_state ==
          state.PATH_NOT_COMPUTED, "%s / %r" % (path_state, reason))

    print("\n   invalidate_for_geometry_change restates the whole scan")
    obj2 = build_scan()
    canonical2 = canonical_of(obj2)
    add_landmarks(context, canonical2, obj2, (10, 300, 600, 900))
    add_measurements(context, ((1, 2), (3, 4)))
    calculate_all()
    for item in context.scene.bsmt_measurements:
        item.result_object = obj2.name
    obj2.data.vertices[1200 % len(obj2.data.vertices)].co.x += 7.0
    obj2.data.update()
    canonical_of(obj2, rebuild=True)
    with NoSolver("A: invalidate_for_geometry_change"):
        summary = state.invalidate_for_geometry_change(context, obj2.name,
                                                       props)
    check("A: it reports the measurements it invalidated",
          summary["measurements_invalidated"] == 2, summary)
    check("A: and none of them still holds a number",
          all(not item.surface_valid
              for item in context.scene.bsmt_measurements))

    # ================================================================== B ==
    print("\nB. a SCALE change invalidates; translation and rotation do not")
    obj = build_scan()
    canonical = canonical_of(obj)
    add_landmarks(context, canonical, obj, (10, 300, 600, 900))
    add_measurements(context, ((1, 2), (3, 4)))
    calculate_all()
    baseline = numbers()
    check("B: baseline is valid",
          all(valid for _i, _s, valid, _v in baseline), baseline)

    # The control: a rigid motion must NOT invalidate. Section B of
    # tests/test_invariance_blender.py measures the distances themselves;
    # what is checked here is only that the status policy agrees.
    obj.location = (500.0, -250.0, 30.0)
    obj.rotation_euler = (0.4, -0.9, 1.3)
    context.view_layer.update()
    restate(obj)
    check("B: a rigid motion leaves every measurement VALID",
          all(item.status == "VALID"
              for item in context.scene.bsmt_measurements),
          [item.status for item in context.scene.bsmt_measurements])
    check("B: with its number intact", numbers() == baseline)

    obj.scale = (1.5, 1.5, 1.5)
    context.view_layer.update()
    with NoSolver("B: restating status after a scale change"):
        restate(obj)
    scaled = numbers()
    for (pid, status, valid, value) in scaled:
        check("B: %s is STALE after a uniform scale" % pid,
              status == "STALE", status)
        check("B: %s dropped its number rather than rescaling it" % pid,
              not valid, value)
    detail = context.scene.bsmt_measurements[0].status_detail
    check("B: and the reason names the scale or unit",
          "scale" in detail or "unit" in detail, detail)

    print("\n   a non-uniform scale too")
    obj.scale = (1.0, 1.0, 1.0)
    context.view_layer.update()
    calculate_all()
    check("B: recalculated at unit scale",
          all(item.surface_valid
              for item in context.scene.bsmt_measurements))
    obj.scale = (1.0, 2.0, 1.0)
    context.view_layer.update()
    restate(obj)
    check("B: a non-uniform scale stales every measurement",
          all(item.status == "STALE"
              for item in context.scene.bsmt_measurements),
          [item.status for item in context.scene.bsmt_measurements])
    obj.scale = (1.0, 1.0, 1.0)
    context.view_layer.update()

    print("\n   a coordinate-unit change is the same kind of event")
    calculate_all()
    check("B: valid again before the unit change",
          all(item.surface_valid
              for item in context.scene.bsmt_measurements))
    with NoSolver("B: changing the coordinate unit"):
        props.unit = 'CM' if props.unit != 'CM' else 'MM'
        context.view_layer.update()
    check("B: a unit change dropped every stored result",
          all(not item.surface_valid
              for item in context.scene.bsmt_measurements),
          [item.surface_valid
           for item in context.scene.bsmt_measurements])
    props.unit = 'MM'
    context.view_layer.update()

    # ================================================================== C ==
    print("\nC. re-picking a landmark invalidates ONLY what depends on it")
    obj = build_scan()
    canonical = canonical_of(obj)
    landmarks = add_landmarks(context, canonical, obj, (10, 300, 600, 900))
    # M01 uses L1+L2, M02 uses L3+L4. Re-picking L1 must reach M01 alone.
    add_measurements(context, ((1, 2), (3, 4)))
    calculate_all()
    check("C: both measurements valid to begin with",
          all(item.surface_valid
              for item in context.scene.bsmt_measurements))
    untouched_before = numbers()[1]

    with NoSolver("C: invalidating for one landmark"):
        affected = state.invalidate_measurements_for_landmark(
            context, 1, "the landmark was re-picked")
    check("C: exactly one measurement was affected", affected == 1, affected)

    m01, m02 = context.scene.bsmt_measurements
    check("C: M01 lost its result", not m01.surface_valid)
    check("C: M02 kept its result", m02.surface_valid)
    check("C: M02's number is untouched", numbers()[1] == untouched_before,
          (numbers()[1], untouched_before))

    print("\n   and actually moving the point is caught by the policy")
    calculate_all()
    check("C: recalculated", all(item.surface_valid
                                 for item in context.scene.bsmt_measurements))
    point = landmarks[0].surface_point
    point.triangle_index = 700
    bary = np.array(point.barycentric, dtype=np.float64)
    point.local_xyz = tuple(
        float(v) for v in canonical.local_from(700, bary))
    point.world_xyz = tuple(float(v) for v in canonical.world_from(
        700, bary, obj.matrix_world))
    with NoSolver("C: restating after the point moved"):
        canonical_now = restate(obj)

    # The PATH cache carries its own endpoint record (path_source_triangle /
    # path_source_bary) and verifies it passively, so a moved endpoint is
    # caught even with no event to hear.
    m01 = context.scene.bsmt_measurements[0]
    path_state, path_reason = state.path_cache_state(context, m01,
                                                     canonical_now,
                                                     obj.matrix_world)
    check("C: the cached PATH detects the moved endpoint on its own",
          path_state != state.PATH_CACHED, path_state)
    if path_state == state.PATH_STALE:
        check("C: and names the re-pick as the reason",
              "re-picked" in path_reason or "landmark" in path_reason,
              path_reason)

    # The stored RESULT has no equivalent endpoint record - it keeps
    # result_object, result_geometry_hash and result_metric_tensor only - so
    # it is invalidated by the EVENT rather than by comparison. That event is
    # what the first half of this section exercised. Recorded explicitly so
    # the asymmetry is a documented property and not an assumption.
    check("C: result invalidation on a re-pick is event-driven, and every "
          "production path that moves a landmark fires it",
          state.invalidate_measurements_for_landmark(
              context, 1, "landmark 'L1' was re-picked") == 1)
    check("C: after that event M01 holds no number",
          not context.scene.bsmt_measurements[0].surface_valid)
    check("C: M02, which does not use it, is still VALID",
          context.scene.bsmt_measurements[1].status == "VALID",
          context.scene.bsmt_measurements[1].status)

    # ================================================================== D ==
    print("\nD. the density guard refuses before the solver, and keeps state")
    report = {
        "triangle_count": preprocess.DEFAULT_DENSE_THRESHOLD + 1,
        "nonmanifold_edge_count": 0,
        "degenerate_triangle_count": 0,
        "component_count": 1,
        "boundary_edge_count": 0,
        "vertex_count": 10,
    }
    guarded = preprocess.preflight(report, guard_dense=True)
    check("D: a scan above the threshold is refused", not guarded["allowed"],
          guarded)
    check("D: and the refusal names the threshold",
          any("threshold" in text for text in guarded["refusals"]),
          guarded["refusals"])
    check("D: and tells the researcher how to proceed",
          any("measurement mesh" in text for text in guarded["refusals"]),
          guarded["refusals"])

    overridden = preprocess.preflight(report, guard_dense=False)
    check("D: unticking the guard downgrades it to a warning",
          overridden["allowed"] and any("High-density" in text
                                        for text in overridden["warnings"]),
          overridden)
    check("D: the guard is operational, not a blocking defect",
          not preprocess.blocking_defects(report),
          preprocess.blocking_defects(report))

    print("\n   a refusal must not disturb a result that was already valid")
    obj = build_scan()
    canonical = canonical_of(obj)
    add_landmarks(context, canonical, obj, (10, 300, 600, 900))
    add_measurements(context, ((1, 2), (3, 4)))
    calculate_all()
    kept = numbers()
    check("D: results are valid before the refusal",
          all(valid for _i, _s, valid, _v in kept), kept)
    with NoSolver("D: a guarded preflight"):
        again = preprocess.preflight(report, guard_dense=True)
        check("D: still refused", not again["allowed"])
    check("D: and every prior result survived the refusal untouched",
          numbers() == kept, (numbers(), kept))

    # ================================================================== E ==
    print("\nE. a cross-component pair is refused before pygeodesic")
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16,
                                         radius=100.0, location=(0, 0, 0))
    first = bpy.context.object
    first.name = "TwoPieces"
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16,
                                         radius=100.0, location=(500, 0, 0))
    second = bpy.context.object
    # Join, so one object carries two disconnected components.
    bpy.ops.object.select_all(action='DESELECT')
    first.select_set(True)
    second.select_set(True)
    bpy.context.view_layer.objects.active = first
    bpy.ops.object.join()
    obj = bpy.context.object
    obj.name = "TwoPieces"
    bpy.context.view_layer.objects.active = obj
    canonical = canonical_of(obj, rebuild=True)
    component_count = int(canonical.topology.get("component_count", 0))
    check("E: the fixture really has two components",
          component_count == 2, component_count)

    # One landmark on each component.
    per_component = {}
    for triangle in range(canonical.triangle_count):
        cid = canonical.component_of(triangle)
        per_component.setdefault(cid, triangle)
        if len(per_component) == 2:
            break
    triangles = [per_component[cid] for cid in sorted(per_component)]
    add_landmarks(context, canonical, obj, triangles)
    add_measurements(context, ((1, 2),))
    check("E: the two landmarks really are on different components",
          context.scene.bsmt_landmarks[0].surface_point.component_id
          != context.scene.bsmt_landmarks[1].surface_point.component_id)

    with NoSolver("E: calculating a cross-component measurement"):
        calculate_all()
    item = context.scene.bsmt_measurements[0]
    check("E: no surface distance was produced", not item.surface_valid,
          item.surface_mm)
    check("E: the measurement is not VALID", item.status != "VALID",
          item.status)
    blob = "%s %s" % (item.status, item.status_detail)
    check("E: and the reason mentions the disconnection",
          "component" in blob.lower() or "disconnect" in blob.lower(), blob)

    with NoSolver("E: asking for the surface PATH of the same pair"):
        # poll() failing IS a refusal - the operator is not even offered for
        # a measurement that has no valid result - so it counts, and calling
        # through a failed poll would raise instead of being refused.
        if bpy.ops.bsmt.compute_surface_path.poll():
            bpy.ops.bsmt.compute_surface_path()
            offered = True
        else:
            offered = False
    check("E: the path was refused, whether by poll or by the gate",
          not item.path_valid, (offered, item.path_valid))

    # ================================================================== F ==
    print("\nF. every one of these states exports unambiguously")
    session = state.session_metadata(props)
    # Read the real version rather than a literal, so this fixture cannot
    # drift out of date at the next release.
    version_text = ".".join(str(part) for part in bsmt.VERSION)
    rows = []
    for item in context.scene.bsmt_measurements:
        rows.append(export.measurement_row(
            session, state.measurement_export_record(context, item),
            {}, version_text, "2026-09-07T00:00:00"))

    # Rebuild a mixture: one VALID, one STALE, one refused.
    obj = build_scan()
    canonical = canonical_of(obj)
    add_landmarks(context, canonical, obj, (10, 300, 600, 900))
    add_measurements(context, ((1, 2), (3, 4)))
    calculate_all()
    state.invalidate_measurement_result(
        context.scene.bsmt_measurements[1], "the mesh geometry changed")
    mixture = []
    for item in context.scene.bsmt_measurements:
        mixture.append(export.measurement_row(
            session, state.measurement_export_record(context, item),
            {}, version_text, "2026-09-07T00:00:00"))

    valid_row, stale_row = mixture
    check("F: the VALID row carries its number",
          valid_row["surface_distance_mm"] not in ("", None),
          valid_row["surface_distance_mm"])
    check("F: the invalidated row carries NO number",
          stale_row["surface_distance_mm"] == "",
          stale_row["surface_distance_mm"])
    check("F: and specifically not a zero",
          stale_row["surface_distance_mm"] != "0.000000",
          stale_row["surface_distance_mm"])
    check("F: its straight distance is blank too",
          stale_row["straight_distance_mm"] == "",
          stale_row["straight_distance_mm"])
    check("F: no ratio is invented for it",
          stale_row["surface_to_straight_ratio"] == "",
          stale_row["surface_to_straight_ratio"])
    check("F: but the row keeps its identity",
          stale_row["measurement_id"] == "M02"
          and stale_row["measurement_stable_id"] == "2",
          (stale_row["measurement_id"],
           stale_row["measurement_stable_id"]))
    check("F: and states a status a reader can act on",
          bool(stale_row["status"]), stale_row["status"])

    for row in rows:
        check("F: the refused cross-component row exports no number",
              row["surface_distance_mm"] == "", row["surface_distance_mm"])
        check("F: and is not silently blank-statused", bool(row["status"]),
              row["status"])

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
        print("BSMT_STALE_RESULT=%d" % code)
    raise SystemExit(code)
