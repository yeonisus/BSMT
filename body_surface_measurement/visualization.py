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
MEASUREMENT_PREFIX = "BSMT_Measurement_"
REPAIR_PREFIX = "BSMT_Repair_"
REPAIR_NON_MANIFOLD = REPAIR_PREFIX + "NonManifold"
REPAIR_BOUNDARY = REPAIR_PREFIX + "Boundary"
ALIGN_PREFIX = "BSMT_Align_"
ALIGN_AXES = ALIGN_PREFIX + "Axes"
STRAIGHT_SUFFIX = "_Straight"
PATH_SUFFIX = "_Path"

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

# Repair highlights. Deliberately alarming colours: they mark the places the
# exact solver is not safe on, and they are overlays only - no mesh is edited
# to draw them.
REPAIR_NON_MANIFOLD_COLOR = (1.0, 0.05, 0.35, 1.0)   # magenta-red
REPAIR_BOUNDARY_COLOR = (0.15, 0.6, 1.0, 1.0)        # blue

# Marker spheres are built once at radius 1.0 and resized with object scale, so
# changing "Marker Size" never rebuilds geometry.
#: The selected landmark's marker is drawn this much larger. Scale only: the
#: stored landmark is not touched to highlight it (sect. 6).
SELECTED_MARKER_SCALE = 1.35

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
        if obj.name.startswith(MEASUREMENT_PREFIX):
            # Measurement visualisation likewise has its own lifetime and its
            # own Clear buttons (sect. 14).
            continue
        if obj.name.startswith(REPAIR_PREFIX):
            # Repair highlights have their own Clear button too.
            continue
        if obj.name.startswith(ALIGN_PREFIX):
            # So does the alignment axis preview.
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


def landmark_color(status, valid_color=None):
    """The display colour for a landmark marker of this status.

    `valid_color` is the researcher's Marker Color and applies to a VALID
    landmark only. A stale, invalid or unverified landmark keeps the status
    colour whatever the setting, because a marker that cannot be trusted must
    never be able to look like one that can (sect. 9).
    """
    if status == landmarks.STATUS_VALID and valid_color is not None:
        return tuple(float(v) for v in valid_color)
    return _LANDMARK_STATUS_COLORS.get(status, LANDMARK_COLOR)


def update_landmark_marker(context, props, item, world_location):
    """Create or move the marker for one named landmark. Returns the object."""
    name = landmark_object_name(item.stable_id)
    color = landmark_color(item.status,
                           getattr(props, "landmark_marker_color", None))
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
    surface location. The selected landmark is drawn slightly larger - an
    emphasis carried entirely by object SCALE, so nothing about the landmark
    itself, not even its stored colour, is changed to highlight it (sect. 6).
    """
    radius = landmark_marker_radius(props)
    show = bool(props.show_landmarks)
    valid_color = getattr(props, "landmark_marker_color", None)
    collection = getattr(context.scene, "bsmt_landmarks", None)
    statuses = {}
    selected_name = ""
    if collection is not None:
        for index, item in enumerate(collection):
            statuses[landmark_object_name(item.stable_id)] = item.status
            if index == int(getattr(props, "landmark_index", -1)):
                selected_name = landmark_object_name(item.stable_id)

    for obj in landmark_marker_objects():
        scale = radius * (SELECTED_MARKER_SCALE
                          if obj.name == selected_name else 1.0)
        obj.scale = (scale, scale, scale)
        obj.hide_viewport = not show
        obj.hide_render = not show
        status = statuses.get(obj.name)
        if status is not None:
            obj.color = landmark_color(status, valid_color)


def clear_landmark_markers():
    """Remove every named landmark marker. Returns how many were removed."""
    removed = 0
    for obj in landmark_marker_objects():
        if remove_object(obj):
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# measurement visualization (Milestone 3.2)
# ---------------------------------------------------------------------------
#
# Geometry is stored in the SCAN OBJECT'S LOCAL SPACE and the helper's
# matrix_world is kept equal to the scan's. A rigid transform is then one
# matrix copy per helper instead of rewriting every point, which matters when
# a geodesic path has thousands of them - and it makes "the path follows the
# scan" true by construction rather than by a per-frame recomputation.
#
# These helpers have their own lifetime: `Clear Points` does not touch them
# and clearing them does not touch A/B (sect. 14).


def measurement_object_name(stable_id, kind):
    """Helper object name for one measurement. Derived from its stable id."""
    suffix = STRAIGHT_SUFFIX if kind == 'STRAIGHT' else PATH_SUFFIX
    return "%s%06d%s" % (MEASUREMENT_PREFIX, int(stable_id), suffix)


def measurement_helper_objects():
    return [
        obj for obj in bpy.data.objects
        if obj.name.startswith(MEASUREMENT_PREFIX) and is_helper(obj)
    ]


def _poly_curve(name, point_count):
    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions = '3D'
    curve.fill_mode = 'FULL'
    curve.bevel_resolution = 2
    spline = curve.splines.new('POLY')
    spline.points.add(max(0, point_count - 1))
    curve[HELPER_FLAG] = True
    return curve


def _set_curve_points(curve, points_local):
    """Rewrite a poly curve's points. `points_local` is (k, 3)."""
    spline = curve.splines[0] if curve.splines else None
    wanted = len(points_local)
    if spline is None or len(spline.points) != wanted:
        curve.splines.clear()
        spline = curve.splines.new('POLY')
        spline.points.add(max(0, wanted - 1))
    flat = []
    for point in points_local:
        flat.extend((float(point[0]), float(point[1]), float(point[2]), 1.0))
    spline.points.foreach_set("co", flat)
    return spline


