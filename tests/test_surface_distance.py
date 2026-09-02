"""Offline tests for the Milestone 2.3 production surface distance pipeline.

    python3 tests/test_surface_distance.py

Two halves, as in tests/test_backend_exact.py:

* **guard tests** run with or without pygeodesic and are never skipped. They
  cover validation, failure codes, invariants and state semantics - the parts
  that must behave correctly on a machine with no exact backend at all.
* **numerical tests** need pygeodesic and report SKIP when it is absent.

Nothing here imports bpy. `geodesic/solve.py` and `geodesic/registry.py` are
pure numpy precisely so the whole measurement pipeline is testable here rather
than only inside Blender.
"""

import importlib.util
import math
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
    package = types.ModuleType("bsmt_m23")
    package.__path__ = [os.path.join(ROOT, "body_surface_measurement")]
    sys.modules["bsmt_m23"] = package
    geodesic = types.ModuleType("bsmt_m23.geodesic")
    geodesic.__path__ = [GEODESIC]
    sys.modules["bsmt_m23.geodesic"] = geodesic

    def load(name, path, search=None):
        spec = importlib.util.spec_from_file_location(
            name, path, submodule_search_locations=search
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    backends = load("bsmt_m23.geodesic.backends",
                    os.path.join(GEODESIC, "backends", "__init__.py"),
                    [os.path.join(GEODESIC, "backends")])
    load("bsmt_m23.geodesic.surface_point",
         os.path.join(GEODESIC, "surface_point.py"))
    registry = load("bsmt_m23.geodesic.registry",
                    os.path.join(GEODESIC, "registry.py"))
    solve = load("bsmt_m23.geodesic.solve", os.path.join(GEODESIC, "solve.py"))
    return backends, registry, solve


backends, registry, solve = _load()
selftest = backends.selftest
HAVE_BACKEND = registry.available()


def spec(triangle_index, bary, component=1, obj="Scan", geometry_hash="hash",
         status="VALID", valid=True):
    return solve.PointSpec(triangle_index, bary, component_id=component,
                           source_object=obj, geometry_hash=geometry_hash,
                           status=status, valid=valid)


def centroid():
    return np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0])


def expect_failure(label, code, call):
    try:
        call()
    except solve.MeasurementError as exc:
        check("%s -> %s" % (label, exc.code), exc.code == code,
              "expected %s, got %s: %s" % (code, exc.code, exc.message))
        check("%s: no number returned" % label, True)
    except Exception as exc:  # noqa: BLE001
        check("%s -> %s" % (label, code), False,
              "raised %s instead: %s" % (type(exc).__name__, exc))
    else:
        check("%s -> %s" % (label, code), False, "returned a value")


# ---------------------------------------------------------------------------
# guard tests
# ---------------------------------------------------------------------------

def test_validation_refuses_before_compute():
    print("\n[guard] every pre-compute refusal of sect. 5")
    V, F = selftest.plane_grid(6, 6, 5.0)
    good_a = spec(0, centroid())
    good_b = spec(30, centroid())

    expect_failure("Point A missing", 'POINTS_MISSING',
                   lambda: solve.surface_distance(V, F, None, good_b))
    expect_failure("Point B missing", 'POINTS_MISSING',
                   lambda: solve.surface_distance(V, F, good_a, None))
    expect_failure("Point A invalid flag", 'POINTS_MISSING',
                   lambda: solve.surface_distance(
                       V, F, spec(0, centroid(), valid=False), good_b))
    expect_failure("Point A status not VALID", 'STALE_POINTS',
                   lambda: solve.surface_distance(
                       V, F, spec(0, centroid(), status="STALE: geometry changed"),
                       good_b))
    expect_failure("different source objects", 'DIFFERENT_OBJECTS',
                   lambda: solve.surface_distance(
                       V, F, good_a, spec(30, centroid(), obj="Other")))
    expect_failure("geometry hash differs from canonical", 'STALE_POINTS',
                   lambda: solve.surface_distance(
                       V, F, good_a, good_b, geometry_hash="different"))
    expect_failure("point hash differs from each other", 'STALE_POINTS',
                   lambda: solve.surface_distance(
                       V, F, good_a, spec(30, centroid(), geometry_hash="other"),
                       geometry_hash="hash"))
    expect_failure("triangle index out of range", 'STALE_POINTS',
                   lambda: solve.surface_distance(
                       V, F, spec(99999, centroid()), good_b))
    expect_failure("negative triangle index", 'STALE_POINTS',
                   lambda: solve.surface_distance(
                       V, F, spec(-1, centroid()), good_b))
    expect_failure("barycentrics not on the triangle", 'INVALID_TOPOLOGY',
                   lambda: solve.surface_distance(
                       V, F, spec(0, np.array([2.0, -1.5, 0.5])), good_b))
    expect_failure("different connected components", 'DISCONNECTED',
                   lambda: solve.surface_distance(
                       V, F, good_a, spec(30, centroid(), component=2)))


