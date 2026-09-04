"""Landmark markers and name labels, drawn as a viewport overlay (3.8, 3.9).

Why an overlay and not helper objects
-------------------------------------
Both halves of this module used to be objects: a UV-sphere per landmark and,
in the alternative that was rejected, a Text object per landmark. Objects are
the wrong tool for an annotation. They are real datablocks - in the Outliner,
in the selection, in the depsgraph, and exportable by accident - and they are
measured in WORLD units, so a marker that reads well on a whole body becomes a
boulder when the researcher zooms in on an acromion, and a label becomes
unreadable at the same moment.

A `SpaceView3D` draw handler in POST_PIXEL space fixes both. Everything is
positioned by projecting the landmark's world position to the region once, and
then sized in SCREEN PIXELS. A 6 px marker is 6 px across at any zoom, at any
depth, in perspective or orthographic, and every landmark is therefore exactly
the same size as every other - which is the whole point of a marker whose job
is "here, precisely".

What things are anchored to
---------------------------
`SurfacePoint.world_xyz`, which `attach.refresh_landmarks` keeps current from
triangle + barycentric + the live object matrix. A rigid transform, Apply
Alignment and Reset Alignment all go through that one path, so markers and
labels follow the scan without this module knowing anything about transforms.
Marker and label share a single projection per landmark, so they can never
separate.

The label TEXT is read from the landmark on every redraw. There is no cache,
so renaming a landmark shows the new name on the next frame.

Drawing is on top of the scan rather than depth-tested. A POST_PIXEL callback
has no usable depth buffer, and for landmark work the alternative is worse: a
landmark on the far side of the body would be silently invisible rather than
visibly behind.
"""

import math

import bpy

from . import geodesic, landmarks, readiness, visualization

#: The draw handler lives in the driver namespace rather than a module global
#: so it survives Blender's "Reload Scripts", which re-imports this module and
#: would otherwise lose the handle and leak an un-removable callback.
_HANDLE_KEY = "bsmt_landmark_overlay_handler"

#: Screen-space defaults, in pixels. Not millimetres: a marker and a label are
#: annotations on the screen, not features of the body (sect. 3, 4).
DEFAULT_MARKER_SIZE = 6
DEFAULT_LABEL_SIZE = 13
DEFAULT_LABEL_OFFSET = 8

#: The selected landmark is emphasised with a RING drawn outside its core, and
#: with a slightly larger label. The core disc is deliberately never resized:
#: every landmark must read as the same size, or the marker stops meaning
#: "this exact point" (sect. 4).
SELECTED_RING_GAP = 2.0
SELECTED_RING_WIDTH = 2.0
SELECTED_LABEL_BONUS = 2
SELECTED_RING_COLOR = (1.0, 1.0, 1.0, 0.95)

#: Segments in a marker disc. Sixteen is round to the eye at 20 px and costs
#: 48 vertices; the whole overlay is one batch per colour, so 100 landmarks is
#: still a handful of draw calls.
DISC_SEGMENTS = 16

#: A cap so a pathological scene cannot make the viewport unusable. A protocol
#: of 100 landmarks draws in full.
MAX_LANDMARKS = 512

#: How much nearer than the landmark something has to be before it counts as
#: hiding it (Milestone 3.10). A FRACTION of the distance from the viewer, so
#: it means the same thing at any zoom and in any unit: at a 2 m view distance
#: 1e-3 is 2 mm.
#:
#: A tolerance is unavoidable. The landmark sits exactly ON the surface, so the
#: ray that looks for an occluder hits that same surface at the landmark's own
#: position; without a margin every landmark would hide itself. Near a
#: silhouette the ray grazes the body and hits a neighbouring triangle a
#: fraction in front, which is why the margin is millimetres rather than
#: microns - and still nowhere near the ~200 mm of body thickness that hides a
#: landmark on the far side.
OCCLUSION_TOLERANCE = 1e-3


# ---------------------------------------------------------------------------
# what to draw - pure, and therefore testable without a viewport
# ---------------------------------------------------------------------------

def label_text(name, status):
    """The text for one landmark.

    A landmark that is not VALID says so in words as well as in colour.
    Colour alone is not a report: it is invisible to a colour-blind reader and
    it does not survive a screenshot pasted into a paper.
    """
    if status == landmarks.STATUS_VALID:
        return name
    short = landmarks.STATUS_SHORT.get(status, status)
    return "%s  [%s]" % (name, short)


def status_color(status, base_color):
    """The colour for one landmark's marker and label.

    `base_color` is the researcher's setting and applies to a VALID landmark.
    Anything else takes the colour the existing status system already assigns,
    so no new colour vocabulary is invented here and a landmark that cannot be
    trusted can never be made to look like one that can.
    """
    if status == landmarks.STATUS_VALID:
        return tuple(float(v) for v in base_color)
    assigned = visualization.landmark_color(status)
    return (float(assigned[0]), float(assigned[1]), float(assigned[2]),
            float(base_color[3]))