def thickness_radius(props, thickness_mm):
    """Curve bevel radius in coordinate units, from a physical mm setting."""
    return max(measurement.mm_to_units(float(thickness_mm) * 0.5, props.unit),
               MIN_HELPER_RADIUS)


def update_measurement_curve(context, props, stable_id, kind, points_local,
                             matrix_world, color, thickness_mm,
                             show_in_front):
    """Create or refresh one measurement helper curve. Returns the object."""
    name = measurement_object_name(stable_id, kind)
    obj = _existing_helper(name)
    if obj is None or not isinstance(obj.data, bpy.types.Curve):
        if obj is not None:
            remove_object(obj)
        curve = _poly_curve(name + "_Curve", len(points_local))
        obj = new_helper_object(context, name, curve, color)
        obj.data.materials.append(
            get_material("BSMT_Material_Measurement_" + kind.title(), color)
        )
    _set_curve_points(obj.data, points_local)
    obj.data.bevel_depth = thickness_radius(props, thickness_mm)
    obj.color = color
    material = obj.data.materials[0] if obj.data.materials else None
    if material is not None:
        material.diffuse_color = color
    obj.matrix_world = matrix_world
    obj.show_in_front = bool(show_in_front)
    return obj


def sync_measurement_transform(stable_id, kind, matrix_world):
    """Point a helper at the scan's current transform. One matrix copy."""
    obj = _existing_helper(measurement_object_name(stable_id, kind))
    if obj is None:
        return False
    obj.matrix_world = matrix_world
    return True


def set_measurement_helper_visible(stable_id, kind, visible):
    """Show or hide a helper WITHOUT destroying it.

    Load-bearing: the path helper's curve is where the computed polyline
    lives, so removing it to hide it would throw away a solve that costs tens
    of seconds. Switching display mode must never do that (sect. 6).
    """
    obj = _existing_helper(measurement_object_name(stable_id, kind))
    if obj is None:
        return False
    obj.hide_viewport = not visible
    obj.hide_render = not visible
    return True


def measurement_helper_exists(stable_id, kind):
    return _existing_helper(measurement_object_name(stable_id, kind)) is not None


def remove_measurement_helper(stable_id, kind):
    obj = _existing_helper(measurement_object_name(stable_id, kind))
    return remove_object(obj) if obj is not None else False


def remove_measurement_helpers(stable_id):
    """Both helpers for one measurement. Returns how many were removed."""
    return sum(1 for kind in ('STRAIGHT', 'PATH')
               if remove_measurement_helper(stable_id, kind))


def clear_measurement_helpers():
    """Every measurement helper. Never touches A/B, landmarks or the scan."""
    removed = 0
    for obj in measurement_helper_objects():
        if remove_object(obj):
            removed += 1
    return removed