def test_disconnected_message_is_the_specified_one():
    print("\n[guard] the DISCONNECTED message is the one sect. 5 specifies")
    text = solve.failure_message('DISCONNECTED')
    check("names disconnected surface components",
          "disconnected surface components" in text, text)
    check("does not offer a number", "mm" not in text, text)
    detailed = solve.failure_message('DISCONNECTED', "A in component #1, B in component #2")
    check("carries component detail", "#1" in detailed and "#2" in detailed, detailed)
    for code in ('POINTS_MISSING', 'STALE_POINTS', 'DIFFERENT_OBJECTS',
                 'BACKEND_MISSING', 'BACKEND_ERROR', 'INVALID_TOPOLOGY',
                 'INSERTION_FAILED', 'INVARIANT_VIOLATION'):
        message = solve.failure_message(code)
        check("%s has a message" % code,
              message.startswith("Surface distance unavailable"), message)


def test_backend_unavailable_is_reported_not_substituted():
    print("\n[guard] a missing backend refuses; it never returns the straight distance")
    V, F = selftest.plane_grid(6, 6, 5.0)
    saved = (registry.exact_mmp.AVAILABLE, registry.exact_mmp._geodesic,
             registry.exact_mmp.IMPORT_ERROR)
    try:
        registry.exact_mmp.AVAILABLE = False
        registry.exact_mmp._geodesic = None
        registry.exact_mmp.IMPORT_ERROR = "ImportError: simulated absence"
        check("registry.available() is False", registry.available() is False)
        check("unavailable_reason carries the original error",
              "simulated absence" in registry.unavailable_reason())
        expect_failure("solve with no backend", 'BACKEND_MISSING',
                       lambda: solve.surface_distance(
                           V, F, spec(0, centroid()), spec(30, centroid())))
    finally:
        (registry.exact_mmp.AVAILABLE, registry.exact_mmp._geodesic,
         registry.exact_mmp.IMPORT_ERROR) = saved
    check("backend restored", registry.available() == HAVE_BACKEND)


def test_analytic_short_circuits_need_no_backend():
    print("\n[guard] same-triangle and A=A are analytic, so they work with no backend")
    V, F = selftest.plane_grid(6, 6, 5.0)
    saved = (registry.exact_mmp.AVAILABLE, registry.exact_mmp._geodesic)
    try:
        registry.exact_mmp.AVAILABLE = False
        registry.exact_mmp._geodesic = None
        # Deliberately still refused: sect. 5 requires the backend check to
        # happen before any measurement is produced, so BSMT never reports a
        # number in a configuration where most measurements would fail.
        expect_failure("same triangle with no backend", 'BACKEND_MISSING',
                       lambda: solve.surface_distance(
                           V, F, spec(0, np.array([0.5, 0.25, 0.25])),
                           spec(0, np.array([0.25, 0.5, 0.25]))))
    finally:
        (registry.exact_mmp.AVAILABLE, registry.exact_mmp._geodesic) = saved


def test_invariant_checker():
    print("\n[guard] the surface >= straight invariant")
    solve._check_invariants(10.0, 10.0)
    solve._check_invariants(12.0, 10.0)
    solve._check_invariants(10.0 - 1e-12, 10.0)          # inside tolerance
    check("equal values accepted", True)

    def rejects(label, distance, straight):
        try:
            solve._check_invariants(distance, straight)
        except solve.MeasurementError as exc:
            check(label, exc.code == 'INVARIANT_VIOLATION', exc.code)
        else:
            check(label, False, "accepted %r vs %r" % (distance, straight))

    rejects("rejects surface shorter than straight", 9.5, 10.0)
    rejects("rejects NaN", float("nan"), 10.0)
    rejects("rejects inf", float("inf"), 10.0)
    rejects("rejects negative", -1.0, 10.0)


