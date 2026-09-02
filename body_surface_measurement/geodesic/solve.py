"""Production A-B surface distance pipeline (Milestone 2.3).

Pure numpy - no bpy - so the whole measurement pipeline is unit-testable
outside Blender. The Blender operator supplies the canonical arrays and the
two SurfacePoint locations; everything numerical happens here.

Pipeline, in order (PROJECT_SPEC.md sect. 5.2, 7.4, 8.4):

    validate both points against the canonical arrays
    -> same source object
    -> same geometry hash
    -> same connected component
    -> same-triangle analytic short-circuit, if applicable
    -> scratch mesh with exact endpoint insertion (canonical mesh untouched)
    -> bounded exact MMP query (registry.bounded_distance)
    -> result invariants
    -> distance

Two rules that outrank convenience everywhere in this module:

* **The canonical mesh is never modified.** Insertion builds a scratch copy;
  `insert_points()` is the Milestone 2.1 utility, used unchanged.
* **No approximate substitute, ever.** Every failure raises `MeasurementError`
  with a code from sect. 5.2. The straight distance is never quietly returned
  in place of a surface distance.

No path is computed here. A polyline requires an unbounded query that costs
tens of seconds at scan scale (sect. 5.1b), so it belongs to Milestone 2.4 and
must never happen as a side effect of measuring a distance.
"""

import time

import numpy as np

from . import registry
from .surface_point import (
    InsertionError,
    KIND_VERTEX,
    SNAP_TOLERANCE,
    insert_points,
    normalize_barycentric,
    reconstruct,
)

# d_surface >= d_straight is exact geometry: the geodesic is a path between
# the endpoints, and the chord is the shortest path in space. Only rounding
# can put the computed value below. float64 over the longest paths this sees
# accumulates ~1e-15 relative; 1e-9 sits six orders above that floor and many
# orders below any real defect, which would be percent-level.
STRAIGHT_TOLERANCE_REL = 1e-9
STRAIGHT_TOLERANCE_MM = 1e-9

MODE_ZERO = 'ZERO'
MODE_SAME_TRIANGLE = 'SAME_TRIANGLE'
MODE_SOLVER = 'SOLVER'

# How far the path polyline's own segment sum, the solver's reported path
# distance, and the production bounded distance may differ.
#
# Measured (PROJECT_SPEC.md sect. 5.1b): a bounded query and an unbounded one
# returned bit-identical values in 18 of 18 tested configurations, and a
# polyline's segment sum matched its reported distance to <= 6.7e-16 relative.
#
# The binding floor is not the solver, though - it is storage. The reference
# handed in here is the production surface distance read back out of a Blender
# FloatProperty, which is SINGLE precision, so it carries ~1.2e-7 relative
# error before any comparison happens. Measured: a 115.068 mm distance came
# back 1.9e-6 mm adrift for exactly that reason. 1e-5 relative sits ~40x above
# that floor and orders of magnitude below any real disagreement, which would
# mean the two solves saw different geometry and would be percent-level.
PATH_AGREEMENT_REL_TOL = 1e-5
PATH_AGREEMENT_ABS_TOL_MM = 1e-6


class MeasurementError(Exception):
    """A surface distance could not be produced. Carries a sect. 5.2 code."""

    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


#: Displayed text for each failure code (sect. 5.2).
FAILURE_TEXT = {
    'PATH_DISTANCE_MISMATCH':
        "Surface path unavailable: the path length disagrees with the "
        "measured surface distance",
    'PATH_UNAVAILABLE': "Surface path unavailable",
    'POINTS_MISSING': "Surface distance unavailable: pick Point A and Point B first",
    'STALE_POINTS': "Surface distance unavailable: mesh changed since points were picked",
    'DIFFERENT_OBJECTS': "Surface distance unavailable: Point A and Point B are on different objects",
    'DISCONNECTED': "Surface distance unavailable: points are on disconnected surface components",
    'INVALID_TOPOLOGY': "Surface distance unavailable: invalid topology",
    'BACKEND_MISSING': "Surface distance unavailable: exact geodesic backend not installed",
    'BACKEND_ERROR': "Surface distance unavailable: exact geodesic backend failed",
    'INSERTION_FAILED': "Surface distance unavailable: endpoint insertion failed",
    'INVARIANT_VIOLATION': "Surface distance unavailable: internal measurement error",
}


