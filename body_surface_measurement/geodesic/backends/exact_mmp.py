"""Exact polyhedral geodesic backend: pygeodesic (MMP / Kirsanov).

Milestone 2.2 — environment proof only. This module is deliberately NOT wired
into the production Point A / Point B measurement path; that is Milestone 2.3.

Design constraints, from PROJECT_SPEC.md:

* Pure numpy. No ``bpy``, so it is unit-testable outside Blender.
* **Importing BSMT must never depend on pygeodesic.** The import is attempted
  once, at module import, inside a guard. A failure leaves this module fully
  importable and reports ``availability() == False``.
* **Import errors are never swallowed.** The exception text and the complete
  original traceback are retained verbatim in ``IMPORT_ERROR`` /
  ``IMPORT_TRACEBACK`` and surfaced through ``status()``. A binary/ABI failure
  must be diagnosable, not hidden behind "not installed".
* **No approximate substitute, ever.** Every failure raises a typed exception.
  Nothing in this module returns a fallback number (sect. 5.2).

Measured API surface (Blender 4.5.13 / Python 3.11.15 / numpy 1.26.4 /
macOS arm64, 2026-09-02)::

    pygeodesic.geodesic.PyGeodesicAlgorithmExact(points, faces)
        .geodesicDistance(sourceIndex, targetIndex) -> (float, ndarray (n,3))
        .geodesicDistances(source_indices[, target_indices])

Endpoints are **vertex indices only**; there is no face+barycentric entry
point. Milestone 2.3 supplies arbitrary in-face endpoints by inserting them as
real vertices of a scratch mesh (sect. 8.4). Nothing here does that.
"""

import traceback

import numpy as np

#: Name recorded in the provenance block of a measurement (sect. 5.4).
BACKEND_NAME = "pygeodesic-MMP"

#: Bumped when the wrapper's own numerical behaviour changes.
WRAPPER_VERSION = "bsmt-exact-mmp/1"

AVAILABLE = False
IMPORT_ERROR = ""
IMPORT_TRACEBACK = ""
MODULE_PATH = ""
VERSION = ""

_geodesic = None


class BackendError(Exception):
    """Base class for every failure this backend can produce."""


class BackendUnavailable(BackendError):
    """pygeodesic could not be imported. Carries the original error text."""


class InvalidMeshError(BackendError):
    """The vertex/triangle arrays are not a mesh this backend can solve."""


class SolverError(BackendError):
    """pygeodesic itself raised, or returned something unusable."""


def _describe(exc):
    return "%s: %s" % (type(exc).__name__, exc)


try:
    import pygeodesic
    import pygeodesic.geodesic as _geodesic  # noqa: F811

    if not hasattr(_geodesic, "PyGeodesicAlgorithmExact"):
        raise ImportError(
            "pygeodesic.geodesic has no PyGeodesicAlgorithmExact; found: %s"
            % ", ".join(sorted(n for n in dir(_geodesic) if not n.startswith("_")))
        )
    AVAILABLE = True
    VERSION = str(getattr(pygeodesic, "__version__", "unknown"))
    MODULE_PATH = str(getattr(pygeodesic, "__file__", ""))
except Exception as exc:  # noqa: BLE001 - any import failure must be reported
    _geodesic = None
    AVAILABLE = False
    IMPORT_ERROR = _describe(exc)
    IMPORT_TRACEBACK = traceback.format_exc()


def availability():
    """True when the exact backend can be used right now."""
    return bool(AVAILABLE and _geodesic is not None)


def backend_version():
    """Resolved pygeodesic version, or '' when unavailable."""
    return VERSION if availability() else ""


def status():
    """Full diagnostic record. Safe to call whether or not the import worked."""
    return {
        "backend_name": BACKEND_NAME,
        "wrapper_version": WRAPPER_VERSION,
        "available": availability(),
        "version": VERSION,
        "module_path": MODULE_PATH,
        "import_error": IMPORT_ERROR,
        "import_traceback": IMPORT_TRACEBACK,
    }