def test_bound_strategy_shape():
    print("\n[guard] the expanding-bound sequence is the specified one")
    check("factors are 1.25, 2, 4, 8",
          registry.BOUND_FACTORS == (1.25, 2.0, 4.0, 8.0),
          str(registry.BOUND_FACTORS))
    check("factors strictly increase",
          all(b > a for a, b in zip(registry.BOUND_FACTORS,
                                    registry.BOUND_FACTORS[1:])))
    check("a positive minimum bound exists", registry.MIN_BOUND_MM > 0.0)
    check("algorithm version is recorded",
          registry.ALGORITHM_VERSION == "bsmt-geodesic/1")


def test_bounded_query_refuses_infinite_bound():
    print("\n[guard] an infinite max_distance is refused, not silently accepted")
    if not HAVE_BACKEND:
        skip("infinite bound refusal", registry.unavailable_reason())
        return
    V, F = selftest.plane_grid(6, 6, 5.0)
    solver = registry.exact_mmp.ExactSolver(V, F)
    for bad, label in ((float("inf"), "inf"), (float("nan"), "nan"),
                       (0.0, "zero"), (-5.0, "negative")):
        try:
            solver.bounded_distances([10], 0, bad)
        except registry.exact_mmp.InvalidMeshError as exc:
            check("bounded_distances refuses %s" % label, True)
            if label == "inf":
                check("the refusal explains why", "full mesh sweep" in str(exc),
                      str(exc))
        except Exception as exc:  # noqa: BLE001
            check("bounded_distances refuses %s" % label, False,
                  "raised %s" % type(exc).__name__)
        else:
            check("bounded_distances refuses %s" % label, False, "accepted it")


def test_metric_tensor_semantics():
    print("\n[guard] metric tensor: rotation invariant, scale and unit sensitive")
    # Reimplemented here exactly as state.metric_tensor does it, because
    # state.py cannot be imported without bpy. The rule under test is sect.
    # 6.4, not the particular file it lives in.
    def tensor(linear, multiplier):
        squared = multiplier * multiplier
        return tuple(
            sum(linear[k][i] * linear[k][j] for k in range(3)) * squared
            for i in range(3) for j in range(3)
        )

    def match(a, b, relative=1e-9):
        scale = max(abs(v) for v in a + b) or 0.0
        if scale == 0.0:
            return all(v == 0.0 for v in a + b)
        return all(abs(x - y) <= relative * scale for x, y in zip(a, b))

    identity = [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]
    angle = 0.7
    cos, sin = math.cos(angle), math.sin(angle)
    rotation = [[cos, -sin, 0.0], [sin, cos, 0.0], [0.0, 0.0, 1.0]]
    uniform = [[2.0, 0, 0], [0, 2.0, 0], [0, 0, 2.0]]
    nonuniform = [[2.0, 0, 0], [0, 3.0, 0], [0, 0, 0.5]]

    base = tensor(identity, 1.0)
    check("rotation does NOT change the metric",
          match(base, tensor(rotation, 1.0)))
    check("uniform scale DOES change the metric",
          not match(base, tensor(uniform, 1.0)))
    check("non-uniform scale DOES change the metric",
          not match(base, tensor(nonuniform, 1.0)))
    check("unit change DOES change the metric",
          not match(base, tensor(identity, 10.0)))
    check("scale compensated by a unit change is the SAME metric",
          match(tensor(uniform, 1.0),
                tensor([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]], 2.0)))
    # A rotated non-uniform scale is still the same metric as itself.
    check("rotation of a scaled object still does not change the metric",
          match(tensor(nonuniform, 1.0),
                tensor([[sum(rotation[i][k] * nonuniform[k][j] for k in range(3))
                         for j in range(3)] for i in range(3)], 1.0)))