def entries(props, collection, active_index=-1, emphasise=True):
    """Everything the draw callback needs for one landmark, as plain data.

    One entry per landmark carries BOTH its marker and its label, because the
    two share a single projection and must never be able to separate.

    Returns dicts with `world`, `radius`, `marker_color`, `selected`,
    `emphasised`, and - when labels are on and this landmark is in scope -
    `text`, `label_color` and `label_size`.

    `selected` and `emphasised` are deliberately two different things.
    `selected` is which row the Landmark Manager list is on, and it decides
    what SELECTED label scope means - suppressing it would hide a label, which
    is a different change entirely. `emphasised` is only whether that landmark
    is drawn with the selection RING and the larger label, and it is switched
    off once the researcher has moved on to another workflow stage, where the
    ring marks one landmark out for a reason nobody looking at the viewport
    could reconstruct.

    Nothing about the ordinary markers changes either way: same radius, same
    colour, same set of landmarks drawn.
    """
    if collection is None:
        return []
    show_markers = bool(props.show_landmarks)
    show_labels = bool(props.show_landmark_labels)
    if not show_markers and not show_labels:
        return []

    marker_base = tuple(float(v) for v in props.landmark_marker_color)
    label_base = tuple(float(v) for v in props.landmark_label_color)
    radius = max(1.0, float(props.landmark_marker_size_px) * 0.5)
    label_size = int(props.landmark_label_size)
    labels_selected_only = props.landmark_label_scope == 'SELECTED'

    result = []
    for index, item in enumerate(collection):
        point = item.surface_point
        if not point.valid:
            # An unpicked landmark has no position, so there is nowhere
            # honest to put a marker or a label.
            continue
        selected = index == int(active_index)
        entry = {
            "stable_id": int(item.stable_id),
            "source_object": str(point.source_object),
            "world": (float(point.world_xyz[0]), float(point.world_xyz[1]),
                      float(point.world_xyz[2])),
            "selected": selected,
            "emphasised": selected and bool(emphasise),
            "show_marker": show_markers,
            # Every marker is the same radius. The selection is shown with a
            # ring, never with a bigger core (sect. 4).
            "radius": radius,
            "marker_color": status_color(item.status, marker_base),
        }
        if show_labels and not (labels_selected_only and not selected):
            entry["text"] = label_text(item.label, item.status)
            entry["label_color"] = status_color(item.status, label_base)
            entry["label_size"] = label_size + (
                SELECTED_LABEL_BONUS if entry["emphasised"] else 0)
        result.append(entry)
        if len(result) >= MAX_LANDMARKS:
            break
    return result


def labelled(props, collection, active_index=-1, emphasise=True):
    """Only the landmarks that will get a label. Convenience over `entries`."""
    return [entry
            for entry in entries(props, collection, active_index, emphasise)
            if "text" in entry]


# ---------------------------------------------------------------------------
# screen-space geometry - also pure
# ---------------------------------------------------------------------------

def disc_triangles(center, radius, segments=DISC_SEGMENTS):
    """A filled circle as a flat list of 2D triangle vertices.

    Explicit triangles rather than a triangle fan: `TRI_FAN` was removed from
    Blender's GPU module in 3.2, and a flat `TRIS` list is the form that works
    on every backend.
    """
    x, y = float(center[0]), float(center[1])
    step = 2.0 * math.pi / int(segments)
    vertices = []
    for index in range(int(segments)):
        a0 = index * step
        a1 = (index + 1) * step
        vertices.append((x, y))
        vertices.append((x + radius * math.cos(a0), y + radius * math.sin(a0)))
        vertices.append((x + radius * math.cos(a1), y + radius * math.sin(a1)))
    return vertices


def ring_lines(center, radius, segments=DISC_SEGMENTS):
    """A circle outline as a flat list of 2D line-segment vertices.

    `LINE_LOOP` went the same way as `TRI_FAN`, so the loop is closed by
    emitting each segment as its own pair.
    """
    x, y = float(center[0]), float(center[1])
    step = 2.0 * math.pi / int(segments)
    vertices = []
    for index in range(int(segments)):
        a0 = index * step
        a1 = (index + 1) * step
        vertices.append((x + radius * math.cos(a0), y + radius * math.sin(a0)))
        vertices.append((x + radius * math.cos(a1), y + radius * math.sin(a1)))
    return vertices


