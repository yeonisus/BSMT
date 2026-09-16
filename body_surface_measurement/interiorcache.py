"""Authoritative Surface Interior classification cache (Milestone 3.33).

What is kept, and why it is kept in full
----------------------------------------
`interior.compute` classifies every triangle as wholly inside, wholly
outside, or CUT - and clips the cut ones exactly. Until this module existed,
the only thing that survived that analysis was the fill helper mesh: the
right faces, drawn, but in Blender's float32 vertex storage and with no link
back to the parent triangle a clipped piece came from.

That is enough to draw. It is not enough to MEASURE:

* float32 is about seven digits, so an area summed from it inherits that -
  and the per-triangle tiling the analysis guarantees to 3.6e-07 would be
  checked against numbers coarser than the guarantee;
* without the parent triangle, "this piece is at most as big as the triangle
  it came from" cannot be asked at all, and that is the check that catches a
  clipping error which happens to look plausible.

So the classification itself is stored, in float64, next to the drawing that
is made from it. One analysis, two consumers, no second algorithm.

Storage
-------
One Mesh datablock per region, ``BSMT_RegionInterior_<stable id>``, with a
fake user so it is saved in the .blend and survives having no object - the
same mechanism and the same reasoning as ``pathcache``. The payload rides in
custom properties rather than in vertices, because MESH VERTICES ARE FLOAT32
and the whole point of this cache is that the classification is not.

    full      int64   [t0, t1, ...]        wholly-interior triangle indices
    parents   int64   [t, t, ...]          parent triangle of each cut piece
    counts    int64   [k, k, ...]          corners in each cut piece
    bary      float64 (sum(counts), 3)     barycentric corners, in order

Barycentric, not coordinates. A piece's shape is a property of its parent
triangle, so storing it that way keeps it tied to that surface: the polygon
can be rebuilt in ANY space - object-local to draw in, physical millimetres
to measure in - by multiplying against that triangle's corners in that space.
Storing millimetres instead would have baked in one transform and one unit.
"""

import base64

import numpy as np

import bpy

from .visualization import HELPER_FLAG

CACHE_PREFIX = "BSMT_RegionInterior_"

KEY_FULL = "bsmt_interior_full"
KEY_PARENTS = "bsmt_interior_parents"
KEY_COUNTS = "bsmt_interior_counts"
KEY_BARY = "bsmt_interior_bary"
KEY_SIDE = "bsmt_interior_side"
KEY_GEOMETRY = "bsmt_interior_geometry"


def cache_name(region_stable_id):
    return "%s%06d" % (CACHE_PREFIX, int(region_stable_id))


def _is_cache(mesh):
    if mesh is None:
        return False
    try:
        return bool(mesh.get(HELPER_FLAG, False))
    except Exception:                                 # pragma: no cover
        return False


def _existing(region_stable_id):
    mesh = bpy.data.meshes.get(cache_name(region_stable_id))
    return mesh if _is_cache(mesh) else None


def _pack(values, dtype):
    array = np.ascontiguousarray(np.asarray(values, dtype=dtype))
    return base64.b64encode(array.tobytes()).decode("ascii")


def _unpack(text, dtype, width=None):
    if not text:
        return np.zeros((0, width) if width else 0, dtype=dtype)
    raw = np.frombuffer(base64.b64decode(text.encode("ascii")), dtype=dtype)
    return raw.reshape(-1, width).copy() if width else raw.copy()


def store(region_stable_id, full_triangles, pieces, side, geometry_hash):
    """Write one region's classification. Returns the piece count.

    `pieces` is [(parent triangle, (k,3) barycentric corners), ...] in the
    order the analysis produced them, so a reload reconstructs exactly the
    same faces in exactly the same order.
    """
    full = np.asarray(list(full_triangles), dtype=np.int64)
    parents = np.asarray([int(parent) for parent, _bary in pieces],
                         dtype=np.int64)
    counts = np.asarray([int(np.asarray(bary).shape[0])
                         for _parent, bary in pieces], dtype=np.int64)
    if len(pieces):
        bary = np.vstack([np.asarray(value, dtype=np.float64)
                          for _parent, value in pieces])
    else:
        bary = np.zeros((0, 3), dtype=np.float64)

    mesh = _existing(region_stable_id)
    if mesh is None:
        mesh = bpy.data.meshes.new(cache_name(region_stable_id))
        mesh[HELPER_FLAG] = True
    mesh.use_fake_user = True
    mesh[KEY_FULL] = _pack(full, np.int64)
    mesh[KEY_PARENTS] = _pack(parents, np.int64)
    mesh[KEY_COUNTS] = _pack(counts, np.int64)
    mesh[KEY_BARY] = _pack(bary, np.float64)
    mesh[KEY_SIDE] = str(side)
    mesh[KEY_GEOMETRY] = str(geometry_hash)
    return int(counts.shape[0])


def load(region_stable_id):
    """The stored classification, or None. float64, exactly as written."""
    mesh = _existing(region_stable_id)
    if mesh is None:
        return None
    try:
        full = _unpack(mesh.get(KEY_FULL, ""), np.int64)
        parents = _unpack(mesh.get(KEY_PARENTS, ""), np.int64)
        counts = _unpack(mesh.get(KEY_COUNTS, ""), np.int64)
        bary = _unpack(mesh.get(KEY_BARY, ""), np.float64, width=3)
    except Exception:                                 # pragma: no cover
        return None
    if counts.shape[0] != parents.shape[0]:
        return None
    if int(counts.sum()) != bary.shape[0]:
        return None
    pieces = []
    offset = 0
    for index in range(counts.shape[0]):
        length = int(counts[index])
        pieces.append((int(parents[index]), bary[offset:offset + length]))
        offset += length
    return {
        "full_triangles": full,
        "pieces": pieces,
        "side": str(mesh.get(KEY_SIDE, "")),
        "geometry_hash": str(mesh.get(KEY_GEOMETRY, "")),
    }


def exists(region_stable_id):
    return load(region_stable_id) is not None


def drop(region_stable_id):
    mesh = _existing(region_stable_id)
    if mesh is None:
        return False
    mesh.use_fake_user = False
    bpy.data.meshes.remove(mesh)
    return True


def cache_meshes():
    return [mesh for mesh in bpy.data.meshes
            if mesh.name.startswith(CACHE_PREFIX) and _is_cache(mesh)]


def keep_only(stable_ids):
    """Drop classifications whose region is gone from this file."""
    wanted = {cache_name(value) for value in stable_ids}
    removed = 0
    for mesh in cache_meshes():
        if mesh.name not in wanted:
            mesh.use_fake_user = False
            bpy.data.meshes.remove(mesh)
            removed += 1
    return removed


def drop_all():
    removed = 0
    for mesh in cache_meshes():
        mesh.use_fake_user = False
        bpy.data.meshes.remove(mesh)
        removed += 1
    return removed