def test_metric_tolerance_survives_float32_transforms():
    print("\n[guard] the metric tolerance is set by float32 matrix_world")
    # Blender stores Object.matrix_world in SINGLE precision. A pure rotation
    # therefore yields L^T L that differs from the identity by ~3.6e-8
    # relative (measured on Blender 4.5.13), not the ~1e-16 a float64 matrix
    # would give. A tolerance tighter than that reports every rotation as a
    # metric change and wrongly invalidates the surface distance - which is
    # exactly what 1e-9 did before this was measured. Simulated here by
    # rounding the rotation through float32.
    TOLERANCE = 1e-6

    def tensor(linear, multiplier):
        squared = multiplier * multiplier
        return tuple(
            sum(linear[k][i] * linear[k][j] for k in range(3)) * squared
            for i in range(3) for j in range(3)
        )

    def match(a, b, relative=TOLERANCE):
        scale = max(abs(v) for v in a + b) or 0.0
        if scale == 0.0:
            return all(v == 0.0 for v in a + b)
        return all(abs(x - y) <= relative * scale for x, y in zip(a, b))

    identity = tensor([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]], 1.0)

    worst = 0.0
    for angles in ((0.3, -0.7, 1.1), (1.0, 2.0, 3.0), (0.01, 0.02, 0.03),
                   (-2.5, 0.9, -1.3)):
        rotation = np.eye(3)
        for axis, angle in enumerate(angles):
            cos, sin = math.cos(angle), math.sin(angle)
            step = np.eye(3)
            i, j = [(1, 2), (0, 2), (0, 1)][axis]
            step[i, i] = cos
            step[j, j] = cos
            step[i, j] = -sin
            step[j, i] = sin
            rotation = rotation @ step
        # Round-trip through float32 exactly as Blender's matrix_world does.
        single = rotation.astype(np.float32).astype(np.float64)
        rotated = tensor(single.tolist(), 1.0)
        deviation = max(abs(a - b) for a, b in zip(identity, rotated))
        worst = max(worst, deviation)
        check("float32 rotation %s is NOT a metric change"
              % (tuple(round(a, 2) for a in angles),),
              match(identity, rotated), "deviation %.3e" % deviation)
    print("        worst float32 rotation deviation: %.3e "
          "(tolerance %.0e)" % (worst, TOLERANCE))
    check("the tolerance is comfortably above the float32 noise floor",
          worst < TOLERANCE / 10.0, "worst %.3e" % worst)

    # And it must still be far below the smallest scale change worth catching.
    smallest = tensor([[1.0001, 0, 0], [0, 1.0001, 0], [0, 0, 1.0001]], 1.0)
    check("a 1.0001x uniform scale is still caught",
          not match(identity, smallest))
    check("a 2x uniform scale is caught",
          not match(identity, tensor([[2.0, 0, 0], [0, 2.0, 0], [0, 0, 2.0]], 1.0)))


# ---------------------------------------------------------------------------
# numerical tests
# ---------------------------------------------------------------------------

def test_plane_surface_equals_straight():
    print("\n[numeric] plane: surface distance == straight distance exactly")
    if not HAVE_BACKEND:
        skip("plane measurement", registry.unavailable_reason())
        return
    for diagonal in ("forward", "backward", "alternating"):
        V, F = selftest.plane_grid(10, 10, 6.0, diagonal=diagonal)
        # Two interior face points in different triangles.
        a = spec(4, np.array([0.5, 0.3, 0.2]))
        b = spec(150, np.array([0.2, 0.3, 0.5]))
        result = solve.surface_distance(V, F, a, b)
        relative = abs(result.distance_mm - result.straight_mm) / result.straight_mm
        check("plane %-11s surface == straight (rel %.3e)" % (diagonal, relative),
              relative < 1e-9,
              "surface %.9f straight %.9f" % (result.distance_mm, result.straight_mm))
        check("plane %-11s ratio is 1" % diagonal, abs(result.ratio - 1.0) < 1e-9)
        check("plane %-11s used the solver" % diagonal,
              result.mode == solve.MODE_SOLVER, result.mode)
        check("plane %-11s bound factor recorded" % diagonal,
              result.report.bound_factor in registry.BOUND_FACTORS
              or result.report.unbounded_fallback,
              str(result.report.bound_factor))
        check("plane %-11s one attempt sufficed" % diagonal,
              result.report.attempts == 1, str(result.report.attempts))


