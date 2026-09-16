"""Authoritative surface-path cache (Milestone 3.14; regions 3.31).

Two kinds of owner, one storage mechanism: a MEASUREMENT's path, keyed by its
stable id, and a SURFACE REGION's boundary segments, keyed by the region and
the segment's position in the loop. They share the code below and nothing
else - separate name spaces, separate lifetimes, separate cleanup - because a
region owns its boundary outright and must not depend on a measurement
existing (sect. 8, Milestone 3.31).

The problem this fixes
----------------------
Until 0.19.0 a computed geodesic polyline existed in exactly one place: the
points of the helper Curve that drew it. That made the DISPLAY the storage,
and it had a consequence that only shows up on a large scan - every route
that removed the helper silently destroyed a solve that had cost tens of
seconds (minutes at 1M+ triangles), and the only way to get it back was to
run the unbounded solver again. Clearing the viewport, or any check that
could not find the helper object by name, was therefore indistinguishable
from "this path was never computed".

The polyline now lives here instead, in a datablock of its own, and the
helper Curve is rebuilt FROM it. Removing, hiding or restyling a helper can
no longer cost a solve, because the helper is not where anything is kept.

Storage
-------
One Mesh datablock per measurement, ``BSMT_PathCache_<stable id>``, with a
fake user so it is saved in the .blend and survives having no object. It
holds ``2 * k`` vertices:

    [0 .. k)      the polyline, in the SCAN OBJECT'S LOCAL SPACE
    [k .. 2k)     the unit surface normal at each of those points

Local space, not world, is what makes a rigid transform free: translating or
rotating the scan cannot change a local coordinate, so the cached path stays
valid and the helper simply follows by matrix (sect. 11). Storing world
coordinates as the authoritative copy would make every move a re-solve.

The normals are cached with the points on purpose. The drawn path is lifted
off the surface by a multiple of its own thickness, and finding those normals
means one BVH query per point - 138 ms for 20,000 points on a 261k-triangle
scan, measured. Doing it once at solve time turns every later thickness or
offset change into one vectorised multiply-add.

Precision: mesh vertex coordinates are float32. That is deliberate and
sufficient - this is DISPLAY geometry, and the Curve it feeds is float32
anyway. The measurement itself is the stored ``path_length_mm`` /
``path_distance_mm``, which are never re-derived from these points.
"""

import numpy as np

import bpy

from .visualization import HELPER_FLAG

#: Datablock name prefix. Anything with this prefix AND the helper flag is
#: ours; nothing else is ever touched.
CACHE_PREFIX = "BSMT_PathCache_"

#: Surface Regions keep their boundary segments here too, in a SEPARATE name
#: space (Milestone 3.31). The storage problem is identical - a solved
#: polyline that must survive the helper being deleted and the file being
#: reloaded - so the mechanics below are shared rather than reimplemented.
#: The key spaces must not be: a region segment is identified by its region
#: and its position in the loop, which is nothing to do with a measurement's
#: stable id, and one prefix for both would let `keep_only` delete the other
#: kind's caches.
REGION_PREFIX = "BSMT_RegionPath_"

#: Bumped on every store. A drawn helper records the generation it was built
#: from, so "is this drawing still the cached path" is one integer compare
#: instead of rewriting thousands of curve points to find out.
GENERATION_KEY = "bsmt_path_generation"


def cache_name(stable_id):
    """The datablock name for one MEASUREMENT's cached path."""
    return "%s%06d" % (CACHE_PREFIX, int(stable_id))


def region_cache_name(region_stable_id, position):
    """The datablock name for one REGION BOUNDARY SEGMENT's cached path.

    Keyed by (region, position in the loop) because that is what a boundary
    segment IS: the region owns its own solved geometry, and no measurement
    has to exist for a region to have a boundary (sect. 8 of the brief).
    """
    return "%s%06d_%03d" % (REGION_PREFIX, int(region_stable_id),
                            int(position))


def _is_cache(mesh):
    if mesh is None:
        return False
    try:
        return bool(mesh.get(HELPER_FLAG, False))
    except Exception:                                 # pragma: no cover
        return False


def _existing_named(name):
    mesh = bpy.data.meshes.get(name)
    return mesh if _is_cache(mesh) else None


def _existing(stable_id):
    return _existing_named(cache_name(stable_id))


