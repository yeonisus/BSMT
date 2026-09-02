"""Controlled mesh repair on a measurement copy (Blender side).

Every operation here is explicit, scoped to a region the researcher chose, and
reversible. There is no global cleanup path: no automatic merge-by-distance,
no fill-every-hole, no "make it manifold" button. On a human scan those fuse
anatomically distinct surfaces that happen to touch and produce a confidently
wrong, systematically short geodesic.

The source scan is never touched. Every operator that reaches this module has
already refused to run on anything but a generated measurement copy.

Each edit is bracketed by a mesh-datablock backup, so a repair can be undone
even if Blender's undo stack has been disturbed by a script.
"""

import bmesh
import bpy
import numpy as np

from . import preprocess, scancopy

BACKUP_SUFFIX = "_BSMT_backup"


class RepairAborted(Exception):
    """A repair could not be completed. The mesh is left as it was."""


# ---------------------------------------------------------------------------
# backup / restore
# ---------------------------------------------------------------------------

def make_backup(obj):
    """Copy the current mesh datablock and return its name.

    Kept as a real datablock rather than a serialised blob so restoring is a
    single assignment and cannot half-succeed.
    """
    previous = bpy.data.meshes.get(obj.data.name + BACKUP_SUFFIX)
    if previous is not None and previous.users == 0:
        bpy.data.meshes.remove(previous)
    backup = obj.data.copy()
    backup.name = obj.data.name + BACKUP_SUFFIX
    backup.use_fake_user = True          # survive a file save/reload
    return backup.name


def restore_backup(obj, backup_name):
    """Put a backed-up mesh back on the object. Returns True on success."""
    backup = bpy.data.meshes.get(backup_name)
    if backup is None:
        return False
    current = obj.data
    obj.data = backup.copy()
    obj.data.name = current.name
    if current.users == 0:
        bpy.data.meshes.remove(current)
    return True


def discard_backup(backup_name):
    backup = bpy.data.meshes.get(backup_name)
    if backup is None:
        return False
    backup.use_fake_user = False
    if backup.users == 0:
        bpy.data.meshes.remove(backup)
    return True


# ---------------------------------------------------------------------------
# bmesh helpers
# ---------------------------------------------------------------------------