def require():
    """Raise BackendUnavailable unless the backend is usable.

    The message keeps the original import error so an ABI or architecture
    mismatch is distinguishable from a plain missing package.
    """
    if availability():
        return
    if IMPORT_ERROR:
        raise BackendUnavailable(
            "exact geodesic backend not usable: %s" % IMPORT_ERROR
        )
    raise BackendUnavailable("exact geodesic backend not usable: unknown reason")


# ---------------------------------------------------------------------------
# mesh validation
# ---------------------------------------------------------------------------

def prepare_mesh(vertices, triangles):
    """Validate and coerce arrays into the exact dtypes handed to the solver.

    Returns ``(V float64 (n,3) C-contiguous, F int32 (m,3) C-contiguous)``.

    Coercion is explicit rather than left to pygeodesic: float32 leakage from
    ``foreach_get`` into the solve path is a named failure mode (sect. 11,
    Milestone 2.1). Raises InvalidMeshError - never returns a repaired mesh.
    """
    try:
        verts = np.asarray(vertices)
        tris = np.asarray(triangles)
    except Exception as exc:  # noqa: BLE001
        raise InvalidMeshError("mesh arrays are not array-like: %s" % _describe(exc))

    if verts.ndim != 2 or verts.shape[1] != 3:
        raise InvalidMeshError(
            "vertices must have shape (n, 3), got %r" % (verts.shape,)
        )
    if tris.ndim != 2 or tris.shape[1] != 3:
        raise InvalidMeshError(
            "triangles must have shape (m, 3), got %r" % (tris.shape,)
        )
    if verts.shape[0] < 3:
        raise InvalidMeshError(
            "mesh has %d vertices; at least 3 are required" % verts.shape[0]
        )
    if tris.shape[0] < 1:
        raise InvalidMeshError("mesh has no triangles")

    verts = np.ascontiguousarray(verts, dtype=np.float64)
    if not np.all(np.isfinite(verts)):
        bad = int(np.count_nonzero(~np.isfinite(verts).all(axis=1)))
        raise InvalidMeshError(
            "mesh has %d vertex/vertices with non-finite coordinates" % bad
        )

    if not np.issubdtype(tris.dtype, np.integer):
        raise InvalidMeshError(
            "triangle indices must be an integer dtype, got %s" % tris.dtype
        )
    lo = int(tris.min())
    hi = int(tris.max())
    if lo < 0:
        raise InvalidMeshError("triangle indices contain a negative value (%d)" % lo)
    if hi >= verts.shape[0]:
        raise InvalidMeshError(
            "triangle index %d is out of range for %d vertices"
            % (hi, verts.shape[0])
        )

    repeated = (
        (tris[:, 0] == tris[:, 1])
        | (tris[:, 1] == tris[:, 2])
        | (tris[:, 2] == tris[:, 0])
    )
    if bool(repeated.any()):
        raise InvalidMeshError(
            "%d triangle(s) repeat a vertex index (topologically degenerate)"
            % int(repeated.sum())
        )

    return verts, np.ascontiguousarray(tris, dtype=np.int32)


def _check_index(name, index, count):
    try:
        value = int(index)
    except Exception as exc:  # noqa: BLE001
        raise InvalidMeshError("%s is not an integer: %s" % (name, _describe(exc)))
    if value < 0 or value >= count:
        raise InvalidMeshError(
            "%s = %d is out of range for %d vertices" % (name, value, count)
        )
    return value


# ---------------------------------------------------------------------------
# solver
# ---------------------------------------------------------------------------

