"""Keep BSMT helper visualisation attached to a transformed scan.

A SurfacePoint is a canonical surface location (triangle + barycentric), so an
object transform cannot change it. What must change is where that location
appears in the world, and therefore where the markers and straight line draw.

    world = matrix_world @ (barycentric @ canonical_local_triangle_corners)

No ray cast, no re-pick, no BVH rebuild, no topology rebuild, and
triangle_index / barycentric are never touched.

Two runtime defects in 0.5.1 are fixed here, both of which stopped helpers
following the scan in real Blender while the offline maths passed:

1. The handlers were not decorated @persistent. Blender clears non-persistent
   application handlers when a .blend is loaded, so opening a scan file
   silently unregistered the handler.
2. Transform following required the canonical mesh to still be cached, and
   used "cache was cleared" as the signal for "geometry changed". A transform
   can clear that cache, after which helpers deliberately refused to move.
   Following a transform never needs the mesh at all: the object-local
   position is already stored on the SurfacePoint, and applying the object
   matrix to it cannot reproject anything onto modified geometry.

Set the "Transform Debug Log" checkbox to print [BSMT TRANSFORM] lines.
"""

import time
import traceback

import bpy
import numpy as np
from bpy.app.handlers import persistent

from . import geodesic, measurement, state, visualization

# Movement below this (world units) is not worth a property write.
_POSITION_EPSILON = 1e-9

# Never print more often than this while an outcome keeps repeating.
_LOG_INTERVAL_SECONDS = 0.5

_updating = False

# Runtime counters, surfaced by the Transform Handler Status operator.
STATS = {
    "fire_count": 0,
    "refresh_count": 0,
    "last_fire": 0.0,
    "last_outcome": "never fired",
    "last_error": "",
}

_last_log_time = 0.0
_last_signature = ""


def log(message):
    print("[BSMT TRANSFORM] " + message)


def _debug_enabled(props):
    return bool(getattr(props, "transform_debug", False))


def _should_log(signature):
    """Throttle: log a repeating outcome at most every _LOG_INTERVAL_SECONDS."""
    global _last_log_time, _last_signature
    now = time.monotonic()
    if signature != _last_signature or now - _last_log_time >= _LOG_INTERVAL_SECONDS:
        _last_log_time = now
        _last_signature = signature
        return True
    return False


def reconstructed_world(corners_local, barycentric, matrix_world):
    """World position of a canonical surface location. Pure maths, no bpy."""
    corners_local = np.asarray(corners_local, dtype=np.float64)
    barycentric = np.asarray(barycentric, dtype=np.float64)
    matrix_world = np.asarray(matrix_world, dtype=np.float64)
    return _apply(matrix_world, barycentric @ corners_local)


def _apply(matrix_world, local):
    matrix_world = np.asarray(matrix_world, dtype=np.float64)
    local = np.asarray(local, dtype=np.float64)
    return matrix_world[:3, :3] @ local + matrix_world[:3, 3]


def _changed(current, wanted):
    difference = np.abs(np.asarray(current, dtype=np.float64) - np.asarray(wanted))
    return float(difference.max()) > _POSITION_EPSILON


def _local_position(point, canonical):
    """Object-local position of a stored surface point, and where it came from.

    The canonical mesh is authoritative when it is cached AND still describes
    the same geometry. Otherwise the local position cached on the SurfacePoint
    is used: it is the same value, and using it cannot reproject anything,
    because no geometry is consulted at all.
    """
    triangle = point.triangle_index
    if (
        canonical is not None
        and canonical.geometry_hash == point.geometry_hash
        and 0 <= triangle < canonical.triangle_count
    ):
        corners = canonical.triangle_corners_local(triangle)
        bary = np.array(point.barycentric, dtype=np.float64)
        return bary @ corners, "canonical"
    return np.array(point.local_xyz, dtype=np.float64), "cached-local"


def refresh(props, watched=None, reason="manual"):
    """Re-derive helper world positions. Returns a short outcome string."""
    if props is None:
        return "no props"

    meshcache = geodesic.meshcache if geodesic.MESHCACHE_AVAILABLE else None
    multiplier = measurement.unit_multiplier(props.unit)
    debug = _debug_enabled(props)

    world_positions = {}
    notes = []
    moved_any = False

    for slot in ('A', 'B'):
        point = state.surface_point(props, slot)
        if not point.valid:
            notes.append("%s:not-picked" % slot)
            continue

        if watched is not None and point.source_object not in watched:
            world_positions[slot] = np.array(point.world_xyz, dtype=np.float64)
            notes.append("%s:not-watched" % slot)
            continue

        obj = bpy.data.objects.get(point.source_object)
        if obj is None:
            notes.append("%s:no-object" % slot)
            continue

        canonical = None
        if meshcache is not None:
            canonical = meshcache.peek(point.source_object)

        local, origin = _local_position(point, canonical)
        matrix = np.array(obj.matrix_world, dtype=np.float64)
        world = _apply(matrix, local)
        world_positions[slot] = world

        marker_moved = visualization.move_marker(slot, world)
        position_changed = _changed(point.world_xyz, world)
        if position_changed:
            point.world_xyz = tuple(float(v) for v in world)
            point.physical_mm_xyz = tuple(float(v) * multiplier for v in world)
            moved_any = True

        # Phase 1 straight-distance state follows the same surface location,
        # so the two can never drift apart.
        if slot == 'A' and _changed(props.point_a, world):
            props.point_a = tuple(float(v) for v in world)
        elif slot == 'B' and _changed(props.point_b, world):
            props.point_b = tuple(float(v) for v in world)

        notes.append(
            "%s:tri=%d src=%s moved=%s marker=%s"
            % (slot, point.triangle_index, origin,
               "yes" if position_changed else "no",
               "ok" if marker_moved else "MISSING")
        )

    line_state = "skipped"
    if 'A' in world_positions and 'B' in world_positions:
        line_state = "ok" if visualization.move_line(
            world_positions['A'], world_positions['B']
        ) else "MISSING"
        if props.distance_valid:
            distance = measurement.straight_distance_mm(
                world_positions['A'], world_positions['B'], props.unit
            )
            if abs(distance - props.distance_mm) > 1e-9:
                props.distance_mm = distance
                notes.append("distance=%.4f mm" % distance)

    STATS["refresh_count"] += 1
    outcome = "%s | line=%s | %s" % (reason, line_state, " ".join(notes) or "nothing")
    STATS["last_outcome"] = outcome

    if debug and _should_log(outcome):
        log("refresh: " + outcome)
    return outcome