def store_named(name, points_local, normals_local=None):
    """Write one measurement's polyline (and normals) into its cache.

    `points_local` is (k, 3) in the scan's local space. `normals_local` may be
    None, in which case zero normals are stored and the display lift falls
    back to sampling the surface.

    Returns the number of points stored, or 0 when there was nothing to store.
    """
    points = np.asarray(points_local, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or points.shape[0] < 2:
        return 0
    count = int(points.shape[0])

    if normals_local is None:
        normals = np.zeros((count, 3), dtype=np.float64)
    else:
        normals = np.asarray(normals_local, dtype=np.float64)
        if normals.shape != points.shape:
            normals = np.zeros((count, 3), dtype=np.float64)

    mesh = _existing_named(name)
    if mesh is None:
        mesh = bpy.data.meshes.new(name)
        mesh[HELPER_FLAG] = True
        mesh[GENERATION_KEY] = 0
    else:
        mesh.clear_geometry()
    mesh[GENERATION_KEY] = int(mesh.get(GENERATION_KEY, 0)) + 1
    # A fake user is what keeps a datablock with no object alive across a
    # save/reload. Without it Blender would garbage-collect the cache on the
    # next file write and the next display would cost a full solve.
    mesh.use_fake_user = True

    mesh.vertices.add(count * 2)
    flat = np.empty(count * 6, dtype=np.float32)
    flat[:count * 3] = points.reshape(-1).astype(np.float32)
    flat[count * 3:] = normals.reshape(-1).astype(np.float32)
    mesh.vertices.foreach_set("co", flat)
    mesh.update()
    return count


def store(stable_id, points_local, normals_local=None):
    """Write one MEASUREMENT's polyline (and normals) into its cache."""
    return store_named(cache_name(stable_id), points_local, normals_local)


def load_named(name):
    """(points_local, normals_local) for one cache entry, or None.

    Both arrays are float64 copies owned by the caller. Nothing native is
    retained: the values are read out of Blender in one ``foreach_get`` and
    the mesh is not referenced again.
    """
    mesh = _existing_named(name)
    if mesh is None:
        return None
    total = len(mesh.vertices)
    if total < 4 or total % 2:
        return None
    count = total // 2
    flat = np.empty(total * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", flat)
    values = flat.astype(np.float64).reshape(total, 3)
    return values[:count].copy(), values[count:].copy()


def load(stable_id):
    """(points_local, normals_local) for one MEASUREMENT, or None."""
    return load_named(cache_name(stable_id))


def point_count_named(name):
    """How many polyline points are cached, without reading them."""
    mesh = _existing_named(name)
    if mesh is None:
        return 0
    total = len(mesh.vertices)
    return total // 2 if total >= 4 and not total % 2 else 0


def point_count(stable_id):
    """How many polyline points one MEASUREMENT has cached."""
    return point_count_named(cache_name(stable_id))


def exists_named(name):
    return point_count_named(name) >= 2


def exists(stable_id):
    return point_count(stable_id) >= 2


def generation(stable_id):
    """Which revision of this cache entry is current. 0 when there is none."""
    mesh = _existing(stable_id)
    return int(mesh.get(GENERATION_KEY, 0)) if mesh is not None else 0


def drop_named(name):
    """Delete one cached polyline. Returns whether there was one."""
    mesh = _existing_named(name)
    if mesh is None:
        return False
    mesh.use_fake_user = False
    bpy.data.meshes.remove(mesh)
    return True


def drop(stable_id):
    """Delete one measurement's cached polyline. Returns whether there was one."""
    return drop_named(cache_name(stable_id))


# ---------------------------------------------------------------------------
# region boundary segments - the same storage, its own name space
# ---------------------------------------------------------------------------

def store_region_segment(region_stable_id, position, points_local,
                         normals_local=None):
    return store_named(region_cache_name(region_stable_id, position),
                       points_local, normals_local)


def load_region_segment(region_stable_id, position):
    return load_named(region_cache_name(region_stable_id, position))


def region_segment_exists(region_stable_id, position):
    return exists_named(region_cache_name(region_stable_id, position))


def region_segment_points(region_stable_id, position):
    return point_count_named(region_cache_name(region_stable_id, position))


def region_cache_meshes():
    """Every region-boundary cache datablock currently in the file."""
    return [mesh for mesh in bpy.data.meshes
            if mesh.name.startswith(REGION_PREFIX) and _is_cache(mesh)]


def drop_region(region_stable_id):
    """Delete every cached boundary segment of one region. Returns the count."""
    prefix = "%s%06d_" % (REGION_PREFIX, int(region_stable_id))
    removed = 0
    for mesh in region_cache_meshes():
        if mesh.name.startswith(prefix):
            mesh.use_fake_user = False
            bpy.data.meshes.remove(mesh)
            removed += 1
    return removed


def keep_only_regions(pairs):
    """Delete region caches outside `pairs` of (region stable id, count).

    A region that shrinks from six boundary landmarks to four must not leave
    segments 5 and 6 behind: they would be orphaned geometry that a later
    six-landmark definition could pick up as if it had been solved for it.
    """
    wanted = set()
    for stable_id, count in pairs:
        for position in range(int(count)):
            wanted.add(region_cache_name(stable_id, position))
    removed = 0
    for mesh in region_cache_meshes():
        if mesh.name not in wanted:
            mesh.use_fake_user = False
            bpy.data.meshes.remove(mesh)
            removed += 1
    return removed


def cache_meshes():
    """Every path-cache datablock currently in the file."""
    return [mesh for mesh in bpy.data.meshes
            if mesh.name.startswith(CACHE_PREFIX) and _is_cache(mesh)]


def drop_all():
    """Delete every cached polyline. Returns how many were removed."""
    removed = 0
    for mesh in cache_meshes():
        mesh.use_fake_user = False
        bpy.data.meshes.remove(mesh)
        removed += 1
    return removed


def keep_only(stable_ids):
    """Delete caches whose measurement no longer exists. Returns the count."""
    wanted = {cache_name(value) for value in stable_ids}
    removed = 0
    for mesh in cache_meshes():
        if mesh.name not in wanted:
            mesh.use_fake_user = False
            bpy.data.meshes.remove(mesh)
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# display lift
# ---------------------------------------------------------------------------

def lift(points_local, normals_local, distance):
    """Offset points along their cached normals. Pure numpy, no geometry read.

    This is the whole reason normals are cached: changing the drawn path's
    thickness re-lifts thousands of points with one vectorised operation
    instead of one BVH query each. A point whose normal was never resolved
    (zero length) stays exactly where it is rather than being guessed at.
    """
    points = np.asarray(points_local, dtype=np.float64)
    if points.size == 0 or float(distance) <= 0.0:
        return points.copy()
    normals = np.asarray(normals_local, dtype=np.float64)
    if normals.shape != points.shape:
        return points.copy()
    lengths = np.linalg.norm(normals, axis=1)
    usable = lengths > 0.0
    if not np.any(usable):
        return points.copy()
    lifted = points.copy()
    unit = normals[usable] / lengths[usable][:, None]
    lifted[usable] = points[usable] + unit * float(distance)
    return lifted