def test_same_triangle_edge_and_vertex():
    print("\n[numeric] same triangle, same edge and same vertex cases")
    if not HAVE_BACKEND:
        skip("degenerate placements", registry.unavailable_reason())
        return
    V, F = selftest.plane_grid(8, 8, 5.0)

    a = spec(0, np.array([0.6, 0.3, 0.1]))
    b = spec(0, np.array([0.1, 0.3, 0.6]))
    result = solve.surface_distance(V, F, a, b)
    check("same triangle short-circuits analytically",
          result.mode == solve.MODE_SAME_TRIANGLE, result.mode)
    check("same triangle surface == straight exactly",
          result.distance_mm == result.straight_mm)
    check("same triangle made no solver call", result.report is None)
    check("same triangle ratio is exactly 1", result.ratio == 1.0)

    # Two points on the SAME edge of one triangle: still the same triangle
    # index, so the analytic path covers it.
    a = spec(0, np.array([0.75, 0.25, 0.0]))
    b = spec(0, np.array([0.25, 0.75, 0.0]))
    result = solve.surface_distance(V, F, a, b)
    check("same edge is analytic", result.mode == solve.MODE_SAME_TRIANGLE)
    corners = V[F[0]]
    expected = float(np.linalg.norm(
        (0.75 * corners[0] + 0.25 * corners[1])
        - (0.25 * corners[0] + 0.75 * corners[1])))
    check("same edge distance matches the closed form",
          abs(result.distance_mm - expected) < 1e-9,
          "%.9f vs %.9f" % (result.distance_mm, expected))

    # Two points on DIFFERENT triangles that share a vertex, both snapped to
    # that vertex: the solver must see one shared scratch vertex, giving 0.
    shared = int(F[0][1])
    tri_b = int(np.argmax([shared in tri and i != 0 for i, tri in enumerate(F)]))
    corner_b = int(np.where(F[tri_b] == shared)[0][0])
    bary_b = np.zeros(3)
    bary_b[corner_b] = 1.0
    a = spec(0, np.array([0.0, 1.0, 0.0]))
    b = spec(tri_b, bary_b)
    result = solve.surface_distance(V, F, a, b)
    check("same vertex from two triangles gives 0",
          result.distance_mm == 0.0, "%r" % result.distance_mm)
    check("same vertex made no solver call", result.report is None)


def test_a_to_a_is_zero():
    print("\n[numeric] A -> A is exactly zero")
    if not HAVE_BACKEND:
        skip("A to A", registry.unavailable_reason())
        return
    V, F = selftest.plane_grid(8, 8, 5.0)
    a = spec(12, np.array([0.4, 0.35, 0.25]))
    b = spec(12, np.array([0.4, 0.35, 0.25]))
    result = solve.surface_distance(V, F, a, b)
    check("A -> A is exactly 0.0", result.distance_mm == 0.0)
    check("A -> A is the ZERO mode", result.mode == solve.MODE_ZERO, result.mode)


def test_symmetry():
    print("\n[numeric] A -> B equals B -> A")
    if not HAVE_BACKEND:
        skip("symmetry", registry.unavailable_reason())
        return
    for label, (V, F), ia, ib in (
        ("plane", selftest.plane_grid(10, 10, 6.0), 7, 160),
        ("cylinder", selftest.cylinder_mesh(50.0, 200.0, 24, 12), 5, 400),
        ("icosphere", selftest.icosphere(100.0, 3), 11, 900),
    ):
        a = spec(ia, np.array([0.5, 0.3, 0.2]))
        b = spec(ib, np.array([0.25, 0.35, 0.4]))
        forward = solve.surface_distance(V, F, a, b).distance_mm
        backward = solve.surface_distance(V, F, b, a).distance_mm
        relative = abs(forward - backward) / forward if forward else 0.0
        check("%s: A->B == B->A (rel %.3e)" % (label, relative), relative < 1e-9,
              "%.9f vs %.9f" % (forward, backward))


