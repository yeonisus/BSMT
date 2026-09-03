"""Acceptance tests for measurement-path visualisation (Milestone 3.14).

    /path/to/blender -b --factory-startup --python tests/test_path_visualization.py

This one CANNOT run offline. Everything it asserts is about what Blender and
the native solver actually do - whether a colour change reaches pygeodesic,
whether hiding a path costs a solve, whether a rigid transform keeps a cache -
and a stub cannot answer any of that.

The central instrument is a counter wrapped around
``pygeodesic.geodesic.PyGeodesicAlgorithmExact`` itself. That is the only door
to the native solver, so "no solve happened" is proved rather than assumed:
every check below records the count before and after and requires it to be
unchanged.
"""

import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bpy  # noqa: E402
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_path_visualization.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_path_visualization.py")
    raise SystemExit(0)

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


# ---------------------------------------------------------------------------
# native solver counter
# ---------------------------------------------------------------------------

SOLVER_CALLS = {"construct": 0, "query": 0}


def instrument_solver(exact_mmp):
    """Count every construction of, and query against, the native solver."""
    module = getattr(exact_mmp, "_geodesic", None)
    if module is None:
        return False
    original = module.PyGeodesicAlgorithmExact

    # Delegation, not subclassing: PyGeodesicAlgorithmExact is a Cython
    # extension type and refuses to be subclassed.
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


class NoSolve(object):
    """Assert that a block of display work never reached the native solver."""

    def __init__(self, label):
        self.label = label
        self.before = None
        self.seconds = 0.0

    def __enter__(self):
        self.before = dict(SOLVER_CALLS)
        self._started = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.seconds = time.perf_counter() - self._started
        check("%s: no pygeodesic call" % self.label,
              SOLVER_CALLS == self.before,
              "%s -> %s" % (self.before, SOLVER_CALLS))
        return False


# ---------------------------------------------------------------------------
# scene fixture
# ---------------------------------------------------------------------------

def build_scene(segments=64, rings=32):
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings,
                                         radius=1.0)
    obj = bpy.context.object
    obj.name = "Scan"
    return obj


