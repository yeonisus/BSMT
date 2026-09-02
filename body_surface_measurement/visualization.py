"""Helper geometry: point markers and the straight measurement line.

Every object created here is tagged with a custom property (HELPER_FLAG) and
linked into a dedicated collection. Deletion only ever touches objects that
carry that tag, so a user's body scan can never be removed by this add-on.
"""

import bmesh
import bpy
from mathutils import Vector

from . import landmarks, measurement

HELPER_FLAG = "bsmt_helper"
COLLECTION_NAME = "BSMT_Helpers"

POINT_A_NAME = "BSMT_Point_A"
POINT_B_NAME = "BSMT_Point_B"
LINE_NAME = "BSMT_Straight_Line"
COMPONENT_PREFIX = "BSMT_Component_"
LANDMARK_PREFIX = "BSMT_Landmark_"

MARKER_NAMES = {'A': POINT_A_NAME, 'B': POINT_B_NAME}
MARKER_COLORS = {
    'A': (1.0, 0.15, 0.15, 1.0),   # red
    'B': (0.15, 0.45, 1.0, 1.0),   # blue
}
LINE_COLOR = (1.0, 0.85, 0.1, 1.0)  # yellow

# Named research landmarks are visually distinct from the A/B markers, and a
# landmark that can no longer be trusted looks different again, so a stale
# marker is never mistaken for a measurable one (sect. 7, sect. 8).
LANDMARK_COLOR = (0.15, 0.9, 0.35, 1.0)         # green
LANDMARK_STALE_COLOR = (1.0, 0.45, 0.0, 1.0)    # orange
LANDMARK_UNVERIFIED_COLOR = (0.75, 0.75, 0.2, 1.0)  # dull yellow

# Marker spheres are built once at radius 1.0 and resized with object scale, so
# changing "Marker Size" never rebuilds geometry.
MARKER_BASE_RADIUS = 1.0
BASE_RADIUS_KEY = "bsmt_base_radius"
MIN_HELPER_RADIUS = 1e-9


def is_helper(obj):
    """True only for objects this add-on created."""
    if obj is None:
        return False
    original = getattr(obj, "original", obj)
    return bool(original.get(HELPER_FLAG, False))


def landmark_marker_radius(props):
    """Named landmark marker radius in coordinate units, from the mm setting."""
    return max(
        measurement.mm_to_units(props.landmark_marker_size_mm * 0.5, props.unit),
        MIN_HELPER_RADIUS,
    )


def landmark_object_name(stable_id):
    """Marker object name for a landmark. Derived from its stable id only."""
    return landmarks.helper_object_name(stable_id)


def marker_radius(props):
    """Marker sphere radius in coordinate units, from the mm setting."""
    return max(
        measurement.mm_to_units(props.marker_size_mm * 0.5, props.unit),
        MIN_HELPER_RADIUS,
    )


def line_radius(props):
    """Line tube radius in coordinate units, from the mm setting."""
    return max(
        measurement.mm_to_units(props.line_thickness_mm * 0.5, props.unit),
        MIN_HELPER_RADIUS,
    )


def get_collection(context):
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(COLLECTION_NAME)
        collection[HELPER_FLAG] = True
    if collection.name not in context.scene.collection.children:
        try:
            context.scene.collection.children.link(collection)
        except RuntimeError:
            # Already linked somewhere else in this scene; that is fine.
            pass
    return collection


def get_material(name, color):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        material.use_nodes = False
        material[HELPER_FLAG] = True
    material.diffuse_color = color
    return material


def _sphere_mesh(name, radius):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    try:
        try:
            bmesh.ops.create_uvsphere(
                bm, u_segments=16, v_segments=8, radius=radius
            )
        except TypeError:
            # Blender 2.8x named this argument "diameter" (it was a radius).
            bmesh.ops.create_uvsphere(
                bm, u_segments=16, v_segments=8, diameter=radius
            )
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh[HELPER_FLAG] = True
    mesh[BASE_RADIUS_KEY] = radius
    return mesh


def _line_curve(name):
    """A two-point poly curve. Bevel depth gives it a controllable thickness."""
    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions = '3D'
    curve.fill_mode = 'FULL'
    curve.bevel_resolution = 2
    spline = curve.splines.new('POLY')
    spline.points.add(1)   # splines start with one point; we need two
    curve[HELPER_FLAG] = True
    return curve