def test_surface_never_shorter_than_straight():
    print("\n[numeric] surface >= straight on curved surfaces")
    if not HAVE_BACKEND:
        skip("curved invariant", registry.unavailable_reason())
        return
    for label, (V, F), ia, ib in (
        ("cylinder", selftest.cylinder_mesh(50.0, 200.0, 32, 16), 3, 700),
        ("icosphere", selftest.icosphere(100.0, 3), 5, 1000),
    ):
        a = spec(ia, np.array([0.4, 0.35, 0.25]))
        b = spec(ib, np.array([0.3, 0.3, 0.4]))
        result = solve.surface_distance(V, F, a, b)
        check("%s: surface >= straight" % label,
              result.distance_mm >= result.straight_mm - 1e-9,
              "%.9f vs %.9f" % (result.distance_mm, result.straight_mm))
        check("%s: ratio > 1 on a curved surface" % label, result.ratio > 1.0,
              "ratio %.6f" % result.ratio)
        check("%s: distance is finite" % label, math.isfinite(result.distance_mm))


def test_cylinder_against_the_analytic_reference():
    print("\n[numeric] cylinder: measured distance vs the unrolled reference")
    if not HAVE_BACKEND:
        skip("cylinder reference", registry.unavailable_reason())
        return
    radius, height, n_theta, n_z = 50.0, 200.0, 96, 48
    V, F = selftest.cylinder_mesh(radius, height, n_theta, n_z)

    # Place A and B ON mesh vertices via barycentric weight 1, so the analytic
    # reference is unambiguous, but still route them through the production
    # pipeline including insertion.
    def at_vertex(vertex_index):
        rows = np.where(F == vertex_index)
        triangle = int(rows[0][0])
        corner = int(rows[1][0])
        bary = np.zeros(3)
        bary[corner] = 1.0
        return spec(triangle, bary)

    i_a, j_a = 0, n_z // 4
    i_b, j_b = n_theta // 4, (3 * n_z) // 4
    source = selftest.cylinder_index(n_theta, i_a, j_a)
    target = selftest.cylinder_index(n_theta, i_b, j_b)
    result = solve.surface_distance(V, F, at_vertex(source), at_vertex(target))
    reference = selftest.cylinder_unrolled(
        radius,
        i_a * 2.0 * math.pi / n_theta, j_a * height / n_z,
        i_b * 2.0 * math.pi / n_theta, j_b * height / n_z,
    )
    relative = abs(result.distance_mm - reference) / reference
    print("        polyhedral %.6f  smooth %.6f  rel err %.3e  bound %s  attempts %d"
          % (result.distance_mm, reference, relative,
             result.report.bound_factor if result.report else "n/a",
             result.report.attempts if result.report else 0))
    check("cylinder within 0.1%% of the smooth reference", relative < 1e-3,
          "relative %.3e" % relative)
    check("cylinder underestimates the smooth surface",
          result.distance_mm <= reference + 1e-9)


class StubSolver(object):
    """Solver that misses the target until the bound reaches `needed`.

    The retry/fallback logic cannot be exercised against the real backend:
    pygeodesic's stop-vertex check keeps propagating until the target is
    settled, so even an absurdly small max_distance returns the exact answer
    (measured below, and in PROJECT_SPEC.md sect. 5.1b). `inf` therefore only
    ever means genuinely unreachable.

    That makes the expanding sequence defensive insurance rather than
    everyday machinery - but it still has to be correct, so it is tested here
    against a stub with the semantics a future pygeodesic might adopt.
    """

    def __init__(self, needed, answer=42.0, reachable=True):
        self.needed = needed
        self.answer = answer
        self.reachable = reachable
        self.bounded_calls = []
        self.unbounded_calls = 0

    def bounded_distance(self, source_index, target_index, max_distance):
        self.bounded_calls.append(float(max_distance))
        if self.reachable and max_distance >= self.needed:
            return self.answer
        return None

    def unbounded_distance(self, source_index, target_index):
        self.unbounded_calls += 1
        return self.answer if self.reachable else None