def _open(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    return bm


def _commit(obj, bm):
    """Write a bmesh back, keeping every custom data layer.

    ``to_mesh`` preserves UV and colour layers, so the texture survives; this
    is verified independently after every repair.
    """
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _triangulate(bm, faces):
    """Triangulate specific faces. The solver only ever sees triangles."""
    ngons = [face for face in faces if len(face.verts) > 3]
    if ngons:
        bmesh.ops.triangulate(bm, faces=ngons, quad_method='BEAUTY',
                              ngon_method='BEAUTY')


def _edge_key(edge):
    a, b = edge.verts[0].index, edge.verts[1].index
    return (a, b) if a < b else (b, a)


# ---------------------------------------------------------------------------
# repairs
# ---------------------------------------------------------------------------

def fill_boundary_loop(obj, loop_edge_pairs):
    """Fill ONE boundary loop and triangulate the result.

    `loop_edge_pairs` is the (n, 2) vertex-index array of that loop's edges, as
    reported by ``repair.boundary_loops``. Only those edges are filled - no
    other boundary in the mesh is touched.
    """
    wanted = {(int(a), int(b)) if a < b else (int(b), int(a))
              for a, b in np.asarray(loop_edge_pairs, dtype=np.int64)}
    if not wanted:
        raise RepairAborted("that boundary loop has no edges")

    bm = _open(obj)
    try:
        edges = [edge for edge in bm.edges if _edge_key(edge) in wanted]
        if not edges:
            raise RepairAborted(
                "the boundary loop no longer matches this mesh - re-run the "
                "diagnostics before filling"
            )
        # Every edge must currently be a real boundary. Filling across an
        # edge that already carries two faces adds a third and manufactures
        # the very non-manifold defect this is meant to remove - which is
        # what an unchecked fill did on the real scan, adding four faces
        # while the non-manifold count only fell from 7 to 4.
        occupied = [edge for edge in edges if len(edge.link_faces) != 1]
        if occupied:
            raise RepairAborted(
                "%d edge(s) of that loop already carry %s face(s); filling "
                "would create non-manifold topology"
                % (len(occupied),
                   "/".join(sorted({str(len(edge.link_faces))
                                    for edge in occupied})))
            )
        before = set(bm.faces)
        result = bmesh.ops.holes_fill(bm, edges=edges, sides=0)
        created = [face for face in result.get("faces", [])
                   if face.is_valid and face not in before]
        if not created:
            # Fall back to a triangle fan, which copes with a loop that
            # holes_fill declines (a non-planar or self-touching boundary).
            result = bmesh.ops.triangle_fill(bm, edges=edges, use_beauty=True)
            created = [face for face in result.get("geom", [])
                       if isinstance(face, bmesh.types.BMFace)]
        if not created:
            raise RepairAborted(
                "Blender could not fill that boundary loop. It may be "
                "self-intersecting or non-planar; manual cleanup is required."
            )
        filled = len(created)
        # Triangulate by PROPERTY, not by tracking the created faces: a
        # BMFace reference taken before a topology-changing op is not
        # reliable afterwards, and a set-membership test against stale
        # references silently left ngons behind. A measurement copy of a scan
        # is already all triangles, so "every face with more than three
        # verts" is exactly the faces the fill just made.
        bm.faces.ensure_lookup_table()
        ngons = [face for face in bm.faces if len(face.verts) > 3]
        if ngons:
            bmesh.ops.triangulate(bm, faces=ngons, quad_method='BEAUTY',
                                  ngon_method='BEAUTY')
            bm.faces.ensure_lookup_table()
        remaining = [face for face in bm.faces if len(face.verts) > 3]
        if remaining:
            raise RepairAborted(
                "%d face(s) could not be triangulated after the fill"
                % len(remaining)
            )
        _commit(obj, bm)
    except RepairAborted:
        bm.free()
        raise
    except Exception as exc:                          # noqa: BLE001
        bm.free()
        raise RepairAborted("hole fill failed: %s: %s"
                            % (type(exc).__name__, exc))
    return filled


def remove_component(obj, vertex_indices):
    """Delete one connected component, named by its vertex indices."""
    wanted = set(int(index) for index in vertex_indices)
    if not wanted:
        raise RepairAborted("that component has no vertices")

    bm = _open(obj)
    try:
        if len(wanted) >= len(bm.verts):
            raise RepairAborted(
                "that component is the whole mesh - removing it would leave "
                "nothing to measure"
            )
        victims = [vert for vert in bm.verts if vert.index in wanted]
        if not victims:
            raise RepairAborted(
                "the component no longer matches this mesh - re-run the "
                "diagnostics before removing it"
            )
        removed = len(victims)
        bmesh.ops.delete(bm, geom=victims, context='VERTS')
        _commit(obj, bm)
    except RepairAborted:
        bm.free()
        raise
    except Exception as exc:                          # noqa: BLE001
        bm.free()
        raise RepairAborted("component removal failed: %s: %s"
                            % (type(exc).__name__, exc))
    return removed


def remove_duplicate_faces(obj, duplicate_indices):
    """Delete faces that repeat another face's vertex set.

    The safest non-manifold repair there is: a duplicated face adds no
    surface, only a second copy of one already present, so removing it cannot
    move any anatomy.
    """
    wanted = set(int(index) for index in duplicate_indices)
    if not wanted:
        return 0
    bm = _open(obj)
    try:
        victims = [face for face in bm.faces if face.index in wanted]
        if not victims:
            return 0
        removed = len(victims)
        bmesh.ops.delete(bm, geom=victims, context='FACES_ONLY')
        _commit(obj, bm)
    except Exception as exc:                          # noqa: BLE001
        bm.free()
        raise RepairAborted("duplicate face removal failed: %s: %s"
                            % (type(exc).__name__, exc))
    return removed


def weld_non_manifold_region(obj, non_manifold_edges, distance):
    """Merge coincident vertices ONLY at the non-manifold edges.

    This is emphatically NOT a global merge-by-distance. The vertex set is the
    endpoints of the reported non-manifold edges and nothing else, so a weld
    cannot reach across a gap between an arm and a torso somewhere else in the
    scan. The tolerance is the researcher's, and the number merged is reported.
    """
    pairs = np.asarray(non_manifold_edges, dtype=np.int64)
    if pairs.size == 0:
        return 0
    wanted = set(int(value) for value in pairs.ravel())

    bm = _open(obj)
    try:
        targets = [vert for vert in bm.verts if vert.index in wanted]
        if not targets:
            return 0
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=targets, dist=float(distance))
        bm.verts.ensure_lookup_table()
        merged = before - len(bm.verts)
        _commit(obj, bm)
    except Exception as exc:                          # noqa: BLE001
        bm.free()
        raise RepairAborted("local weld failed: %s: %s"
                            % (type(exc).__name__, exc))
    return merged


