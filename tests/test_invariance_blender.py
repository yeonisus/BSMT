"""Rigid-transform and save/reload invariance (Milestone 3.25).

    /path/to/blender -b --factory-startup --python tests/test_invariance_blender.py

A rigid motion is an isometry of the embedding, so it cannot change a distance
between two points ON the surface. BSMT relies on that at three levels - the
canonical mesh keeps local coordinates, `geometry_hash` excludes
`matrix_world`, and `metric_key` is built from L^T L - and this suite measures
whether the claim survives the whole pipeline rather than each layer alone.

What the tolerance is, and why it is not zero
--------------------------------------------
The first version of this suite asserted BIT-IDENTICAL distances, on the
reasoning that the solver is handed canonical LOCAL vertices that a transform
never touches. **That reasoning was wrong, and the test caught it.**
`spaces.to_solver_space` builds the solver mesh by applying `matrix_world` -
it has to, because the solver works in physical millimetres and the object
transform is what carries the scan into them. So a rigid motion does reach the
solver's input, as a rotation that is mathematically an isometry but is stored
by Blender in **single precision**.

The justified tolerance is therefore the precision of that storage, and it is
the same one alignment already uses (PROJECT_SPEC sect. 11aa.3): about 1e-7 of
the largest coordinate magnitude involved, taken here at 1e-6 of the
coordinate reach for headroom. That is not a fudge factor - it is a property
of where Blender keeps an object's pose, it scales with the scene as the error
does, and the measured errors below sit one to two orders of magnitude inside
it.

In practice: on a 100 mm sphere the invariance error is ~1.5e-5 mm, and at the
real scan's position 28 metres from the origin it is ~8e-4 mm. Sub-micrometre,
against scans whose own accuracy is measured in tenths of a millimetre.

Save/reload is the same story: a .blend stores transforms in single precision
and the SurfacePoint's cached fields are Blender FloatProperties. The stored
RESULTS, being float64 properties written once, come back exactly.
"""

import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_invariance_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_invariance_blender.py")
    raise SystemExit(0)

import numpy as np  # noqa: E402

FAILURES = []
CHECKS = [0]
SOLVER_CALLS = {"construct": 0, "query": 0}

#: Relative tolerance for "a rigid transform changed nothing". Blender stores
#: an object pose in single precision, so a distance computed through
#: matrix_world carries about 1e-7 of the coordinate magnitudes involved;
#: 1e-6 is that with headroom, and it is the same constant family
#: alignment.position_tolerance uses for the same reason.
TRANSFORM_RELATIVE = 1e-6