def _watched_objects(props):
    names = set()
    for point in (props.surface_a, props.surface_b):
        if point.valid and point.source_object:
            names.add(point.source_object)
    return names


def _is_helper(identifier):
    try:
        return bool(identifier.get(visualization.HELPER_FLAG, False))
    except Exception:
        return False


def _describe_update(update):
    identifier = getattr(update.id, "original", None) or update.id
    return (
        identifier,
        getattr(identifier, "name", "?"),
        type(identifier).__name__,
        bool(getattr(update, "is_updated_transform", False)),
        bool(getattr(update, "is_updated_geometry", False)),
    )


@persistent
def _on_depsgraph_update(scene, depsgraph=None):
    """depsgraph_update_post handler. Must never raise into Blender."""
    global _updating
    if _updating:
        return

    STATS["fire_count"] += 1
    STATS["last_fire"] = time.monotonic()

    try:
        # Writes must land on the original scene, not an evaluated copy.
        original_scene = getattr(scene, "original", None) or scene
        props = getattr(original_scene, "bsmt", None)
        if props is None:
            STATS["last_outcome"] = "no add-on state on scene"
            return
        debug = _debug_enabled(props)

        watched = _watched_objects(props)
        if not watched:
            STATS["last_outcome"] = "no valid surface points"
            if debug and _should_log("no-points"):
                log("fired, but no valid SurfacePoints to follow")
            return

        if depsgraph is None:
            depsgraph = getattr(bpy.context, "evaluated_depsgraph_get", None)
            depsgraph = depsgraph() if depsgraph else None
        if depsgraph is None:
            STATS["last_outcome"] = "no depsgraph argument"
            return

        moved = set()
        seen = []
        for update in depsgraph.updates:
            identifier, name, kind, transform, geometry = _describe_update(update)
            helper = _is_helper(identifier)
            is_object = isinstance(identifier, bpy.types.Object)
            relevant = is_object and not helper and name in watched
            if relevant and (transform or geometry):
                moved.add(name)
            if debug and not helper:
                seen.append(
                    "id='%s' type=%s transform=%s geometry=%s watched=%s"
                    % (name, kind, transform, geometry, relevant)
                )

        if debug and seen and _should_log("updates:" + "|".join(seen)):
            log("fired with %d update(s)" % len(seen))
            for line in seen[:6]:
                log("  " + line)
            if len(seen) > 6:
                log("  ... %d more" % (len(seen) - 6))

        if not moved:
            STATS["last_outcome"] = "no watched object moved"
            return

        _updating = True
        try:
            refresh(props, watched=moved, reason="depsgraph")
        finally:
            _updating = False

    except Exception as exc:                          # noqa: BLE001
        # Report rather than swallow: a silent handler is how 0.5.1 hid its
        # own failure.
        _updating = False
        STATS["last_error"] = "%s: %s" % (type(exc).__name__, exc)
        STATS["last_outcome"] = "ERROR " + STATS["last_error"]
        log("handler error: " + STATS["last_error"])
        traceback.print_exc()


def _purge(handler_list, function):
    """Remove any copy of this handler, including stale ones from a reload.

    After Reload Scripts the old module's function object is a different
    object, so an identity check would leave a duplicate behind that still
    points at the previous module.
    """
    removed = 0
    for existing in list(handler_list):
        if existing is function or (
            getattr(existing, "__name__", "") == function.__name__
            and getattr(existing, "__module__", "").endswith("attach")
        ):
            handler_list.remove(existing)
            removed += 1
    return removed


def handler_count():
    """(exact instances, name matches) currently registered."""
    handlers = bpy.app.handlers.depsgraph_update_post
    exact = sum(1 for h in handlers if h is _on_depsgraph_update)
    named = sum(
        1 for h in handlers
        if getattr(h, "__name__", "") == _on_depsgraph_update.__name__
        and getattr(h, "__module__", "").endswith("attach")
    )
    return exact, named


def register():
    handlers = bpy.app.handlers.depsgraph_update_post
    _purge(handlers, _on_depsgraph_update)
    handlers.append(_on_depsgraph_update)


def unregister():
    _purge(bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update)