def failure_message(code, detail=""):
    base = FAILURE_TEXT.get(
        code, "Surface distance unavailable: %s" % code.lower().replace("_", " ")
    )
    return "%s (%s)" % (base, detail) if detail else base


class PointSpec(object):
    """One endpoint, as the canonical location plus the identity it belongs to."""

    __slots__ = ("triangle_index", "barycentric", "component_id",
                 "source_object", "geometry_hash", "status", "valid")

    def __init__(self, triangle_index, barycentric, component_id=0,
                 source_object="", geometry_hash="", status="VALID", valid=True):
        self.triangle_index = int(triangle_index)
        self.barycentric = np.asarray(barycentric, dtype=np.float64)
        self.component_id = int(component_id)
        self.source_object = source_object
        self.geometry_hash = geometry_hash
        self.status = status
        self.valid = bool(valid)


class SurfaceResult(object):
    """A successful surface measurement and everything it took to get it."""

    __slots__ = ("distance_mm", "straight_mm", "ratio", "mode", "report",
                 "component_id", "insertion_seconds", "solver_seconds",
                 "total_seconds", "scratch_vertex_count", "scratch_triangle_count",
                 "added_vertex_count", "endpoint_kinds")

    def summary(self):
        if self.mode == MODE_SAME_TRIANGLE:
            return "Exact (same triangle, planar) | %.3f s" % self.total_seconds
        if self.mode == MODE_ZERO:
            return "Exact (coincident points)"
        return self.report.summary()


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate_points(point_a, point_b, triangle_count, geometry_hash=None):
    """Every pre-solve check of sect. 5. Raises MeasurementError, or returns None."""
    for slot, point in (('A', point_a), ('B', point_b)):
        if point is None or not point.valid:
            raise MeasurementError('POINTS_MISSING', failure_message('POINTS_MISSING'))
        if point.status != "VALID":
            raise MeasurementError(
                'STALE_POINTS',
                failure_message('STALE_POINTS', "Point %s: %s" % (slot, point.status)),
            )

    if point_a.source_object != point_b.source_object:
        raise MeasurementError(
            'DIFFERENT_OBJECTS',
            failure_message(
                'DIFFERENT_OBJECTS',
                "A on '%s', B on '%s'" % (point_a.source_object, point_b.source_object),
            ),
        )

    if geometry_hash is not None:
        for slot, point in (('A', point_a), ('B', point_b)):
            if point.geometry_hash != geometry_hash:
                raise MeasurementError(
                    'STALE_POINTS',
                    failure_message(
                        'STALE_POINTS',
                        "Point %s refers to mesh %s, current mesh is %s"
                        % (slot, point.geometry_hash[:8] or "?", geometry_hash[:8]),
                    ),
                )

    for slot, point in (('A', point_a), ('B', point_b)):
        if not 0 <= point.triangle_index < triangle_count:
            raise MeasurementError(
                'STALE_POINTS',
                failure_message(
                    'STALE_POINTS',
                    "Point %s triangle %d is outside the canonical array (%d triangles)"
                    % (slot, point.triangle_index, triangle_count),
                ),
            )
        _clean, ok, deviation = normalize_barycentric(point.barycentric)
        if not ok:
            raise MeasurementError(
                'INVALID_TOPOLOGY',
                failure_message(
                    'INVALID_TOPOLOGY',
                    "Point %s barycentric coordinates are not on its triangle "
                    "(deviation %.3e)" % (slot, deviation),
                ),
            )

    # sect. 7.4: connectivity is a property of the PAIR, not of the mesh, and
    # it is decided here - before any solver call, and cheaply.
    if point_a.component_id != point_b.component_id:
        raise MeasurementError(
            'DISCONNECTED',
            failure_message(
                'DISCONNECTED',
                "A in component #%d, B in component #%d"
                % (point_a.component_id, point_b.component_id),
            ),
        )


# ---------------------------------------------------------------------------
# the pipeline
# ---------------------------------------------------------------------------

