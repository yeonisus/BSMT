"""Measurement visualisation: straight chords and exact geodesic paths.

Display only. Nothing in this module computes a distance, decides a
measurement's validity, or changes a stored result. It turns numbers that
already exist into helper geometry, and removes that geometry when the
numbers stop being current.

Coordinate handling
-------------------
Helper geometry is written in the SCAN OBJECT'S LOCAL SPACE and the helper's
``matrix_world`` is kept equal to the scan's. Following a rigid transform is
then one matrix assignment per helper rather than rewriting every point -
which matters when a geodesic path has thousands of them - and "the path
follows the scan" becomes true by construction.

Solver space is physical millimetres, centred (sect. 6.2)::

    v_solver = (matrix_world @ v_local) * unit_multiplier - center_mm

so the inverse used here is::

    v_local = matrix_world^-1 @ ((v_solver + center_mm) / unit_multiplier)
"""

import numpy as np

import bpy
from mathutils import Vector

from . import measurements, state, visualization

#: The drawn path is lifted along the surface normal by this multiple of its
#: own bevel radius, so it sits on the scan rather than half inside it. Purely
#: a display offset: the stored polyline, its length and every reported
#: distance are computed before it is applied and are unaffected (sect. 9).
NORMAL_OFFSET_RADII = 1.2


def solver_to_local(canonical, points_solver):
    """Solver-space millimetres to object-local coordinates."""
    points = np.asarray(points_solver, dtype=np.float64)
    if points.size == 0:
        return np.zeros((0, 3), dtype=np.float64)
    world = (points + np.asarray(canonical.center_mm, dtype=np.float64)) \
        / float(canonical.unit_multiplier)
    matrix = np.asarray(canonical.matrix_world, dtype=np.float64)
    inverse = np.linalg.inv(matrix[:3, :3])
    return (world - matrix[:3, 3]) @ inverse.T


def offset_along_normals(canonical, points_local, distance):
    """Lift points off the surface along its normal. Display only.

    Returns a new array; the input is untouched. Uses the canonical BVH, which
    is already built in local space, so no extra structure is created. A point
    whose nearest surface cannot be found is left exactly where it was rather
    than guessed at.
    """
    points = np.asarray(points_local, dtype=np.float64)
    if points.size == 0 or distance <= 0.0:
        return points.copy()
    lifted = points.copy()
    bvh = getattr(canonical, "bvh", None)
    if bvh is None:
        return lifted
    for index in range(points.shape[0]):
        try:
            hit = bvh.find_nearest(Vector(points[index].tolist()))
        except Exception:                             # pragma: no cover
            continue
        if hit is None or hit[1] is None:
            continue
        normal = np.array(hit[1], dtype=np.float64)
        length = float(np.linalg.norm(normal))
        if length > 0.0:
            lifted[index] = points[index] + (normal / length) * distance
    return lifted


def _canonical_for(context, props, object_name, build=False):
    """The cached canonical mesh for a scan, or None. Never builds in draw()."""
    from . import geodesic
    if not geodesic.MESHCACHE_AVAILABLE or geodesic.meshcache is None:
        return None
    if build:
        obj = bpy.data.objects.get(object_name)
        if obj is None or obj.type != 'MESH':
            return None
        try:
            return geodesic.meshcache.get(context, obj, props.unit)
        except Exception:                             # noqa: BLE001
            return None
    return geodesic.meshcache.peek(object_name)


def endpoint_locals(context, item):
    """Object-local positions of a measurement's two landmarks, or None.

    Read straight from the landmarks' cached local coordinates, which are
    maintained by the existing attachment machinery. Nothing is recomputed,
    and in particular the straight distance is not re-derived merely to draw
    it (sect. 2).
    """
    source, target = state.resolve_measurement_landmarks(context, item)
    if source is None or target is None:
        return None
    if not (source.surface_point.valid and target.surface_point.valid):
        return None
    if source.surface_point.source_object != target.surface_point.source_object:
        return None
    return (
        np.array(source.surface_point.local_xyz, dtype=np.float64),
        np.array(target.surface_point.local_xyz, dtype=np.float64),
        source.surface_point.source_object,
    )


def build_straight(context, props, item):
    """Create or refresh the straight helper for one measurement."""
    ends = endpoint_locals(context, item)
    if ends is None:
        visualization.remove_measurement_helper(item.stable_id, 'STRAIGHT')
        return False
    local_a, local_b, object_name = ends
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        visualization.remove_measurement_helper(item.stable_id, 'STRAIGHT')
        return False
    visualization.update_measurement_curve(
        context, props, item.stable_id, 'STRAIGHT',
        np.asarray([local_a, local_b], dtype=np.float64),
        obj.matrix_world,
        tuple(props.viz_straight_color),
        props.viz_straight_thickness_mm,
        # A chord passes through the body by its nature, so it is drawn in
        # front; hiding it inside the scan would defeat the point of showing it.
        show_in_front=True,
    )
    return True


