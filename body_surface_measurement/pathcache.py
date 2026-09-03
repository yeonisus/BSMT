"""Authoritative per-measurement surface-path cache (Milestone 3.14).

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

#: Bumped on every store. A drawn helper records the generation it was built
#: from, so "is this drawing still the cached path" is one integer compare
#: instead of rewriting thousands of curve points to find out.
GENERATION_KEY = "bsmt_path_generation"


def cache_name(stable_id):
    return "%s%06d" % (CACHE_PREFIX, int(stable_id))


def _is_cache(mesh):
    if mesh is None:
        return False
    try:
        return bool(mesh.get(HELPER_FLAG, False))
    except Exception:                                 # pragma: no cover
        return False


def _existing(stable_id):
    mesh = bpy.data.meshes.get(cache_name(stable_id))
    return mesh if _is_cache(mesh) else None


def store(stable_id, points_local, normals_local=None):
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

    mesh = _existing(stable_id)
    if mesh is None:
        mesh = bpy.data.meshes.new(cache_name(stable_id))
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


def load(stable_id):
    """(points_local, normals_local) for one measurement, or None.

    Both arrays are float64 copies owned by the caller. Nothing native is
    retained: the values are read out of Blender in one ``foreach_get`` and
    the mesh is not referenced again.
    """
    mesh = _existing(stable_id)
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


def point_count(stable_id):
    """How many polyline points are cached, without reading them."""
    mesh = _existing(stable_id)
    if mesh is None:
        return 0
    total = len(mesh.vertices)
    return total // 2 if total >= 4 and not total % 2 else 0


def exists(stable_id):
    return point_count(stable_id) >= 2


def generation(stable_id):
    """Which revision of this cache entry is current. 0 when there is none."""
    mesh = _existing(stable_id)
    return int(mesh.get(GENERATION_KEY, 0)) if mesh is not None else 0


def drop(stable_id):
    """Delete one measurement's cached polyline. Returns whether there was one."""
    mesh = _existing(stable_id)
    if mesh is None:
        return False
    mesh.use_fake_user = False
    bpy.data.meshes.remove(mesh)
    return True


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