def new_helper_object(context, name, data, color):
    obj = bpy.data.objects.new(name, data)
    obj[HELPER_FLAG] = True
    obj.color = color
    obj.show_in_front = True          # stay visible even inside the scan
    obj.hide_select = True            # avoid accidental user edits
    get_collection(context).objects.link(obj)
    return obj


def remove_object(obj):
    """Delete a helper object and its orphaned mesh. Ignores anything else."""
    if not is_helper(obj):
        return False
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and data.users == 0 and data.get(HELPER_FLAG, False):
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)
    return True


def _existing_helper(name):
    obj = bpy.data.objects.get(name)
    return obj if is_helper(obj) else None


def update_marker(context, props, slot, location):
    """Create or move the marker for slot 'A' or 'B'. Returns the object."""
    name = MARKER_NAMES[slot]
    color = MARKER_COLORS[slot]
    obj = _existing_helper(name)
    if (
        obj is None
        or not isinstance(obj.data, bpy.types.Mesh)
        or obj.data.get(BASE_RADIUS_KEY) != MARKER_BASE_RADIUS
    ):
        # Rebuild if missing, or if it is a pre-scaling marker from an older
        # version of this add-on (whose mesh was baked at a different radius).
        if obj is not None:
            remove_object(obj)
        mesh = _sphere_mesh(name + "_Mesh", MARKER_BASE_RADIUS)
        obj = new_helper_object(context, name, mesh, color)
        obj.data.materials.append(
            get_material("BSMT_Material_Point_" + slot, color)
        )
    obj.location = Vector(location)
    apply_display_settings(context, props)
    return obj


def update_line(context, props, point_a, point_b):
    """Create or refresh the straight line between the two points."""
    obj = _existing_helper(LINE_NAME)
    if obj is None or not isinstance(obj.data, bpy.types.Curve):
        # Also upgrades the old wire-mesh line from an earlier .blend file.
        if obj is not None:
            remove_object(obj)
        curve = _line_curve(LINE_NAME + "_Curve")
        obj = new_helper_object(context, LINE_NAME, curve, LINE_COLOR)
        obj.data.materials.append(get_material("BSMT_Material_Line", LINE_COLOR))
    spline = obj.data.splines[0]
    spline.points[0].co = (point_a[0], point_a[1], point_a[2], 1.0)
    spline.points[1].co = (point_b[0], point_b[1], point_b[2], 1.0)
    obj.location = (0.0, 0.0, 0.0)    # curve points are already world-space
    apply_display_settings(context, props)
    return obj


def apply_display_settings(context, props):
    """Push size / thickness / visibility onto whichever helpers exist.

    Purely cosmetic: this never creates, deletes or moves a helper, and never
    touches the stored measurement coordinates.
    """
    radius = marker_radius(props)
    for slot in MARKER_NAMES:
        obj = _existing_helper(MARKER_NAMES[slot])
        if obj is None:
            continue
        obj.scale = (radius, radius, radius)
        obj.hide_viewport = not props.show_markers
        obj.hide_render = not props.show_markers

    obj = _existing_helper(LINE_NAME)
    if obj is not None:
        if isinstance(obj.data, bpy.types.Curve):
            obj.data.bevel_depth = line_radius(props)
        obj.hide_viewport = not props.show_line
        obj.hide_render = not props.show_line


def move_marker(slot, world_location):
    """Move an existing marker. No geometry, material or collection work.

    Deliberately minimal so it is safe to call from a depsgraph handler during
    an interactive gizmo drag.
    """
    obj = _existing_helper(MARKER_NAMES[slot])
    if obj is None:
        return False
    obj.location = (
        float(world_location[0]),
        float(world_location[1]),
        float(world_location[2]),
    )
    return True


def move_line(point_a, point_b):
    """Move the straight line's two endpoints. Curve data is reused."""
    obj = _existing_helper(LINE_NAME)
    if obj is None or not isinstance(obj.data, bpy.types.Curve):
        return False
    if not obj.data.splines:
        return False
    spline = obj.data.splines[0]
    if len(spline.points) < 2:
        return False
    spline.points[0].co = (
        float(point_a[0]), float(point_a[1]), float(point_a[2]), 1.0
    )
    spline.points[1].co = (
        float(point_b[0]), float(point_b[1]), float(point_b[2]), 1.0
    )
    return True


def remove_line(context):
    obj = _existing_helper(LINE_NAME)
    if obj is not None:
        remove_object(obj)


