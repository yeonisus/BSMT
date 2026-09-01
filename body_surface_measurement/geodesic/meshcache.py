"""Canonical computational mesh, cached per source object (Blender side).

There is exactly ONE canonical triangle array per source object. Topology
diagnostics, component labels, BVH picking, SurfacePoint triangle indices and
every future scratch-mesh or solver operation index that same array. No
translation between Blender polygon indices, loop-triangle indices, BVH
indices or preview indices exists anywhere, because the BVH is built directly
from the canonical arrays and therefore returns canonical indices.

The source object is never modified: geometry comes from a temporary
evaluated mesh (see extract.py) which is released immediately.

Picking deliberately happens in object-local coordinates rather than centred
solver millimetres. Barycentric coordinates are affine invariant, so a
triangle index plus barycentrics picked in local space is equally valid in
solver space - and a local BVH does not need rebuilding when the object is
merely translated or rotated.
"""

import time

import bpy
import numpy as np
from bpy.app.handlers import persistent
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ..measurement import unit_multiplier
from ..visualization import HELPER_FLAG
from .extract import ExtractionError, extract_solver_mesh
from .spaces import metric_key, to_solver_space
from .surface_point import barycentric, normalize_barycentric, reconstruct
from .topology import analyse, component_labels

_CACHE = {}


class CanonicalMesh(object):
    """One canonical computational representation of a source scan."""

    __slots__ = (
        "source_object",
        "geometry_hash",
        "metric_key",
        "vertices_local",        # (n,3) float64, untransformed mesh space
        "triangles",             # (m,3) int64  <- the canonical triangle array
        "vertices_solver",       # (n,3) float64, physical mm, centred
        "center_mm",
        "unit",
        "unit_multiplier",
        "matrix_world",          # (4,4) float64 the solver coords were built from
        "triangle_components",   # (m,) int64, 0-based
        "vertex_components",     # (n,) int64, 0-based, -1 when unused
        "component_triangle_counts",
        "component_vertex_counts",
        "topology",              # analyse() summary
        "bvh",                   # BVHTree over vertices_local + triangles
        "build_seconds",
        "fingerprint",
    )

    # -- canonical location evaluation ------------------------------------

    @property
    def triangle_count(self):
        return int(self.triangles.shape[0])

    @property
    def vertex_count(self):
        return int(self.vertices_local.shape[0])

    def triangle_corners_local(self, triangle_index):
        return self.vertices_local[self.triangles[triangle_index]]

    def triangle_corners_solver(self, triangle_index):
        return self.vertices_solver[self.triangles[triangle_index]]

    def barycentric_local(self, triangle_index, point_local):
        return barycentric(self.triangle_corners_local(triangle_index), point_local)

    def local_from(self, triangle_index, bary):
        return reconstruct(self.triangle_corners_local(triangle_index), bary)

    def solver_from(self, triangle_index, bary):
        """Physical millimetre position of a canonical location."""
        return reconstruct(self.triangle_corners_solver(triangle_index), bary)

    def world_from(self, triangle_index, bary, matrix_world=None):
        """World position, evaluated against the CURRENT object transform.

        Passing the live matrix is what lets a marker follow a translated or
        rotated scan without the stored point changing at all.
        """
        local = self.local_from(triangle_index, bary)
        matrix = self.matrix_world if matrix_world is None else np.asarray(
            matrix_world, dtype=np.float64
        )
        return matrix[:3, :3] @ local + matrix[:3, 3]

    def world_vertices(self, matrix_world=None):
        """All canonical vertices in world space, current transform applied."""
        matrix = self.matrix_world if matrix_world is None else np.asarray(
            matrix_world, dtype=np.float64
        )
        return self.vertices_local @ matrix[:3, :3].T + matrix[:3, 3]

    def component_of(self, triangle_index):
        """1-based component id, matching the component preview numbering."""
        if self.triangle_components.size == 0:
            return 0
        return int(self.triangle_components[triangle_index]) + 1

    # -- transform refresh -------------------------------------------------

    def refresh_transform(self, matrix_world, unit):
        """Recompute transform-dependent data without re-reading geometry.

        Geometry identity (geometry_hash, triangles, local vertices, BVH,
        components) is untouched: a transform cannot change any of them.
        """
        matrix = np.asarray(matrix_world, dtype=np.float64)
        multiplier = unit_multiplier(unit)
        if (
            np.array_equal(matrix, self.matrix_world)
            and unit == self.unit
        ):
            return False
        solver, center = to_solver_space(self.vertices_local, matrix, multiplier)
        self.vertices_solver = solver
        self.center_mm = center
        self.matrix_world = matrix
        self.unit = unit
        self.unit_multiplier = multiplier
        self.metric_key = metric_key(matrix[:3, :3], multiplier)
        return True


def _fingerprint(obj):
    """Cheap staleness signal that needs no mesh evaluation."""
    mesh = obj.data
    return (
        obj.name,
        getattr(mesh, "name", ""),
        len(mesh.vertices),
        len(mesh.polygons),
        len(obj.modifiers),
    )


