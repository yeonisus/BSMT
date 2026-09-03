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

from . import measurements, pathcache, state, timing, visualization

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


def surface_normals(canonical, points_local):
    """Unit surface normal at each point, from the canonical BVH.

    (k, 3) float64, with a zero row wherever the nearest surface could not be
    found - a zero normal means "leave this point exactly where it is" rather
    than a guessed direction.

    This is the expensive half of the display lift: one BVH query per point,
    measured at 138 ms for 20,000 points on a 261k-triangle scan. It is
    called ONCE, at solve time, and the result is cached alongside the
    polyline, so every later thickness or offset change is a vectorised
    multiply-add instead of thousands of queries (see pathcache.py).
    """
    points = np.asarray(points_local, dtype=np.float64)
    normals = np.zeros(points.shape, dtype=np.float64)
    if points.size == 0:
        return normals
    bvh = getattr(canonical, "bvh", None)
    if bvh is None:
        return normals
    with timing.stage(timing.NORMAL_SAMPLE, "%d points" % points.shape[0]):
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
                normals[index] = normal / length
    return normals


def offset_along_normals(canonical, points_local, distance):
    """Lift points off the surface along its normal. Display only.

    The slow route, kept for a cache entry that has no usable normals. The
    fast route is ``pathcache.lift`` with the normals stored at solve time.
    """
    points = np.asarray(points_local, dtype=np.float64)
    if points.size == 0 or distance <= 0.0:
        return points.copy()
    return pathcache.lift(points, surface_normals(canonical, points), distance)


def _usable_normals(normals):
    """Whether a cached normal set can drive the display lift at all."""
    array = np.asarray(normals, dtype=np.float64)
    return bool(array.size) and bool(np.any(np.linalg.norm(array, axis=1) > 0.0))


def _repair_normals(context, props, item, points_local, normals_local):
    """Sample and store missing normals once, rather than every redisplay.

    A cache written when no canonical mesh was available holds zero normals,
    and a zero normal means "do not move this point" - so the path would draw
    correctly but flat against the surface, for ever. If the canonical mesh
    is available NOW, the normals are sampled once and written back, so the
    cost is paid a single time and never again.
    """
    canonical = _canonical_for(context, props,
                               item.path_object or item.result_object)
    if canonical is None:
        return normals_local
    sampled = surface_normals(canonical, points_local)
    if not _usable_normals(sampled):
        return normals_local
    pathcache.store(item.stable_id, points_local, sampled)
    return sampled


def _canonical_for(context, props, object_name):
    """The cached canonical mesh for a scan, or None.

    ``peek`` only, and there is deliberately no option to do otherwise.
    Building a canonical mesh costs 1.7 s per million triangles, and nothing
    in this module - which runs from panel draws, update callbacks and a
    depsgraph handler - is ever entitled to spend that. A scan that has not
    been analysed simply returns None, and the checks that need it are
    skipped rather than forced.
    """
    from . import geodesic
    if not geodesic.MESHCACHE_AVAILABLE or geodesic.meshcache is None:
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
    """Cache and draw a freshly solved surface path.

    Called from exactly one place - the Compute Surface Path operator, with
    the polyline that solve has just returned. It converts to the scan's
    LOCAL space, samples the surface normals once, writes both into the
    measurement's cache, and then draws from that cache like any other
    redisplay. The drawn copy may be lifted off the surface; the stored
    polyline and every reported length are unaffected (sect. 9).
    """
    object_name = item.path_object or item.result_object
    if canonical is None:
        canonical = _canonical_for(context, props, object_name)
    if canonical is None:
        return False
    if bpy.data.objects.get(object_name) is None:
        return False

    with timing.stage(timing.RESULT_COPY) as measured:
        points_local = np.array(
            solver_to_local(canonical, polyline_solver), dtype=np.float64
        )
        measured.note("%d points" % points_local.shape[0])
    normals_local = surface_normals(canonical, points_local)

    with timing.stage(timing.CACHE_STORE) as measured:
        stored = pathcache.store(item.stable_id, points_local, normals_local)
        measured.note("%d points" % stored)
    if not stored:
        return False
    item.path_point_count = int(stored)
    return draw_cached_path(context, props, item, canonical=canonical)


#: Custom property recording what a drawn helper was built from. Rewriting a
#: 20,000-point curve costs about 5 ms and re-evaluates its bevel, so a
#: redisplay that would produce identical points must not do it - and
#: "identical" is decided by comparing this string, not the points.
DRAW_SIGNATURE_KEY = "bsmt_path_draw_signature"