def clear_all(context):
    """Remove every BSMT helper object. Never touches non-helper objects.

    Returns the number of helper objects removed.
    """
    removed = 0
    for obj in list(bpy.data.objects):
        if not is_helper(obj):
            continue
        if obj.name.startswith(COMPONENT_PREFIX):
            # Diagnostics preview has its own Clear button.
            continue
        if obj.name.startswith(LANDMARK_PREFIX):
            # Named research landmarks have their own lifetime and their own
            # Clear button. `Clear Points` is about A/B (sect. 20).
            continue
        if remove_object(obj):
            removed += 1

    remove_collection_if_empty()
    return removed


def remove_collection_if_empty():
    collection = bpy.data.collections.get(COLLECTION_NAME)
    if (
        collection is not None
        and collection.get(HELPER_FLAG, False)
        and not collection.objects
        and not collection.children
    ):
        bpy.data.collections.remove(collection)


# ---------------------------------------------------------------------------
# named research landmark markers (Milestone 3.0)
# ---------------------------------------------------------------------------
#
# These have a lifetime of their own. `Clear Points` must not remove them and
# `Clear Landmark Data` must not remove A/B or the component preview
# (sect. 20), which is why clear_all() skips this prefix exactly as it skips
# COMPONENT_PREFIX.

_LANDMARK_STATUS_COLORS = {
    landmarks.STATUS_VALID: LANDMARK_COLOR,
    landmarks.STATUS_NEEDS_REFRESH: LANDMARK_UNVERIFIED_COLOR,
    landmarks.STATUS_STALE: LANDMARK_STALE_COLOR,
    landmarks.STATUS_INVALID: LANDMARK_STALE_COLOR,
}


def landmark_color(status):
    return _LANDMARK_STATUS_COLORS.get(status, LANDMARK_COLOR)


def update_landmark_marker(context, props, item, world_location):
    """Create or move the marker for one named landmark. Returns the object."""
    name = landmark_object_name(item.stable_id)
    color = landmark_color(item.status)
    obj = _existing_helper(name)
    if (
        obj is None
        or not isinstance(obj.data, bpy.types.Mesh)
        or obj.data.get(BASE_RADIUS_KEY) != MARKER_BASE_RADIUS
    ):
        if obj is not None:
            remove_object(obj)
        mesh = _sphere_mesh(name + "_Mesh", MARKER_BASE_RADIUS)
        obj = new_helper_object(context, name, mesh, color)
        obj.data.materials.append(
            get_material("BSMT_Material_Landmark", LANDMARK_COLOR)
        )
    obj.location = Vector(world_location)
    obj.color = color
    apply_landmark_display(context, props)
    return obj


def move_landmark_marker(stable_id, world_location):
    """Move an existing landmark marker. Cheap enough for a depsgraph handler."""
    obj = _existing_helper(landmark_object_name(stable_id))
    if obj is None:
        return False
    obj.location = (
        float(world_location[0]),
        float(world_location[1]),
        float(world_location[2]),
    )
    return True


def remove_landmark_marker(stable_id):
    obj = _existing_helper(landmark_object_name(stable_id))
    return remove_object(obj) if obj is not None else False


def landmark_marker_objects():
    """Every landmark marker helper currently in the file."""
    return [
        obj for obj in bpy.data.objects
        if obj.name.startswith(LANDMARK_PREFIX) and is_helper(obj)
    ]


def remove_orphan_landmark_markers(valid_stable_ids):
    """Delete markers whose landmark no longer exists. Returns the count."""
    wanted = {landmark_object_name(value) for value in valid_stable_ids}
    removed = 0
    for obj in landmark_marker_objects():
        if obj.name not in wanted and remove_object(obj):
            removed += 1
    return removed


def apply_landmark_display(context, props):
    """Push landmark marker size, colour and visibility. Cosmetic only.

    Never creates, deletes or moves a marker, and never touches a stored
    surface location.
    """
    radius = landmark_marker_radius(props)
    show = bool(props.show_landmarks)
    collection = getattr(context.scene, "bsmt_landmarks", None)
    statuses = (
        {landmark_object_name(item.stable_id): item.status
         for item in collection}
        if collection is not None else {}
    )
    for obj in landmark_marker_objects():
        obj.scale = (radius, radius, radius)
        obj.hide_viewport = not show
        obj.hide_render = not show
        status = statuses.get(obj.name)
        if status is not None:
            obj.color = landmark_color(status)


def clear_landmark_markers():
    """Remove every named landmark marker. Returns how many were removed."""
    removed = 0
    for obj in landmark_marker_objects():
        if remove_object(obj):
            removed += 1
    return removed