def _build(context, obj, unit):
    started = time.perf_counter()
    solver_mesh = extract_solver_mesh(obj, context.evaluated_depsgraph_get(), unit)

    triangle_components, vertex_components, triangle_counts, vertex_counts = (
        component_labels(solver_mesh.vertices, solver_mesh.triangles)
    )
    summary = analyse(solver_mesh.vertices, solver_mesh.triangles)

    # The BVH is built from the canonical arrays themselves, in order, so the
    # index it returns IS the canonical triangle index. No mapping exists to
    # get wrong.
    bvh = BVHTree.FromPolygons(
        solver_mesh.vertices_local.tolist(),
        solver_mesh.triangles.tolist(),
        all_triangles=True,
    )

    mesh = CanonicalMesh()
    mesh.source_object = solver_mesh.object_name
    mesh.geometry_hash = solver_mesh.geometry_hash
    mesh.metric_key = solver_mesh.metric_key
    mesh.vertices_local = solver_mesh.vertices_local
    mesh.triangles = np.asarray(solver_mesh.triangles, dtype=np.int64)
    mesh.vertices_solver = solver_mesh.vertices
    mesh.center_mm = solver_mesh.center_mm
    mesh.unit = solver_mesh.unit
    mesh.unit_multiplier = solver_mesh.unit_multiplier
    mesh.matrix_world = solver_mesh.matrix_world
    mesh.triangle_components = triangle_components
    mesh.vertex_components = vertex_components
    mesh.component_triangle_counts = triangle_counts
    mesh.component_vertex_counts = vertex_counts
    mesh.topology = summary
    mesh.bvh = bvh
    mesh.fingerprint = _fingerprint(obj)
    mesh.build_seconds = time.perf_counter() - started
    return mesh


def get(context, obj, unit, rebuild=False):
    """Canonical mesh for `obj`, cached. Raises ExtractionError on failure."""
    if obj is None or obj.type != 'MESH':
        raise ExtractionError("select a mesh object")

    cached = _CACHE.get(obj.name)
    if (
        not rebuild
        and cached is not None
        and cached.fingerprint == _fingerprint(obj)
    ):
        cached.refresh_transform(np.array(obj.matrix_world, dtype=np.float64), unit)
        return cached

    mesh = _build(context, obj, unit)
    _CACHE[obj.name] = mesh
    return mesh


def peek(object_name):
    """Cached mesh for a name, or None. Never builds; safe inside draw()."""
    return _CACHE.get(object_name)


def invalidate(object_name=None):
    if object_name is None:
        _CACHE.clear()
    else:
        _CACHE.pop(object_name, None)


def ray_cast_local(canonical, matrix_world, origin_world, direction_world):
    """Cast a world-space ray against the canonical BVH in local coordinates.

    Returns (triangle_index, location_local, location_world, bary) or None.
    The returned index directly indexes canonical.triangles.
    """
    matrix = np.asarray(matrix_world, dtype=np.float64)
    linear = matrix[:3, :3]
    determinant = float(np.linalg.det(linear))
    if abs(determinant) < 1e-30:
        return None

    inverse = np.linalg.inv(linear)
    translation = matrix[:3, 3]

    origin_local = inverse @ (np.asarray(origin_world, dtype=np.float64) - translation)
    direction_local = inverse @ np.asarray(direction_world, dtype=np.float64)
    length = float(np.linalg.norm(direction_local))
    if length == 0.0:
        return None
    direction_local = direction_local / length

    hit = canonical.bvh.ray_cast(
        Vector(origin_local.tolist()), Vector(direction_local.tolist())
    )
    if hit is None or hit[0] is None:
        return None

    location_local = np.array(hit[0], dtype=np.float64)
    triangle_index = int(hit[2])
    if not 0 <= triangle_index < canonical.triangle_count:
        return None

    bary = canonical.barycentric_local(triangle_index, location_local)
    bary, ok, _deviation = normalize_barycentric(bary)
    if not ok:
        return None

    location_world = linear @ location_local + translation
    return triangle_index, location_local, location_world, bary


# ---------------------------------------------------------------------------
# cache invalidation on geometry change
# ---------------------------------------------------------------------------

def _is_helper_id(identifier):
    try:
        return bool(identifier.get(HELPER_FLAG, False))
    except Exception:                                # pragma: no cover
        return False


@persistent
def _on_depsgraph_update(scene, depsgraph=None):
    """Drop the cache when scan geometry changes. Transforms never invalidate.

    Updates originating from BSMT helper objects are ignored: moving a marker
    or retargeting the measurement curve must not throw away the canonical
    mesh, or every gizmo drag would trigger a full rebuild.
    """
    try:
        if depsgraph is None:
            return
        for update in depsgraph.updates:
            if not getattr(update, "is_updated_geometry", False):
                continue
            identifier = getattr(update.id, "original", update.id)
            if _is_helper_id(identifier):
                continue
            _CACHE.clear()
            return
    except Exception:                                # pragma: no cover
        # A handler must never break the user's Blender session.
        _CACHE.clear()


def _purge(handler_list):
    """Remove this handler, including stale copies left by Reload Scripts."""
    removed = 0
    for existing in list(handler_list):
        if existing is _on_depsgraph_update or (
            getattr(existing, "__name__", "") == _on_depsgraph_update.__name__
            and getattr(existing, "__module__", "").endswith("meshcache")
        ):
            handler_list.remove(existing)
            removed += 1
    return removed


def handler_count():
    """(exact instances, name matches) currently registered."""
    handlers = bpy.app.handlers.depsgraph_update_post
    exact = sum(1 for h in handlers if h is _on_depsgraph_update)
    named = sum(
        1 for h in handlers
        if getattr(h, "__name__", "") == _on_depsgraph_update.__name__
        and getattr(h, "__module__", "").endswith("meshcache")
    )
    return exact, named


def register_handlers():
    handlers = bpy.app.handlers.depsgraph_update_post
    _purge(handlers)
    handlers.append(_on_depsgraph_update)


def unregister_handlers():
    _purge(bpy.app.handlers.depsgraph_update_post)
    _CACHE.clear()