def add_landmark(collection, canonical, obj, stable_id, triangle):
    item = collection.add()
    item.stable_id = stable_id
    item.name = "L%d" % stable_id
    item.protocol_id = "P%02d" % stable_id
    point = item.surface_point
    point.triangle_index = int(triangle)
    point.barycentric = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
    point.source_object = obj.name
    point.geometry_hash = canonical.geometry_hash
    point.component_id = canonical.component_of(triangle)
    point.status = "VALID"
    point.valid = True
    bary = np.array(point.barycentric, dtype=np.float64)
    point.local_xyz = tuple(
        float(v) for v in canonical.local_from(triangle, bary))
    point.world_xyz = tuple(
        float(v) for v in canonical.world_from(triangle, bary, obj.matrix_world))
    return item


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (geodesic, pathcache, state, timing,
                                          viz, visualization)

    check("pygeodesic is instrumented",
          instrument_solver(geodesic.registry.exact_mmp))

    context = bpy.context
    props = context.scene.bsmt
    obj = build_scene()
    canonical = geodesic.meshcache.get(context, obj, props.unit)
    print("  scan: %d triangles" % canonical.triangle_count)

    landmarks = context.scene.bsmt_landmarks
    source = add_landmark(landmarks, canonical, obj, 1, 40)
    target = add_landmark(landmarks, canonical, obj, 2,
                          canonical.triangle_count // 2)

    measurements = context.scene.bsmt_measurements
    item = measurements.add()
    item.stable_id = 1
    item.name = "M1"
    item.protocol_id = "M01"
    item.source_stable_id = 1
    item.target_stable_id = 2
    props.measurement_index = 0

    # ---------------------------------------------------------------- A ----
    print("\nA. the straight line is display-only")
    with NoSolve("straight line build") as timer:
        drawn = viz.build_straight(context, props, item)
    check("straight line is drawn", drawn)
    check("straight line is effectively instant (%.2f ms)"
          % (timer.seconds * 1000), timer.seconds < 0.05)
    with NoSolve("straight line restyle"):
        props.viz_straight_color = (1.0, 0.0, 0.0, 1.0)
        props.viz_straight_thickness_mm = 5.0
        props.show_line = False
        props.show_line = True

    print("\n   state before any solve")
    current, _reason = viz.path_state(context, props, item)
    check("path reports NOT COMPUTED", current == state.PATH_NOT_COMPUTED,
          current)
    with NoSolve("refresh with no cached path"):
        viz.refresh(context, props)
    check("and no path helper was created",
          not visualization.measurement_helper_exists(1, 'PATH'))

    # ---------------------------------------------------------------- B ----
    print("\nB. the exact path is solved exactly once, on request")
    solve = geodesic.solve

    def spec(landmark):
        point = landmark.surface_point
        return solve.PointSpec(
            point.triangle_index, np.array(point.barycentric, dtype=np.float64),
            component_id=point.component_id, source_object=point.source_object,
            geometry_hash=point.geometry_hash, status=point.status,
            valid=point.valid)

    distance = solve.surface_distance(
        canonical.vertices_solver, canonical.triangles,
        spec(source), spec(target), geometry_hash=canonical.geometry_hash)
    item.surface_valid = True
    item.surface_mm = distance.distance_mm
    item.straight_valid = True
    item.straight_mm = distance.straight_mm
    item.result_object = obj.name
    item.result_geometry_hash = canonical.geometry_hash
    item.result_metric_tensor = state.metric_tensor(
        obj.matrix_world, canonical.unit_multiplier)

    before = dict(SOLVER_CALLS)
    bpy.ops.bsmt.compute_surface_path()
    solves = SOLVER_CALLS["query"] - before["query"]
    check("exactly one path solve ran", solves == 1, solves)
    check("the path is now CACHED",
          viz.path_state(context, props, item)[0] == state.PATH_CACHED)
    check("the polyline is stored outside the helper",
          pathcache.exists(item.stable_id))
    points, normals = pathcache.load(item.stable_id)
    check("cached points match the reported count",
          points.shape[0] == item.path_point_count,
          "%d vs %d" % (points.shape[0], item.path_point_count))
    check("surface normals were cached with the points",
          float(np.linalg.norm(normals, axis=1).min()) > 0.0)
    check("the cache is stored in LOCAL coordinates, not world",
          float(np.abs(np.linalg.norm(points, axis=1) - 1.0).max()) < 0.05,
          "points are not on the unit-radius local sphere")
    check("the numeric surface distance is untouched by the path",
          abs(item.surface_mm - distance.distance_mm) < 1e-6)

    # ---------------------------------------------------------------- C ----
    print("\nC. hide -> show a cached path")
    with NoSolve("hide path") as timer:
        bpy.ops.bsmt.toggle_surface_path()
    check("the helper is hidden", not visualization.measurement_helper_visible(
        1, 'PATH'))
    check("but the cache survives hiding", pathcache.exists(1))
    check("and the hide is recorded on the measurement", not item.path_shown)
    with NoSolve("a refresh does not undo the hide"):
        viz.refresh(context, props)
    check("the path stays hidden across a refresh",
          not visualization.measurement_helper_visible(1, 'PATH'))
    with NoSolve("show path") as timer:
        bpy.ops.bsmt.toggle_surface_path()
    check("the helper is shown again",
          visualization.measurement_helper_visible(1, 'PATH'))
    check("and the measurement says so", item.path_shown)
    check("redisplay is immediate (%.2f ms)" % (timer.seconds * 1000),
          timer.seconds < 0.25)

    print("\n   and deleting the helper outright is survivable")
    with NoSolve("remove helper, then redraw") as timer:
        visualization.remove_measurement_helper(1, 'PATH')
        check("path is still CACHED with no helper present",
              viz.path_state(context, props, item)[0] == state.PATH_CACHED)
        redrawn = viz.draw_cached_path(context, props, item)
    check("the helper was rebuilt from the cache", redrawn)
    check("rebuild is immediate (%.2f ms)" % (timer.seconds * 1000),
          timer.seconds < 0.25)

    # -------------------------------------------------------------- D, E ---
    print("\nD/E. colour and thickness are cosmetic")
    with NoSolve("colour change") as timer:
        props.viz_path_color = (1.0, 0.2, 0.2, 1.0)
    check("colour change is immediate (%.2f ms)" % (timer.seconds * 1000),
          timer.seconds < 0.05)
    with NoSolve("thickness change") as timer:
        props.viz_path_thickness_mm = 6.0
    check("thickness change is immediate (%.2f ms)" % (timer.seconds * 1000),
          timer.seconds < 0.05)
    with NoSolve("surface-offset toggle"):
        props.viz_surface_offset = False
        props.viz_surface_offset = True
    check("the cache is untouched by restyling", pathcache.exists(1))
    check("and the path is still CACHED",
          viz.path_state(context, props, item)[0] == state.PATH_CACHED)

    # ---------------------------------------------------------------- F ----
    print("\nF. viewport navigation and redraws")
    with NoSolve("panel redraws"):
        for _ in range(20):
            viz.display_report(context, props)
            viz.path_state(context, props, item)
    with NoSolve("scene redraws / depsgraph ticks"):
        for _ in range(20):
            context.view_layer.update()
    check("still CACHED after 20 redraws",
          viz.path_state(context, props, item)[0] == state.PATH_CACHED)

    # ---------------------------------------------------------------- G ----
    print("\nG. rigid translation and rotation keep the cache")
    curve = bpy.data.objects[visualization.measurement_object_name(1, 'PATH')]
    with NoSolve("translate + rotate") as timer:
        obj.location = (0.5, -0.25, 0.125)
        obj.rotation_euler = (0.3, -0.7, 1.1)
        context.view_layer.update()
        viz.refresh(context, props)
    check("path is still CACHED after a rigid transform",
          viz.path_state(context, props, item)[0] == state.PATH_CACHED)
    check("the helper followed the scan",
          all(abs(curve.matrix_world[r][c] - obj.matrix_world[r][c]) < 1e-6
              for r in range(4) for c in range(4)))
    stored, _normals = pathcache.load(1)
    check("the cached polyline is still local (unmoved by the transform)",
          float(np.abs(np.linalg.norm(stored, axis=1) - 1.0).max()) < 0.05)

    # ---------------------------------------------------------------- H ----
    print("\nH. a scale change makes the path stale, silently and safely")
    with NoSolve("scale the scan"):
        obj.scale = (1.5, 1.5, 1.5)
        context.view_layer.update()
        geodesic.meshcache.get(context, obj, props.unit)
        current, reason = viz.path_state(context, props, item)
    check("path reports STALE", current == state.PATH_STALE, current)
    check("with a reason the researcher can read", bool(reason), reason)
    with NoSolve("refresh with a stale path"):
        viz.refresh(context, props)
    check("a stale path is not drawn",
          not visualization.measurement_helper_visible(1, 'PATH'))
    check("but the cache is NOT thrown away", pathcache.exists(1))
    check("and path_valid is still set (recompute is the user's call)",
          item.path_valid)

    obj.scale = (1.0, 1.0, 1.0)
    context.view_layer.update()
    geodesic.meshcache.get(context, obj, props.unit)
    with NoSolve("restore the original scale"):
        current, _reason = viz.path_state(context, props, item)
    check("undoing the scale makes the cache usable again",
          current == state.PATH_CACHED, current)

    # ---------------------------------------------------------------- I ----
    print("\nI. a landmark re-pick invalidates only what depends on it")
    other = add_landmark(landmarks, canonical, obj, 3, 60)
    second = measurements.add()
    second.stable_id = 2
    second.name = "M2"
    second.protocol_id = "M02"
    second.source_stable_id = 1
    second.target_stable_id = 3
    # Give the second measurement a cache of its own, without a solve: this
    # test is about which entries invalidate, not about solving twice.
    second.path_valid = True
    second.path_object = obj.name
    second.path_geometry_hash = canonical.geometry_hash
    second.path_metric_tensor = state.metric_tensor(obj.matrix_world,
                                                    canonical.unit_multiplier)
    second.path_source_stable_id = 1
    second.path_target_stable_id = 3
    second.path_source_triangle = source.surface_point.triangle_index
    second.path_source_bary = tuple(source.surface_point.barycentric)
    second.path_target_triangle = other.surface_point.triangle_index
    second.path_target_bary = tuple(other.surface_point.barycentric)
    pathcache.store(2, np.zeros((4, 3)), np.zeros((4, 3)))
    check("the second measurement starts CACHED",
          viz.path_state(context, props, second)[0] == state.PATH_CACHED)

    with NoSolve("re-pick landmark 2"):
        moved = 80
        point = target.surface_point
        point.triangle_index = moved
        bary = np.array(point.barycentric, dtype=np.float64)
        point.local_xyz = tuple(
            float(v) for v in canonical.local_from(moved, bary))
        current, reason = viz.path_state(context, props, item)
        untouched, _ = viz.path_state(context, props, second)
    check("the dependent measurement goes STALE",
          current == state.PATH_STALE, current)
    check("naming the re-pick as the reason", "re-picked" in reason, reason)
    check("the independent measurement stays CACHED",
          untouched == state.PATH_CACHED, untouched)

    # ------------------------------------------------------------ handlers -
    print("\nhandlers never reach the solver")
    from body_surface_measurement import attach
    with NoSolve("attach.refresh from a handler"):
        for _ in range(10):
            attach.refresh(props, reason="test")
    with NoSolve("geometry edit marks stale, never re-solves"):
        obj.data.vertices[0].co.x += 0.05
        obj.data.update()
        context.view_layer.update()
    check("a geometry edit dropped the canonical cache",
          geodesic.meshcache.peek(obj.name) is None)
    check("and marked the dependent path stale", second.path_stale)

    # ------------------------------------------------------------- timing --
    print("\ntiming diagnostics")
    totals = timing.totals()
    check("stages were recorded", bool(totals), totals)
    for stage in (timing.CACHE_VALIDATE, timing.CACHE_STORE, timing.CACHE_LOAD,
                  timing.PATH_SOLVE, timing.SOLVER_BUILD,
                  timing.HELPER_CREATE):
        check("timed: %s" % stage, stage in totals, sorted(totals))
    for label in sorted(totals, key=lambda key: -totals[key][1]):
        count, total = totals[label]
        print("    %-18s %4dx %9.2f ms" % (label, count, total))

    # ----------------------------------------------------- discard is explicit
    print("\nthe cache is discarded only when asked")
    bpy.ops.bsmt.clear_visualization()
    check("Clear Selected keeps the cached path", pathcache.exists(1))
    props.measurement_index = 0
    bpy.ops.bsmt.clear_cached_path()
    check("Clear Cached Path discards it", not pathcache.exists(1))
    check("and the state returns to NOT COMPUTED",
          viz.path_state(context, props, item)[0] == state.PATH_NOT_COMPUTED)

    # ------------------------------------------------- panel draw ---------
    print("\nthe visualisation panel draws in every path state")

    class FakeLayout(object):
        """Records what a panel asks for. Enough to run draw() headless.

        A panel's draw code is ordinary Python and can break like any other -
        a renamed helper, a missing argument - and Blender cannot build a
        real UILayout in background mode. This stands in for one so the draw
        path is at least executed in every state it has to handle.
        """

        def __init__(self, log):
            self.log = log
            self.alert = False
            self.enabled = True
            self.scale_y = 1.0

        def _child(self, *args, **kwargs):
            return FakeLayout(self.log)

        box = row = column = split = _child

        def label(self, **kwargs):
            self.log.append(("label", kwargs.get("text", "")))

        def prop(self, *args, **kwargs):
            self.log.append(("prop", args[1] if len(args) > 1 else ""))

        def operator(self, idname, **kwargs):
            self.log.append(("operator", idname))
            return FakeLayout(self.log)

        def separator(self, *args, **kwargs):
            pass

    from body_surface_measurement import panels
    panel_class = panels.BSMT_PT_measurement_visualization

    def make_cached():
        """Give the measurement a cache matching its landmarks as they are now."""
        pathcache.store(item.stable_id, np.zeros((4, 3)), np.zeros((4, 3)))
        item.path_valid = True
        item.path_point_count = 4
        item.path_object = obj.name
        item.path_source_stable_id = item.source_stable_id
        item.path_target_stable_id = item.target_stable_id
        item.path_source_triangle = source.surface_point.triangle_index
        item.path_source_bary = tuple(source.surface_point.barycentric)
        item.path_target_triangle = target.surface_point.triangle_index
        item.path_target_bary = tuple(target.surface_point.barycentric)
        item.path_stale = False
        item.path_stale_reason = ""

    def make_stale():
        make_cached()
        state.mark_path_stale(item, "a test made it stale")

    props.measurement_index = 0
    for label, prepare in (
        ("CACHED", make_cached),
        ("STALE", make_stale),
        ("NOT COMPUTED", lambda: state.clear_measurement_path(item)),
    ):
        prepare()
        log = []
        # draw() is an ordinary function on the class; a bpy Panel cannot be
        # instantiated from Python, so it is called unbound with a stand-in.
        panel = type("Stub", (object,), {
            name: staticmethod(getattr(panel_class, name))
            for name in dir(panel_class) if name.startswith("_draw")
        })()
        panel.layout = FakeLayout(log)
        with NoSolve("panel draw (%s)" % label):
            panel_class.draw(panel, context)
        operators_drawn = [name for kind, name in log if kind == "operator"]
        check("panel draws in state %s" % label, bool(log), log)
        check("  and offers Compute Surface Path" ,
              "bsmt.compute_surface_path" in operators_drawn, operators_drawn)
        check("  and names the state in a label",
              any(label in text for kind, text in log if kind == "label"),
              [text for kind, text in log if kind == "label"][:6])

    # ------------------------------------------------- save / reload -------
    print("\nthe cache survives a save and reload")
    # A fresh scene: the sections above deliberately edited geometry and
    # re-picked a landmark, and this check is about persistence, not about
    # those. Re-solve so there is something real to lose, then round-trip the
    # file. This is the case that used to be fatal - the polyline lived in a
    # helper curve, so anything that lost the helper lost the whole solve.
    obj = build_scene()
    canonical = geodesic.meshcache.get(context, obj, props.unit)
    landmarks.clear()
    measurements.clear()
    pathcache.drop_all()
    source = add_landmark(landmarks, canonical, obj, 1, 40)
    target = add_landmark(landmarks, canonical, obj, 2,
                          canonical.triangle_count // 2)
    item = measurements.add()
    item.stable_id = 1
    item.name = "M1"
    item.protocol_id = "M01"
    item.source_stable_id = 1
    item.target_stable_id = 2
    props.measurement_index = 0
    distance = solve.surface_distance(
        canonical.vertices_solver, canonical.triangles,
        spec(source), spec(target), geometry_hash=canonical.geometry_hash)
    item.surface_valid = True
    item.surface_mm = distance.distance_mm
    item.result_object = obj.name
    item.result_geometry_hash = canonical.geometry_hash
    bpy.ops.bsmt.compute_surface_path()
    check("a path is cached again before saving", pathcache.exists(1))
    expected, _normals = pathcache.load(1)
    blend = os.path.join(bpy.app.tempdir or "/tmp", "bsmt_pathcache_test.blend")
    bpy.ops.wm.save_mainfile(filepath=blend)
    bpy.ops.wm.open_mainfile(filepath=blend)

    context = bpy.context
    props = context.scene.bsmt
    item = context.scene.bsmt_measurements[0]
    with NoSolve("redisplay after reload") as timer:
        check("the cached polyline came back with the file",
              pathcache.exists(item.stable_id))
        reloaded, _normals = pathcache.load(item.stable_id)
        check("and it is the same polyline",
              reloaded.shape == expected.shape
              and float(np.abs(reloaded - expected).max()) < 1e-5)
        check("the path still reports CACHED",
              viz.path_state(context, props, item)[0] == state.PATH_CACHED)
        redrawn = viz.draw_cached_path(context, props, item)
    check("and it redraws from the file's cache", redrawn)
    check("reload redisplay is immediate (%.2f ms)" % (timer.seconds * 1000),
          timer.seconds < 0.5)
    try:
        os.remove(blend)
    except OSError:
        pass

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
        # Blender's -b exit code is not ours to set directly; print a token
        # the caller can grep for instead.
        print("BSMT_PATH_VIZ_RESULT=%d" % code)
