"""Create a lighter TEXTURED measurement copy of a dense scan (Blender side).

The source scan is never modified. Its object, mesh, UV layers, materials and
image textures are left exactly as they were; everything happens on a
duplicate named ``<source>_BSMT``.

The decisions - ratio, texture verification, safety - live in
``preprocess.py`` and are pure. This module does the Blender work and records
the facts those decisions need.
"""

import time

import bpy
import numpy as np

from . import preprocess


def triangle_count(mesh):
    """Triangles in a mesh, counting an ngon as its triangulation.

    ``len(polygons)`` is not the answer: a decimated or imported mesh may hold
    quads or ngons, and the solver only ever sees triangles.
    """
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


def color_attributes(mesh):
    """(name, domain, data_type) for every colour attribute on a mesh.

    Guarded because the API is version-dependent: ``color_attributes`` arrived
    in Blender 3.2 and replaced ``vertex_colors``. A PLY scan's entire
    appearance is usually one of these, so falling back rather than raising
    matters - a missing API must not make BSMT report "no colour" on a mesh
    that has one.
    """
    layers = getattr(mesh, "color_attributes", None)
    if layers is not None:
        return [(layer.name,
                 str(getattr(layer, "domain", "")),
                 str(getattr(layer, "data_type", "")))
                for layer in layers]
    legacy = getattr(mesh, "vertex_colors", None) or ()
    return [(layer.name, "CORNER", "BYTE_COLOR") for layer in legacy]


def active_color_name(mesh):
    """The mesh's active colour attribute, or "" when it has none."""
    layers = getattr(mesh, "color_attributes", None)
    if layers is None:
        return ""
    return str(getattr(layers, "active_color_name", "") or "")


def used_material_names(obj):
    """Slot names that at least one face actually references.

    Measured on Blender 4.5.13: a material slot SURVIVES collapse decimation
    even when every face that used it has been collapsed away. Slot presence
    is therefore a weaker check than it looks, and this is what distinguishes
    "the material is still there" from "the material is still used".

    The per-face indices are read from the mesh's own ``material_index``
    attribute rather than through ``polygons.foreach_get``. This function
    runs on every Scan Preprocessing redraw, and measured on the reported
    1,069,448-face scan the polygon path costs **88 ms** against **0.1 ms**
    for the attribute - the difference between a sidebar that responds and
    one that stutters whenever the pointer crosses it. The answer is
    identical: same attribute, same values, read a faster way, with the
    polygon path kept as the fallback for a mesh that does not carry the
    attribute at all.
    """
    mesh = obj.data
    slots = [slot.material.name if slot.material else "" for slot in obj.material_slots]
    if not slots:
        return []
    if not mesh.polygons:
        return []

    indices = np.empty(len(mesh.polygons), dtype=np.int32)
    attributes = getattr(mesh, "attributes", None)
    attribute = attributes.get("material_index") if attributes is not None else None
    if attribute is not None and str(getattr(attribute, "domain", "")) == 'FACE':
        attribute.data.foreach_get("value", indices)
    else:
        # A mesh that does not expose the attribute - an older Blender, or a
        # datablock in an unusual state. Slow, but always correct.
        mesh.polygons.foreach_get("material_index", indices)

    used = set()
    for index in np.unique(indices).tolist():
        if 0 <= index < len(slots) and slots[index]:
            used.add(slots[index])
    return [name for name in slots if name in used]


def audit_object(obj):
    """Record the appearance-bearing datablocks an object currently references."""
    mesh = obj.data
    uv_layers = [layer.name for layer in mesh.uv_layers]
    material_slots = []
    images = []
    image_paths = []
    for slot in obj.material_slots:
        material = slot.material
        if material is None:
            continue
        material_slots.append(material.name)
        if not material.use_nodes or material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            image = getattr(node, "image", None)
            if image is None:
                continue
            if image.name not in images:
                images.append(image.name)
                image_paths.append(getattr(image, "filepath", "") or "")
    return preprocess.texture_facts(
        uv_layers, material_slots, images, image_paths,
        color_attributes=color_attributes(mesh),
        active_color=active_color_name(mesh),
        used_materials=used_material_names(obj),
    )


# ---------------------------------------------------------------------------
# Whether a measurement mesh may be created at all (Milestone 3.26)
# ---------------------------------------------------------------------------
#
# ONE place decides this, and it decides it for the operator's poll() AND for
# the panel that has to explain a greyed button. A panel that re-derives the
# rule eventually disagrees with the operator, and the researcher is left
# reading a button whose state nothing on screen accounts for.
#
# What is NOT on this list matters as much as what is. Preprocessing is the
# step that PRODUCES a measurement mesh, so a source scan's own defects -
# non-manifold edges, boundary edges, several components, degenerate
# triangles, coincident vertices, sheer density - never block it and never
# remove its controls. They are diagnosed on the mesh that comes out, and it
# is `preprocess.preflight` that refuses to MEASURE on a mesh still carrying
# them. Neither of those gates is touched here, and nothing here repairs
# anything.

