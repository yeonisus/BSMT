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

from . import preprocess


def triangle_count(mesh):
    """Triangles in a mesh, counting an ngon as its triangulation.

    ``len(polygons)`` is not the answer: a decimated or imported mesh may hold
    quads or ngons, and the solver only ever sees triangles.
    """
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


def audit_object(obj):
    """Record the texture-bearing datablocks an object currently references."""
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
    return preprocess.texture_facts(uv_layers, material_slots, images,
                                    image_paths)


def describe(obj):
    """What the panel shows about the active object. Read-only."""
    if obj is None or obj.type != 'MESH':
        return None
    mesh = obj.data
    facts = audit_object(obj)
    return {
        "name": obj.name,
        "mesh_name": mesh.name,
        "vertex_count": len(mesh.vertices),
        "triangle_count": triangle_count(mesh),
        "polygon_count": len(mesh.polygons),
        "uv_layers": facts["uv_layers"],
        "material_slots": facts["material_slots"],
        "images": facts["images"],
        "image_paths": facts["image_paths"],
        "has_uv": bool(facts["uv_layers"]),
        "has_material": bool(facts["material_slots"]),
        "has_image": bool(facts["images"]),
    }


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