def _prepare(vertices_solver, triangles, point_a, point_b, geometry_hash):
    """Validation and endpoint reconstruction shared by distance and path.

    Both the production distance query and the on-demand path query must see
    exactly the same endpoints on exactly the same scratch topology, or the
    two answers could disagree for reasons that have nothing to do with the
    solver. Extracting this is what makes that structural rather than a
    convention two functions have to remember to follow.
    """
    vertices_solver = np.asarray(vertices_solver, dtype=np.float64)
    triangles = np.asarray(triangles, dtype=np.int64)
    triangle_count = int(triangles.shape[0])

    validate_points(point_a, point_b, triangle_count, geometry_hash)

    if not registry.available():
        raise MeasurementError(
            'BACKEND_MISSING',
            failure_message('BACKEND_MISSING', registry.unavailable_reason()),
        )

    # Endpoint positions in solver millimetres, from the canonical location.
    # Barycentric coordinates are affine invariant, so this is the same
    # surface point the marker shows, expressed in the solver's own space.
    xyz_a = reconstruct(vertices_solver[triangles[point_a.triangle_index]],
                        point_a.barycentric)
    xyz_b = reconstruct(vertices_solver[triangles[point_b.triangle_index]],
                        point_b.barycentric)
    return {
        "vertices": vertices_solver,
        "triangles": triangles,
        "triangle_count": triangle_count,
        "xyz_a": xyz_a,
        "xyz_b": xyz_b,
        "straight_mm": float(np.linalg.norm(xyz_b - xyz_a)),
    }


def _build_scratch(vertices_solver, triangles, point_a, point_b,
                   xyz_a, xyz_b, straight_mm):
    """Insert both endpoints into a scratch copy. Returns (insertion, seconds).

    The Milestone 2.1 utility, used unchanged. Both points are classified
    against the ORIGINAL canonical topology and the scratch mesh is built in
    one pass, because inserting A would renumber the triangles B refers to.
    Production landmarks are never snapped to existing mesh vertices.
    """
    started = time.perf_counter()
    try:
        insertion = insert_points(
            vertices_solver,
            triangles,
            [(point_a.triangle_index, point_a.barycentric),
             (point_b.triangle_index, point_b.barycentric)],
            tolerance=SNAP_TOLERANCE,
        )
    except InsertionError as exc:
        raise MeasurementError(
            'INSERTION_FAILED', failure_message('INSERTION_FAILED', str(exc))
        )
    elapsed = time.perf_counter() - started
    _check_scratch(insertion, xyz_a, xyz_b, straight_mm)
    return insertion, elapsed


