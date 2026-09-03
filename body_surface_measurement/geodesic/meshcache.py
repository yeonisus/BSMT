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
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from ..measurement import unit_multiplier
from ..visualization import HELPER_FLAG
from .extract import ExtractionError, extract_solver_mesh
from .spaces import metric_key, to_solver_space
from .surface_point import barycentric, locate_hit, reconstruct
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


def is_current(obj):
    """Whether the cached mesh for `obj` still matches its cheap fingerprint.

    O(1): it compares object name, mesh name, vertex count, polygon count and
    modifier count - the same fingerprint `get()` uses to decide whether to
    rebuild. No geometry is read, so this is safe from a panel draw.

    What it CANNOT see is a pure vertex move, which changes no count. That
    case is covered by the depsgraph handler below, which drops the entry on
    any geometry update - verified: moving vertices and letting the depsgraph
    run leaves peek() returning None. This is the cheap backstop for the
    count-changing edits, not a substitute for that handler.
    """
    if obj is None:
        return False
    cached = _CACHE.get(obj.name)
    if cached is None:
        return False
    try:
        return cached.fingerprint == _fingerprint(obj)
    except Exception:                                # pragma: no cover
        return False


def peek_current(obj):
    """The cached mesh for `obj`, but only while it still describes it.

    The one lookup the UI should use. `peek()` answers "is there a cached
    report", which is not the same question as "may I show it": a report that
    no longer describes the object is worse than none, because it renders as
    a confident status line about geometry that has moved on.
    """
    if obj is None:
        return None
    return _CACHE.get(obj.name) if is_current(obj) else None


def invalidate(object_name=None):
    if object_name is None:
        _CACHE.clear()
    else:
        _CACHE.pop(object_name, None)


def _local_ray(matrix_world, origin_world, direction_world):
    """A world ray expressed in object-local coordinates, or None.

    One definition, because both picking and the overlay's occlusion test have
    to agree exactly about where a world ray lands in the local space the BVH
    is built in.
    """
    matrix = np.asarray(matrix_world, dtype=np.float64)
    linear = matrix[:3, :3]
    if abs(float(np.linalg.det(linear))) < 1e-30:
        return None

    inverse = np.linalg.inv(linear)
    translation = matrix[:3, 3]
    origin_local = inverse @ (np.asarray(origin_world, dtype=np.float64)
                              - translation)
    direction_local = inverse @ np.asarray(direction_world, dtype=np.float64)
    length = float(np.linalg.norm(direction_local))
    if length == 0.0:
        return None
    return linear, translation, origin_local, direction_local / length


def hit_distance_caster(canonical, matrix_world):
    """A reusable "how far to the first surface hit" query for one object.

    Returns a callable (origin_world, direction_world) -> distance-or-None, or
    None if the transform is not invertible.

    It exists so the matrix inverse is computed ONCE per object rather than
    once per ray. The landmark overlay casts one ray per landmark on every
    redraw, and inverting a 3x3 a hundred times a frame was measurably more
    expensive than the ray casts themselves.

    The distance is all it returns. A hit whose barycentric coordinates fail
    to normalise is still a real occluder, so this deliberately does not go
    through ray_cast_local's validation.
    """
    rows = np.asarray(matrix_world, dtype=np.float64)
    if abs(float(np.linalg.det(rows[:3, :3]))) < 1e-30:
        return None

    # mathutils, not numpy, for everything the ray touches. The BVH takes and
    # returns mathutils Vectors, so working in numpy would convert twice per
    # ray - measurably more than the ray cast itself once there are a hundred
    # landmarks and this runs on every redraw.
    linear = Matrix([list(row[:3]) for row in rows[:3]])
    inverse = linear.inverted()
    translation = Vector(rows[:3, 3].tolist())

    def cast(origin_world, direction_world):
        origin = Vector((float(origin_world[0]), float(origin_world[1]),
                         float(origin_world[2])))
        direction_local = inverse @ Vector(
            (float(direction_world[0]), float(direction_world[1]),
             float(direction_world[2])))
        if direction_local.length == 0.0:
            return None
        hit = canonical.bvh.ray_cast(inverse @ (origin - translation),
                                     direction_local.normalized())
        if hit is None or hit[0] is None:
            # A miss is a real answer. It also covers the tangent case: a
            # point stored in single precision can sit a fraction OUTSIDE the
            # surface, and a ray aimed at it along the normal at a silhouette
            # extremum can graze past. Reporting "nothing in the way" is the
            # right answer there - the landmark is being looked at head on.
            return None
        return float(((linear @ hit[0]) + translation - origin).length)

    return cast


def ray_hit_distance(canonical, matrix_world, origin_world, direction_world):
    """One-shot form of `hit_distance_caster`, for a single ray."""
    cast = hit_distance_caster(canonical, matrix_world)
    return None if cast is None else cast(origin_world, direction_world)