def is_occluded(hit_distance, landmark_distance,
                tolerance=OCCLUSION_TOLERANCE):
    """Is something hiding a landmark at `landmark_distance` from the viewer?

    Pure, so the rule can be tested without a viewport. `hit_distance` is where
    the view ray first meets the mesh, or None when it misses entirely.

    A miss means nothing is in the way. A hit at (or behind) the landmark is
    the landmark's own surface, which does not hide it. Only a hit clearly in
    FRONT of it does.
    """
    if hit_distance is None:
        return False
    if landmark_distance <= 0.0:
        return False
    margin = float(landmark_distance) * float(tolerance)
    return float(hit_distance) < float(landmark_distance) - margin


def selected_ring_radius(radius):
    """Where the selection ring sits: outside the core, never replacing it."""
    return float(radius) + SELECTED_RING_GAP


def group_by_color(items):
    """Group (color, vertices) pairs into one vertex list per colour.

    The whole overlay is then a handful of batches rather than one per
    landmark, which is what keeps 100 landmarks free.
    """
    grouped = {}
    order = []
    for color, vertices in items:
        key = tuple(round(float(v), 6) for v in color)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].extend(vertices)
    return [(key, grouped[key]) for key in order]


def marker_batches(drawn):
    """(disc groups, ring groups) for a list of projected entries.

    `drawn` is a list of dicts with `screen`, `radius`, `marker_color` and
    `emphasised`. Pure: it returns vertex lists, and draws nothing.

    The ring is the only thing `emphasised` removes. Every disc is still
    built, at the configured radius and colour, so switching the emphasis off
    never rebuilds or hides a marker - the ring batch simply comes back empty.
    """
    discs = []
    rings = []
    for entry in drawn:
        if not entry.get("show_marker", True):
            continue
        discs.append((entry["marker_color"],
                      disc_triangles(entry["screen"], entry["radius"])))
        if entry.get("emphasised"):
            rings.append((SELECTED_RING_COLOR,
                          ring_lines(entry["screen"],
                                     selected_ring_radius(entry["radius"]))))
    return group_by_color(discs), group_by_color(rings)


# ---------------------------------------------------------------------------
# visibility against the mesh (Milestone 3.10)
# ---------------------------------------------------------------------------

def hide_occluded(drawn, region, rv3d):
    """Drop the landmarks the mesh is standing in front of.

    `drawn` are entries that already carry their `screen` position, so the
    view ray is built from that projection - the same one the marker is drawn
    at - rather than from a second, possibly disagreeing, camera model. That
    also makes this correct in an orthographic view, where there is no single
    eye point and `region_2d_to_origin_3d` returns a per-pixel origin.

    The canonical mesh is read with `peek()`, which never builds one. A draw
    callback must not be able to start a rebuild, and a mesh that has not been
    analysed yet simply occludes nothing.
    """
    from bpy_extras.view3d_utils import (region_2d_to_origin_3d,
                                         region_2d_to_vector_3d)

    meshcache = (geodesic.meshcache
                 if geodesic.MESHCACHE_AVAILABLE else None)
    if meshcache is None:
        return drawn

    # One canonical lookup, one matrix and ONE matrix inverse per object -
    # not per landmark. Built fresh every frame on purpose: nothing here is
    # remembered between redraws, so a moved object or a re-analysed mesh is
    # picked up at once.
    per_object = {}
    visible = []
    for entry in drawn:
        name = entry.get("source_object") or ""
        if name not in per_object:
            canonical = meshcache.peek(name) if name else None
            obj = bpy.data.objects.get(name) if name else None
            per_object[name] = (
                meshcache.hit_distance_caster(canonical, obj.matrix_world)
                if canonical is not None and obj is not None else None
            )
        cast = per_object[name]
        if cast is None:
            # Nothing to test against. Showing the landmark is the honest
            # failure direction: hiding one because the mesh is not cached
            # would look like the landmark had been lost.
            visible.append(entry)
            continue

        origin = region_2d_to_origin_3d(region, rv3d, entry["screen"])
        direction = region_2d_to_vector_3d(region, rv3d, entry["screen"])
        if origin is None or direction is None:
            visible.append(entry)
            continue

        world = entry["world"]
        landmark_distance = math.sqrt(
            (world[0] - origin[0]) ** 2 + (world[1] - origin[1]) ** 2
            + (world[2] - origin[2]) ** 2)
        hit = cast(origin, direction)
        if not is_occluded(hit, landmark_distance):
            visible.append(entry)
    return visible


# ---------------------------------------------------------------------------
# the draw callback - as thin as it can be, because it cannot run headless
# ---------------------------------------------------------------------------

def _set_font_size(font_id, size):
    """blf.size lost its dpi argument in Blender 4.0; accept both."""
    import blf
    try:
        blf.size(font_id, size)
    except TypeError:                                # pragma: no cover
        blf.size(font_id, size, 72)


def _shader():
    """The builtin flat-colour shader.

    Built on first use, never at import: GPU shaders cannot be created in
    background mode, and this module must import there for the tests to run.
    """
    import gpu
    return gpu.shader.from_builtin('UNIFORM_COLOR')