def _draw_signature(item, lift_distance, object_name):
    return "%d|%d|%.9g|%s" % (
        int(item.stable_id),
        pathcache.generation(item.stable_id),
        float(lift_distance),
        object_name,
    )


def _lift_distance(props):
    """How far the drawn path is pushed off the surface. Zero when off."""
    if not props.viz_surface_offset:
        return 0.0
    radius = visualization.thickness_radius(props, props.viz_path_thickness_mm)
    return radius * NORMAL_OFFSET_RADII


def draw_cached_path(context, props, item, canonical=None, force=False):
    """Draw a measurement's path from its cache. Never solves anything.

    This is the whole cache-hit path, and it is deliberately the only route
    that ever creates a path helper. It reads the stored local polyline,
    applies the display lift with the stored normals - one vectorised
    multiply-add, no geometry query - and writes the result into the helper
    curve. No canonical mesh is needed and none is built, so a cached path
    redisplays at the same speed on a 20k-triangle mesh and a 1M-triangle one.

    An existing helper that was built from the same cache generation and the
    same lift distance is left alone: its points would come out identical,
    and rewriting them would re-evaluate the bevel for nothing (sect. 6).
    """
    object_name = item.path_object or item.result_object
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        return False

    lift_distance = _lift_distance(props)
    signature = _draw_signature(item, lift_distance, object_name)
    helper = visualization.measurement_helper(item.stable_id, 'PATH')
    if not force and helper is not None and \
            helper.get(DRAW_SIGNATURE_KEY, "") == signature:
        # Same cache, same lift: the geometry on screen is already right.
        # Colour, thickness and transform are still pushed, and each of those
        # writes is itself guarded, so an unchanged one costs nothing.
        with timing.stage(timing.HELPER_UPDATE, "unchanged"):
            visualization.restyle_measurement_curve(
                props, helper, tuple(props.viz_path_color),
                props.viz_path_thickness_mm, obj.matrix_world,
            )
        return True

    with timing.stage(timing.CACHE_LOAD) as measured:
        cached = pathcache.load(item.stable_id)
        measured.note("miss" if cached is None
                      else "%d points" % cached[0].shape[0])
    if cached is None:
        return False
    points_local, normals_local = cached

    if lift_distance > 0.0:
        if not _usable_normals(normals_local):
            normals_local = _repair_normals(context, props, item,
                                            points_local, normals_local)
            signature = _draw_signature(item, lift_distance, object_name)
        points_local = pathcache.lift(points_local, normals_local,
                                      lift_distance)

    label = timing.HELPER_UPDATE if helper is not None else timing.HELPER_CREATE
    with timing.stage(label, "%d points" % points_local.shape[0]):
        helper = visualization.update_measurement_curve(
            context, props, item.stable_id, 'PATH',
            points_local, obj.matrix_world,
            tuple(props.viz_path_color), props.viz_path_thickness_mm,
            # The path lies ON the surface, so real occlusion is correct: the
            # far side of a wrapping geodesic should be hidden by the body.
            show_in_front=False,
        )
    helper[DRAW_SIGNATURE_KEY] = signature
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
    """Definitions whose visualisation should currently be drawn (sect. 10).

    Three scopes, and no fourth hidden one: the selected measurement, the
    ones ticked for display, or every enabled measurement. Drafts are never
    drawn - there is nothing to draw between two landmarks that have not been
    chosen.

    This function decides what SHOULD be visible. It never computes anything,
    so widening the scope cannot trigger a surface-path solve; a measurement
    with no cached path simply gets no path helper.
    """
    collection = state.get_measurements(context)
    if not collection:
        return []
    scope = getattr(props, "viz_scope", 'SELECTED')
    if scope == 'SELECTED':
        item = state.active_measurement(context, props)
        if item is None or state.measurement_is_draft(item):
            return []
        return [item]
    if scope == 'ENABLED':
        candidates = [item for item in collection if item.enabled]
    elif scope == 'TICKED':
        candidates = [item for item in collection if item.show_visualization]
    else:
        # An unknown scope shows nothing rather than guessing at one.
        return []
    return [item for item in candidates
            if not state.measurement_is_draft(item)]


def path_state(context, props, item):
    """(state, reason) for one measurement's path. Reads only; never solves."""
    canonical = _canonical_for(context, props,
                               item.path_object or item.result_object)
    obj = bpy.data.objects.get(item.path_object) if item.path_object else None
    matrix = obj.matrix_world if obj is not None else None
    return state.path_cache_state(context, item, canonical, matrix)