def ray_cast_local(canonical, matrix_world, origin_world, direction_world):
    """Cast a world-space ray against the canonical BVH in local coordinates.

    Returns (triangle_index, location_local, location_world, bary) or None.
    The returned index directly indexes canonical.triangles.
    """
    prepared = _local_ray(matrix_world, origin_world, direction_world)
    if prepared is None:
        return None
    linear, translation, origin_local, direction_local = prepared

    hit = canonical.bvh.ray_cast(
        Vector(origin_local.tolist()), Vector(direction_local.tolist())
    )
    if hit is None or hit[0] is None:
        return None

    location_local = np.array(hit[0], dtype=np.float64)
    triangle_index = int(hit[2])
    if not 0 <= triangle_index < canonical.triangle_count:
        return None

    # Milestone 3.12: the BVH hit is a float32 point, so it lies a few ulps
    # off the plane of the triangle the BVH named - and when the true
    # intersection is on a shared edge, a few ulps OUTSIDE it. locate_hit
    # projects it onto that triangle's own plane and seats it, refusing
    # anything that would have to move further than float32 noise. The
    # triangle index is never reconsidered: it is the BVH's answer.
    seated = locate_hit(canonical.triangle_corners_local(triangle_index),
                        location_local,
                        ray_scale=float(np.abs(origin_local).max()))
    if not seated["ok"]:
        return None

    # The stored position is the reconstruction of the accepted coordinates,
    # so triangle + barycentric and the reported XYZ describe the same point
    # by construction rather than to within the hit's noise.
    location_local = seated["point"]
    location_world = linear @ location_local + translation
    return triangle_index, location_local, location_world, seated["bary"]


# ---------------------------------------------------------------------------
# cache invalidation on geometry change
# ---------------------------------------------------------------------------

def _is_helper_id(identifier):
    try:
        return bool(identifier.get(HELPER_FLAG, False))
    except Exception:                                # pragma: no cover
        return False


def _cached_names_for(identifier):
    """Which cache entries a changed datablock invalidates.

    An Object invalidates its own entry. A Mesh invalidates every cached
    object that uses it - the same mesh can be shared, and a cache keyed by
    object name would otherwise keep serving edited geometry.
    """
    if isinstance(identifier, bpy.types.Object):
        return [identifier.name] if identifier.name in _CACHE else []
    names = []
    for name in list(_CACHE):
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.data is identifier:
            names.append(name)
    return names


def _mark_paths_stale(scene, object_name):
    """Tell the measurements solved on this scan that their path is stale.

    Property writes only - no geometry read, no canonical mesh, no solver.
    Nothing is recomputed and nothing is deleted: a stale path is reported as
    stale and re-solved only if the researcher asks for it (sect. 7, sect. 9).
    """
    try:
        from .. import state
        # Writes must land on the ORIGINAL scene, never on an evaluated copy.
        original = getattr(scene, "original", None) or scene
        return state.mark_paths_stale_for_object(
            original, object_name, "the mesh geometry changed"
        )
    except Exception:                                # pragma: no cover
        return 0


@persistent
def _on_depsgraph_update(scene, depsgraph=None):
    """Drop the cache when scan geometry changes. Transforms never invalidate.

    Updates originating from BSMT helper objects are ignored: moving a marker
    or retargeting the measurement curve must not throw away the canonical
    mesh, or every gizmo drag would trigger a full rebuild. Helper flagged
    datablocks include the per-measurement path caches, so writing one can
    never invalidate the mesh it was solved on.
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
            # Only an Object or a Mesh datablock can mean the scan's geometry
            # changed. Scene and Collection datablocks raise
            # is_updated_geometry whenever their MEMBERSHIP changes, which
            # happens every time BSMT links or unlinks a helper - measured on
            # Blender 4.5.13: creating one marker reports geometry updates on
            # 'Scene Collection' and 'Collection', neither of them helper
            # tagged. Clearing on those threw the canonical mesh away every
            # time a marker, line or path appeared, forcing a full rebuild
            # (about a second on a 314k-triangle scan) and leaving peek()
            # returning None so transform-dependent checks silently skipped.
            # A real mesh edit always reports on the Object and its Mesh.
            if not isinstance(identifier, (bpy.types.Object, bpy.types.Mesh)):
                continue
            # Targeted rather than a sweep: editing one object must not cost
            # a full canonical rebuild of every other scan in the file, which
            # is seconds apiece at scan density.
            names = _cached_names_for(identifier)
            for name in names:
                _CACHE.pop(name, None)
                _mark_paths_stale(scene, name)
            if not names:
                # A changed datablock nothing has cached. Nothing to drop,
                # and nothing to mark: no measurement was solved against it.
                continue
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