BLOCK_NOT_REGISTERED = 'NOT_REGISTERED'
BLOCK_RUNNING = 'RUNNING'
BLOCK_NO_OBJECT = 'NO_OBJECT'
BLOCK_NOT_MESH = 'NOT_MESH'
BLOCK_HELPER = 'HELPER'
BLOCK_IS_COPY = 'IS_COPY'
BLOCK_NO_GEOMETRY = 'NO_GEOMETRY'

#: The codes that make the operator UNAVAILABLE. The rest are refusals the
#: operator makes, with an explanation, when it is actually pressed - which
#: is deliberate: "this is already a measurement mesh, select the original"
#: is worth reading, and a silently greyed button never says it.
POLL_BLOCKS = (BLOCK_NOT_REGISTERED, BLOCK_RUNNING, BLOCK_NO_OBJECT,
               BLOCK_NOT_MESH, BLOCK_HELPER)


def _block(code, reason, remedy):
    return {"code": code, "reason": reason, "remedy": remedy,
            "blocks_poll": code in POLL_BLOCKS}


def creation_block(obj, props=None):
    """Why Create Measurement Mesh cannot run on `obj` right now, or None.

    Returns a dict with `code`, `reason`, `remedy` and `blocks_poll`. Cheap
    enough for a panel draw: it reads object type, the helper flag, the
    provenance group and one polygon count, and touches no geometry.

    The poll-blocking codes are exactly the conditions
    `BSMT_OT_create_measurement_copy.poll` used to test inline, in the same
    order, so making the operator ask this question changed nothing about
    when the button is available.
    """
    # Imported here rather than at module scope for the same reason
    # `diagnostics` imports geodesic locally: this module is imported early,
    # and a UI helper must not decide the package's import order.
    from . import visualization

    if props is None:
        return _block(BLOCK_NOT_REGISTERED,
                      "BSMT is not registered in this scene.",
                      "Re-enable the add-on.")
    if getattr(props, "preprocess_running", False):
        return _block(BLOCK_RUNNING,
                      "Preprocessing is already running.",
                      "Wait for it to finish.")
    if obj is None:
        return _block(BLOCK_NO_OBJECT,
                      "No active object.",
                      "Select the source scan in the viewport or the"
                      " Outliner.")
    if obj.type != 'MESH':
        return _block(BLOCK_NOT_MESH,
                      "'%s' is a %s, not a mesh." % (obj.name,
                                                     str(obj.type).lower()),
                      "Select the source scan.")
    if visualization.is_helper(obj):
        return _block(BLOCK_HELPER,
                      "'%s' is a BSMT helper object, not a scan." % obj.name,
                      "Select the source scan.")

    provenance = getattr(obj, "bsmt_scan", None)
    if provenance is not None and provenance.is_measurement_copy:
        return _block(BLOCK_IS_COPY,
                      "'%s' is already a measurement mesh." % obj.name,
                      "Select the original scan to make another.")
    if not len(obj.data.polygons):
        return _block(BLOCK_NO_GEOMETRY,
                      "'%s' has no faces." % obj.name,
                      "Select a scan that carries geometry.")
    return None


def diagnostics(obj):
    """The cached topology report for an object, or None if not analysed.

    ``peek`` only. This is read from a panel draw, and building a canonical
    mesh costs about 1.7 s per million triangles - never something a redraw
    is entitled to spend. A scan that has not been analysed reports None, and
    the panel offers the Analyze Scan button instead of guessing.
    """
    from . import geodesic
    if not geodesic.MESHCACHE_AVAILABLE or geodesic.meshcache is None:
        return None
    # peek_current, not peek: a cached report that no longer describes this
    # object must read as "not analysed" rather than render as a confident
    # topology line about geometry that has moved on.
    canonical = geodesic.meshcache.peek_current(obj)
    if canonical is None:
        return None
    report = dict(canonical.topology or {})
    # A cached report describing DIFFERENT geometry is worse than none: it
    # would show a clean topology for a mesh that has since been edited.
    report["geometry_hash"] = canonical.geometry_hash
    report["canonical_triangles"] = canonical.triangle_count
    return report