def apply_measurement_display(context, props):
    """Push colour and thickness onto existing helpers. Cosmetic only.

    Never creates, deletes or moves a helper, never rewrites a curve point,
    and therefore never recomputes a distance or a path.
    """
    straight_radius = thickness_radius(props, props.viz_straight_thickness_mm)
    path_radius = thickness_radius(props, props.viz_path_thickness_mm)
    for obj in measurement_helper_objects():
        is_path = obj.name.endswith(PATH_SUFFIX)
        color = (tuple(props.viz_path_color) if is_path
                 else tuple(props.viz_straight_color))
        if isinstance(obj.data, bpy.types.Curve):
            obj.data.bevel_depth = path_radius if is_path else straight_radius
            if obj.data.materials:
                obj.data.materials[0].diffuse_color = color
        obj.color = color


# ---------------------------------------------------------------------------
# repair highlights (Milestone 3.4)
# ---------------------------------------------------------------------------
#
# Edge-only helper meshes drawn over the scan. They carry no faces, are never
# selectable, and never touch the mesh they describe - highlighting a defect
# must not be able to change it.


def _edge_mesh(name, points_local, edges):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(float(v) for v in point) for point in points_local],
                     [(int(a), int(b)) for a, b in edges], [])
    mesh.update()
    mesh[HELPER_FLAG] = True
    return mesh


def show_repair_edges(context, props, name, points_local, edges, matrix_world,
                      color):
    """Draw a set of edges over a scan. Returns the helper object, or None."""
    existing = _existing_helper(name)
    if existing is not None:
        remove_object(existing)
    if len(edges) == 0:
        return None
    mesh = _edge_mesh(name + "_Mesh", points_local, edges)
    obj = new_helper_object(context, name, mesh, color)
    obj.show_in_front = True          # a defect hidden inside the scan is
    obj.display_type = 'WIRE'         # exactly the one you need to see
    obj.matrix_world = matrix_world
    return obj


def clear_repair_highlights():
    """Remove every repair highlight. Nothing else is affected."""
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.name.startswith(REPAIR_PREFIX) and is_helper(obj):
            if remove_object(obj):
                removed += 1
    return removed


def repair_highlight_exists(name):
    return _existing_helper(name) is not None


# ---------------------------------------------------------------------------
# alignment axis preview (Milestone 3.6)
# ---------------------------------------------------------------------------
#
# Three coloured axis lines drawn at the object, so the researcher can check
# upright / left-right / front-back by eye before committing. An overlay only:
# it never touches the object or its mesh, and it has its own Clear button so
# it cannot be confused with measurement helpers.

ALIGN_AXIS_COLORS = {
    'X': (1.0, 0.2, 0.2, 1.0),      # subject's LEFT
    'Y': (0.2, 1.0, 0.2, 1.0),      # POSTERIOR
    'Z': (0.2, 0.4, 1.0, 1.0),      # SUPERIOR
}


def show_alignment_axes(context, origin_world, length, matrix_world=None):
    """Draw the anatomical frame at `origin_world`. Returns the object."""
    existing = _existing_helper(ALIGN_AXES)
    if existing is not None:
        remove_object(existing)
    if length <= 0.0:
        return None

    # Drawn in world space with an identity transform: the axes describe the
    # WORLD anatomical frame the object has been aligned to, not the object's
    # own local axes, so they must not inherit the object's rotation.
    points = [(0.0, 0.0, 0.0)]
    edges = []
    for index, axis in enumerate(('X', 'Y', 'Z')):
        direction = [0.0, 0.0, 0.0]
        direction[index] = length
        points.append(tuple(direction))
        edges.append((0, len(points) - 1))

    mesh = bpy.data.meshes.new(ALIGN_AXES + "_Mesh")
    mesh.from_pydata(points, edges, [])
    mesh.update()
    mesh[HELPER_FLAG] = True
    obj = new_helper_object(context, ALIGN_AXES, mesh, ALIGN_AXIS_COLORS['Z'])
    obj.show_in_front = True
    obj.display_type = 'WIRE'
    obj.location = tuple(float(v) for v in origin_world)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    return obj


def clear_alignment_helpers():
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.name.startswith(ALIGN_PREFIX) and is_helper(obj):
            if remove_object(obj):
                removed += 1
    return removed


def alignment_helper_exists():
    return _existing_helper(ALIGN_AXES) is not None