def display_report(context, props):
    """What the visualisation panel should say about the current scope.

    Separates "drawn" from "has no cached path yet", so a researcher showing
    ten measurements can see at a glance which ones are missing a path
    without any of them being recomputed to find out.
    """
    wanted = visible_measurements(context, props)
    mode = getattr(props, "viz_mode", 'BOTH')
    want_path = mode in ('SURFACE', 'BOTH')
    with_path = []
    without_path = []
    stale = []
    for item in wanted:
        current, _reason = path_state(context, props, item)
        if current == state.PATH_CACHED:
            with_path.append(item.label)
        elif current == state.PATH_STALE:
            stale.append(item.label)
        else:
            without_path.append(item.label)
    return {
        "count": len(wanted),
        "with_path": with_path,
        "without_path": without_path,
        "stale": stale,
        "path_wanted": want_path,
    }


def refresh(context, props=None):
    """Rebuild which helpers exist, to match the current display settings.

    Never solves anything, and - since 0.20.0 - never destroys a cached path
    either. A measurement whose path is stale is HIDDEN and reported as
    stale; the polyline stays in its cache so the researcher decides whether
    a solve that costs minutes is worth running again (sect. 9).
    """
    if props is None:
        props = state.get_props(context)
    if props is None:
        return "no props"
    collection = measurements_of(context, props)
    if collection is None:
        return "no measurements"

    wanted = {int(item.stable_id)
              for item in visible_measurements(context, props)}
    mode = props.viz_mode
    want_straight = mode in ('STRAIGHT', 'BOTH')
    want_path = mode in ('SURFACE', 'BOTH')

    drawn_straight = 0
    drawn_path = 0
    stale = 0
    for item in collection:
        stable_id = int(item.stable_id)
        visible = stable_id in wanted

        # The straight chord is two points read from the landmarks. It costs
        # nothing to build and calls nothing expensive, so it is rebuilt or
        # removed freely - there is nothing to lose (sect. 1).
        if visible and want_straight and build_straight(context, props, item):
            drawn_straight += 1
        else:
            visualization.remove_measurement_helper(stable_id, 'STRAIGHT')

        if not (visible and want_path and item.path_shown):
            # Out of scope, or hidden by Show/Hide Path: hide the helper and
            # keep the cache. Hiding a path must never be able to cost a
            # solve to undo, and the choice has to survive the next refresh
            # or the button would undo itself.
            visualization.set_measurement_helper_visible(stable_id, 'PATH',
                                                         False)
            continue

        current, _reason = path_state(context, props, item)
        if current != state.PATH_CACHED:
            # NOT COMPUTED or STALE: hide, and change nothing. A path that is
            # not current must not sit on screen looking like one that is,
            # and nothing is deleted or recomputed to achieve that.
            #
            # INVALID is the one case where the drawing is also removed: its
            # polyline, its landmark or its scan is gone, so there is nothing
            # left for the helper to be a picture of. No cache is discarded -
            # there is none to discard.
            if current == state.PATH_INVALID:
                visualization.remove_measurement_helper(stable_id, 'PATH')
            else:
                visualization.set_measurement_helper_visible(stable_id, 'PATH',
                                                             False)
            if current == state.PATH_STALE:
                stale += 1
            continue

        if draw_cached_path(context, props, item):
            visualization.set_measurement_helper_visible(stable_id, 'PATH',
                                                         True)
            drawn_path += 1

    sync_transforms(context, props)
    return "straight=%d path=%d stale=%d" % (drawn_straight, drawn_path, stale)


def sync_transforms(context, props=None):
    """Keep every measurement helper aligned with its scan. One matrix each.

    Called from the transform handler, so it must stay cheap and must never
    consult geometry. It does not: it reads each scan's live matrix once,
    then hands it to a helper that writes it only when it has actually
    changed. Rewriting an unchanged matrix would tag the helper for
    re-evaluation on every depsgraph tick, and a beveled curve with
    thousands of points is not free to re-evaluate.
    """
    if props is None:
        props = state.get_props(context)
    if props is None:
        return 0
    collection = measurements_of(context, props)
    if not collection:
        return 0
    landmarks_collection = None
    scene = _scene_for(context, props)
    if scene is not None:
        landmarks_collection = getattr(scene, "bsmt_landmarks", None)

    with timing.stage(timing.HELPER_TRANSFORM) as measured:
        matrices = {}
        synced = 0
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
        measured.note("%d helper(s) moved" % synced)
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