def surface_distance(vertices_solver, triangles, point_a, point_b,
                     geometry_hash=None, bound_factors=registry.BOUND_FACTORS,
                     allow_unbounded_fallback=True):
    """Exact geodesic distance between two SurfacePoints, in millimetres.

    `vertices_solver` must be the canonical vertices in solver space, which is
    physical millimetres (sect. 6.2). The backend therefore returns
    millimetres directly and **no unit conversion is applied to the result
    anywhere downstream** - double conversion is a named failure mode.

    Neither `vertices_solver` nor `triangles` is modified.
    """
    started_total = time.perf_counter()
    prepared = _prepare(vertices_solver, triangles, point_a, point_b,
                        geometry_hash)
    vertices_solver = prepared["vertices"]
    triangles = prepared["triangles"]
    triangle_count = prepared["triangle_count"]
    xyz_a, xyz_b = prepared["xyz_a"], prepared["xyz_b"]
    straight_mm = prepared["straight_mm"]

    result = SurfaceResult()
    result.straight_mm = straight_mm
    result.component_id = int(point_a.component_id)
    result.insertion_seconds = 0.0
    result.solver_seconds = 0.0
    result.added_vertex_count = 0
    result.scratch_vertex_count = int(vertices_solver.shape[0])
    result.scratch_triangle_count = triangle_count
    result.endpoint_kinds = ("", "")
    result.report = None

    # --- A -> A is exactly zero -------------------------------------------
    if straight_mm == 0.0 and point_a.triangle_index == point_b.triangle_index:
        result.distance_mm = 0.0
        result.ratio = 1.0
        result.mode = MODE_ZERO
        result.total_seconds = time.perf_counter() - started_total
        return result

    # --- same triangle: planar, so the geodesic IS the chord --------------
    # A triangle is flat and convex, so the straight segment between two
    # points inside it lies entirely inside it and is the shortest surface
    # path. Exact by construction, and it skips a multi-second solver call.
    if point_a.triangle_index == point_b.triangle_index:
        result.distance_mm = straight_mm
        result.ratio = 1.0
        result.mode = MODE_SAME_TRIANGLE
        result.total_seconds = time.perf_counter() - started_total
        return result

    # --- scratch mesh with both endpoints inserted ------------------------
    insertion, result.insertion_seconds = _build_scratch(
        vertices_solver, triangles, point_a, point_b,
        xyz_a, xyz_b, straight_mm,
    )

    source_index, target_index = insertion.point_vertex_indices
    result.endpoint_kinds = tuple(insertion.point_kinds)
    result.added_vertex_count = int(insertion.added_vertex_count)
    result.scratch_vertex_count = int(insertion.vertices.shape[0])
    result.scratch_triangle_count = int(insertion.triangles.shape[0])

    if source_index == target_index:
        # Both endpoints resolved to the same scratch vertex: they are the
        # same surface location to within the snapping tolerance.
        result.distance_mm = 0.0
        result.ratio = 1.0 if straight_mm == 0.0 else 0.0
        result.mode = MODE_ZERO
        result.total_seconds = time.perf_counter() - started_total
        return result

    # --- bounded exact query ----------------------------------------------
    started = time.perf_counter()
    try:
        distance_mm, report = registry.bounded_distance(
            insertion.vertices,
            insertion.triangles,
            source_index,
            target_index,
            straight_mm,
            bound_factors=bound_factors,
            allow_unbounded_fallback=allow_unbounded_fallback,
        )
    except registry.BackendUnavailable as exc:
        raise MeasurementError('BACKEND_MISSING',
                               failure_message('BACKEND_MISSING', str(exc)))
    except registry.QueryFailed as exc:
        # Endpoints validated as same-component, so a query that still cannot
        # reach the target means the scratch topology is not what it should
        # be. Reported as a failure, never as a number.
        raise MeasurementError('BACKEND_ERROR',
                               failure_message('BACKEND_ERROR', str(exc)))
    except Exception as exc:  # noqa: BLE001
        raise MeasurementError(
            'BACKEND_ERROR',
            failure_message('BACKEND_ERROR',
                            "%s: %s" % (type(exc).__name__, exc)),
        )
    result.solver_seconds = time.perf_counter() - started
    result.report = report

    _check_invariants(distance_mm, straight_mm)

    result.distance_mm = float(distance_mm)
    result.ratio = (distance_mm / straight_mm) if straight_mm > 0.0 else 1.0
    result.mode = MODE_SOLVER
    result.total_seconds = time.perf_counter() - started_total
    return result


class SurfacePathResult(object):
    """An exact geodesic path polyline and the checks it passed."""

    __slots__ = ("polyline_solver", "distance_mm", "polyline_length_mm",
                 "point_count", "mode", "straight_mm", "component_id",
                 "insertion_seconds", "solver_seconds", "total_seconds",
                 "agreement_mm", "compared_to_mm")


