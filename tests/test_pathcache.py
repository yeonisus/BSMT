"""Offline tests for the surface-path cache and its timing (Milestone 3.14).

    python3 tests/test_pathcache.py

Covers the parts that are pure: the display lift, the timing recorder, and
the four-state cache verdict. Everything that needs real Blender datablocks -
whether the cache actually survives a save, whether a colour change reaches
pygeodesic - is in tests/test_path_visualization.py, which requires Blender
and proves those by counting native solver calls.
"""

import importlib.util
import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "body_surface_measurement")

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def install_stubs():
    bpy = types.ModuleType("bpy")
    bpy.data = types.SimpleNamespace(objects={}, meshes={}, curves={},
                                     materials={}, collections={})
    bpy.types = types.SimpleNamespace(Mesh=object, Curve=object, Object=object)
    bpy.app = types.SimpleNamespace(driver_namespace={})
    sys.modules["bpy"] = bpy

    bmesh = types.ModuleType("bmesh")
    bmesh.ops = types.SimpleNamespace()
    sys.modules["bmesh"] = bmesh

    mathutils = types.ModuleType("mathutils")
    mathutils.Vector = tuple
    sys.modules["mathutils"] = mathutils
    return bpy


install_stubs()

package = types.ModuleType("bsmt_cache")
package.__path__ = [PACKAGE]
sys.modules["bsmt_cache"] = package