def _draw_markers(discs, rings):
    import gpu
    from gpu_extras.batch import batch_for_shader

    shader = _shader()
    gpu.state.blend_set('ALPHA')
    try:
        for color, vertices in discs:
            batch = batch_for_shader(shader, 'TRIS', {"pos": vertices})
            shader.bind()
            shader.uniform_float("color", color)
            batch.draw(shader)
        if rings:
            gpu.state.line_width_set(SELECTED_RING_WIDTH)
            for color, vertices in rings:
                batch = batch_for_shader(shader, 'LINES', {"pos": vertices})
                shader.bind()
                shader.uniform_float("color", color)
                batch.draw(shader)
            gpu.state.line_width_set(1.0)
    finally:
        gpu.state.blend_set('NONE')


def _draw():
    """POST_PIXEL callback. Reads the scene; never writes to it."""
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    context = bpy.context
    space = getattr(context, "space_data", None)
    if space is None or space.type != 'VIEW_3D':
        return
    props = getattr(context.scene, "bsmt", None)
    if props is None:
        return
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return

    collection = getattr(context.scene, "bsmt_landmarks", None)
    # The selection index is read unchanged - the emphasis is a display
    # decision layered on top of it, never a change to what is selected.
    wanted = entries(props, collection, props.landmark_index,
                     emphasise=readiness.landmark_emphasis_visible(
                         getattr(props, "ui_stage", readiness.STAGE_LANDMARKS)))
    if not wanted:
        return

    # Project once per landmark. The marker and its label are placed from the
    # same result, so they cannot drift apart.
    drawn = []
    for entry in wanted:
        position = location_3d_to_region_2d(region, rv3d, entry["world"])
        if position is None:
            # Behind the camera, or outside the region. Not an error: there is
            # simply nowhere on screen to put it.
            continue
        entry = dict(entry)
        entry["screen"] = (float(position[0]), float(position[1]))
        drawn.append(entry)

    # Sect. 1: hide what the body is standing in front of. Both the marker and
    # its label go, together - a name floating where its marker is not would
    # be worse than either.
    if props.landmark_visibility == 'OCCLUDED':
        try:
            drawn = hide_occluded(drawn, region, rv3d)
        except Exception:                            # pragma: no cover
            # A visibility failure must never blank the overlay. Showing
            # everything is the behaviour of the other mode, not a broken one.
            import traceback
            traceback.print_exc()
    if not drawn:
        return

    try:
        discs, rings = marker_batches(drawn)
        if discs or rings:
            _draw_markers(discs, rings)
    except Exception:                                # pragma: no cover
        # A drawing failure must never take the viewport down with it, and it
        # must not stop the labels from being drawn either.
        import traceback
        traceback.print_exc()

    font_id = 0
    offset = int(props.landmark_label_offset)
    shadow = bool(props.landmark_label_shadow)
    if shadow:
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.9)
        blf.shadow_offset(font_id, 1, -1)
    try:
        for entry in drawn:
            if "text" not in entry:
                continue
            _set_font_size(font_id, entry["label_size"])
            color = entry["label_color"]
            blf.color(font_id, color[0], color[1], color[2], color[3])
            # Offset in SCREEN space so the text never sits on top of the
            # exact surface point it names.
            blf.position(font_id, entry["screen"][0] + offset,
                         entry["screen"][1] + offset, 0.0)
            blf.draw(font_id, entry["text"])
    finally:
        if shadow:
            blf.disable(font_id, blf.SHADOW)


# ---------------------------------------------------------------------------
# handler lifetime
# ---------------------------------------------------------------------------

def is_registered():
    return bpy.app.driver_namespace.get(_HANDLE_KEY) is not None


def register():
    """Install the overlay. Idempotent, and safe across Reload Scripts."""
    unregister()
    handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw, (), 'WINDOW', 'POST_PIXEL')
    bpy.app.driver_namespace[_HANDLE_KEY] = handle
    return handle


def unregister():
    """Remove the overlay if one is installed. Never raises."""
    handle = bpy.app.driver_namespace.pop(_HANDLE_KEY, None)
    if handle is None:
        return False
    try:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    except Exception:                                # pragma: no cover
        # The handle can already be gone after a file load; losing it is not
        # a reason to break registration.
        return False
    return True


def tag_redraw(context=None):
    """Ask every 3D viewport to redraw. Returns how many were tagged.

    A marker or label setting changes no object, so nothing makes Blender
    repaint on its own; without this, changing Marker Size would appear to do
    nothing until the viewport redrew for some other reason.
    """
    windows = getattr(getattr(bpy.context, "window_manager", None),
                      "windows", None)
    if windows is None:
        return 0
    tagged = 0
    for window in windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
                tagged += 1
    return tagged