def test_bound_expansion_and_fallback():
    print("\n[guard] bound expansion, retry, and the unbounded fallback")
    V, F = selftest.plane_grid(4, 4, 5.0)
    straight = 100.0

    # Needs the 4.0x bound: the first two must fail, the third must succeed.
    stub = StubSolver(needed=straight * 3.5)
    value, report = registry.bounded_distance(
        V, F, 0, 1, straight, solver=stub
    )
    check("expansion returns the backend value", value == 42.0)
    check("expansion stopped at the first sufficient factor",
          report.bound_factor == 4.0, str(report.bound_factor))
    check("expansion made exactly three attempts", report.attempts == 3,
          str(report.attempts))
    check("bounds grew 1.25x, 2x, 4x",
          stub.bounded_calls == [125.0, 200.0, 400.0], str(stub.bounded_calls))
    check("no unbounded call was needed", stub.unbounded_calls == 0)
    check("not flagged as a fallback", report.unbounded_fallback is False)
    check("attempt log marks the misses then the hit",
          [entry[3] for entry in report.attempt_log] == [False, False, True],
          str([entry[3] for entry in report.attempt_log]))
    check("elapsed time is recorded", report.seconds >= 0.0)

    # Beyond every factor: the unbounded fallback must engage.
    stub = StubSolver(needed=straight * 1000.0)
    value, report = registry.bounded_distance(V, F, 0, 1, straight, solver=stub)
    check("fallback returns the exact value", value == 42.0)
    check("fallback is flagged", report.unbounded_fallback is True)
    check("fallback used every factor first",
          report.attempts == len(registry.BOUND_FACTORS) + 1,
          str(report.attempts))
    check("fallback records bound_factor None", report.bound_factor is None)
    check("fallback called the unbounded query exactly once",
          stub.unbounded_calls == 1, str(stub.unbounded_calls))

    # Fallback disabled: must fail, never guess.
    stub = StubSolver(needed=straight * 1000.0)
    try:
        registry.bounded_distance(V, F, 0, 1, straight, solver=stub,
                                  allow_unbounded_fallback=False)
    except registry.QueryFailed:
        check("no fallback -> QueryFailed, never a substituted number", True)
        check("no fallback -> unbounded query never made",
              stub.unbounded_calls == 0)
    else:
        check("no fallback -> QueryFailed, never a substituted number", False)

    # Genuinely unreachable, fallback allowed: still must fail.
    stub = StubSolver(needed=0.0, reachable=False)
    try:
        registry.bounded_distance(V, F, 0, 1, straight, solver=stub)
    except registry.QueryFailed as exc:
        check("unreachable target -> QueryFailed", True)
        check("the failure explains itself", "did not reach" in str(exc), str(exc))
    else:
        check("unreachable target -> QueryFailed", False)

    # The minimum bound keeps a near-zero straight distance from asking for a
    # propagation radius that is guaranteed to be useless.
    stub = StubSolver(needed=0.0)
    registry.bounded_distance(V, F, 0, 1, 0.0, solver=stub)
    check("a zero straight distance still uses the minimum bound",
          stub.bounded_calls[0] == registry.MIN_BOUND_MM * 1.25,
          str(stub.bounded_calls[:1]))


def test_small_bounds_are_still_exact():
    print("\n[numeric] a too-small bound is exact, not short (sect. 5.1b)")
    if not HAVE_BACKEND:
        skip("small bound exactness", registry.unavailable_reason())
        return
    V, F = selftest.cylinder_mesh(50.0, 200.0, 48, 24)
    solver = registry.exact_mmp.ExactSolver(V, F)
    source, target = 0, int(V.shape[0]) - 5
    straight = float(np.linalg.norm(V[target] - V[source]))
    reference = solver.unbounded_distance(source, target)

    for factor in (1e-6, 0.1, 0.5, 0.9):
        value = solver.bounded_distance(source, target, straight * factor)
        check("bound %.1e x straight is still exact" % factor,
              value is not None and abs(value - reference) < 1e-9,
              "%r vs %.9f" % (value, reference))
    check("this is why the retry sequence never fires in practice", True)

    # Which means the first factor always succeeds on a reachable pair.
    _value, report = registry.bounded_distance(
        V, F, source, target, straight, solver=solver
    )
    check("the production strategy needs one attempt", report.attempts == 1,
          str(report.attempts))
    check("and does not fall back", report.unbounded_fallback is False)
    check("timing is recorded", report.seconds > 0.0)


def test_bounded_matches_unbounded_on_every_factor():
    print("\n[numeric] every bound factor yields the identical exact value")
    if not HAVE_BACKEND:
        skip("bound equivalence", registry.unavailable_reason())
        return
    V, F = selftest.icosphere(100.0, 3)
    solver = registry.exact_mmp.ExactSolver(V, F)
    source, target = 0, 400
    straight = float(np.linalg.norm(V[target] - V[source]))
    unbounded = solver.unbounded_distance(source, target)
    for factor in registry.BOUND_FACTORS:
        value = solver.bounded_distance(source, target, straight * factor)
        check("bound %.2fx equals the unbounded answer" % factor,
              value is not None and abs(value - unbounded) < 1e-9,
              "%r vs %.9f" % (value, unbounded))


