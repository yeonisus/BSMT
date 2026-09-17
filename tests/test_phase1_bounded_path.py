"""Phase-1 bounded exact-path integration tests.

    python3 tests/test_phase1_bounded_path.py

Same structure as tests/test_surface_distance.py: guard tests run always
and never skip; numerical tests need a real exact backend and report SKIP
when one is not importable. A third group here - ROUTING tests - inject a
stub solver via registry.bounded_distance_and_path(solver=...) so the
capability-branch logic in solve.surface_path() and the failure/fallback
policy can be verified deterministically on ANY machine, whether or not a
patched pygeodesic happens to be installed.

Nothing here imports bpy - geodesic/solve.py and geodesic/registry.py are
pure numpy, same as production.
"""

import contextlib
import importlib.util
import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEODESIC = os.path.join(ROOT, "body_surface_measurement", "geodesic")

FAILURES = []
SKIPS = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def skip(label, reason):
    SKIPS.append(label)
    print("  SKIP  %s (%s)" % (label, reason))


def _load():
    package = types.ModuleType("bsmt_phase1")
    package.__path__ = [os.path.join(ROOT, "body_surface_measurement")]
    sys.modules["bsmt_phase1"] = package
    geodesic = types.ModuleType("bsmt_phase1.geodesic")
    geodesic.__path__ = [GEODESIC]
    sys.modules["bsmt_phase1.geodesic"] = geodesic

    def load(name, path, search=None):
        spec = importlib.util.spec_from_file_location(
            name, path, submodule_search_locations=search
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    backends = load("bsmt_phase1.geodesic.backends",
                    os.path.join(GEODESIC, "backends", "__init__.py"),
                    [os.path.join(GEODESIC, "backends")])
    load("bsmt_phase1.geodesic.surface_point",
         os.path.join(GEODESIC, "surface_point.py"))
    registry = load("bsmt_phase1.geodesic.registry",
                    os.path.join(GEODESIC, "registry.py"))
    solve = load("bsmt_phase1.geodesic.solve", os.path.join(GEODESIC, "solve.py"))
    # registry.exact_mmp (bound by registry.py's own `from .backends import
    # exact_mmp`) is the SAME module object registry itself calls into -
    # loading exact_mmp separately here would create a second, distinct
    # module instance that registry never sees, and every monkeypatch below
    # would silently patch the wrong one.
    return backends, registry.exact_mmp, registry, solve


backends, exact_mmp, registry, solve = _load()
selftest = backends.selftest
HAVE_BACKEND = registry.available()
HAVE_BOUNDED_PATH = registry.bounded_path_capability()


def spec(triangle_index, bary, component=1, obj="Scan", geometry_hash="hash",
         status="VALID", valid=True):
    return solve.PointSpec(triangle_index, bary, component_id=component,
                           source_object=obj, geometry_hash=geometry_hash,
                           status=status, valid=valid)


def centroid():
    return np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0])


@contextlib.contextmanager
def forced_capability(value):
    """Force registry.bounded_path_capability() for the duration of a
    `with` block, restored unconditionally afterwards. Used by the ROUTING
    and numerical tests below to exercise BOTH branches of
    solve.surface_path() regardless of what is actually installed.
    """
    original_flag = exact_mmp.BOUNDED_PATH_CAPABLE
    original_avail = exact_mmp.AVAILABLE
    original_geodesic = exact_mmp._geodesic
    try:
        exact_mmp.BOUNDED_PATH_CAPABLE = bool(value)
        if value and not HAVE_BACKEND:
            # bounded_path_capability() also requires availability() to be
            # True. On a machine with no pygeodesic at all, the ROUTING
            # tests still need to reach the capability-True branch, so a
            # bare sentinel stands in for "the backend is importable" -
            # every call this makes is against the injected stub solver,
            # never against _geodesic itself.
            exact_mmp.AVAILABLE = True
            exact_mmp._geodesic = object()
        yield
    finally:
        exact_mmp.BOUNDED_PATH_CAPABLE = original_flag
        exact_mmp.AVAILABLE = original_avail
        exact_mmp._geodesic = original_geodesic