def describe(obj):
    """What the panel shows about the active object. Read-only.

    Reads no geometry beyond the mesh's own counts and reuses the EXISTING
    topology diagnostics rather than defining a second set (sect. 0). The
    topology fields are absent when the scan has not been analysed yet.
    """
    if obj is None or obj.type != 'MESH':
        return None
    mesh = obj.data
    facts = audit_object(obj)
    report = diagnostics(obj)
    info = {
        "name": obj.name,
        "mesh_name": mesh.name,
        "vertex_count": len(mesh.vertices),
        "triangle_count": triangle_count(mesh),
        "polygon_count": len(mesh.polygons),
        "uv_layers": facts["uv_layers"],
        "material_slots": facts["material_slots"],
        "used_materials": facts["used_materials"],
        "images": facts["images"],
        "image_paths": facts["image_paths"],
        "color_attributes": facts["color_attributes"],
        "color_attribute_names": preprocess.color_attribute_names(facts),
        "active_color": facts["active_color"],
        "has_uv": bool(facts["uv_layers"]),
        "has_material": bool(facts["material_slots"]),
        "has_image": bool(facts["images"]),
        "has_color": bool(facts["color_attributes"]),
        "analysed": report is not None,
        "facts": facts,
    }
    if report is not None:
        for key in ("component_count", "boundary_edge_count",
                    "nonmanifold_edge_count", "degenerate_triangle_count",
                    "duplicate_vertex_count"):
            info[key] = int(report.get(key, 0) or 0)
        info["report"] = report
    return info


def _link_beside(source, copy):
    """Put the copy in the same collections as its source."""
    linked = False
    for collection in source.users_collection:
        try:
            collection.objects.link(copy)
            linked = True
        except RuntimeError:
            pass
    if not linked:
        bpy.context.scene.collection.objects.link(copy)


def duplicate(source):
    """An independent object + mesh copy of `source`. Source untouched.

    ``obj.copy()`` alone shares the mesh datablock, so the mesh is copied too -
    otherwise decimating the copy would decimate the original.
    """
    copy = source.copy()
    copy.data = source.data.copy()
    copy.name = source.name + preprocess.COPY_SUFFIX
    copy.data.name = source.data.name + preprocess.COPY_SUFFIX
    # Materials are intentionally SHARED, not copied: the copy must show the
    # same texture, and duplicating a material would duplicate nothing useful
    # while doubling the image references.
    _link_beside(source, copy)
    return copy


def apply_decimation(context, obj, ratio):
    """Collapse-decimate `obj` in place and bake the result. Returns seconds.

    The modifier is baked through the depsgraph rather than
    ``bpy.ops.object.modifier_apply``: it needs no operator context, works
    headless, and ``preserve_all_data_layers=True`` is what carries the UV
    layers across - which is the whole point of this workflow.
    """
    started = time.perf_counter()
    modifier = obj.modifiers.new(name="BSMT_Decimate", type='DECIMATE')
    modifier.decimate_type = 'COLLAPSE'
    modifier.ratio = float(ratio)
    # Collapse decimation interpolates UVs; without this it can still swap
    # triangles across a UV seam and smear the texture.
    modifier.use_collapse_triangulate = True

    depsgraph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    baked = bpy.data.meshes.new_from_object(
        evaluated, preserve_all_data_layers=True, depsgraph=depsgraph
    )
    baked.name = obj.data.name

    obj.modifiers.remove(modifier)
    previous = obj.data
    obj.data = baked
    if previous.users == 0:
        bpy.data.meshes.remove(previous)
    return time.perf_counter() - started


def write_provenance(copy, source, record):
    """Store the sect. 11 provenance on the generated object.

    The identity that matters is a real Blender POINTER to the source object,
    which survives a rename; the name string is kept alongside only as a
    human-readable fallback for when the pointer cannot be followed.
    """
    provenance = copy.bsmt_scan
    provenance.is_measurement_copy = True
    provenance.source = source
    provenance.source_name = record["source_name"]
    provenance.source_mesh_name = record["source_mesh_name"]
    provenance.original_triangles = int(record["original_triangles"])
    provenance.target_triangles = int(record["target_triangles"])
    provenance.actual_triangles = int(record["actual_triangles"])
    provenance.method = record["method"]
    provenance.ratio = float(record["ratio"])
    provenance.bsmt_version = record["bsmt_version"]
    provenance.created = record["created"]
    provenance.representation = preprocess.REPRESENTATION
    return provenance


def resolve_source(copy):
    """The source object of a measurement copy, by pointer then by name."""
    provenance = getattr(copy, "bsmt_scan", None)
    if provenance is None or not provenance.is_measurement_copy:
        return None
    if provenance.source is not None:
        return provenance.source
    return bpy.data.objects.get(provenance.source_name)


def find_measurement_copy(source):
    """An existing measurement copy pointing at `source`, or None."""
    for obj in bpy.data.objects:
        provenance = getattr(obj, "bsmt_scan", None)
        if provenance is None or not provenance.is_measurement_copy:
            continue
        if provenance.source is source:
            return obj
        if provenance.source is None and provenance.source_name == source.name:
            return obj
    return None