def surface_path(vertices_solver, triangles, point_a, point_b,
                 geometry_hash=None, expected_distance_mm=None):
    """Exact geodesic PATH between two SurfacePoints, in solver millimetres.

    On-demand only. This is the expensive query: unlike the production
    distance, a path needs ``geodesicDistance()``, which cannot be bounded and
    sweeps the whole mesh - tens of seconds at scan scale (sect. 5.1b). It is
    never called from a distance calculation, from measurement creation or
    from landmark picking.

    It uses ``_prepare`` and ``_build_scratch``, so it sees exactly the same
    endpoints on exactly the same scratch topology the distance query saw.

    `expected_distance_mm`, when given, is the already-stored production
    surface distance. The path is refused if it disagrees, rather than
    quietly presenting a second opinion. **The stored measurement is never
    modified by this function.**

    Returns a SurfacePathResult whose `polyline_solver` is (k, 3) float64 in
    solver space. Raises MeasurementError; never returns an approximate path.
    """
    started_total = time.perf_counter()
    prepared = _prepare(vertices_solver, triangles, point_a, point_b,
                        geometry_hash)
    vertices_solver = prepared["vertices"]
    triangles = prepared["triangles"]
    xyz_a, xyz_b = prepared["xyz_a"], prepared["xyz_b"]
    straight_mm = prepared["straight_mm"]

    result = SurfacePathResult()
    result.straight_mm = straight_mm
    result.component_id = int(point_a.component_id)
    result.insertion_seconds = 0.0
    result.solver_seconds = 0.0
    result.agreement_mm = 0.0
    result.compared_to_mm = (float(expected_distance_mm)
                             if expected_distance_mm is not None else 0.0)

    # --- degenerate and analytic cases need no solver ---------------------
    if point_a.triangle_index == point_b.triangle_index:
        # One planar triangle: the geodesic IS the straight segment, so the
        # path is exactly the two endpoints. Consistent with the analytic
        # short-circuit the distance query already takes.
        result.polyline_solver = np.asarray([xyz_a, xyz_b], dtype=np.float64)
        result.distance_mm = straight_mm
        result.polyline_length_mm = straight_mm
        result.point_count = 2
        result.mode = (MODE_ZERO if straight_mm == 0.0 else MODE_SAME_TRIANGLE)
        _check_path_agreement(result, expected_distance_mm)
        result.total_seconds = time.perf_counter() - started_total
        return result

    insertion, result.insertion_seconds = _build_scratch(
        vertices_solver, triangles, point_a, point_b,
        xyz_a, xyz_b, straight_mm,
    )
    source_index, target_index = insertion.point_vertex_indices

    if source_index == target_index:
        result.polyline_solver = np.asarray([xyz_a, xyz_a], dtype=np.float64)
        result.distance_mm = 0.0
        result.polyline_length_mm = 0.0
        result.point_count = 2
        result.mode = MODE_ZERO
        _check_path_agreement(result, expected_distance_mm)
        result.total_seconds = time.perf_counter() - started_total
        return result

    # --- the expensive unbounded query ------------------------------------
    started = time.perf_counter()
    try:
        distance_mm, polyline = registry.exact_mmp.compute_distance_and_path(
            insertion.vertices, insertion.triangles, source_index, target_index
        )
    except registry.exact_mmp.BackendUnavailable as exc:
        raise MeasurementError('BACKEND_MISSING',
                               failure_message('BACKEND_MISSING', str(exc)))
    except Exception as exc:  # noqa: BLE001
        raise MeasurementError(
            'BACKEND_ERROR',
            failure_message('BACKEND_ERROR',
                            "%s: %s" % (type(exc).__name__, exc)),
        )
    result.solver_seconds = time.perf_counter() - started

    polyline = np.ascontiguousarray(np.asarray(polyline, dtype=np.float64))
    if polyline.ndim != 2 or polyline.shape[1] != 3 or polyline.shape[0] < 2:
        raise MeasurementError(
            'BACKEND_ERROR',
            failure_message('BACKEND_ERROR',
                            "the backend returned a path of shape %r"
                            % (polyline.shape,)),
        )

    # The polyline must actually start and end at the picked landmarks.
    scale = max(straight_mm, 1.0)
    for label, point, wanted in (("start", polyline[0], xyz_a),
                                 ("end", polyline[-1], xyz_b)):
        offset = float(np.linalg.norm(point - wanted))
        if offset > 1e-6 * scale:
            raise MeasurementError(
                'BACKEND_ERROR',
                failure_message(
                    'BACKEND_ERROR',
                    "the returned path's %s is %.3e mm from the landmark"
                    % (label, offset),
                ),
            )

    _check_invariants(distance_mm, straight_mm)

    result.polyline_solver = polyline
    result.distance_mm = float(distance_mm)
    result.polyline_length_mm = float(
        np.linalg.norm(np.diff(polyline, axis=0), axis=1).sum()
    )
    result.point_count = int(polyline.shape[0])
    result.mode = MODE_SOLVER
    _check_path_agreement(result, expected_distance_mm)
    result.total_seconds = time.perf_counter() - started_total
    return result


def _check_path_agreement(result, expected_distance_mm):
    """The sect. 4 agreement checks. Raises rather than displaying a bad path.

    Three numbers must agree: what the path solver reported, the sum of the
    polyline's own segment lengths, and the production surface distance from
    the bounded query. They come from two independent solves, so a
    disagreement means the two saw different geometry - which is exactly the
    thing that must never be drawn as if it were the measurement.
    """
    def tolerance(reference):
        return max(PATH_AGREEMENT_ABS_TOL_MM,
                   PATH_AGREEMENT_REL_TOL * abs(reference))

    difference = abs(result.polyline_length_mm - result.distance_mm)
    if difference > tolerance(result.distance_mm):
        raise MeasurementError(
            'PATH_DISTANCE_MISMATCH',
            failure_message(
                'PATH_DISTANCE_MISMATCH',
                "the polyline's segments sum to %.9f mm but the solver "
                "reported %.9f mm (difference %.3e mm)"
                % (result.polyline_length_mm, result.distance_mm, difference),
            ),
        )

    if expected_distance_mm is None:
        result.agreement_mm = difference
        return

    expected = float(expected_distance_mm)
    difference = abs(result.distance_mm - expected)
    result.agreement_mm = difference
    if difference > tolerance(expected):
        raise MeasurementError(
            'PATH_DISTANCE_MISMATCH',
            failure_message(
                'PATH_DISTANCE_MISMATCH',
                "the path solve gives %.9f mm but the stored surface "
                "distance is %.9f mm (difference %.3e mm). The stored "
                "measurement has NOT been changed."
                % (result.distance_mm, expected, difference),
            ),
        )


