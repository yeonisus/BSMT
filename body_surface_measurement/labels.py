"""Landmark name labels drawn as a viewport overlay (Milestone 3.8).

Why an overlay and not one Text object per landmark
---------------------------------------------------
A Blender Text object would be a real datablock: it appears in the Outliner,
takes part in selection and the depsgraph, has to be rotated to face the
viewer every frame, and scales in world units so it becomes unreadable as
soon as the researcher zooms. Fifty landmarks would mean fifty extra objects
in a file that must stay recognisably the researcher's scan.

A `SpaceView3D` draw handler in POST_PIXEL space avoids every one of those:
the text is drawn in screen pixels (so it is the same size at any zoom), it
creates no datablock, and it cannot be selected, moved or exported by
accident. Nothing here writes to the scene at all - the callback only reads.

What the label is anchored to
-----------------------------
The marker helper object's world location, when it exists. The marker and the
label then move together *by construction*: whatever moves the marker - a
translate, a rotate, Apply Alignment, Reset Alignment - has already moved the
thing the label is positioned from, so the two cannot drift apart. The stored
`SurfacePoint.world_xyz` is the fallback for a landmark whose marker has been
removed, and is the same value by definition.

The label TEXT is read from the landmark on every redraw. There is no cache,
so renaming a landmark shows the new name on the next frame with nothing to
invalidate (sect. 10).
"""

import bpy

from . import landmarks, visualization

#: The draw handler lives in the driver namespace rather than a module global
#: so it survives Blender's "Reload Scripts", which re-imports this module and
#: would otherwise lose the handle and leak an un-removable callback.
_HANDLE_KEY = "bsmt_landmark_label_handler"

#: Screen-space label defaults. Pixels, not millimetres: a label is an
#: annotation on the screen, not a feature of the body (sect. 4).
DEFAULT_LABEL_SIZE = 13
DEFAULT_LABEL_OFFSET = 8

#: The selected landmark's label is drawn this many pixels larger. The
#: matching marker emphasis lives in visualization.SELECTED_MARKER_SCALE -
#: one definition each, so the two cannot drift (sect. 6).
SELECTED_SIZE_BONUS = 2

#: A label is not drawn past this many landmarks-worth of text in one frame.
#: A protocol of 100 landmarks draws in full; the cap only exists so that a
#: pathological scene cannot make the viewport unusable.
MAX_LABELS = 512


# ---------------------------------------------------------------------------
# what to draw - pure, and therefore testable without a viewport
# ---------------------------------------------------------------------------

def label_text(name, status):
    """The text for one landmark.

    A landmark that is not VALID says so in words as well as in colour.
    Colour alone is not a report: it is invisible to a colour-blind reader and
    it does not survive a screenshot pasted into a paper (sect. 9).
    """
    if status == landmarks.STATUS_VALID:
        return name
    short = landmarks.STATUS_SHORT.get(status, status)
    return "%s  [%s]" % (name, short)


def label_color(status, base_color):
    """The colour for one label.

    `base_color` is the researcher's Label Color and is used for a VALID
    landmark. Anything else takes the colour the existing status system
    already assigns to that status, so the label and the marker agree and no
    new colour vocabulary is invented here (sect. 9).
    """
    if status == landmarks.STATUS_VALID:
        return tuple(float(v) for v in base_color)
    status_color = visualization.landmark_color(status)
    return (float(status_color[0]), float(status_color[1]),
            float(status_color[2]), float(base_color[3]))


def label_entries(props, collection, active_index=-1):
    """Everything the draw callback needs, as plain data.

    Returns a list of dicts: `world`, `text`, `color`, `size`, `selected`.
    Deliberately free of any drawing call so the decision of *what* to show
    can be tested without a viewport.
    """
    if collection is None or not props.show_landmark_labels:
        return []

    base_color = tuple(float(v) for v in props.landmark_label_color)
    base_size = int(props.landmark_label_size)
    selected_only = props.landmark_label_scope == 'SELECTED'

    entries = []
    for index, item in enumerate(collection):
        point = item.surface_point
        if not point.valid:
            # An unpicked landmark has no position, so there is nowhere
            # honest to put its label.
            continue
        selected = index == int(active_index)
        if selected_only and not selected:
            continue
        world = _world_position(item, point)
        if world is None:
            continue
        entries.append({
            "stable_id": int(item.stable_id),
            "world": world,
            "text": label_text(item.label, item.status),
            "color": label_color(item.status, base_color),
            "size": base_size + (SELECTED_SIZE_BONUS if selected else 0),
            "selected": selected,
        })
        if len(entries) >= MAX_LABELS:
            break
    return entries


def _world_position(item, point):
    """Where the label goes: the marker's position, or the stored one."""
    marker = bpy.data.objects.get(
        visualization.landmark_object_name(item.stable_id))
    if marker is not None and visualization.is_helper(marker):
        location = marker.matrix_world.translation
        return (float(location[0]), float(location[1]), float(location[2]))
    return (float(point.world_xyz[0]), float(point.world_xyz[1]),
            float(point.world_xyz[2]))


# ---------------------------------------------------------------------------
# the draw callback
# ---------------------------------------------------------------------------

def _set_size(font_id, size):
    """blf.size lost its dpi argument in Blender 4.0; accept both."""
    import blf
    try:
        blf.size(font_id, size)
    except TypeError:                                # pragma: no cover
        blf.size(font_id, size, 72)


def _draw():
    """POST_PIXEL callback. Reads the scene; never writes to it."""
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    context = bpy.context
    space = getattr(context, "space_data", None)
    if space is None or space.type != 'VIEW_3D':
        return
    props = getattr(context.scene, "bsmt", None)
    if props is None or not props.show_landmark_labels:
        return
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return

    collection = getattr(context.scene, "bsmt_landmarks", None)
    entries = label_entries(props, collection, props.landmark_index)
    if not entries:
        return

    font_id = 0
    offset = int(props.landmark_label_offset)
    shadow = bool(props.landmark_label_shadow)
    if shadow:
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.9)
        blf.shadow_offset(font_id, 1, -1)
    try:
        for entry in entries:
            position = location_3d_to_region_2d(region, rv3d, entry["world"])
            if position is None:
                # Behind the camera, or outside the region. Not an error:
                # there is simply nowhere on screen to put it.
                continue
            _set_size(font_id, entry["size"])
            color = entry["color"]
            blf.color(font_id, color[0], color[1], color[2], color[3])
            # Offset in SCREEN space so the text never sits on top of the
            # exact surface point it names (sect. 7).
            blf.position(font_id, position[0] + offset,
                         position[1] + offset, 0.0)
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

    A label setting changes no object, so nothing makes Blender repaint on its
    own; without this, changing Label Size would appear to do nothing until
    the viewport happened to redraw for another reason.
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