def test_disconnected_pair_never_reaches_the_solver():
    print("\n[numeric] a disconnected pair fails before any solver call")
    if not HAVE_BACKEND:
        skip("disconnected", registry.unavailable_reason())
        return
    # Two separated planar patches in one array: genuinely disconnected.
    V1, F1 = selftest.plane_grid(4, 4, 5.0)
    V2, F2 = selftest.plane_grid(4, 4, 5.0)
    V2 = V2 + np.array([500.0, 0.0, 0.0])
    V = np.vstack([V1, V2])
    F = np.vstack([F1, F2 + V1.shape[0]]).astype(np.int32)

    a = spec(0, centroid(), component=1)
    b = spec(int(F1.shape[0]) + 1, centroid(), component=2)
    expect_failure("cross-component pair", 'DISCONNECTED',
                   lambda: solve.surface_distance(V, F, a, b))

    # Same components declared equal, so it does reach the solver and must
    # then fail honestly rather than return a number.
    b_same = spec(int(F1.shape[0]) + 1, centroid(), component=1)
    try:
        solve.surface_distance(V, F, a, b_same)
    except solve.MeasurementError as exc:
        check("an unreachable target fails, never returns a number",
              exc.code in ('BACKEND_ERROR', 'INVARIANT_VIOLATION'), exc.code)
    else:
        check("an unreachable target fails, never returns a number", False,
              "returned a value across disconnected patches")


def test_insertion_does_not_touch_the_canonical_arrays():
    print("\n[numeric] the canonical arrays are never modified")
    if not HAVE_BACKEND:
        skip("canonical immutability", registry.unavailable_reason())
        return
    V, F = selftest.plane_grid(8, 8, 5.0)
    V_before, F_before = V.copy(), F.copy()
    result = solve.surface_distance(
        V, F, spec(3, np.array([0.5, 0.3, 0.2])),
        spec(90, np.array([0.2, 0.4, 0.4])))
    check("canonical vertices unchanged", np.array_equal(V, V_before))
    check("canonical triangles unchanged", np.array_equal(F, F_before))
    check("scratch mesh grew by the inserted endpoints",
          result.scratch_vertex_count == V.shape[0] + result.added_vertex_count)
    check("scratch mesh has more triangles than canonical",
          result.scratch_triangle_count > F.shape[0])
    check("two endpoints were inserted", result.added_vertex_count == 2,
          str(result.added_vertex_count))


def main():
    print("BSMT Milestone 2.3 - offline surface distance tests")
    print("  python     : %s" % sys.version.split()[0])
    print("  numpy      : %s" % np.__version__)
    print("  pygeodesic : %s"
          % (registry.backend_version() if HAVE_BACKEND
             else "NOT AVAILABLE - %s" % registry.unavailable_reason()))

    for test in (
        test_validation_refuses_before_compute,
        test_disconnected_message_is_the_specified_one,
        test_backend_unavailable_is_reported_not_substituted,
        test_analytic_short_circuits_need_no_backend,
        test_invariant_checker,
        test_bound_strategy_shape,
        test_bounded_query_refuses_infinite_bound,
        test_metric_tensor_semantics,
        test_metric_tolerance_survives_float32_transforms,
        test_plane_surface_equals_straight,
        test_same_triangle_edge_and_vertex,
        test_a_to_a_is_zero,
        test_symmetry,
        test_surface_never_shorter_than_straight,
        test_cylinder_against_the_analytic_reference,
        test_bound_expansion_and_fallback,
        test_small_bounds_are_still_exact,
        test_bounded_matches_unbounded_on_every_factor,
        test_disconnected_pair_never_reaches_the_solver,
        test_insertion_does_not_touch_the_canonical_arrays,
    ):
        test()

    print("\n%d checks, %d failure(s), %d skipped group(s)"
          % (CHECKS[0], len(FAILURES), len(SKIPS)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