def _check_scratch(insertion, xyz_a, xyz_b, straight_mm):
    """Post-insertion assertions from sect. 8.4."""
    vertices = insertion.vertices
    triangles = insertion.triangles
    count = int(vertices.shape[0])

    for slot, index in zip(('A', 'B'), insertion.point_vertex_indices):
        if not 0 <= int(index) < count:
            raise MeasurementError(
                'INSERTION_FAILED',
                failure_message(
                    'INSERTION_FAILED',
                    "Point %s mapped to scratch vertex %d of %d"
                    % (slot, index, count),
                ),
            )

    if triangles.size == 0:
        raise MeasurementError(
            'INSERTION_FAILED',
            failure_message('INSERTION_FAILED', "scratch mesh has no triangles"),
        )
    if int(triangles.max()) >= count or int(triangles.min()) < 0:
        raise MeasurementError(
            'INSERTION_FAILED',
            failure_message('INSERTION_FAILED',
                            "scratch triangle indices are out of range"),
        )

    # The inserted vertex must sit exactly where the landmark was picked. A
    # displacement here would mean the measurement is not of the point the
    # operator chose, which is worse than no measurement at all.
    scale = max(straight_mm, 1.0)
    for slot, index, wanted in (
        ('A', insertion.point_vertex_indices[0], xyz_a),
        ('B', insertion.point_vertex_indices[1], xyz_b),
    ):
        offset = float(np.linalg.norm(vertices[int(index)] - wanted))
        if offset > 1e-6 * scale:
            raise MeasurementError(
                'INSERTION_FAILED',
                failure_message(
                    'INSERTION_FAILED',
                    "inserted endpoint %s is %.3e mm from the picked location"
                    % (slot, offset),
                ),
            )

    # Only the triangles that were retriangulated may have changed, and the
    # rebuild must not have produced a degenerate one at the endpoints.
    touched = sorted(insertion.replaced_triangles)
    if touched:
        incident = triangles[np.any(
            np.isin(triangles, np.asarray(insertion.point_vertex_indices,
                                          dtype=triangles.dtype)), axis=1
        )]
        if incident.size:
            corners = vertices[incident]
            areas = 0.5 * np.linalg.norm(
                np.cross(corners[:, 1] - corners[:, 0],
                         corners[:, 2] - corners[:, 0]), axis=1
            )
            if not np.all(areas > 0.0):
                raise MeasurementError(
                    'INSERTION_FAILED',
                    failure_message(
                        'INSERTION_FAILED',
                        "insertion produced %d zero-area triangle(s) at an endpoint"
                        % int(np.count_nonzero(areas <= 0.0)),
                    ),
                )


def _check_invariants(distance_mm, straight_mm):
    """sect. 9.2 result invariants. A violation is an error, not a warning."""
    if not np.isfinite(distance_mm):
        raise MeasurementError(
            'INVARIANT_VIOLATION',
            failure_message('INVARIANT_VIOLATION',
                            "surface distance is not finite (%r)" % distance_mm),
        )
    if distance_mm < 0.0:
        raise MeasurementError(
            'INVARIANT_VIOLATION',
            failure_message('INVARIANT_VIOLATION',
                            "surface distance is negative (%r)" % distance_mm),
        )
    tolerance = max(STRAIGHT_TOLERANCE_MM, STRAIGHT_TOLERANCE_REL * straight_mm)
    if distance_mm < straight_mm - tolerance:
        raise MeasurementError(
            'INVARIANT_VIOLATION',
            failure_message(
                'INVARIANT_VIOLATION',
                "surface distance %.9f mm is shorter than the straight "
                "distance %.9f mm, which is geometrically impossible"
                % (distance_mm, straight_mm),
            ),
        )