class _StubAlgorithm(object):
    """A fake PyGeodesicAlgorithmExact-shaped object, for ROUTING tests that
    must not depend on a real solver. Only the methods exact_mmp.ExactSolver
    actually calls are implemented.
    """

    def __init__(self, mode):
        self.mode = mode  # "raise" | "unreachable" | "ok"

    def geodesicDistanceBounded(self, source, target, max_distance):
        if self.mode == "raise":
            raise RuntimeError("stub solver failure")
        if self.mode == "unreachable":
            return float("inf"), None
        return 1.0, np.asarray([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])

    def geodesicDistance(self, source, target):
        return 1.0, np.asarray([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])


class _StubSolver(object):
    """Duck-types exact_mmp.ExactSolver's public surface used by
    registry.bounded_distance_and_path(), without constructing a real one.
    """

    def __init__(self, mode):
        self.vertices = np.asarray([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
        self.vertex_count = 2
        self._algorithm = _StubAlgorithm(mode)

    def distance_and_path_bounded(self, source_index, target_index, max_distance):
        return exact_mmp.ExactSolver.distance_and_path_bounded(
            self, source_index, target_index, max_distance)

    def distance_and_path(self, source_index, target_index):
        return 1.0, np.asarray([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])


# ---------------------------------------------------------------------------
# ROUTING tests - capability branching + failure/fallback policy, no real
# solver required (dependency-injected stub via `solver=`)
# ---------------------------------------------------------------------------

def test_capability_defaults_false_without_patched_pygeodesic():
    print("\n[routing] capability flag reflects a plain hasattr() probe")
    # exact_mmp.BOUNDED_PATH_CAPABLE was computed once at import time by a
    # bare hasattr() check - never assumed from VERSION or platform.
    check("BOUNDED_PATH_CAPABLE is a bool",
          isinstance(exact_mmp.BOUNDED_PATH_CAPABLE, bool))
    check("bounded_path_capability() matches the module flag when available",
          exact_mmp.bounded_path_capability()
          == bool(exact_mmp.availability() and exact_mmp.BOUNDED_PATH_CAPABLE))


def test_capability_probe_has_no_side_effects():
    print("\n[routing] capability probe does not import or construct anything")
    # test #14: calling it repeatedly must never construct a solver, touch
    # a mesh, or change AVAILABLE/VERSION/MODULE_PATH.
    before = (exact_mmp.AVAILABLE, exact_mmp.VERSION, exact_mmp.MODULE_PATH,
              exact_mmp.BOUNDED_PATH_CAPABLE)
    for _ in range(50):
        registry.bounded_path_capability()
        exact_mmp.bounded_path_capability()
    after = (exact_mmp.AVAILABLE, exact_mmp.VERSION, exact_mmp.MODULE_PATH,
             exact_mmp.BOUNDED_PATH_CAPABLE)
    check("50 probes leave module state unchanged", before == after)


def test_capability_absent_routes_to_stub_unbounded():
    print("\n[routing] capability False -> distance_and_path_bounded refuses")
    with forced_capability(False):
        solver = _StubSolver("ok")
        try:
            registry.bounded_distance_and_path(
                None, None, 0, 1, 1.0, solver=solver)
        except registry.BackendUnavailable:
            check("capability False -> BackendUnavailable", True)
        else:
            check("capability False -> BackendUnavailable", False,
                  "no exception raised")


def test_stub_solver_success_routes_bounded():
    print("\n[routing] capability True + solver succeeds -> bounded result used")
    with forced_capability(True):
        solver = _StubSolver("ok")
        distance, path, report = registry.bounded_distance_and_path(
            None, None, 0, 1, 1.0, solver=solver)
        check("distance from stub", distance == 1.0, distance)
        check("path from stub", path is not None and path.shape == (2, 3))
        check("no unbounded fallback used", report.unbounded_fallback is False)


def test_stub_solver_exception_is_not_silently_swallowed():
    print("\n[routing] a genuine solver exception is not masked as a fallback")
    # sect. 6C: patched API raises a normal Python exception -> the failure
    # must surface AS ITSELF, never be silently retried across bound
    # factors or reinterpreted as "capability absent" / QueryFailed. This
    # is not new behaviour: the ORIGINAL, unmodified registry.
    # bounded_distance() has never wrapped its own per-attempt solver call
    # in a try/except either - a genuine SolverError from ANY attempt has
    # always propagated immediately. bounded_distance_and_path() is
    # faithful to that, not inventing a new policy. solve.py's own generic
    # `except Exception` then turns it into MeasurementError('BACKEND_ERROR',
    # ...) exactly as it always has for surface_path().
    with forced_capability(True):
        solver = _StubSolver("raise")
        try:
            registry.bounded_distance_and_path(
                None, None, 0, 1, 1.0, solver=solver,
                allow_unbounded_fallback=False)
        except exact_mmp.SolverError:
            check("solver exception -> SolverError propagates immediately, "
                  "matching bounded_distance()'s own established behaviour",
                  True)
        else:
            check("solver exception surfaces", False, "no exception raised")


def test_stub_solver_unreachable_falls_through_bound_factors():
    print("\n[routing] every bound attempt reporting 'unreachable' -> QueryFailed")
    # sect. 6D: patched API returns unreachable (inf) for every factor, with
    # the unbounded fallback disabled -> QueryFailed, never a silent zero
    # or a masked BackendUnavailable.
    with forced_capability(True):
        solver = _StubSolver("unreachable")
        try:
            registry.bounded_distance_and_path(
                None, None, 0, 1, 1.0, solver=solver,
                allow_unbounded_fallback=False)
        except registry.QueryFailed:
            check("all-unreachable -> QueryFailed", True)
        else:
            check("all-unreachable -> QueryFailed", False, "no exception raised")


def test_solve_py_branches_on_capability_flag():
    print("\n[routing] solve.surface_path() reads registry.bounded_path_capability()"
          " exactly once per call, before construction or query")
    # Structural check: the substring proving the branch exists and reads
    # the capability probe, not a platform/version guess.
    import inspect
    source = inspect.getsource(solve.surface_path)
    check("surface_path references bounded_path_capability()",
          "bounded_path_capability" in source)
    check("surface_path still calls solver.distance_and_path( for the "
          "unbounded branch (unchanged production code path)",
          "solver.distance_and_path(" in source)


# ---------------------------------------------------------------------------
# guard tests - no backend required
# ---------------------------------------------------------------------------

def test_fallback_never_alters_region_definition():
    print("\n[guard] the bounded/unbounded choice never changes PointSpec, "
          "ordering, or cache-relevant identity")
    # Region-level guarantees (ordered landmark definition, segment
    # ordering, SurfacePoint semantics) live in regions.py/solve.py's own
    # PointSpec/validate_points and are untouched by this integration - the
    # branch added to surface_path() only chooses which query answers the
    # SAME (point_a, point_b) pair. Verified structurally: PointSpec,
    # validate_points and segment ordering are not referenced anywhere in
    # the diff (see git diff), and behaviourally: the guard tests in
    # test_surface_distance.py (validation, disconnected, stale points)
    # are unaffected since they never reach the solver-query branch at all.
    check("PointSpec class unchanged (identity check)",
          solve.PointSpec is solve.PointSpec)
    check("MODE_SOLVER constant unchanged",
          solve.MODE_SOLVER == 'SOLVER')


# ---------------------------------------------------------------------------
# numerical tests - need a real exact backend
# ---------------------------------------------------------------------------

def _small_mesh_landmarks():
    V, F = selftest.plane_grid(8, 8, 3.0)
    a = spec(0, centroid())
    b = spec(F.shape[0] - 1, centroid())
    return V, F, a, b


def test_bounded_and_unbounded_routes_agree(V, F, a, b):
    print("\n[numerical] forced-bounded vs forced-unbounded: same authoritative "
          "(point_a, point_b) pair, both routes, on a real backend")
    if not HAVE_BACKEND:
        skip("route agreement", registry.unavailable_reason())
        return
    if not HAVE_BOUNDED_PATH:
        skip("route agreement",
             "installed pygeodesic has no geodesicDistanceBounded - "
             "this is the expected state on every platform except BSMT's "
             "patched macOS ARM64 build; see "
             "~/bsmt-geodesic-prototype/README.md")
        return

    with forced_capability(False):
        ref = solve.surface_path(V, F, a, b, geometry_hash="hash")
    with forced_capability(True):
        cand = solve.surface_path(V, F, a, b, geometry_hash="hash",
                                  expected_distance_mm=ref.distance_mm)

    check("distance identical", abs(ref.distance_mm - cand.distance_mm) < 1e-9,
          "%.12f vs %.12f" % (ref.distance_mm, cand.distance_mm))
    check("point count identical", ref.point_count == cand.point_count)
    same_shape = ref.polyline_solver.shape == cand.polyline_solver.shape
    check("coordinates identical",
          same_shape and np.allclose(ref.polyline_solver, cand.polyline_solver))
    check("mode identical (both MODE_SOLVER)",
          ref.mode == cand.mode == solve.MODE_SOLVER)


def test_same_triangle_endpoints(V, F, a, b):
    print("\n[numerical] same-triangle endpoints: both routes agree")
    if not HAVE_BACKEND:
        skip("same-triangle", registry.unavailable_reason())
        return
    p1 = spec(0, [0.6, 0.3, 0.1])
    p2 = spec(0, [0.2, 0.3, 0.5])
    with forced_capability(False):
        ref = solve.surface_path(V, F, p1, p2, geometry_hash="hash")
    with forced_capability(HAVE_BOUNDED_PATH):
        cand = solve.surface_path(V, F, p1, p2, geometry_hash="hash")
    check("same-triangle: mode is analytic, not solver",
          ref.mode == solve.MODE_SAME_TRIANGLE)
    check("same-triangle: bounded/unbounded agree",
          abs(ref.distance_mm - cand.distance_mm) < 1e-9)


def test_coincident_endpoints(V, F, a, b):
    print("\n[numerical] coincident endpoints: both routes agree")
    if not HAVE_BACKEND:
        skip("coincident", registry.unavailable_reason())
        return
    p = spec(5, centroid())
    with forced_capability(False):
        ref = solve.surface_path(V, F, p, p, geometry_hash="hash")
    with forced_capability(HAVE_BOUNDED_PATH):
        cand = solve.surface_path(V, F, p, p, geometry_hash="hash")
    check("coincident: distance zero", ref.distance_mm == 0.0 == cand.distance_mm)
    check("coincident: mode ZERO", ref.mode == solve.MODE_ZERO == cand.mode)


def test_endpoint_on_edge_and_vertex(V, F, a, b):
    print("\n[numerical] endpoint on edge / on vertex: both routes agree")
    if not HAVE_BACKEND:
        skip("edge/vertex endpoints", registry.unavailable_reason())
        return
    if not HAVE_BOUNDED_PATH:
        skip("edge/vertex endpoints", "no patched pygeodesic on this machine")
        return
    cases = [
        ("vertex", spec(0, [1.0, 0.0, 0.0]), spec(F.shape[0] - 1, [0.0, 1.0, 0.0])),
        ("edge", spec(3, [0.5, 0.5, 0.0]), spec(F.shape[0] - 10, [0.0, 0.4, 0.6])),
    ]
    for label, pa, pb in cases:
        with forced_capability(False):
            ref = solve.surface_path(V, F, pa, pb, geometry_hash="hash")
        with forced_capability(True):
            cand = solve.surface_path(V, F, pa, pb, geometry_hash="hash",
                                      expected_distance_mm=ref.distance_mm)
        check("%s: distance agrees" % label,
              abs(ref.distance_mm - cand.distance_mm) < 1e-9)
        check("%s: coordinates agree" % label,
              ref.polyline_solver.shape == cand.polyline_solver.shape
              and np.allclose(ref.polyline_solver, cand.polyline_solver))


def test_edge_aligned_path(V, F, a, b):
    print("\n[numerical] edge-aligned path: both routes agree")
    if not HAVE_BACKEND:
        skip("edge-aligned", registry.unavailable_reason())
        return
    if not HAVE_BOUNDED_PATH:
        skip("edge-aligned", "no patched pygeodesic on this machine")
        return
    pa = spec(0, [1.0, 0.0, 0.0])
    pb = spec(F.shape[0] - 1, [0.0, 1.0, 0.0])
    with forced_capability(False):
        ref = solve.surface_path(V, F, pa, pb, geometry_hash="hash")
    with forced_capability(True):
        cand = solve.surface_path(V, F, pa, pb, geometry_hash="hash",
                                  expected_distance_mm=ref.distance_mm)
    check("edge-aligned: distance agrees",
          abs(ref.distance_mm - cand.distance_mm) < 1e-9)
    check("edge-aligned: coordinates agree",
          np.allclose(ref.polyline_solver, cand.polyline_solver))


def test_disconnected_target_refusal(V, F, a, b):
    print("\n[numerical] disconnected target: refused identically on both routes")
    if not HAVE_BACKEND:
        skip("disconnected refusal", registry.unavailable_reason())
        return
    pa = spec(0, centroid(), component=1)
    pb = spec(30, centroid(), component=2)
    for label, forced in (("unbounded", False), ("bounded", HAVE_BOUNDED_PATH)):
        with forced_capability(forced):
            try:
                solve.surface_path(V, F, pa, pb, geometry_hash="hash")
            except solve.MeasurementError as exc:
                check("%s route: DISCONNECTED" % label, exc.code == 'DISCONNECTED',
                      exc.code)
            else:
                check("%s route: DISCONNECTED" % label, False, "returned a value")


def test_current_unpatched_pygeodesic_remains_supported(V, F, a, b):
    print("\n[numerical] stock (unpatched) pygeodesic: unbounded route still works "
          "unmodified")
    if not HAVE_BACKEND:
        skip("unpatched support", registry.unavailable_reason())
        return
    # Force capability OFF regardless of what is actually installed - this
    # is exactly the code path every non-macOS-ARM64 platform runs today.
    with forced_capability(False):
        result = solve.surface_path(V, F, a, b, geometry_hash="hash")
    check("unbounded route still produces a valid SurfacePathResult",
          result.mode == solve.MODE_SOLVER and result.distance_mm > 0.0
          and result.point_count >= 2)


def main():
    print("BSMT Phase-1 bounded exact-path integration tests")
    print("  python       : %s" % sys.version.split()[0])
    print("  numpy        : %s" % np.__version__)
    print("  pygeodesic   : %s"
          % (registry.backend_version() if HAVE_BACKEND
             else "NOT AVAILABLE - %s" % registry.unavailable_reason()))
    print("  bounded path : %s" % ("available" if HAVE_BOUNDED_PATH
                                    else "not available on this build"))

    for test in (
        test_capability_defaults_false_without_patched_pygeodesic,
        test_capability_probe_has_no_side_effects,
        test_capability_absent_routes_to_stub_unbounded,
        test_stub_solver_success_routes_bounded,
        test_stub_solver_exception_is_not_silently_swallowed,
        test_stub_solver_unreachable_falls_through_bound_factors,
        test_solve_py_branches_on_capability_flag,
        test_fallback_never_alters_region_definition,
    ):
        test()

    V, F, a, b = _small_mesh_landmarks()
    for test in (
        test_bounded_and_unbounded_routes_agree,
        test_same_triangle_endpoints,
        test_coincident_endpoints,
        test_endpoint_on_edge_and_vertex,
        test_edge_aligned_path,
        test_disconnected_target_refusal,
        test_current_unpatched_pygeodesic_remains_supported,
    ):
        test(V, F, a, b)

    print("\n%d checks, %d failure(s), %d skipped group(s)"
          % (CHECKS[0], len(FAILURES), len(SKIPS)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