def remove_faces_by_vertex_sets(obj, wanted_counts):
    """Delete faces addressed by their VERTEX SET, not by index.

    Index-mapping-free on purpose. The repair plan is computed on the
    canonical triangle array, and assuming canonical triangle *i* is mesh
    polygon *i* is exactly the class of silent mis-indexing PROJECT_SPEC
    sect. 7.7 warns about. A face's sorted vertex tuple identifies it without
    any mapping at all.

    `wanted_counts` maps a sorted vertex tuple to how many faces with that
    tuple to remove, so a duplicated face can have one copy removed and the
    other kept.
    """
    remaining = dict(wanted_counts)
    if not remaining:
        return 0
    bm = _open(obj)
    try:
        victims = []
        for face in bm.faces:
            key = tuple(sorted(vert.index for vert in face.verts))
            if remaining.get(key, 0) > 0:
                victims.append(face)
                remaining[key] -= 1
        if not victims:
            raise RepairAborted(
                "the faces to remove no longer match this mesh - re-analyse"
            )
        removed = len(victims)
        # 'FACES' - not 'FACES_ONLY' - so a vertex or edge used ONLY by a
        # removed face goes with it. A fin's apex is exactly that, and
        # leaving it behind strands a loose vertex and two wire edges that
        # then confuse the boundary analysis. Anything still referenced by a
        # surviving face is untouched.
        bmesh.ops.delete(bm, geom=victims, context='FACES')
        _commit(obj, bm)
    except RepairAborted:
        bm.free()
        raise
    except Exception as exc:                          # noqa: BLE001
        bm.free()
        raise RepairAborted("face removal failed: %s: %s"
                            % (type(exc).__name__, exc))
    return removed


def fill_small_loops(obj, loops):
    """Fill a set of small closed boundary loops. Returns faces created.

    Each loop is filled independently and triangulated; a loop Blender
    declines is skipped rather than forced, and the rest still proceed.
    """
    created_total = 0
    filled_loops = 0
    skipped = []
    for loop in loops:
        try:
            created_total += fill_boundary_loop(obj, loop["edges"])
            filled_loops += 1
        except RepairAborted as exc:
            skipped.append("loop %s: %s" % (loop.get("loop_id", "?"), exc))
    return created_total, filled_loops, skipped


def triangulate_all(obj):
    """Ensure the mesh is pure triangles. Returns how many faces were split."""
    bm = _open(obj)
    try:
        ngons = [face for face in bm.faces if len(face.verts) > 3]
        if not ngons:
            bm.free()
            return 0
        count = len(ngons)
        bmesh.ops.triangulate(bm, faces=ngons, quad_method='BEAUTY',
                              ngon_method='BEAUTY')
        _commit(obj, bm)
    except Exception as exc:                          # noqa: BLE001
        bm.free()
        raise RepairAborted("triangulation failed: %s: %s"
                            % (type(exc).__name__, exc))
    return count


# ---------------------------------------------------------------------------
# texture verification (sect. 8)
# ---------------------------------------------------------------------------

def verify_texture(obj, before_facts):
    """Confirm a repair kept the UV map, material and image. (ok, problems)."""
    after = scancopy.audit_object(obj)
    ok, problems, _notes = preprocess.compare_texture(before_facts, after)
    return ok, problems, after