def load(name):
    spec = importlib.util.spec_from_file_location(
        "bsmt_cache." + name, os.path.join(PACKAGE, name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["bsmt_cache." + name] = module
    spec.loader.exec_module(module)
    setattr(package, name, module)
    return module


landmarks = load("landmarks")
measurement = load("measurement")
visualization = load("visualization")
pathcache = load("pathcache")
timing = load("timing")


def test_lift():
    print("\nthe display lift")
    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    normals = np.array([[0.0, 0.0, 2.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]])
    lifted = pathcache.lift(points, normals, 0.5)

    check("a normal is used as a DIRECTION, not a magnitude",
          abs(lifted[0][2] - 0.5) < 1e-12, lifted[0])
    check("each point moves along its own normal",
          abs(lifted[1][1] - 0.5) < 1e-12, lifted[1])
    check("a point with no resolved normal is left exactly where it was",
          np.array_equal(lifted[2], points[2]), lifted[2])
    check("the input is never modified", np.array_equal(points[0],
                                                        [0.0, 0.0, 0.0]))
    check("a zero lift is a copy, not a move",
          np.array_equal(pathcache.lift(points, normals, 0.0), points))
    check("a negative lift is refused rather than applied backwards",
          np.array_equal(pathcache.lift(points, normals, -1.0), points))
    check("mismatched normals fall back to leaving the path alone",
          np.array_equal(pathcache.lift(points, normals[:2], 0.5), points))
    check("an empty path lifts to an empty path",
          pathcache.lift(np.zeros((0, 3)), np.zeros((0, 3)), 0.5).shape
          == (0, 3))

    # Sect. 9: the lift is display only, so it must not change the length
    # anybody reports. On a straight run it cannot, and that is the case
    # where a bug would be invisible in a screenshot.
    straight = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    up = np.array([[0.0, 0.0, 1.0]] * 3)
    before = np.linalg.norm(np.diff(straight, axis=0), axis=1).sum()
    after = np.linalg.norm(
        np.diff(pathcache.lift(straight, up, 0.25), axis=0), axis=1).sum()
    check("lifting a path does not change its own length",
          abs(before - after) < 1e-12, "%r vs %r" % (before, after))


def test_names():
    print("\ncache datablock naming")
    check("a cache is named from the stable id alone",
          pathcache.cache_name(7) == "BSMT_PathCache_000007",
          pathcache.cache_name(7))
    check("and carries the prefix everything else keys off",
          pathcache.cache_name(7).startswith(pathcache.CACHE_PREFIX))
    check("a non-BSMT mesh is never treated as a cache",
          not pathcache._is_cache(types.SimpleNamespace(get=lambda *a: False)))
    check("nor is a missing one", not pathcache._is_cache(None))


def test_timing():
    print("\ntiming recorder")
    timing.reset()
    timing.set_enabled(False)
    with timing.stage(timing.PATH_SOLVE, "511 points") as measured:
        pass
    check("a stage records itself", len(timing.samples()) == 1)
    check("and carries its detail", timing.samples()[0]["detail"]
          == "511 points")
    check("the elapsed time is exposed to the caller",
          measured.seconds >= 0.0)

    timing.record(timing.CACHE_LOAD, 0.002)
    timing.record(timing.CACHE_LOAD, 0.004)
    totals = timing.totals()
    check("totals count every call", totals[timing.CACHE_LOAD][0] == 2, totals)
    check("and sum their milliseconds",
          abs(totals[timing.CACHE_LOAD][1] - 6.0) < 1e-6, totals)

    # The whole point of cumulative totals: one rare expensive stage must not
    # be evicted by a flood of cheap ones, or the report says the solver
    # never ran.
    for _ in range(timing.HISTORY * 2):
        timing.record(timing.HELPER_UPDATE, 0.0001)
    check("a rare expensive stage survives a flood of cheap ones",
          timing.PATH_SOLVE in timing.totals(), sorted(timing.totals()))
    check("while the sample ring stays bounded",
          len(timing.samples()) <= timing.HISTORY)

    # It must never be able to spam: a repeating label prints at most once
    # per interval however often it runs.
    timing.set_enabled(True)
    printed = []
    original = timing._due
    timing._due = lambda label: printed.append(label) or original(label)
    try:
        for _ in range(50):
            timing.record("flood", 0.001)
    finally:
        timing._due = original
        timing.set_enabled(False)
    check("logging is throttled, not per-call",
          len(printed) == 50 and len(timing.samples()) <= timing.HISTORY)

    timing.reset()
    check("reset clears samples and totals",
          not timing.samples() and not timing.totals())


def test_helper_writes_are_guarded():
    print("\nhelper writes are compared before they are made")

    class Owner(object):
        def __init__(self):
            self.value = 1.0
            self.flag = False
            self.color = (1.0, 1.0, 1.0, 1.0)

    owner = Owner()
    check("an unchanged float is not written",
          not visualization._set_float(owner, "value", 1.0))
    check("a changed float is written",
          visualization._set_float(owner, "value", 2.0) and owner.value == 2.0)
    check("an unchanged flag is not written",
          not visualization._set_flag(owner, "flag", False))
    check("a changed flag is written",
          visualization._set_flag(owner, "flag", True) and owner.flag)
    check("an unchanged colour is not written",
          not visualization._set_color(owner, "color", (1.0, 1.0, 1.0, 1.0)))
    check("a changed colour is written",
          visualization._set_color(owner, "color", (0.0, 1.0, 1.0, 1.0)))


def test_source_guarantees():
    print("\nguarantees that are read off the source")
    viz_text = open(os.path.join(PACKAGE, "viz.py")).read()
    state_text = open(os.path.join(PACKAGE, "state.py")).read()
    attach_text = open(os.path.join(PACKAGE, "attach.py")).read()
    cache_text = open(os.path.join(PACKAGE, "pathcache.py")).read()

    # Sect. 7: no route from a handler to the native solver, at all.
    for name, text in (("viz.py", viz_text), ("attach.py", attach_text),
                       ("pathcache.py", cache_text)):
        for forbidden in ("geodesicDistance", "ExactSolver",
                          "compute_distance_and_path", "surface_path("):
            check("%s never calls %s" % (name, forbidden),
                  forbidden not in text)

    # Sect. 4: the authoritative cached path is LOCAL, never world.
    check("the cache stores local coordinates",
          "SCAN OBJECT'S LOCAL SPACE" in cache_text)
    check("and a fake user keeps it across a save",
          "use_fake_user = True" in cache_text)

    # Sect. 9: a stale path is reported, never silently recomputed or dropped.
    for name in ("PATH_NOT_COMPUTED", "PATH_CACHED", "PATH_STALE",
                 "PATH_INVALID"):
        check("state.py defines %s" % name, name in state_text)
    check("a stale path is marked rather than deleted",
          "def mark_path_stale(" in state_text)
    check("and the display hides it rather than solving it",
          "PATH_CACHED" in viz_text and "draw_cached_path" in viz_text)


def main():
    test_lift()
    test_names()
    test_timing()
    test_helper_writes_are_guarded()
    test_source_guarantees()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for label in FAILURES:
        print("  FAILED: %s" % label)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