def table(name, header, rows):
    widths = [max(len(str(header[i])), max(len(str(r[i])) for r in rows))
              for i in range(len(header))]
    print("\n  | " + " | ".join(str(header[i]).ljust(widths[i])
                                for i in range(len(header))) + " |")
    print("  |-" + "-|-".join("-" * w for w in widths) + "-|")
    for row in rows:
        print("  | " + " | ".join(str(row[i]).ljust(widths[i])
                                  for i in range(len(row))) + " |")
    print("")


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def instrument_solver(exact_mmp):
    """Count every construction of, and query against, the native solver."""
    module = getattr(exact_mmp, "_geodesic", None)
    if module is None:
        return False
    original = module.PyGeodesicAlgorithmExact

    class Counted(object):
        def __init__(self, *args, **kwargs):
            SOLVER_CALLS["construct"] += 1
            self._inner = original(*args, **kwargs)

        def geodesicDistance(self, *args, **kwargs):
            SOLVER_CALLS["query"] += 1
            return self._inner.geodesicDistance(*args, **kwargs)

        def geodesicDistances(self, *args, **kwargs):
            SOLVER_CALLS["query"] += 1
            return self._inner.geodesicDistances(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    module.PyGeodesicAlgorithmExact = Counted
    return True


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def rotation_euler(seed):
    """A reproducible, definitely-not-axis-aligned orientation."""
    rng = np.random.default_rng(seed)
    return tuple(float(v) for v in rng.uniform(-math.pi, math.pi, 3))


# ---------------------------------------------------------------------------


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (attach, geodesic, measurements,
                                          pathcache, state)

    unavailable = geodesic.ensure_loaded()
    if unavailable:
        print("SKIP  the exact backend is unavailable: %s" % unavailable)
        raise SystemExit(0)
    check("pygeodesic is instrumented",
          instrument_solver(geodesic.registry.exact_mmp))

    context = bpy.context
    props = context.scene.bsmt

    # ------------------------------------------------------------ fixture --
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24,
                                         radius=100.0)
    obj = context.object
    obj.name = "Scan"
    context.view_layer.objects.active = obj
    context.view_layer.update()
    canonical = geodesic.meshcache.get(context, obj, props.unit, rebuild=True)
    print("  fixture: %d triangles, unit %s"
          % (canonical.triangle_count, props.unit))

    landmark_collection = context.scene.bsmt_landmarks
    landmark_collection.clear()
    triangles = (17, canonical.triangle_count // 3,
                 (2 * canonical.triangle_count) // 3, canonical.triangle_count - 9)
    for index, triangle in enumerate(triangles):
        item = landmark_collection.add()
        item.stable_id = index + 1
        item.name = "L%d" % (index + 1)
        item.protocol_id = "P%02d" % (index + 1)
        point = item.surface_point
        bary = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
        local = canonical.local_from(triangle, np.array(bary))
        state.fill_surface_point(
            point, obj.name, canonical.geometry_hash, triangle, bary,
            canonical.component_of(triangle), "FACE", local, local,
            tuple(float(v) * canonical.unit_multiplier for v in local), 0.0)
        item.status = "VALID"

    measurement_collection = context.scene.bsmt_measurements
    measurement_collection.clear()
    pairs = ((1, 2), (1, 3), (2, 4), (3, 4))
    for index, (a, b) in enumerate(pairs):
        item = measurement_collection.add()
        item.stable_id = index + 1
        item.name = "M%d" % (index + 1)
        item.protocol_id = "M%02d" % (index + 1)
        item.source_stable_id = a
        item.target_stable_id = b
        item.measurement_type = 'BOTH'
        item.enabled = True

    def calculate_all():
        props.measurement_index = 0
        result = bpy.ops.bsmt.calculate_all_measurements()
        context.view_layer.update()
        return result

    def snapshot():
        """Every number a researcher would read, plus the identity fields."""
        return [{
            "stable_id": int(item.stable_id),
            "protocol_id": item.protocol_id,
            "straight_valid": bool(item.straight_valid),
            "straight_mm": float(item.straight_mm),
            "surface_valid": bool(item.surface_valid),
            "surface_mm": float(item.surface_mm),
            "status": item.status,
            "result_geometry_hash": item.result_geometry_hash,
        } for item in measurement_collection]

    def landmark_state():
        return [(int(item.stable_id), item.surface_point.valid,
                 int(item.surface_point.triangle_index),
                 tuple(round(float(v), 12) for v in item.surface_point.barycentric),
                 item.surface_point.geometry_hash, item.status)
                for item in landmark_collection]

    # ------------------------------------------------------------------ A --
    print("\nA. the baseline, at the identity transform")
    check("every measurement calculated", calculate_all() == {'FINISHED'})
    baseline = snapshot()
    baseline_landmarks = landmark_state()
    for row in baseline:
        check("%s has a straight distance" % row["protocol_id"],
              row["straight_valid"] and row["straight_mm"] > 0.0)
        check("%s has a surface distance" % row["protocol_id"],
              row["surface_valid"] and row["surface_mm"] > 0.0)
        check("%s: surface >= straight" % row["protocol_id"],
              row["surface_mm"] >= row["straight_mm"] - 1e-9,
              "%.9f vs %.9f" % (row["surface_mm"], row["straight_mm"]))
    print("  baseline: " + ", ".join(
        "%s %.6f/%.6f" % (r["protocol_id"], r["straight_mm"], r["surface_mm"])
        for r in baseline))

    # ------------------------------------------------------------------ B --
    print("\nB. translate, rotate, and both - distances must not move at all")
    poses = (
        ("translate", (0.0, 0.0, 0.0), (1234.5, -6789.0, 42.0)),
        ("rotate", rotation_euler(1), (0.0, 0.0, 0.0)),
        ("translate + rotate", rotation_euler(2), (-28570.0, -2692.0, -176.0)),
        ("another rotation", rotation_euler(3), (5.0, 5.0, 5.0)),
    )
    invariance_rows = []
    for label, rotation, location in poses:
        obj.rotation_euler = rotation
        obj.location = location
        context.view_layer.update()
        attach.refresh(props, reason="test")
        check("%s: every SurfacePoint is still VALID" % label,
              all(item.surface_point.valid for item in landmark_collection))
        check("%s: and still names the same triangle and barycentric" % label,
              landmark_state() == baseline_landmarks)

        moved = calculate_all()
        check("%s: recalculating succeeds" % label, moved == {'FINISHED'})
        after = snapshot()
        check("%s: the measurement IDs are unchanged" % label,
              [r["stable_id"] for r in after] == [r["stable_id"] for r in baseline]
              and [r["protocol_id"] for r in after]
              == [r["protocol_id"] for r in baseline])
        check("%s: the geometry hash is unchanged - a transform is not a "
              "geometry change" % label,
              [r["result_geometry_hash"] for r in after]
              == [r["result_geometry_hash"] for r in baseline])
        worst_straight = max(abs(a["straight_mm"] - b["straight_mm"])
                             for a, b in zip(after, baseline))
        worst_surface = max(abs(a["surface_mm"] - b["surface_mm"])
                            for a, b in zip(after, baseline))
        reach = max(abs(v) for v in location) or 100.0
        reach = max(reach, 100.0)                      # the sphere's own size
        tolerance = TRANSFORM_RELATIVE * reach
        invariance_rows.append((
            label, "%.4g" % reach, "%.3e" % worst_straight,
            "%.3e" % worst_surface, "%.3e" % tolerance,
            ("exact" if max(worst_surface, worst_straight) == 0.0
             else "%.0fx" % (tolerance
                             / max(worst_surface, worst_straight)))))
        check("%s: SURFACE distances invariant to %.3e mm, inside the %.3e "
              "the single-precision pose allows"
              % (label, worst_surface, tolerance), worst_surface <= tolerance)
        check("%s: STRAIGHT distances invariant to %.3e mm, inside %.3e"
              % (label, worst_straight, tolerance), worst_straight <= tolerance)
        check("%s: every status is unchanged" % label,
              [r["status"] for r in after] == [r["status"] for r in baseline])

    table("invariance",
          ("pose", "coord reach", "worst straight diff", "worst surface diff",
           "tolerance", "margin"), invariance_rows)

    # ------------------------------------------------------------------ C --
    print("\nC. a transform must not make the solver run again")
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.location = (0.0, 0.0, 0.0)
    context.view_layer.update()
    calculate_all()
    cached = snapshot()

    before_calls = dict(SOLVER_CALLS)
    obj.rotation_euler = rotation_euler(7)
    obj.location = (900.0, -400.0, 250.0)
    context.view_layer.update()
    attach.refresh(props, reason="test")
    check("C: moving the object calls the solver ZERO times",
          SOLVER_CALLS == before_calls,
          "%s -> %s" % (before_calls, SOLVER_CALLS))
    check("C: and the stored results are still marked valid",
          all(item.surface_valid for item in measurement_collection))
    check("C: with the same numbers", snapshot() == cached)

    # The cached exact path is stored in LOCAL coordinates, so a rigid
    # transform is exactly the case it is meant to survive.
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.location = (0.0, 0.0, 0.0)
    context.view_layer.update()
    props.measurement_index = 0
    path_result = bpy.ops.bsmt.compute_surface_path()
    check("C: an exact surface path can be computed",
          path_result == {'FINISHED'}, str(path_result))
    first = measurement_collection[0]
    cached_points = pathcache.point_count(int(first.stable_id))
    cached_generation = pathcache.generation(int(first.stable_id))
    check("C: and it is cached", cached_points > 0, cached_points)

    before_calls = dict(SOLVER_CALLS)
    obj.rotation_euler = rotation_euler(11)
    obj.location = (-2000.0, 700.0, 15.0)
    context.view_layer.update()
    attach.refresh(props, reason="test")
    bpy.ops.bsmt.refresh_visualization()
    context.view_layer.update()
    check("C: refreshing the visualization after a transform calls the "
          "solver ZERO times", SOLVER_CALLS == before_calls,
          "%s -> %s" % (before_calls, SOLVER_CALLS))
    check("C: the cached path is still there, same generation",
          pathcache.exists(int(first.stable_id))
          and pathcache.point_count(int(first.stable_id)) == cached_points
          and pathcache.generation(int(first.stable_id)) == cached_generation,
          "%s points, generation %s"
          % (pathcache.point_count(int(first.stable_id)),
             pathcache.generation(int(first.stable_id))))
    check("C: and the result it belongs to is still valid",
          first.surface_valid)

    # ------------------------------------------------------------------ D --
    print("\nD. save and reload the .blend")
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "invariance.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)

        context = bpy.context
        props = context.scene.bsmt
        landmark_collection = context.scene.bsmt_landmarks
        measurement_collection = context.scene.bsmt_measurements
        obj = bpy.data.objects["Scan"]

        check("D: the landmarks came back", len(landmark_collection) == 4,
              len(landmark_collection))
        check("D: the measurements came back",
              len(measurement_collection) == 4, len(measurement_collection))
        check("D: every SurfacePoint is still VALID",
              all(item.surface_point.valid for item in landmark_collection))
        check("D: with the same triangle and barycentric",
              landmark_state() == baseline_landmarks, landmark_state())
        reloaded = snapshot()
        check("D: the measurement IDs survived",
              [r["stable_id"] for r in reloaded]
              == [r["stable_id"] for r in cached])
        check("D: and the stored results survived unchanged",
              reloaded == cached,
              [(a, b) for a, b in zip(reloaded, cached) if a != b])

        # And recomputing from the reloaded file gives the same answer, which
        # is the claim that actually matters for reproducibility.
        recomputed = calculate_all()
        check("D: recalculating after reload succeeds",
              recomputed == {'FINISHED'})
        again = snapshot()
        worst = max(abs(a["surface_mm"] - b["surface_mm"])
                    for a, b in zip(again, cached))
        # The file was saved in the transformed pose of section C, so this is
        # the reload AND the transform together. The tolerance is therefore
        # the same single-precision one, taken against that pose's reach.
        reload_tolerance = TRANSFORM_RELATIVE * 2000.0
        check("D: and reproduces every surface distance to %.3e mm, inside "
              "the %.3e a reloaded single-precision pose allows"
              % (worst, reload_tolerance), worst <= reload_tolerance)
        check("D: which is far below any scan's own accuracy",
              worst < 1e-3, "%.3e mm" % worst)

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
        print("BSMT_INVARIANCE_RESULT=%d" % code)
    raise SystemExit(code)