def build_path(context, props, item, polyline_solver, canonical=None):
    """Create or refresh the surface path helper from a solver-space polyline.

    The stored polyline is converted, optionally lifted for display, and
    written into the helper curve. The lift is applied to the drawn copy only.
    """
    object_name = item.path_object or item.result_object
    if canonical is None:
        canonical = _canonical_for(context, props, object_name)
    if canonical is None:
        return False
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        return False

    points_local = solver_to_local(canonical, polyline_solver)
    if props.viz_surface_offset:
        radius = visualization.thickness_radius(
            props, props.viz_path_thickness_mm
        )
        points_local = offset_along_normals(
            canonical, points_local, radius * NORMAL_OFFSET_RADII
        )
    visualization.update_measurement_curve(
        context, props, item.stable_id, 'PATH',
        points_local, obj.matrix_world,
        tuple(props.viz_path_color), props.viz_path_thickness_mm,
        # The path lies ON the surface, so real occlusion is correct: the far
        # side of a wrapping geodesic should be hidden by the body.
        show_in_front=False,
    )
    return True


def _scene_for(context, props):
    """The Scene, from a context or from the props that live on it.

    The transform handler calls in with no context, so the Scene is taken
    from `props.id_data` - the same route attach.py already uses.
    """
    scene = getattr(context, "scene", None) if context is not None else None
    if scene is None and props is not None:
        scene = getattr(props, "id_data", None)
    return scene


def measurements_of(context, props):
    collection = None
    scene = _scene_for(context, props)
    if scene is not None:
        collection = getattr(scene, "bsmt_measurements", None)
    return collection


def visible_measurements(context, props):
    """Definitions whose visualisation should currently be drawn."""
    collection = state.get_measurements(context)
    if not collection:
        return []
    if props.viz_selected_only:
        item = state.active_measurement(context, props)
        return [item] if item is not None else []
    return [item for item in collection if item.show_visualization]


def refresh(context, props=None):
    """Rebuild which helpers exist, to match the current display settings.

    Never solves anything. A measurement with no cached path simply gets no
    path helper - enabling visibility must not trigger a path computation
    (sect. 10).
    """
    if props is None:
        props = state.get_props(context)
    if props is None:
        return "no props"
    collection = measurements_of(context, props)
    if collection is None:
        return "no measurements"

    wanted = {int(item.stable_id) for item in visible_measurements(context, props)}
    mode = props.viz_mode
    want_straight = mode in ('STRAIGHT', 'BOTH')
    want_path = mode in ('SURFACE', 'BOTH')

    drawn_straight = 0
    drawn_path = 0
    for item in collection:
        stable_id = int(item.stable_id)
        visible = stable_id in wanted

        # The straight chord is two points derived from the landmarks, so it
        # is rebuilt or removed freely - there is nothing to lose.
        if visible and want_straight and build_straight(context, props, item):
            drawn_straight += 1
        else:
            visualization.remove_measurement_helper(stable_id, 'STRAIGHT')

        # The path helper's curve IS the cached polyline, and computing it
        # costs tens of seconds. It is therefore only ever HIDDEN when it is
        # not wanted, and removed only when the cache is genuinely dead.
        if not item.path_valid:
            visualization.remove_measurement_helper(stable_id, 'PATH')
            continue

        canonical = _canonical_for(context, props, item.path_object)
        obj = bpy.data.objects.get(item.path_object)
        matrix = obj.matrix_world if obj is not None else None
        if not state.path_is_current(item, canonical, matrix):
            # The cache no longer describes this configuration. Dropping it
            # is the point: a path that is not current must not stay on
            # screen looking like one that is.
            state.clear_measurement_path(item)
            continue

        show_path = visible and want_path
        visualization.set_measurement_helper_visible(stable_id, 'PATH',
                                                     show_path)
        if show_path:
            drawn_path += 1

    sync_transforms(context, props)
    return "straight=%d path=%d" % (drawn_straight, drawn_path)


def sync_transforms(context, props=None):
    """Keep every measurement helper aligned with its scan. One matrix each."""
    if props is None:
        props = state.get_props(context)
    if props is None:
        return 0
    collection = measurements_of(context, props)
    if not collection:
        return 0
    matrices = {}
    synced = 0
    landmarks_collection = None
    scene = _scene_for(context, props)
    if scene is not None:
        landmarks_collection = getattr(scene, "bsmt_landmarks", None)
    for item in collection:
        for kind, object_name in (
            ('STRAIGHT', _straight_object_name(landmarks_collection, item)),
            ('PATH', item.path_object),
        ):
            if not object_name:
                continue
            if object_name not in matrices:
                obj = bpy.data.objects.get(object_name)
                matrices[object_name] = obj.matrix_world if obj else None
            matrix = matrices[object_name]
            if matrix is None:
                continue
            if visualization.sync_measurement_transform(
                item.stable_id, kind, matrix
            ):
                synced += 1
    return synced


def _straight_object_name(landmarks_collection, item):
    """Scan the straight helper belongs to, resolved by stable id."""
    if not landmarks_collection:
        return ""
    source = state.landmark_by_stable_id(landmarks_collection,
                                         item.source_stable_id)
    if source is None or not source.surface_point.valid:
        return ""
    return source.surface_point.source_object


def clear_for(item):
    """Remove one measurement's helpers. Its definition and result remain."""
    return visualization.remove_measurement_helpers(item.stable_id)


def clear_all():
    """Remove every measurement helper. A/B and landmarks are untouched."""
    return visualization.clear_measurement_helpers()