class ExactSolver(object):
    """One constructed pygeodesic solver over one fixed mesh.

    Held separately from the query so repeated A-B queries on the same mesh do
    not pay construction again, and so construction and query cost can be
    timed apart (Milestone 2.2 benchmark requirement).
    """

    def __init__(self, vertices, triangles):
        require()
        self.vertices, self.triangles = prepare_mesh(vertices, triangles)
        self.vertex_count = int(self.vertices.shape[0])
        self.triangle_count = int(self.triangles.shape[0])
        try:
            self._algorithm = _geodesic.PyGeodesicAlgorithmExact(
                self.vertices, self.triangles
            )
        except Exception as exc:  # noqa: BLE001
            raise SolverError(
                "pygeodesic solver construction failed: %s" % _describe(exc)
            )

    def distance_and_path(self, source_index, target_index):
        """Exact geodesic distance and the path polyline between two vertices.

        Returns ``(distance float, path ndarray (k,3) float64)`` with the
        polyline ordered **source -> target**. Distances are in whatever unit
        the vertex array is in; BSMT always passes solver-space millimetres,
        so the returned number is millimetres and is never rescaled
        downstream (sect. 6.2).
        """
        source = _check_index("source_index", source_index, self.vertex_count)
        target = _check_index("target_index", target_index, self.vertex_count)

        if source == target:
            # A -> A is exactly zero; no solver call and no rounding.
            return 0.0, np.asarray(
                [self.vertices[source], self.vertices[source]], dtype=np.float64
            )

        try:
            result = self._algorithm.geodesicDistance(source, target)
        except Exception as exc:  # noqa: BLE001
            raise SolverError(
                "pygeodesic raised during geodesicDistance(%d, %d): %s"
                % (source, target, _describe(exc))
            )

        if isinstance(result, tuple):
            if len(result) < 2:
                raise SolverError(
                    "pygeodesic returned a %d-tuple; expected (distance, path)"
                    % len(result)
                )
            distance, path = result[0], result[1]
        else:
            raise SolverError(
                "pygeodesic returned %s, not the (distance, path) tuple this "
                "wrapper was written against" % type(result).__name__
            )

        try:
            distance = float(distance)
        except Exception as exc:  # noqa: BLE001
            raise SolverError("distance is not a float: %s" % _describe(exc))
        if not np.isfinite(distance):
            raise SolverError("pygeodesic returned a non-finite distance (%r)" % distance)
        if distance < 0.0:
            raise SolverError("pygeodesic returned a negative distance (%r)" % distance)

        path = np.ascontiguousarray(np.asarray(path, dtype=np.float64))
        if path.ndim != 2 or path.shape[1] != 3 or path.shape[0] < 2:
            raise SolverError(
                "pygeodesic returned a path of shape %r; expected (k>=2, 3)"
                % (path.shape,)
            )
        if not np.all(np.isfinite(path)):
            raise SolverError("pygeodesic returned a path with non-finite points")

        return distance, orient_path(path, self.vertices[source], self.vertices[target])

    def distance(self, source_index, target_index):
        return self.distance_and_path(source_index, target_index)[0]


def orient_path(path, source_xyz, target_xyz):
    """Return the polyline ordered source -> target.

    pygeodesic's documented ordering is not relied on: the endpoints are
    compared against the actual source and target positions and the array is
    reversed if needed. An ordering assumption that silently reverses a path
    is a named Milestone 2.4 failure mode; deciding it from the data removes
    the assumption entirely.
    """
    head = float(np.linalg.norm(path[0] - source_xyz))
    tail = float(np.linalg.norm(path[-1] - source_xyz))
    if tail < head:
        return np.ascontiguousarray(path[::-1])
    return path


def path_length(path):
    """Sum of the segment lengths of a polyline. Pure geometry, no solver."""
    points = np.asarray(path, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or points.shape[0] < 2:
        raise InvalidMeshError(
            "path must have shape (k>=2, 3), got %r" % (points.shape,)
        )
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())


def compute_distance_and_path(vertices, triangles, source_index, target_index):
    """One-shot exact geodesic query. Convenience over ExactSolver.

    Raises BackendUnavailable / InvalidMeshError / SolverError. It never
    returns an approximate result in place of an exact one.
    """
    solver = ExactSolver(vertices, triangles)
    return solver.distance_and_path(source_index, target_index)
