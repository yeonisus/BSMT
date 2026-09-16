"""Persistent add-on state.

State lives in a PropertyGroup attached to the Scene rather than in Python
globals, so it survives UI redraws, undo steps, script reloads and .blend
save/reload.
"""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from . import (alignment, export, geodesic, interior, interiorcache,
               landmarks, measurement, measurements, overlay, panelpreview,
               pathcache, preprocess, readiness, regions, surfacearea,
               timing, visualization)


def _on_display_changed(self, context):
    """Re-apply cosmetic settings to existing helper objects.

    Only touches visibility and size of helper objects; stored coordinates and
    the calculated distance are never modified here.
    """
    visualization.apply_display_settings(context, self)


def _on_unit_changed(self, context):
    """Keep an already calculated result consistent with the chosen unit."""
    if self.distance_valid and self.point_a_valid and self.point_b_valid:
        self.distance_mm = measurement.straight_distance_mm(
            self.point_a, self.point_b, self.unit
        )
    # A unit change rescales the physical metric (sect. 6.4), so any stored
    # surface distance was computed under a different metric and must not be
    # displayed. The straight distance above can simply be rescaled; a
    # geodesic cannot, because a non-uniform metric change moves the path.
    clear_surface_result(self)
    # Same reasoning for every user-defined measurement: a distance computed
    # under a different physical metric is not this measurement's answer.
    try:
        invalidate_all_measurement_results(
            context, "the coordinate unit interpretation changed"
        )
    except Exception:                                 # pragma: no cover
        pass
    # Physical millimetre coordinates depend on the unit but not on the world
    # position, so nothing else would refresh them. Imported late: attach
    # imports state, and this is the only direction that would close a cycle.
    try:
        from . import attach
        attach.refresh_physical_mm(self)
    except Exception:                                 # pragma: no cover
        # A display refresh must never break the unit setting itself.
        pass
    # Helper sizes are specified in mm, so they depend on the unit too.
    _on_display_changed(self, context)


def _on_component_isolate_changed(self, context):
    """Show all components, or only the selected one. Visibility only."""
    preview = getattr(geodesic, "preview", None)
    if preview is None:
        return
    preview.apply_isolation(context, self.component_isolate, len(self.components))


class BSMT_SurfacePoint(bpy.types.PropertyGroup):
    """Canonical surface location of a picked landmark.

    The canonical location is triangle_index + barycentric coordinates against
    the canonical triangle array. local/world/physical XYZ are cached display
    values derived from it, never the source of truth.
    """

    valid: BoolProperty(
        name="Valid",
        description="True once a surface location has been captured",
        default=False,
    )
    source_object: StringProperty(name="Source Object", default="")
    geometry_hash: StringProperty(
        name="Canonical Geometry Hash",
        description="Identity of the canonical mesh this location refers to",
        default="",
    )
    triangle_index: IntProperty(
        name="Triangle",
        description="Index into the canonical triangle array",
        default=-1,
    )
    barycentric: FloatVectorProperty(
        name="Barycentric",
        description="u, v, w against the canonical triangle corners",
        size=3,
        default=(0.0, 0.0, 0.0),
    )
    component_id: IntProperty(
        name="Component",
        description="1-based connected component, matching the preview numbering",
        default=0,
    )
    kind: StringProperty(
        name="Position Kind",
        description="FACE, EDGE or VERTEX position within the triangle",
        default="",
    )
    local_xyz: FloatVectorProperty(name="Local XYZ", size=3, subtype='XYZ')
    world_xyz: FloatVectorProperty(name="World XYZ", size=3, subtype='XYZ')
    physical_mm_xyz: FloatVectorProperty(
        name="Physical XYZ (mm)",
        description="Position in physical millimetres (world scale, uncentred)",
        size=3,
        subtype='XYZ',
    )
    status: StringProperty(name="Status", default="NOT PICKED")
    reconstruction_error: FloatProperty(
        name="Reconstruction Error",
        description="Distance between the ray hit and the reconstructed "
                    "position, in local units",
        default=0.0,
    )


def _on_landmark_name_changed(self, context):
    """Keep auto-named measurements in step when a landmark is renamed.

    Only the measurements that reference this landmark are visited, and only
    those with Auto Name on are rewritten, so a custom name is never touched.
    """
    try:
        refresh_auto_names(context, self.stable_id)
    except Exception:                                 # pragma: no cover
        # A convenience refresh must never break renaming a landmark.
        pass


def _on_landmark_display_changed(self, context):
    """A marker or label setting changes no object, so ask for a repaint.

    Since Milestone 3.9 both markers and labels are drawn by the screen-space
    overlay, so nothing here touches an object at all. Without the redraw the
    overlay would keep the old size or colour until the 3D view happened to
    repaint for some other reason, and the control would look broken.
    """
    overlay.tag_redraw(context)


#: Markers and labels are now the same overlay, so they refresh the same way.
#: Both are Landmark Manager controls, so both note the stage - see
#: `_on_landmark_control_used`, defined below and bound at the bottom of this
#: block once `enter_stage` exists.


# ---------------------------------------------------------------------------
# Which workflow stage the researcher is working in (display state only)
# ---------------------------------------------------------------------------
#
# The selected landmark is emphasised with a ring so that, WHILE PICKING, it
# is obvious which row the next click belongs to. Once the researcher has
# moved on to building measurements, computing paths or exporting, that ring
# is no longer telling them anything - it is a leftover from an earlier stage
# marking one landmark out from its neighbours for no reason a reader of the
# viewport could guess.
#
# So the emphasis follows the stage. `ui_stage` is display state and nothing
# else: no landmark, no selection index, no SurfacePoint and no measurement
# reads it, and changing it cannot invalidate a result or move a marker. The
# landmarks themselves keep their configured size and colour throughout - it
# is only the SELECTED ring, and the selected label's size bonus, that come
# and go.


def enter_stage(context_or_props, stage):
    """Record which workflow stage the researcher is working in.

    Display state. Accepts either props or a context so a caller does not have
    to know which it holds. Returns the stage actually stored, or "" when
    there were no props to store it on - a headless or half-registered scene
    must not make an operator fail over a UI hint.
    """
    props = context_or_props
    if props is not None and not hasattr(props, "ui_stage"):
        props = get_props(props)
    if props is None:
        return ""
    if props.ui_stage != stage:
        props.ui_stage = stage
        # The ring appears or disappears without any object changing, so
        # nothing else would ask the viewport to repaint.
        overlay.tag_redraw(None)
    return stage


def landmark_emphasis_visible(props):
    """Whether the SELECTED-landmark ring should be drawn right now.

    The decision itself is `readiness.landmark_emphasis_visible`; this is the
    props-shaped way to ask it.
    """
    if props is None:
        return False
    return readiness.landmark_emphasis_visible(props.ui_stage)


def _on_measurement_index_changed(self, context):
    """Selecting a measurement row is working in Measurement Manager.

    A UIList selection is a property change, not an operator, so this is the
    only place it can be noticed.
    """
    enter_stage(self, readiness.STAGE_MEASUREMENTS)


def _on_landmark_index_changed(self, context):
    """Selecting a landmark row is working in Landmark Manager.

    Note that Blender fires an update only when the value actually CHANGES, so
    clicking the row that is already active does not come through here. That
    is why every Landmark Manager control says the same thing - see
    `_on_landmark_control_used` - and why the landmark operators say it too:
    the researcher coming back to this stage will touch one of them.
    """
    _on_landmark_control_used(self, context)


def _on_landmark_control_used(self, context):
    """Any Landmark Manager display control: repaint, and note the stage.

    Marker size, colour, label scope, visibility mode and the selected row all
    belong to Landmark Manager and to nothing else, so touching one of them is
    a plain statement of where the researcher is working.
    """
    enter_stage(self, readiness.STAGE_LANDMARKS)
    _on_landmark_display_changed(self, context)


#: Markers and labels are the same overlay and the same stage.
_on_landmark_label_changed = _on_landmark_control_used


class BSMT_Landmark(bpy.types.PropertyGroup):
    """One named research landmark.

    Composition, not a second surface-point representation (sect. 16): the
    canonical location lives in a nested `BSMT_SurfacePoint`, the very same
    PropertyGroup the A/B workflow uses, and every piece of mathematics on it
    stays in surface_point.py / landmarks.py.

    `stable_id` is a monotonic integer that is never reused within a scene.
    It names the marker object, so a landmark can be renamed, reordered or
    reloaded from a protocol without its helper object changing identity or
    colliding with another landmark's (sect. 7).
    """

    stable_id: IntProperty(
        name="Stable ID",
        description="Internal identifier, unique within the scene and never "
                    "reused. Names the marker object",
        default=0,
    )
    protocol_id: StringProperty(
        name="ID",
        description="Protocol-facing identifier, e.g. L01",
        default="",
    )
    name: StringProperty(
        name="Name",
        description="Anatomical landmark name. Chosen entirely by the "
                    "researcher; BSMT never assumes or rewrites one",
        default="",
        update=_on_landmark_name_changed,
    )
    display_name: StringProperty(
        name="Display Name",
        description="Optional label shown instead of the name",
        default="",
    )
    notes: StringProperty(
        name="Notes",
        description="Free-text notes for this landmark",
        default="",
    )
    surface_point: PointerProperty(
        name="Surface Point",
        description="Canonical surface location: triangle index + barycentric",
        type=BSMT_SurfacePoint,
    )
    status: EnumProperty(
        name="Status",
        items=landmarks.STATUS_ITEMS,
        default=landmarks.STATUS_NOT_PICKED,
    )
    status_detail: StringProperty(name="Status Detail", default="")

    @property
    def label(self):
        return self.display_name or self.name or "(unnamed)"


# Blender garbage-collects the strings behind a dynamic EnumProperty unless a
# reference is held, so the generated list is cached here on purpose.
_LANDMARK_ENUM_CACHE = [('0', "(no landmarks)", "")]


def landmark_enum_items(self, context):
    """Items for the From/To pickers: "L03  Shoulder_L", valued by stable id.

    WRITE-ONLY. Measured in Blender 4.5.13: a dynamic enum remaps by index
    when its item list changes, so after deleting landmark 42 a picker set to
    "42" reads back as "43" - a different landmark, silently. Nothing reads
    these back; the authoritative reference is the integer stable id, and the
    update callbacks below are the only place the picker is consumed.
    """
    global _LANDMARK_ENUM_CACHE
    scene = getattr(context, "scene", None) if context is not None else None
    collection = getattr(scene, "bsmt_landmarks", None) if scene else None
    items = []
    if collection:
        for item in collection:
            items.append((
                str(item.stable_id),
                "%s  %s" % (item.protocol_id or "--", item.label),
                item.notes or "",
            ))
    if not items:
        items = [('0', "(no landmarks)", "")]
    _LANDMARK_ENUM_CACHE = items
    return _LANDMARK_ENUM_CACHE


def auto_name_for(context, item):
    """The automatic name for a measurement, or "" while it is still a draft.

    Resolved through the AUTHORITATIVE stable ids, never through the From/To
    pickers: a dynamic enum remaps by index when the landmark list changes,
    so naming from it could label a measurement after a landmark it does not
    actually reference.

    A draft gets no name (sect. 7). Naming an unfinished row "? to ?" would
    put a meaningless label in the list and, eventually, in an export.
    """
    if measurement_is_draft(item):
        return ""
    source, target = resolve_measurement_landmarks(context, item)
    return measurements.default_name(
        source.label if source is not None else item.source_name,
        target.label if target is not None else item.target_name,
    )


def apply_auto_name(context, item):
    """Refresh an auto-named measurement's name. Returns True if it changed.

    A no-op when Auto Name is off, so a custom name is never overwritten.
    Writing `name` does not invalidate the result - a label is not part of
    what is measured.
    """
    if not item.auto_name:
        return False
    wanted = auto_name_for(context, item)
    if not wanted and not item.name:
        return False
    if item.name != wanted:
        item.name = wanted
        return True
    return False


def refresh_auto_names(context, landmark_stable_id=None):
    """Re-apply auto names, optionally only where a landmark is referenced."""
    collection = get_measurements(context)
    if not collection:
        return 0
    if landmark_stable_id is None:
        candidates = list(collection)
    else:
        candidates = measurements_referencing(collection, landmark_stable_id)
    return sum(1 for item in candidates if apply_auto_name(context, item))


def _on_auto_name_toggled(self, context):
    """Turning Auto Name on adopts the generated name straight away."""
    apply_auto_name(context, self)


def _adopt_landmark(measurement_item, context, slot, raw_value):
    """Copy a picker choice into the authoritative reference fields."""
    try:
        stable_id = int(raw_value)
    except (TypeError, ValueError):
        return
    collection = get_landmarks(context)
    landmark = (landmark_by_stable_id(collection, stable_id)
                if collection else None)
    if landmark is None:
        return
    setattr(measurement_item, slot + "_stable_id", stable_id)
    setattr(measurement_item, slot + "_protocol_id", landmark.protocol_id)
    setattr(measurement_item, slot + "_name", landmark.name)
    invalidate_measurement_result(
        measurement_item, "the %s landmark selection changed" % slot
    )
    apply_auto_name(context, measurement_item)


def _on_source_picker(self, context):
    _adopt_landmark(self, context, "source", self.source_picker)


def _on_target_picker(self, context):
    _adopt_landmark(self, context, "target", self.target_picker)


def _on_preprocess_preset_changed(self, context):
    """A preset writes the target count; the count itself stays editable."""
    target = preprocess.PRESET_TARGETS.get(self.preprocess_preset, 0)
    if target:
        self.preprocess_target_triangles = target


def _on_timing_debug_changed(self, context):
    """Console logging follows the checkbox. Recording is never turned off."""
    try:
        timing.set_enabled(self.timing_debug)
    except Exception:                                 # pragma: no cover
        pass


def _on_visualization_style_changed(self, context):
    """Colour or thickness changed. Cosmetic: no rebuild, no recomputation.

    Nothing here can reach the solver, and nothing here invalidates a cache.
    The refresh is needed because the drawn path is lifted off the surface by
    a multiple of its own thickness, so a thickness change moves the drawn
    points - and it is cheap because a redisplay whose points would come out
    identical is skipped by signature (viz.draw_cached_path).
    """
    try:
        visualization.apply_measurement_display(context, self)
    except Exception:                                 # pragma: no cover
        pass
    try:
        from . import viz
        viz.refresh(context, self)
    except Exception:                                 # pragma: no cover
        pass


def _on_visualization_changed(self, context):
    """Display mode or scope changed. Rebuilds visibility, never geometry.

    Switching mode must not recompute a distance or a path (sect. 6), so this
    only ever shows, hides or removes helpers that already exist.
    """
    try:
        from . import viz
        viz.refresh(context, self)
    except Exception:                                 # pragma: no cover
        pass


def _on_definition_changed(self, context):
    """Any change to what a measurement MEANS invalidates its result."""
    invalidate_measurement_result(self, "the measurement definition changed")


class BSMT_Measurement(bpy.types.PropertyGroup):
    """One user-defined measurement between two named landmarks.

    Landmark references are stable ids, never list indices: deleting or
    reordering landmarks must not be able to repoint a measurement at a
    different one (sect. 2). The cached protocol id and name are for
    templates and for saying *which* landmark is missing when a reference
    cannot be resolved - they are never used to find a substitute.
    """

    stable_id: IntProperty(name="Stable ID", default=0)
    protocol_id: StringProperty(name="ID", default="")
    name: StringProperty(
        name="Name",
        description="Label for this measurement. Renaming does not change "
                    "what is measured, so it never invalidates a result",
        default="",
    )
    auto_name: BoolProperty(
        name="Auto Name",
        description="Keep the name in step with the chosen landmarks, e.g. "
                    "\"P01 to P02\". Turn off to write your own name; a "
                    "custom name then survives any From/To change",
        default=True,
        update=_on_auto_name_toggled,
    )
    notes: StringProperty(name="Notes", default="")
    enabled: BoolProperty(
        name="Enabled",
        description="Include this measurement in Calculate All",
        default=True,
    )

    source_stable_id: IntProperty(name="From (stable id)", default=0)
    target_stable_id: IntProperty(name="To (stable id)", default=0)
    source_protocol_id: StringProperty(default="")
    target_protocol_id: StringProperty(default="")
    source_name: StringProperty(default="")
    target_name: StringProperty(default="")

    source_picker: EnumProperty(
        name="From", description="Source landmark",
        items=landmark_enum_items, update=_on_source_picker,
    )
    target_picker: EnumProperty(
        name="To", description="Target landmark",
        items=landmark_enum_items, update=_on_target_picker,
    )

    measurement_type: EnumProperty(
        name="Type",
        items=measurements.TYPE_ITEMS,
        default=measurements.TYPE_BOTH,
        update=_on_definition_changed,
    )

    status: EnumProperty(
        name="Status",
        items=measurements.STATUS_ITEMS,
        default=measurements.STATUS_NOT_READY,
    )
    status_detail: StringProperty(default="")

    # --- results, cleanly separated from the definition ------------------
    straight_valid: BoolProperty(default=False)
    straight_mm: FloatProperty(name="Straight (mm)", default=0.0)
    surface_valid: BoolProperty(default=False)
    surface_mm: FloatProperty(name="Surface (mm)", default=0.0)
    ratio: FloatProperty(name="Surface / Straight", default=0.0)

    # --- provenance of the surface result --------------------------------
    backend_name: StringProperty(default="")
    backend_version: StringProperty(default="")
    bound_factor: FloatProperty(default=0.0)
    unbounded_fallback: BoolProperty(default=False)
    attempts: IntProperty(default=0)
    elapsed_s: FloatProperty(default=0.0)
    surface_mode: StringProperty(default="")

    # --- visualisation (Milestone 3.2) ------------------------------------
    show_visualization: BoolProperty(
        name="Show",
        description="Draw this measurement in the viewport. Enabling it never "
                    "computes a surface path",
        default=False,
    )

    # --- cached surface path ---------------------------------------------
    # The polyline itself lives in a datablock of its own (pathcache.py), in
    # the scan's local space - NOT in the helper curve, and never in world
    # coordinates. These fields are the provenance that says whether that
    # polyline is still the path for the current landmarks, geometry and
    # metric, so validating a cache costs no geometry read at all.
    #
    # Everything sect. 4 of the visualisation brief asks a cache entry to
    # carry is here: the measurement's own stable id (this PropertyGroup),
    # both landmark stable ids, both endpoint SurfacePoint locations
    # (triangle + barycentric), the geometry hash, the metric/scale key, the
    # polyline (in pathcache), its length, its status and its solve time.
    path_valid: BoolProperty(default=False)
    path_point_count: IntProperty(default=0)
    path_length_mm: FloatProperty(default=0.0)
    path_distance_mm: FloatProperty(default=0.0)
    path_agreement_mm: FloatProperty(default=0.0)
    path_elapsed_s: FloatProperty(default=0.0)
    path_mode: StringProperty(default="")
    path_object: StringProperty(default="")
    path_geometry_hash: StringProperty(default="")
    path_metric_tensor: FloatVectorProperty(size=9, default=(0.0,) * 9)
    path_metric_key: StringProperty(default="")
    path_unit: StringProperty(default="")
    path_source_stable_id: IntProperty(default=0)
    path_target_stable_id: IntProperty(default=0)

    # The exact endpoints the path was solved from. Landmark stable ids alone
    # are not enough: re-picking a landmark keeps its id and moves the point,
    # and a path from where a landmark USED to be is not this measurement.
    path_source_triangle: IntProperty(default=-1)
    path_source_bary: FloatVectorProperty(size=3, default=(0.0,) * 3)
    path_target_triangle: IntProperty(default=-1)
    path_target_bary: FloatVectorProperty(size=3, default=(0.0,) * 3)

    # Set when a dependency changed under a cache that still exists. The
    # entry is KEPT and reported as STALE rather than silently deleted or,
    # worse, silently recomputed (sect. 9).
    path_stale: BoolProperty(default=False)
    path_stale_reason: StringProperty(default="")

    # Whether the researcher currently wants THIS measurement's path drawn.
    # Separate from `show_visualization`, which is about the measurement as a
    # whole: hiding a path must not also hide its straight chord. Purely a
    # display choice - it never touches the cache (sect. 9).
    path_shown: BoolProperty(
        name="Show Path",
        description="Draw this measurement's cached surface path. Hiding it "
                    "keeps the cache; showing it again computes nothing",
        default=True,
    )

    # --- dependency fingerprint, for sect. 12 invalidation ---------------
    result_object: StringProperty(default="")
    result_geometry_hash: StringProperty(default="")
    result_metric_tensor: FloatVectorProperty(size=9, default=(0.0,) * 9)

    @property
    def label(self):
        return self.name or self.protocol_id or "(unnamed)"

    @property
    def has_result(self):
        return bool(self.straight_valid or self.surface_valid)


class BSMT_RegionLandmark(bpy.types.PropertyGroup):
    """One boundary landmark of a Surface Region. THE authoritative reference.

    A region IS its ordered landmark ids (Milestone 3.31). Everything else a
    region stores - the segments, the polylines, the verdict - is derived from
    this list and can be thrown away and recomputed; this cannot.

    The reference is a LANDMARK stable id, never a list index and never a
    dynamic enum value. Measured in Blender 4.5.13 and recorded in
    measurements.py: an enum built from the landmark collection remaps by
    index when that collection changes, so a picker reading "42" after
    landmark 42 is deleted comes back as "43" - a different landmark, no
    error. The name and protocol id beside it are cached for diagnostics and
    for naming a reference that has gone; they are never used to find a
    substitute.
    """

    landmark_stable_id: IntProperty(name="Landmark", default=0)
    landmark_protocol_id: StringProperty(default="")
    landmark_name: StringProperty(default="")


class BSMT_RegionSegment(bpy.types.PropertyGroup):
    """One computed boundary segment. A CACHED RESULT, not a definition.

    Derived from consecutive boundary landmarks - position i joins landmark i
    to landmark i+1, and the last joins Ln back to L1 - so there is nothing
    here for a researcher to order or reverse. The polyline itself lives in
    ``pathcache`` under the region's own name space; these are the facts
    needed to decide whether that polyline is still the answer WITHOUT
    reading geometry or running anything.

    Region-owned, deliberately. A boundary segment is not a measurement: it
    has no protocol id, appears in no CSV, and exists only as long as the
    landmark definition that produced it.
    """

    from_landmark: IntProperty(default=0)
    to_landmark: IntProperty(default=0)
    computed: BoolProperty(default=False)

    point_count: IntProperty(default=0)
    length_mm: FloatProperty(default=0.0)
    distance_mm: FloatProperty(default=0.0)
    mode: StringProperty(default="")
    elapsed_s: FloatProperty(default=0.0)

    #: What it was solved on, and where its ends were at the time.
    object_name: StringProperty(default="")
    geometry_hash: StringProperty(default="")
    metric_tensor: FloatVectorProperty(size=9, default=(0.0,) * 9)
    metric_key: StringProperty(default="")
    unit: StringProperty(default="")
    from_triangle: IntProperty(default=-1)
    from_bary: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    to_triangle: IntProperty(default=-1)
    to_bary: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    component_id: IntProperty(default=0)


def _on_region_landmark_picker(self, context):
    """Append the chosen landmark to this region's boundary. WRITE-ONLY.

    The picker exists so a human can choose; the authoritative write is the
    integer it copies into a new `BSMT_RegionLandmark`. Nothing ever reads it
    back - see `landmark_enum_items` on why a dynamic enum cannot be trusted
    as a reference.
    """
    try:
        stable_id = int(self.landmark_picker)
    except (TypeError, ValueError):
        return
    if stable_id <= 0:
        return
    collection = get_landmarks(context)
    landmark = (landmark_by_stable_id(collection, stable_id)
                if collection else None)
    if landmark is None:
        return
    append_region_landmark(self, landmark)
    refresh_region_status(context, self)


class BSMT_SurfaceRegion(bpy.types.PropertyGroup):
    """A researcher-defined CLOSED BOUNDARY, specified by ordered landmarks.

    Milestone 3.31. Consecutive boundary landmarks - including the
    final-to-first pair, which is implicit and mandatory - are joined by
    cached surface geodesic paths on the triangular mesh.

    This is a boundary and nothing more: there is no area here, no enclosed
    face set and no coverage figure. Those belong to a later milestone, and
    building them on a boundary that has not first been proved closed is how
    a plausible wrong number gets produced.

    Definition vs. cache
    --------------------
    `landmarks` is the definition and is authoritative. `segments` plus the
    polylines in `pathcache` are a CACHED RESULT of it, stamped with
    `cached_definition` - the definition key they were computed for. Any edit
    to the definition changes that key, which makes the cache stale rather
    than wrong, and nothing is ever recomputed without an explicit press.

    The status fields are a RECORD of the last validation, not an authority.
    The verdict is re-derived from the live landmarks whenever it is needed.
    Deriving and RECORDING are two different acts with two different callers:
    `state.validate_region` derives, is pure, and is what the panel uses on
    every draw; `state.refresh_region_status` derives and then writes, and
    only an operator or an invalidation path may call it - Blender forbids
    writing to ID-backed data while the UI is drawing.
    """

    stable_id: IntProperty(name="Stable ID", default=0)
    protocol_id: StringProperty(name="ID", default="")
    name: StringProperty(
        name="Name",
        description="Label for this region. Renaming changes nothing about "
                    "the boundary, so it never invalidates it",
        default="",
    )
    notes: StringProperty(name="Notes", default="")

    # --- the definition ---------------------------------------------------
    landmarks: CollectionProperty(type=BSMT_RegionLandmark)
    landmark_index: IntProperty(default=0, min=0)
    landmark_picker: EnumProperty(
        name="Add Landmark",
        description="Append a landmark to this region's boundary. Nothing is "
                    "computed - press Compute Boundary when the order is right",
        items=landmark_enum_items,
        update=_on_region_landmark_picker,
    )

    # --- the cached result ------------------------------------------------
    segments: CollectionProperty(type=BSMT_RegionSegment)
    #: The definition key `segments` were computed for. Empty means never.
    cached_definition: StringProperty(default="")
    computed_elapsed_s: FloatProperty(default=0.0)

    status: EnumProperty(
        name="Status",
        items=regions.STATUS_ITEMS,
        default=regions.STATUS_DRAFT,
    )
    status_code: StringProperty(default=regions.CODE_EMPTY)
    status_detail: StringProperty(default="")
    #: The full multi-line report from the last explicit Validate press.
    report: StringProperty(default="")
    #: Has an explicit Validate Region run against the region AS IT NOW
    #: STANDS? Only that press runs the shared-point self-intersection test.
    #: The flag would rot the moment anything was edited, so it is not
    #: trusted: `region_fingerprint` is recomputed on every refresh and any
    #: difference clears both of these.
    validated: BoolProperty(default=False)
    validated_fingerprint: StringProperty(default="")

    # --- what the last validation measured, for display only -------------
    boundary_closed: BoolProperty(default=False)
    boundary_length_mm: FloatProperty(default=0.0)
    boundary_point_count: IntProperty(default=0)
    boundary_object: StringProperty(default="")
    boundary_geometry_hash: StringProperty(default="")
    boundary_component_id: IntProperty(default=0)

    # --- the interior: which SIDE of the boundary is the region ----------
    #
    # Derived from the boundary the same way the boundary is derived from the
    # landmarks: an explicit press computes it, any change upstream makes it
    # stale, and nothing is ever recomputed unasked. The classification
    # itself lives in the fill helper mesh - one face per full interior
    # triangle, one per clipped partial piece - because that geometry IS the
    # representation, and a later Surface Area milestone sums exactly it.
    interior_status: EnumProperty(
        name="Interior", items=interior.STATUS_ITEMS,
        default=interior.STATUS_NONE,
    )
    interior_code: StringProperty(default=interior.CODE_NOT_COMPUTED)
    interior_detail: StringProperty(default="")
    #: The boundary definition key and geometry the interior was computed
    #: against. A mismatch is what makes it stale.
    interior_definition: StringProperty(default="")
    interior_geometry_hash: StringProperty(default="")
    interior_side: EnumProperty(
        name="Interior Side",
        description="Which side of the boundary is the region. The smaller "
                    "side is a DEFAULT, not a claim that it is anatomically "
                    "inside - switch if the other side is what you meant",
        items=interior.SIDE_ITEMS,
        default=interior.SIDE_SMALLER,
    )
    interior_full_count: IntProperty(default=0)
    interior_partial_count: IntProperty(default=0)
    interior_component_id: IntProperty(default=0)
    interior_elapsed_s: FloatProperty(default=0.0)

    # --- display ----------------------------------------------------------
    show_boundary: BoolProperty(
        name="Show",
        description="Draw this region's closed boundary. Showing it never "
                    "computes anything - the cached polylines are reused",
        default=False,
    )
    show_fill: BoolProperty(
        name="Show Fill",
        description="Draw the computed interior on the body surface. Nothing "
                    "is computed - the classified faces are reused",
        default=False,
    )
    fill_color: FloatVectorProperty(
        name="Fill Colour", subtype='COLOR', size=4,
        min=0.0, max=1.0, default=(0.15, 0.55, 1.0, 1.0),
    )
    fill_opacity: FloatProperty(
        name="Fill Opacity",
        description="How solid the fill looks. Semi-transparent by default "
                    "so landmarks and the scan surface stay visible",
        default=0.30, min=0.02, max=1.0,
    )

    # --- thickness preview -----------------------------------------------
    #
    # A VISUALIZATION of the computed interior given a thickness, built from
    # the classified faces and nothing else. Not a physical simulation and
    # not a manufacturing model - see `panelpreview.LIMITS`, which the
    # operator prints every time.
    panel_thickness_mm: FloatProperty(
        name="Thickness (mm)",
        description="How far to offset the region outward along the body "
                    "surface normals. A preview only: a normal offset "
                    "distorts on a curved body",
        default=panelpreview.DEFAULT_THICKNESS_MM,
        min=panelpreview.MIN_THICKNESS_MM,
        max=panelpreview.MAX_THICKNESS_MM,
        soft_min=1.0, soft_max=50.0, step=100, precision=2,
    )
    show_panel: BoolProperty(
        name="Show Thickness Preview",
        description="Draw the interior offset outward by the thickness. "
                    "Nothing is computed - the classified faces are reused",
        default=False,
    )
    panel_color: FloatVectorProperty(
        name="Preview Colour", subtype='COLOR', size=4,
        min=0.0, max=1.0, default=(1.0, 0.75, 0.2, 1.0),
    )
    panel_opacity: FloatProperty(
        name="Preview Opacity", default=0.45, min=0.02, max=1.0)
    #: What the drawn preview was built from: the interior it belongs to and
    #: the thickness it was given. A thickness change moves this, which
    #: rebuilds the preview geometry and NOTHING else.
    panel_built: StringProperty(default="")
    panel_detail: StringProperty(default="")
    panel_folded_faces: IntProperty(default=0)

    # --- surface area ------------------------------------------------------
    #
    # THE MESH SURFACE AREA OF THE SELECTED REGION ON THE TRIANGULAR BODY
    # MESH - not the true anatomical area, and not an exact smooth-body area.
    # Derived from the stored interior classification and from nothing else:
    # if there is no current interior there is no area, which is a refusal
    # rather than a reason to derive one.
    #
    # mm^2 is the only authoritative value. cm^2 is derived at display time;
    # storing both would be two numbers that can disagree.
    #
    # PRECISION: the area is COMPUTED in float64 and STORED here at float32,
    # because that is what a Blender FloatProperty is - about seven
    # significant digits, so roughly 0.004 mm^2 on a 400 cm^2 region. That is
    # far finer than the mesh's own fidelity to a body and finer than the
    # displayed resolution, but it is why the sum of the two sides agrees
    # with the component area to ~1e-7 when read back from properties, while
    # the same sum computed in float64 agrees to ~3e-09.
    area_mm2: FloatProperty(default=0.0)
    area_full_mm2: FloatProperty(default=0.0)
    area_partial_mm2: FloatProperty(default=0.0)
    area_component_mm2: FloatProperty(default=0.0)
    area_status: EnumProperty(name="Area", items=surfacearea.STATUS_ITEMS,
                              default=surfacearea.STATUS_NONE)
    area_code: StringProperty(default=surfacearea.CODE_NOT_COMPUTED)
    area_detail: StringProperty(default="")
    area_method: StringProperty(default="")
    #: What the stored area was computed against: the interior fingerprint
    #: and the geometry. A mismatch is what makes it stale.
    area_interior_key: StringProperty(default="")
    area_geometry_hash: StringProperty(default="")
    area_side: StringProperty(default="")
    area_elapsed_s: FloatProperty(default=0.0)
    color: FloatVectorProperty(
        name="Boundary Colour", subtype='COLOR', size=4,
        min=0.0, max=1.0, default=(0.2, 1.0, 0.6, 1.0),
    )

    @property
    def label(self):
        return self.name or self.protocol_id or "(unnamed region)"

    @property
    def landmark_ids(self):
        """The ordered definition, as plain integers."""
        return [int(entry.landmark_stable_id) for entry in self.landmarks]


class BSMT_ScanProvenance(bpy.types.PropertyGroup):
    """Provenance stored ON a generated measurement copy (sect. 11).

    `source` is a real Blender object POINTER, so it keeps pointing at the
    right scan after a rename and cannot be confused by two objects that
    briefly share a name. `source_name` is kept alongside purely as a
    human-readable fallback for when the pointer cannot be followed - for
    instance after the source has been deleted, when naming the loss is more
    useful than a null.
    """

    is_measurement_copy: BoolProperty(default=False)
    source: PointerProperty(
        name="Source Scan", type=bpy.types.Object,
        description="The scan this measurement copy was generated from",
    )
    source_name: StringProperty(default="")
    source_mesh_name: StringProperty(default="")
    original_triangles: IntProperty(default=0)
    target_triangles: IntProperty(default=0)
    actual_triangles: IntProperty(default=0)
    method: StringProperty(default="")
    ratio: FloatProperty(default=0.0)
    bsmt_version: StringProperty(default="")
    created: StringProperty(default="")
    representation: StringProperty(
        default="",
        description="What this object IS. Decimation changes the polyhedral "
                    "surface, so it is a representation of the scan, not the "
                    "same exact surface",
    )

    # Milestone 3.19 - repair provenance, APPENDED to the preprocessing
    # record above rather than replacing it: a repaired mesh is still a
    # decimated copy of a particular scan, and losing that would lose where
    # the measurements came from.
    repair_applied: BoolProperty(default=False)
    repair_type: StringProperty(default="")
    repair_degenerate_before: IntProperty(default=0)
    repair_degenerate_after: IntProperty(default=0)
    repair_merged_vertices: IntProperty(default=0)
    repair_removed_faces: IntProperty(default=0)
    repair_version: StringProperty(default="")
    repair_created: StringProperty(default="")


class BSMT_BoundaryLoop(bpy.types.PropertyGroup):
    """One detected boundary loop, for the repair list. Display only."""

    loop_id: IntProperty(default=0)
    edge_count: IntProperty(default=0)
    vertex_count: IntProperty(default=0)
    perimeter_mm: FloatProperty(default=0.0)
    bbox_x: FloatProperty(default=0.0)
    bbox_y: FloatProperty(default=0.0)
    bbox_z: FloatProperty(default=0.0)
    closed: BoolProperty(default=False)
    label: StringProperty(default="")


class BSMT_RepairComponent(bpy.types.PropertyGroup):
    """One connected component, for the repair list. Display only."""

    index: IntProperty(default=0)
    triangle_count: IntProperty(default=0)
    vertex_count: IntProperty(default=0)
    percent: FloatProperty(default=0.0)
    is_small: BoolProperty(default=False)
    is_largest: BoolProperty(default=False)
    label: StringProperty(default="")


class BSMT_NonManifoldDefect(bpy.types.PropertyGroup):
    """One non-manifold DEFECT REGION, for the repair list. Display only.

    A region, not an edge: the real Design X scan's seven non-manifold edges
    are one artefact around one vertex, and listing them separately would
    describe seven problems that do not exist. `repair.group_nonmanifold_edges`
    is the one place that grouping rule lives.

    Holds no authoritative index. `region_id` and `component_index` are the
    numbering of ONE analysis, valid only while `geometry_hash` still matches
    the mesh; the centre is carried in OBJECT-LOCAL coordinates so the
    viewport can be framed on it without re-reading the mesh, and so it
    survives the scan being moved. Anything that edits geometry re-derives
    both from the fresh canonical mesh (sect. 7.7).
    """

    region_id: IntProperty(default=0)
    edge_count: IntProperty(default=0)
    vertex_count: IntProperty(default=0)
    bbox_diagonal_mm: FloatProperty(default=0.0)
    center_local: FloatVectorProperty(size=3, default=(0.0,) * 3)
    label: StringProperty(default="")

    #: The connected component this defect sits in, as that analysis numbered
    #: it. Display and pre-flight only - never the index a deletion acts on.
    component_index: IntProperty(default=0)
    component_triangle_count: IntProperty(default=0)
    component_vertex_count: IntProperty(default=0)
    component_percent: FloatProperty(default=0.0)
    component_is_largest: BoolProperty(default=False)
    component_is_small: BoolProperty(default=False)
    component_bbox: FloatVectorProperty(size=3, default=(0.0,) * 3)
    #: Empty when the component may be offered for deletion, otherwise the
    #: reason it may not - shown in the panel instead of a destructive button.
    deletion_block: StringProperty(default="")


class BSMT_DegenerateDefect(bpy.types.PropertyGroup):
    """One degenerate triangle, for the repair list. Display only.

    Holds no geometry: the triangle index addresses the CANONICAL array the
    diagnostics were computed from, and the centroid is carried only so the
    viewport can be framed on it without re-reading the mesh.
    """

    triangle_index: IntProperty(default=-1)
    kind: StringProperty(default="")
    label: StringProperty(default="")
    repairable: BoolProperty(default=False)
    centroid: FloatVectorProperty(size=3, default=(0.0,) * 3)
    area: FloatProperty(default=0.0)
    merge_count: IntProperty(default=0)


class BSMT_ComponentInfo(bpy.types.PropertyGroup):
    """One connected component in the diagnostics preview (display only)."""

    index: IntProperty(
        name="Component",
        description="Component number, 1 = most triangles",
        default=0,
    )
    triangle_count: IntProperty(name="Triangles", default=0)
    vertex_count: IntProperty(name="Vertices", default=0)
    color: FloatVectorProperty(
        name="Color",
        description="Preview colour of this component",
        size=4,
        subtype='COLOR',
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
    )


class BSMT_Properties(bpy.types.PropertyGroup):
    """All Phase 1 state for the Body Surface Measurement Tool."""

    unit: EnumProperty(
        name="Coordinate Unit",
        description=(
            "Physical size represented by one coordinate unit of the imported "
            "mesh. Results are always reported in millimetres"
        ),
        items=measurement.UNIT_ITEMS,
        default='MM',
        update=_on_unit_changed,
    )

    marker_size_mm: FloatProperty(
        name="Marker Size (mm)",
        description=(
            "Diameter of the Point A / Point B marker spheres, in millimetres. "
            "Converted into coordinate units using the Coordinate Unit setting"
        ),
        default=5.0,
        min=1.0,
        max=30.0,
        soft_min=1.0,
        soft_max=30.0,
        step=10,          # 0.1 mm per arrow click
        precision=2,
        update=_on_display_changed,
    )

    line_thickness_mm: FloatProperty(
        name="Line Thickness (mm)",
        description=(
            "Diameter of the straight measurement line, in millimetres. "
            "Converted into coordinate units using the Coordinate Unit setting"
        ),
        default=2.0,
        min=0.2,
        max=20.0,
        soft_min=0.2,
        soft_max=20.0,
        step=10,
        precision=2,
        update=_on_display_changed,
    )

    show_markers: BoolProperty(
        name="Show Markers",
        description="Show the Point A / Point B markers. Display only - picked coordinates are kept",
        default=True,
        update=_on_display_changed,
    )

    show_line: BoolProperty(
        name="Show Straight Line",
        description="Show the straight measurement line. Display only - picked coordinates are kept",
        default=True,
        update=_on_display_changed,
    )

    near_coincident_mm: FloatProperty(
        name="Near-Coincident Tolerance (mm)",
        description=(
            "Distance below which two vertices are reported as near-coincident. "
            "Diagnostic only: BSMT never welds vertices automatically"
        ),
        default=0.01,
        min=0.0,
        max=10.0,
        soft_max=1.0,
        precision=4,
        step=1,
    )

    topology_valid: BoolProperty(
        name="Topology Report Valid",
        description="True once topology diagnostics have been run",
        default=False,
    )
    topology_object: StringProperty(
        name="Diagnosed Object",
        description="Object the current topology report was computed from",
        default="",
    )
    topology_report: StringProperty(
        name="Topology Report",
        description="Most recent topology diagnostics output",
        default="",
    )

    surface_a: PointerProperty(
        name="Surface Point A",
        type=BSMT_SurfacePoint,
    )
    surface_b: PointerProperty(
        name="Surface Point B",
        type=BSMT_SurfacePoint,
    )
    transform_debug: BoolProperty(
        name="Transform Debug Log",
        description=(
            "Print [BSMT TRANSFORM] diagnostics to the system console while "
            "the scan is transformed. Development aid"
        ),
        default=True,
    )

    show_surface_debug: BoolProperty(
        name="SurfacePoint Debug",
        description="Show canonical surface attachment details for A and B",
        default=False,
    )

    # Milestone 3.14: which of the four possible causes of a slow measurement
    # line is actually responsible - the solver, a cache miss, Blender curve
    # construction, or handler churn. Recording is always on and costs a
    # deque append; this only controls whether it is also printed, and even
    # then a repeating stage prints at most once every two seconds so a
    # redraw-rate stage can never flood the console.
    timing_debug: BoolProperty(
        name="Timing Log",
        description=(
            "Print [BSMT TIMING] stage timings to the system console. The "
            "timings are always recorded and shown in the panel; this only "
            "adds the console lines. Development aid"
        ),
        default=False,
        update=_on_timing_debug_changed,
    )
    show_timing: BoolProperty(
        name="Path Timing",
        description="Show what the last path operations actually spent their "
                    "time on",
        default=False,
    )

    # ------------------------------------------------------------------
    # Milestone 2.2 - exact geodesic backend proof of concept.
    # Development diagnostics only. None of this participates in the Phase 1
    # straight-line measurement or in any displayed research result, and no
    # surface distance is written anywhere in Phase 2 until Milestone 2.3.
    # ------------------------------------------------------------------
    show_geodesic_backend: BoolProperty(
        name="Geodesic Backend",
        description=(
            "Show the Milestone 2.2 exact-geodesic backend diagnostics panel"
        ),
        default=False,
    )
    env_report: StringProperty(
        name="Environment Report",
        description="Most recent runtime environment report",
        default="",
    )
    env_report_valid: BoolProperty(default=False)

    backend_test_report: StringProperty(
        name="Backend Self-Test Report",
        description="Most recent synthetic exact-geodesic backend test output",
        default="",
    )
    backend_test_valid: BoolProperty(default=False)

    backend_test_dense: BoolProperty(
        name="Include Dense Benchmark",
        description=(
            "Also benchmark a synthetic mesh of roughly the real scan's "
            "triangle count. This takes seconds to minutes and Blender will "
            "not redraw while it runs. The real scan is never loaded"
        ),
        default=True,
    )
    backend_test_triangles: IntProperty(
        name="Benchmark Triangles",
        description=(
            "Target triangle count for the dense benchmark. Defaults to the "
            "triangle count of the reference scan 21_M_3400E"
        ),
        default=314086,
        min=1000,
        max=5000000,
    )
    backend_test_dijkstra: BoolProperty(
        name="Compare Edge-Dijkstra",
        description=(
            "Also report edge-graph Dijkstra on the planar meshes, to show "
            "its triangulation-dependent positive bias. DIAGNOSTIC ONLY: it "
            "is never a BSMT measurement backend"
        ),
        default=True,
    )

    components: CollectionProperty(
        name="Components",
        description="Connected components found by the last preview",
        type=BSMT_ComponentInfo,
    )
    component_preview_valid: BoolProperty(
        name="Component Preview Valid",
        default=False,
    )
    component_preview_object: StringProperty(
        name="Previewed Object",
        description="Object the current component preview was built from",
        default="",
    )
    component_report: StringProperty(
        name="Component Verification Report",
        description="Most recent component integrity verification output",
        default="",
    )
    component_report_valid: BoolProperty(default=False)

    component_isolate: IntProperty(
        name="Isolate",
        description="0 shows every component; otherwise show only that component",
        default=0,
        min=0,
        update=_on_component_isolate_changed,
    )

    # ------------------------------------------------------------------
    # Milestone 3.0 - Landmark Manager.
    # The landmark COLLECTION lives on the Scene as scene.bsmt_landmarks, as
    # specified. These are the manager's settings, kept here with every other
    # BSMT setting so the panel has one props object to read.
    # ------------------------------------------------------------------
    landmark_index: IntProperty(
        name="Active Landmark",
        description="Row selected in the landmark list",
        default=0,
        min=0,
        # Selecting a different row changes which marker is emphasised and
        # which label is drawn in SELECTED scope, and neither is something
        # Blender would repaint on its own. It is also the plainest possible
        # statement that the researcher is working in Landmark Manager.
        update=_on_landmark_index_changed,
    )
    landmark_next_id: IntProperty(
        name="Next Stable ID",
        description="Monotonic counter. Never decreases, so a stable id is "
                    "never reused within a scene",
        default=1,
        min=1,
    )
    protocol_name: StringProperty(
        name="Protocol",
        description="Name of the landmark protocol currently loaded",
        default="",
    )
    # ------------------------------------------------------------------
    # Milestone 3.8 - landmark display. Every property here is cosmetic:
    # none of them can reach a SurfacePoint, a mesh or a distance.
    # ------------------------------------------------------------------
    show_landmarks: BoolProperty(
        name="Show Markers",
        description="Show the landmark markers in the viewport. Independent "
                    "of the Point A/B markers",
        default=True,
        update=_on_landmark_control_used,
    )
    landmark_marker_size_px: IntProperty(
        name="Marker Size (px)",
        description="Diameter of a landmark marker in SCREEN PIXELS, so every "
                    "landmark reads at exactly the same size however far you "
                    "zoom. The marker is an annotation; changing it never "
                    "moves a landmark",
        default=overlay.DEFAULT_MARKER_SIZE,
        min=2, max=20,
        update=_on_landmark_control_used,
    )
    landmark_marker_color: FloatVectorProperty(
        name="Marker Color",
        description="Color of a VALID landmark marker. A stale or invalid "
                    "landmark keeps its status color instead, so a marker "
                    "that cannot be trusted never looks like one that can",
        subtype='COLOR', size=4,
        default=visualization.LANDMARK_COLOR,
        min=0.0, max=1.0,
        update=_on_landmark_control_used,
    )
    show_landmark_labels: BoolProperty(
        name="Show Labels",
        description="Draw each landmark's name next to its marker in the "
                    "viewport. Display only: nothing is added to the scene",
        default=True,
        update=_on_landmark_label_changed,
    )
    landmark_label_color: FloatVectorProperty(
        name="Label Color",
        description="Color of a VALID landmark's label. A stale or invalid "
                    "landmark uses its status color instead",
        subtype='COLOR', size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0, max=1.0,
        update=_on_landmark_label_changed,
    )
    landmark_label_size: IntProperty(
        name="Label Size",
        description="Label text height in SCREEN PIXELS, so labels stay the "
                    "same size however far you zoom",
        default=overlay.DEFAULT_LABEL_SIZE,
        min=6, max=64,
        update=_on_landmark_label_changed,
    )
    landmark_label_offset: IntProperty(
        name="Label Offset",
        description="How far the label sits from the marker, in screen "
                    "pixels, so the text never covers the surface point it "
                    "names",
        default=overlay.DEFAULT_LABEL_OFFSET,
        min=0, max=60,
        update=_on_landmark_label_changed,
    )
    landmark_label_shadow: BoolProperty(
        name="Label Shadow",
        description="Draw a dark shadow behind the label so it stays readable "
                    "over a light-colored scan",
        default=True,
        update=_on_landmark_label_changed,
    )
    #: Which workflow stage the researcher is working in. DISPLAY STATE
    #: ONLY - see enter_stage(). Defaults to LANDMARKS so a scene that has
    #: never left the stage behaves exactly as it did before this existed.
    ui_stage: StringProperty(default=readiness.STAGE_LANDMARKS)

    show_landmark_display: BoolProperty(
        name="Landmark Display",
        description="Show the landmark marker and label settings",
        default=False,
    )
    landmark_visibility: EnumProperty(
        name="Landmark Visibility",
        description="Whether landmarks behind the mesh are still drawn",
        items=(
            ('ALWAYS', "Always on Top",
             "Draw every landmark, including the ones on the far side of the "
             "body"),
            ('OCCLUDED', "Visible Surface Only",
             "Hide a landmark's marker and label while the mesh is in front "
             "of it"),
        ),
        default='ALWAYS',
        update=_on_landmark_control_used,
    )
    landmark_label_scope: EnumProperty(
        name="Show",
        description="Which landmarks get a label",
        items=(
            ('ALL', "All Landmarks", "Label every positioned landmark"),
            ('SELECTED', "Selected Landmark Only",
             "Label only the landmark selected in the list"),
        ),
        default='ALL',
        update=_on_landmark_label_changed,
    )
    landmark_summary: StringProperty(
        name="Landmark Summary",
        description="Result of the last Validate All",
        default="",
    )

    # guided picking (sect. 13). Deliberately UI state rather than a
    # long-lived modal operator: only the individual pick is modal, exactly
    # as the A/B workflow already does it, so normal Blender keyboard input
    # is never globally swallowed.
    guided_active: BoolProperty(name="Guided Picking", default=False,
                                options={'SKIP_SAVE'})
    guided_index: IntProperty(name="Guided Position", default=0, min=0,
                              options={'SKIP_SAVE'})
    guided_skip_valid: BoolProperty(
        name="Skip Already Picked",
        description="During guided picking, step over landmarks that are "
                    "already VALID",
        default=True,
    )

    # ------------------------------------------------------------------
    # Milestone 3.1 - Measurement Manager settings. The definitions live on
    # the Scene as scene.bsmt_measurements.
    # ------------------------------------------------------------------
    measurement_index: IntProperty(
        name="Active Measurement", default=0, min=0,
        update=_on_measurement_index_changed,
    )
    measurement_next_id: IntProperty(
        name="Next Measurement ID", default=1, min=1,
    )
    measurement_protocol_name: StringProperty(
        name="Measurement Template", default="",
    )
    measurement_summary: StringProperty(
        name="Measurement Summary", default="",
    )
    measurement_progress: StringProperty(
        name="Batch Progress", default="",
    )
    measurement_running: BoolProperty(
        name="Batch Running", default=False, options={'SKIP_SAVE'},
    )
    show_measurement_detail: BoolProperty(
        name="Details", description="Show the selected measurement in full",
        default=True,
    )
    # ------------------------------------------------------------------
    # Milestone 3.6 - rigid anatomical alignment. Object transforms only:
    # nothing here touches mesh geometry, so a SurfacePoint cannot go stale.
    # ------------------------------------------------------------------
    align_mode: EnumProperty(
        name="Mode",
        items=(
            ('LANDMARK', "Landmark-Based",
             "Build an anatomical frame from four picked reference points"),
            ('MANUAL', "Manual", "Rotate and reset by hand"),
        ),
        default='LANDMARK',
    )
    align_left: PointerProperty(type=BSMT_SurfacePoint)
    align_right: PointerProperty(type=BSMT_SurfacePoint)
    align_superior: PointerProperty(type=BSMT_SurfacePoint)
    align_inferior: PointerProperty(type=BSMT_SurfacePoint)

    align_object: StringProperty(name="Aligned Object", default="")
    align_applied: BoolProperty(default=False)
    #: BSMT has moved this object and holds a pre-alignment matrix
    #: for it. True even when the alignment failed validation.
    align_moved: BoolProperty(default=False)
    align_method: StringProperty(default="")
    align_created: StringProperty(default="")
    align_report: StringProperty(default="")
    align_previous_matrix: FloatVectorProperty(size=16, default=(
        1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))
    align_applied_matrix: FloatVectorProperty(size=16, default=(
        1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))
    align_residual_degrees: FloatProperty(default=0.0)

    # Milestone 3.20 - the MEASURED postcondition, kept beside the claim.
    # 'PASS' / 'FAIL' / 'UNKNOWN' / '' (never applied). These are a record of
    # what was measured at Apply; the panel re-measures every draw, because a
    # stored verdict about a pose the user can still change is exactly the
    # thing that lied before.
    align_validation: StringProperty(default="")
    #: Whether Move To World Origin was part of what the applied
    #: alignment was asked to do. The live status is judged against
    #: this, not against the checkbox's current state.
    align_origin_requested: BoolProperty(default=False)
    align_validation_report: StringProperty(default="")
    #: Why the last Apply was refused, measured on the trial pose.
    #: That pose no longer exists after the rollback, so this cannot
    #: be re-derived and is the only record of it.
    align_refusal_report: StringProperty(default="")
    align_lr_dot_x: FloatProperty(default=0.0)
    align_si_dot_z: FloatProperty(default=0.0)
    align_axis_error_degrees: FloatProperty(default=0.0)
    align_orthogonality_error: FloatProperty(default=0.0)
    align_fine_degrees: FloatProperty(
        name="Rotate By (deg)", default=5.0, min=-180.0, max=180.0,
        description="How far the X / Y / Z buttons below rotate the object",
    )
    align_move_to_origin: BoolProperty(
        name="Move To World Origin",
        description="Also translate the inferior reference to the world "
                    "origin when applying a landmark alignment",
        default=True,
    )
    align_preview: BoolProperty(default=False)
    show_alignment: BoolProperty(name="Alignment", default=False)

    # ------------------------------------------------------------------
    # Milestone 3.4 - controlled mesh repair.
    # ------------------------------------------------------------------
    repair_object: StringProperty(name="Repair Target", default="")
    repair_report: StringProperty(name="Repair Report", default="")
    repair_valid: BoolProperty(default=False)
    repair_log: StringProperty(name="Repair Log", default="")
    repair_backup_mesh: StringProperty(default="")
    repair_running: BoolProperty(default=False, options={'SKIP_SAVE'})
    show_repair: BoolProperty(name="Mesh Repair", default=False)

    boundary_loops: CollectionProperty(type=BSMT_BoundaryLoop)
    boundary_loop_index: IntProperty(default=0, min=0)
    repair_components: CollectionProperty(type=BSMT_RepairComponent)
    repair_component_index: IntProperty(default=0, min=0)

    # Milestone 3.28 - the non-manifold defect the researcher is inspecting,
    # and the connected component it sits in. Filled by Analyze alongside
    # every other repair list, so the panel can never describe one state of
    # the mesh while another list describes a different one.
    # ------------------------------------------------------------------
    # Milestone 3.29 - Surface Regions. The definitions live on the Scene
    # (bsmt_regions); these are the selection and the id counter.
    # ------------------------------------------------------------------
    region_index: IntProperty(default=0, min=0)
    region_next_id: IntProperty(default=1, min=1)
    region_boundary_thickness_mm: FloatProperty(
        name="Boundary Thickness (mm)",
        description="Drawn thickness of a region boundary. Display only - it "
                    "never changes a path or triggers a solve",
        default=3.0, min=0.1, max=50.0,
    )
    show_regions: BoolProperty(name="Surface Regions", default=False)

    repair_nonmanifold_defects: CollectionProperty(type=BSMT_NonManifoldDefect)
    repair_nonmanifold_index: IntProperty(
        name="Defect", default=0, min=0,
        description="Which non-manifold defect is focused for inspection",
    )
    #: The canonical geometry hash the lists above were computed from. A
    #: geometry-changing action compares it to the live mesh and refuses to
    #: act on numbers that describe a mesh that no longer exists, rather than
    #: trusting a stored index (sect. 7.7).
    repair_geometry_hash: StringProperty(default="")
    repair_artifact_preview: StringProperty(default="")

    # ------------------------------------------------------------------
    # Milestone 3.30 - local face repair inside a component that may not be
    # deleted. Every field here is a DISPLAY CACHE of one inspection and
    # carries no authority: `repair_local_hash` is what says which mesh it
    # describes, and the removal re-derives the candidate from the live
    # canonical mesh rather than trusting anything stored (sect. 7.7).
    # ------------------------------------------------------------------
    #: The inspection report, as `localrepair.report_lines` produced it.
    repair_local_report: StringProperty(default="")
    #: The classification code, e.g. SMALL_DANGLING_FLAP or AMBIGUOUS.
    repair_local_classification: StringProperty(default="")
    #: True only when the inspection found an unambiguous removable
    #: candidate. The destructive button is not drawn at all otherwise.
    repair_local_removable: BoolProperty(default=False)
    #: The canonical geometry hash the inspection was computed against, and
    #: the defect it was computed for. Either one failing to match the live
    #: mesh makes the report stale, and a stale report never acts.
    repair_local_hash: StringProperty(default="")
    repair_local_region_id: IntProperty(default=0)
    #: Candidate size, for the one-line panel summary only.
    repair_local_face_count: IntProperty(default=0)
    repair_local_vertex_count: IntProperty(default=0)
    repair_local_area_mm2: FloatProperty(default=0.0)

    # Milestone 3.19 - degenerate triangles, the one blocking defect Mesh
    # Repair v1 can fix. The list is what Analyze Repair Issues produced; it
    # is never recomputed by a redraw.
    repair_degenerates: CollectionProperty(type=BSMT_DegenerateDefect)
    repair_degenerate_index: IntProperty(
        name="Defect", default=0, min=0,
        description="Which degenerate triangle is selected for inspection",
    )
    repair_degenerate_preview: StringProperty(default="")
    repair_degenerate_scope: EnumProperty(
        name="Repair",
        description="How much to repair in one action",
        items=(
            ('SELECTED', "Selected Defect",
             "Repair only the degenerate triangle selected above"),
            ('ALL_SAFE', "All Repairable",
             "Repair every degenerate triangle whose vertices are exactly "
             "coincident. Slivers are left alone"),
        ),
        default='ALL_SAFE',
    )

    repair_weld_distance_mm: FloatProperty(
        name="Local Weld Distance (mm)",
        description="Merge distance used ONLY at the reported non-manifold "
                    "edges. This is never a global merge-by-distance: the "
                    "vertex set is those edges' endpoints and nothing else",
        default=0.01, min=0.0001, max=5.0, precision=4,
    )
    repair_readiness: StringProperty(name="Readiness", default="")

    # ------------------------------------------------------------------
    # Milestone 3.3 - scan preprocessing and the solver safety gate.
    # ------------------------------------------------------------------
    preprocess_preset: EnumProperty(
        name="Preset",
        items=[(name, label, desc) for name, label, desc, _t in preprocess.PRESETS],
        default='STANDARD',
        update=_on_preprocess_preset_changed,
    )
    preprocess_target_triangles: IntProperty(
        name="Target Triangles",
        description="Approximate triangle count for the measurement copy. "
                    "Collapse decimation lands near it, not exactly on it",
        default=preprocess.DEFAULT_TARGET_TRIANGLES,
        min=1000, max=20000000,
    )
    preprocess_report: StringProperty(name="Preprocessing Report", default="")
    preprocess_valid: BoolProperty(default=False)
    preprocess_copy_name: StringProperty(default="")
    preprocess_running: BoolProperty(default=False, options={'SKIP_SAVE'})

    # Milestone 3.15: the measurement-ready verdict for the copy that was
    # just produced. Stored rather than recomputed on every redraw, because
    # it is a statement about ONE preprocessing run and must not silently
    # change underneath the report that explains it.
    preprocess_status: StringProperty(default="")
    preprocess_status_detail: StringProperty(default="")
    preprocess_seconds: FloatProperty(default=0.0)
    preprocess_diagnostic_seconds: FloatProperty(default=0.0)
    show_preprocess_detail: BoolProperty(
        name="Scan Detail",
        description="Show the full topology and appearance breakdown for the "
                    "selected scan",
        default=True,
    )

    dense_threshold_triangles: IntProperty(
        name="Dense Mesh Threshold",
        description="Triangle count above which exact geodesic computation is "
                    "treated as unsafe. An operational threshold, not a "
                    "mathematical limit",
        default=preprocess.DEFAULT_DENSE_THRESHOLD,
        min=10000, max=50000000,
    )
    guard_dense_solve: BoolProperty(
        name="Block Solving Above Threshold",
        description="Refuse exact geodesic computation on a mesh above the "
                    "density threshold. A dense scan has crashed Blender with "
                    "SIGSEGV, and a crash loses the session, so this defaults "
                    "on. Untick to warn instead of refusing",
        default=True,
    )
    show_preprocessing: BoolProperty(name="Scan Preprocessing", default=False)
    # ------------------------------------------------------------------
    # Milestone 3.11 - session metadata. METADATA ONLY: nothing here is read
    # by any geometry, landmark or measurement code path, and a test asserts
    # it. It rides along in the .blend so an exported file can say which
    # subject and condition it belongs to.
    # ------------------------------------------------------------------
    session_subject_id: StringProperty(
        name="Subject ID",
        description="Participant identifier, e.g. S01. Metadata only - it "
                    "never affects geometry or any calculation",
        default="",
    )
    session_condition: StringProperty(
        name="Condition",
        description="Condition or posture, e.g. SV2. Metadata only",
        default="",
    )
    session_scan_id: StringProperty(
        name="Scan ID",
        description="Identifier for this particular scan, e.g. S01_SV2_01. "
                    "Metadata only",
        default="",
    )
    session_notes: StringProperty(
        name="Notes",
        description="Anything worth recording about this session. Metadata "
                    "only",
        default="",
    )
    show_about: BoolProperty(
        name="About BSMT",
        description="Show the version, platform and solver summary to copy "
                    "into a bug report",
        default=False,
    )
    show_session: BoolProperty(
        name="Session Info",
        description="Show the session metadata, export and protocol controls",
        default=False,
    )
    export_report: StringProperty(
        name="Export Report",
        description="What the last export wrote",
        default="",
    )

    show_readiness: BoolProperty(
        name="Readiness Detail",
        description="Show every reason behind the readiness line",
        default=False,
    )

    # ------------------------------------------------------------------
    # Milestone 3.2 - measurement visualisation. Display only: nothing here
    # can change a distance, a path or a measurement's validity.
    # ------------------------------------------------------------------
    viz_mode: EnumProperty(
        name="Draw",
        items=(
            ('STRAIGHT', "Straight Distance",
             "Draw only the straight line between the two landmarks"),
            ('SURFACE', "Surface Path",
             "Draw only the exact geodesic path across the surface"),
            ('BOTH', "Both",
             "Draw the straight line and the surface path together"),
        ),
        default='BOTH',
        update=_on_visualization_changed,
    )
    viz_straight_color: FloatVectorProperty(
        name="Straight Line Color", subtype='COLOR', size=4,
        default=(1.0, 0.85, 0.1, 1.0), min=0.0, max=1.0,
        update=_on_visualization_style_changed,
    )
    viz_path_color: FloatVectorProperty(
        name="Surface Path Color", subtype='COLOR', size=4,
        default=(0.1, 0.9, 0.8, 1.0), min=0.0, max=1.0,
        update=_on_visualization_style_changed,
    )
    viz_straight_thickness_mm: FloatProperty(
        name="Straight Line Thickness (mm)", default=3.0, min=0.5, max=20.0,
        update=_on_visualization_style_changed,
    )
    viz_path_thickness_mm: FloatProperty(
        name="Surface Path Thickness (mm)", default=3.0, min=0.5, max=20.0,
        update=_on_visualization_style_changed,
    )
    viz_scope: EnumProperty(
        name="Show",
        description="Which measurements to draw in the viewport. Changing "
                    "this never computes anything: a measurement with no "
                    "cached surface path simply gets no path drawn",
        items=(
            ('SELECTED', "Selected Measurement",
             "Draw only the measurement selected in the Measurement Manager"),
            ('TICKED', "Selected Measurements",
             "Draw every measurement whose Show box is ticked"),
            ('ENABLED', "All Enabled Measurements",
             "Draw every enabled measurement that is fully defined"),
        ),
        default='SELECTED',
        update=_on_visualization_changed,
    )
    viz_surface_offset: BoolProperty(
        name="Lift Path Off Surface",
        description="Offset the drawn path along the surface normal so it is "
                    "not half-buried in the scan. Display only: it does not "
                    "change the stored path or its length",
        default=True,
        update=_on_visualization_changed,
    )
    viz_status: StringProperty(name="Visualization Status", default="")
    viz_running: BoolProperty(default=False, options={'SKIP_SAVE'})
    show_measurement_visualization: BoolProperty(
        name="Measurement Visualization", default=False,
    )

    show_measurement_results: BoolProperty(
        name="Measurement Results",
        description="Show every defined measurement's result in order, "
                    "without selecting each one",
        default=True,
    )

    # ------------------------------------------------------------------
    # Milestone 2.3 - production surface (geodesic) distance.
    # Stored SEPARATELY from the Phase 1 straight distance so neither can
    # overwrite the other, and invalidated independently (sect. 6.5).
    # ------------------------------------------------------------------
    surface_valid: BoolProperty(
        name="Surface Distance Valid",
        description="True when a surface distance has been computed and is current",
        default=False,
    )
    surface_distance_mm: FloatProperty(
        name="Surface Distance",
        description="Exact geodesic distance along the surface, in millimetres",
        default=0.0,
    )
    surface_straight_mm: FloatProperty(
        name="Straight Distance At Solve",
        description="Straight distance between the same two surface locations "
                    "at the moment of the solve, in millimetres",
        default=0.0,
    )
    surface_ratio: FloatProperty(
        name="Surface / Straight",
        default=0.0,
    )
    surface_status: StringProperty(
        name="Surface Status",
        description="Failure message when no surface distance is available",
        default="",
    )
    surface_summary: StringProperty(
        name="Surface Query Summary",
        description="Compact record of how the result was obtained",
        default="",
    )
    surface_mode: StringProperty(name="Surface Mode", default="")

    # identity the result belongs to, for invalidation
    surface_object: StringProperty(name="Surface Source Object", default="")
    surface_geometry_hash: StringProperty(name="Surface Geometry Hash", default="")
    surface_metric_key: StringProperty(name="Surface Metric Key", default="")
    surface_metric_tensor: FloatVectorProperty(
        name="Surface Metric Tensor",
        description="(L^T L) * unit_multiplier^2 at solve time, row major. "
                    "Rotation invariant by construction, so translation and "
                    "rotation do not invalidate the result while any scale or "
                    "unit change does (sect. 6.4)",
        size=9,
        default=(0.0,) * 9,
    )
    surface_component_id: IntProperty(name="Surface Component", default=0)

    # provenance
    surface_backend_name: StringProperty(name="Backend", default="")
    surface_backend_version: StringProperty(name="Backend Version", default="")
    surface_algorithm_version: StringProperty(name="Algorithm Version", default="")
    surface_bound_factor: FloatProperty(
        name="Bound Factor",
        description="Multiple of the straight distance used as max_distance. "
                    "0 means the unbounded last-resort fallback was used",
        default=0.0,
    )
    surface_unbounded_fallback: BoolProperty(default=False)
    surface_attempts: IntProperty(name="Attempts", default=0)
    surface_seconds: FloatProperty(name="Elapsed", default=0.0)
    surface_insertion_seconds: FloatProperty(default=0.0)
    surface_solver_seconds: FloatProperty(default=0.0)
    surface_provenance: StringProperty(
        name="Surface Provenance",
        description="Full provenance record for the stored surface distance",
        default="",
    )

    show_surface_provenance: BoolProperty(
        name="Provenance",
        description="Show the full provenance record for the surface distance",
        default=False,
    )
    surface_running: BoolProperty(
        name="Surface Solve Running",
        description="Guard against starting a second surface solve while one "
                    "is already running",
        default=False,
        options={'SKIP_SAVE'},
    )

    point_a_valid: BoolProperty(
        name="Point A Valid",
        description="True once Point A has been picked on a mesh surface",
        default=False,
    )
    point_a: FloatVectorProperty(
        name="Point A",
        description="World-space location of Point A, in coordinate units",
        size=3,
        subtype='XYZ',
        default=(0.0, 0.0, 0.0),
    )

    point_b_valid: BoolProperty(
        name="Point B Valid",
        description="True once Point B has been picked on a mesh surface",
        default=False,
    )
    point_b: FloatVectorProperty(
        name="Point B",
        description="World-space location of Point B, in coordinate units",
        size=3,
        subtype='XYZ',
        default=(0.0, 0.0, 0.0),
    )

    distance_valid: BoolProperty(
        name="Distance Valid",
        description="True once a distance has been calculated for the current points",
        default=False,
    )
    distance_mm: FloatProperty(
        name="Straight Distance",
        description="Last calculated straight-line distance, in millimetres",
        default=0.0,
    )


def get_props(context):
    """Convenience accessor; returns None if the add-on is not registered."""
    return getattr(context.scene, "bsmt", None)


def surface_point(props, slot):
    """The SurfacePoint for slot 'A' or 'B'."""
    return props.surface_a if slot == 'A' else props.surface_b


def clear_surface_point(point):
    point.valid = False
    point.source_object = ""
    point.geometry_hash = ""
    point.triangle_index = -1
    point.barycentric = (0.0, 0.0, 0.0)
    point.component_id = 0
    point.kind = ""
    point.local_xyz = (0.0, 0.0, 0.0)
    point.world_xyz = (0.0, 0.0, 0.0)
    point.physical_mm_xyz = (0.0, 0.0, 0.0)
    point.status = "NOT PICKED"
    point.reconstruction_error = 0.0


def fill_surface_point(point, source_object, geometry_hash, triangle_index,
                       barycentric, component_id, kind, local_xyz, world_xyz,
                       physical_mm_xyz, reconstruction_error):
    """Write a canonical surface location into any BSMT_SurfacePoint.

    Side-effect free, so it serves both the A/B slots and a named landmark's
    nested surface point. There is exactly one place these fields are written
    (sect. 16); the A/B-specific consequences live in set_surface_point().
    """
    point.valid = True
    point.source_object = source_object
    point.geometry_hash = geometry_hash
    point.triangle_index = int(triangle_index)
    point.barycentric = tuple(float(v) for v in barycentric)
    point.component_id = int(component_id)
    point.kind = kind
    point.local_xyz = tuple(float(v) for v in local_xyz)
    point.world_xyz = tuple(float(v) for v in world_xyz)
    point.physical_mm_xyz = tuple(float(v) for v in physical_mm_xyz)
    point.status = "VALID"
    point.reconstruction_error = float(reconstruction_error)
    return point


def set_surface_point(props, slot, source_object, geometry_hash, triangle_index,
                      barycentric, component_id, kind, local_xyz, world_xyz,
                      physical_mm_xyz, reconstruction_error):
    """Store the canonical surface location for slot 'A' or 'B'."""
    point = surface_point(props, slot)
    fill_surface_point(
        point, source_object, geometry_hash, triangle_index, barycentric,
        component_id, kind, local_xyz, world_xyz, physical_mm_xyz,
        reconstruction_error,
    )
    # A re-picked A/B point invalidates any surface distance that used the
    # old one. set_point() also clears it, but this function is reachable on
    # its own, so the rule is enforced in both places rather than assumed.
    # Landmarks go through fill_surface_point() instead and therefore do NOT
    # disturb the A/B result.
    clear_surface_result(props)
    props.surface_status = ""
    return point


# ---------------------------------------------------------------------------
# Landmark Manager (Milestone 3.0)
# ---------------------------------------------------------------------------
#
# The collection lives on the Scene (scene.bsmt_landmarks); its settings live
# on props. These helpers are the only place landmarks are created, removed or
# re-statused, so the invariants of sect. 4 and sect. 8 hold in one place.


def get_landmarks(context):
    """The scene's landmark collection, or None if the add-on is not registered."""
    scene = getattr(context, "scene", None)
    return getattr(scene, "bsmt_landmarks", None) if scene is not None else None


def landmark_names(collection):
    return [item.name for item in collection]


def landmark_protocol_ids(collection):
    return [item.protocol_id for item in collection]


def active_landmark(context, props=None):
    """The selected landmark, or None."""
    collection = get_landmarks(context)
    if not collection:
        return None
    if props is None:
        props = get_props(context)
    if props is None:
        return None
    index = props.landmark_index
    if 0 <= index < len(collection):
        return collection[index]
    return None


def add_landmark(context, props, name, protocol_id="", notes="",
                 allow_duplicate=False):
    """Append a landmark. Returns it. Raises landmarks.LandmarkError.

    Creation never requires picking: the new landmark starts NOT_PICKED
    (sect. 4). Creation order is the collection order and is preserved.
    """
    collection = get_landmarks(context)
    if collection is None:
        raise landmarks.LandmarkError("landmark collection is not registered")

    cleaned = landmarks.clean_name(name)
    existing = landmark_names(collection)
    if landmarks.name_exists(existing, cleaned):
        if not allow_duplicate:
            raise landmarks.LandmarkError(
                "a landmark named '%s' already exists. Enable 'Allow "
                "Duplicate Name' to add it anyway." % cleaned
            )
        # Explicitly confirmed: kept distinguishable rather than silently
        # duplicated, so two rows can never be confused later (sect. 20).
        cleaned = landmarks.unique_name(existing, cleaned)

    item = collection.add()
    item.stable_id = props.landmark_next_id
    props.landmark_next_id += 1
    item.protocol_id = (str(protocol_id).strip()
                        or landmarks.next_protocol_id(
                            landmark_protocol_ids(collection)))
    item.name = cleaned
    item.notes = str(notes or "")
    item.status = landmarks.STATUS_NOT_PICKED
    item.status_detail = ""
    clear_surface_point(item.surface_point)
    props.landmark_index = len(collection) - 1
    return item


def remove_landmark(context, props, index):
    """Remove one landmark. Returns its stable id, or None."""
    collection = get_landmarks(context)
    if collection is None or not 0 <= index < len(collection):
        return None
    stable_id = int(collection[index].stable_id)
    label = collection[index].label
    collection.remove(index)
    if props.landmark_index >= len(collection):
        props.landmark_index = max(0, len(collection) - 1)
    # Measurements that referenced it are NOT deleted or redirected. They
    # keep their definition and become INVALID_REFERENCE, so the loss is
    # visible rather than silently repaired (sect. 14).
    invalidate_measurements_for_landmark(
        context, stable_id, "landmark '%s' was deleted" % label
    )
    for item in measurements_referencing(get_measurements(context), stable_id):
        item.status = measurements.STATUS_INVALID_REFERENCE
        item.status_detail = "landmark '%s' (id %d) was deleted" % (label, stable_id)
    # Regions that used it are restated the same way, and for the same
    # reason: the reference is KEPT and named so the loss is visible, and
    # nothing is substituted. The region's boundary depends on the landmark
    # directly - no measurement need ever have existed.
    invalidate_regions_for_landmark(
        context, stable_id, "landmark '%s' was deleted" % label
    )
    return stable_id


def clear_landmarks(context, props):
    """Remove every named landmark. Returns the removed stable ids.

    Named research landmarks only. A/B, the topology preview and the scan are
    untouched (sect. 20).
    """
    collection = get_landmarks(context)
    if collection is None:
        return []
    stable_ids = [int(item.stable_id) for item in collection]
    collection.clear()
    props.landmark_index = 0
    props.landmark_summary = ""
    props.guided_active = False
    props.guided_index = 0
    # Every region is now missing every landmark it named. Restated in one
    # pass; no definition is edited and no boundary cache is thrown away.
    refresh_all_region_statuses(context)
    return stable_ids


def clear_landmark_position(item, context=None):
    """Forget a landmark's surface location, keeping its name and stable id.

    Any measurement that referenced it loses its result: a distance to a
    landmark that no longer has a position is not a measurement (sect. 12).
    """
    clear_surface_point(item.surface_point)
    item.status = landmarks.STATUS_NOT_PICKED
    item.status_detail = ""
    if context is not None:
        invalidate_measurements_for_landmark(
            context, item.stable_id,
            "landmark '%s' position was cleared" % item.label,
        )
        invalidate_regions_for_landmark(
            context, item.stable_id,
            "landmark '%s' position was cleared" % item.label,
        )


def set_landmark_status(item, status, detail=""):
    item.status = status
    item.status_detail = detail


def refresh_landmark_status(item, canonical=None, object_exists=None):
    """Recompute one landmark's status. Never re-projects anything.

    `canonical` is None when the mesh is not cached, which yields
    NEEDS_REFRESH rather than an assertion of validity (sect. 8).
    """
    point = item.surface_point
    if object_exists is None:
        object_exists = bool(
            point.source_object
            and bpy.data.objects.get(point.source_object) is not None
        )
    status, detail = landmarks.classify(
        picked=bool(point.valid),
        point_geometry_hash=point.geometry_hash,
        triangle_index=point.triangle_index,
        source_object=point.source_object,
        object_exists=object_exists,
        canonical_geometry_hash=(
            canonical.geometry_hash if canonical is not None else None
        ),
        triangle_count=(
            canonical.triangle_count if canonical is not None else None
        ),
    )
    set_landmark_status(item, status, detail)
    return status


def invalidate_for_geometry_change(context, object_name, props=None):
    """Re-state every dependency after this object's geometry was edited.

    A repair changes the polyhedral surface, so the geometry hash changes and
    a stored triangle index no longer names the same point. Nothing is
    re-projected - that would move a researcher's landmark silently - and
    nothing is silently kept VALID. Existing machinery does the deciding:
    `refresh_landmark_status` classifies each landmark against the new
    canonical mesh, and the existing measurement invalidation drops results
    and cached paths that were computed on the old geometry.

    Returns a dict of what changed.
    """
    if props is None:
        props = get_props(context)
    canonical = None
    if geodesic.MESHCACHE_AVAILABLE:
        obj = bpy.data.objects.get(object_name)
        canonical = (geodesic.meshcache.peek_current(obj)
                     if obj is not None else None)

    stale = 0
    collection = get_landmarks(context) or ()
    for item in collection:
        point = item.surface_point
        if not point.valid or point.source_object != object_name:
            continue
        before = item.status
        refresh_landmark_status(item, canonical)
        if item.status != before:
            stale += 1

    measurements_hit = 0
    measurement_collection = get_measurements(context) or ()
    for item in measurement_collection:
        touched = (item.result_object == object_name
                   or item.path_object == object_name)
        if not touched:
            continue
        invalidate_measurement_result(item, "the mesh geometry was repaired")
        measurements_hit += 1

    # A/B is Phase 1 state on the same mesh and must not outlive the edit.
    if props is not None and props.surface_object == object_name:
        clear_surface_result(props)

    # Milestone 3.29. A Surface Region is a boundary made of those paths, so
    # it cannot survive an edit they did not survive. Restated, never rebuilt:
    # BSMT does not reproject a boundary onto changed geometry any more than
    # it reprojects a landmark.
    regions_restated = refresh_all_region_statuses(context, canonical)

    return {
        "landmarks_restated": stale,
        "measurements_invalidated": measurements_hit,
        "regions_restated": regions_restated,
    }


def picked_landmarks(collection):
    """Landmarks that hold a surface location, in collection order."""
    return [item for item in collection if item.surface_point.valid]


def landmark_by_stable_id(collection, stable_id):
    for item in collection:
        if int(item.stable_id) == int(stable_id):
            return item
    return None


# ---------------------------------------------------------------------------
# Measurement Manager (Milestone 3.1)
# ---------------------------------------------------------------------------


def get_measurements(context):
    scene = getattr(context, "scene", None)
    return getattr(scene, "bsmt_measurements", None) if scene is not None else None


def active_measurement(context, props=None):
    collection = get_measurements(context)
    if not collection:
        return None
    if props is None:
        props = get_props(context)
    if props is None:
        return None
    index = props.measurement_index
    return collection[index] if 0 <= index < len(collection) else None


def measurement_protocol_ids(collection):
    return [item.protocol_id for item in collection]


def measurement_is_draft(item):
    """True while a row has not yet become a real measurement (sect. 6).

    The cached names are passed on purpose: a measurement loaded from a
    template whose landmark is missing has an unresolved id but a remembered
    name, and it must stay a real measurement with a broken reference rather
    than disappear into the drafts.
    """
    return measurements.is_draft(
        item.source_stable_id, item.target_stable_id,
        item.source_name or item.source_protocol_id,
        item.target_name or item.target_protocol_id,
    )


def defined_measurements(collection):
    """Only the real measurements. Drafts are not measurements."""
    return measurements.defined(collection or ())


def trailing_draft_index(collection):
    """Index of the draft at the END of the list, or -1.

    Only the last row counts. A draft in the middle was left there on purpose
    - the researcher is presumably still filling it in - and silently
    recycling it would move their selection somewhere they did not ask for.
    """
    if not collection:
        return -1
    last = len(collection) - 1
    return last if measurement_is_draft(collection[last]) else -1


def add_measurement(context, props, name="", source=None, target=None,
                    measurement_type=None, notes="", protocol_id=""):
    """Append a measurement definition, or a draft. Returns it.

    Landmarks need not be PICKED yet (sect. 5): a definition is a plan, and
    its status simply reports that it is not ready. They need not even be
    CHOSEN yet - a row with no endpoints is a draft, which is how Add
    Measurement starts one.
    """
    collection = get_measurements(context)
    if collection is None:
        raise measurements.MeasurementError(
            "measurement collection is not registered"
        )
    item = collection.add()
    item.stable_id = props.measurement_next_id
    props.measurement_next_id += 1
    item.protocol_id = (str(protocol_id).strip()
                        or measurements.next_protocol_id(
                            measurement_protocol_ids(collection)))
    item.measurement_type = measurement_type or measurements.TYPE_BOTH
    item.notes = str(notes or "")
    item.enabled = True

    if source is not None:
        bind_measurement_landmark(item, "source", source)
    if target is not None:
        bind_measurement_landmark(item, "target", target)

    if name:
        # An explicit name means the caller chose it - a template load, or a
        # researcher typing one - so it is theirs to keep.
        item.auto_name = False
        item.name = name
    else:
        item.auto_name = True
        # Empty while this is a draft; filled in the moment both endpoints
        # are chosen, by the picker update callbacks.
        item.name = auto_name_for(context, item)
    props.measurement_index = len(collection) - 1
    clear_measurement_result(item)
    return item


def bind_measurement_landmark(item, slot, landmark, context=None):
    """Point one end of a measurement at a landmark, by stable id."""
    setattr(item, slot + "_stable_id", int(landmark.stable_id))
    setattr(item, slot + "_protocol_id", landmark.protocol_id)
    setattr(item, slot + "_name", landmark.name)
    # Keep the picker in step. Guarded: setting a dynamic enum to an
    # identifier that is not currently in its item list raises.
    try:
        setattr(item, slot + "_picker", str(int(landmark.stable_id)))
    except (TypeError, ValueError):
        pass
    if context is not None:
        apply_auto_name(context, item)


def remove_measurement(context, props, index):
    collection = get_measurements(context)
    if collection is None or not 0 <= index < len(collection):
        return None
    stable_id = int(collection[index].stable_id)
    collection.remove(index)
    # The cached polyline outlives the helper on purpose, but not the
    # definition that owns it: a cache with no measurement is unreachable
    # weight in the .blend.
    try:
        pathcache.drop(stable_id)
    except Exception:                                 # pragma: no cover
        pass
    if props.measurement_index >= len(collection):
        props.measurement_index = max(0, len(collection) - 1)
    # Surface Regions are NOT restated here, and that is the point of
    # Milestone 3.31: a region is defined by landmarks and owns its own
    # boundary cache, so deleting a measurement cannot reach one. Under the
    # old model this line existed because a region referenced measurement
    # paths; it would now be a coupling with nothing behind it.
    return stable_id


def clear_measurements(context, props):
    collection = get_measurements(context)
    if collection is None:
        return 0
    count = len(collection)
    collection.clear()
    try:
        pathcache.drop_all()
    except Exception:                                 # pragma: no cover
        pass
    props.measurement_index = 0
    props.measurement_summary = ""
    props.measurement_progress = ""
    # Regions are untouched: clearing every measurement removes no part of a
    # region's definition and no part of its boundary cache.
    return count


#: The four states a surface path can be in. Made explicit because the only
#: safe response to "stale" is to SAY so - never to silently recompute a
#: solve that costs minutes on a dense scan (sect. 9).
PATH_NOT_COMPUTED = 'NOT_COMPUTED'
PATH_CACHED = 'CACHED'
PATH_STALE = 'STALE'
PATH_INVALID = 'INVALID'

PATH_STATE_LABELS = {
    PATH_NOT_COMPUTED: "NOT COMPUTED",
    PATH_CACHED: "CACHED",
    PATH_STALE: "STALE",
    PATH_INVALID: "INVALID",
}


def clear_measurement_path(item, remove_helper=True):
    """Forget a cached surface path. The measurement result is untouched.

    This is the deliberate, explicit discard - the Clear Cached Path button
    and the invalidation paths. It is NOT what a display change does: a
    helper can be removed and rebuilt as often as the researcher likes
    without the polyline going anywhere, because the polyline does not live
    in the helper (see pathcache.py).
    """
    had = bool(item.path_valid)
    item.path_valid = False
    item.path_point_count = 0
    item.path_length_mm = 0.0
    item.path_distance_mm = 0.0
    item.path_agreement_mm = 0.0
    item.path_elapsed_s = 0.0
    item.path_mode = ""
    item.path_object = ""
    item.path_geometry_hash = ""
    item.path_metric_tensor = (0.0,) * 9
    item.path_metric_key = ""
    item.path_unit = ""
    item.path_source_stable_id = 0
    item.path_target_stable_id = 0
    item.path_source_triangle = -1
    item.path_source_bary = (0.0,) * 3
    item.path_target_triangle = -1
    item.path_target_bary = (0.0,) * 3
    item.path_stale = False
    item.path_stale_reason = ""
    try:
        pathcache.drop(item.stable_id)
    except Exception:                                 # pragma: no cover
        pass
    if remove_helper:
        try:
            visualization.remove_measurement_helper(item.stable_id, 'PATH')
        except Exception:                             # pragma: no cover
            pass
    return had


def _endpoints_match(item, source, target):
    """Whether the cache was solved from the landmarks' CURRENT locations.

    Compared as triangle index plus barycentric coordinates, which is what a
    SurfacePoint actually is. Re-picking a landmark keeps its stable id, so
    ids alone would let a path outlive the point it was solved from.
    """
    for landmark, triangle, bary in (
        (source, item.path_source_triangle, item.path_source_bary),
        (target, item.path_target_triangle, item.path_target_bary),
    ):
        if landmark is None:
            return False
        point = landmark.surface_point
        if not point.valid:
            return False
        if int(point.triangle_index) != int(triangle):
            return False
        for stored, live in zip(bary, point.barycentric):
            # Barycentric coordinates are stored in single precision on both
            # sides, so they are compared at the float32 noise floor rather
            # than exactly.
            if abs(float(stored) - float(live)) > 1e-6:
                return False
    return True


def _landmarks_for(context, item):
    """The landmark collection this measurement lives beside, or None.

    Reached from the measurement's own `id_data` - the Scene its collection
    is attached to - when no context is available, which is the case in a
    depsgraph handler. Returning None means "cannot check", and the endpoint
    comparison is then skipped rather than guessed at: a cache must never be
    declared invalid because the caller had no context.
    """
    collection = get_landmarks(context) if context is not None else None
    if collection is not None:
        return collection
    scene = getattr(item, "id_data", None)
    return getattr(scene, "bsmt_landmarks", None) if scene is not None else None


def path_cache_state(context, item, canonical=None, matrix_world=None):
    """(state, reason) for one measurement's cached surface path.

    Pure inspection: it reads properties and asks pathcache whether a
    polyline exists. It never touches geometry, never builds a canonical
    mesh and above all never calls the solver, so it is safe from a panel
    draw and from a depsgraph handler.

    A rigid transform deliberately cannot reach STALE: the metric tensor is
    rotation invariant and carries no translation, so a translated or rotated
    scan keeps its path and the helper simply follows (sect. 11). A SCALE
    change does reach it, because it changes the physical metric the geodesic
    was solved under.
    """
    with timing.stage(timing.CACHE_VALIDATE):
        return _path_cache_state(context, item, canonical, matrix_world)


def _path_cache_state(context, item, canonical, matrix_world):
    if not item.path_valid:
        return PATH_NOT_COMPUTED, ""

    try:
        cached_points = pathcache.exists(item.stable_id)
    except Exception:                                 # pragma: no cover
        cached_points = False
    if not cached_points:
        return PATH_INVALID, "the cached polyline is missing from this file"

    if item.path_source_stable_id != item.source_stable_id or \
            item.path_target_stable_id != item.target_stable_id:
        return PATH_STALE, "the measurement now uses different landmarks"

    collection = _landmarks_for(context, item)
    if collection is not None:
        source = landmark_by_stable_id(collection, item.source_stable_id)
        target = landmark_by_stable_id(collection, item.target_stable_id)
        if source is None or target is None:
            return PATH_INVALID, "a referenced landmark no longer exists"
        if not _endpoints_match(item, source, target):
            return PATH_STALE, "a landmark has been re-picked since the solve"

    if item.path_object and bpy.data.objects.get(item.path_object) is None:
        return PATH_INVALID, "the scan '%s' is no longer in the file" % item.path_object

    if item.path_stale:
        return PATH_STALE, item.path_stale_reason or "a dependency changed"

    if canonical is not None:
        if canonical.geometry_hash != item.path_geometry_hash:
            return PATH_STALE, "the mesh geometry changed since the solve"
        if matrix_world is not None:
            live = metric_tensor(matrix_world, canonical.unit_multiplier)
            if not metric_tensors_match(live, item.path_metric_tensor):
                return PATH_STALE, ("the object scale or coordinate unit "
                                    "changed since the solve")
    return PATH_CACHED, ""


def path_is_current(item, canonical=None, matrix_world=None, context=None):
    """Whether a cached path still describes the current configuration.

    Thin wrapper over `path_cache_state` kept because it reads better at the
    call sites that only care whether the path may be drawn.
    """
    state, _reason = path_cache_state(context, item, canonical, matrix_world)
    return state == PATH_CACHED


def mark_path_stale(item, reason):
    """Record that a cached path no longer matches its dependencies.

    Deliberately does NOT delete anything. A stale path is reported as stale
    and recomputed only when the researcher asks, because the alternative -
    quietly re-running the unbounded solve - is minutes of frozen Blender
    triggered by something as innocent as a mesh edit (sect. 9).
    """
    if not item.path_valid or item.path_stale:
        return False
    item.path_stale = True
    item.path_stale_reason = str(reason)
    return True


def mark_paths_stale_for_object(scene, object_name, reason):
    """Mark every cached path solved on this scan stale. Returns the count.

    Cheap enough for a depsgraph handler by construction: it is a loop over
    the measurement definitions writing two properties, with no geometry
    read, no canonical mesh and no solver anywhere near it (sect. 7).
    """
    collection = getattr(scene, "bsmt_measurements", None) if scene else None
    if not collection:
        return 0
    marked = 0
    for item in collection:
        if not item.path_valid:
            continue
        if object_name and item.path_object and item.path_object != object_name:
            continue
        if mark_path_stale(item, reason):
            marked += 1
    return marked


def clear_measurement_result(item):
    """Forget a measurement's numbers. The definition is untouched.

    The cached surface path goes with them. A path is solved against the same
    landmarks, geometry and metric as the distance, so a path that outlived
    its result would be claiming to match a number that no longer exists.
    """
    clear_measurement_path(item)
    item.straight_valid = False
    item.straight_mm = 0.0
    item.surface_valid = False
    item.surface_mm = 0.0
    item.ratio = 0.0
    item.backend_name = ""
    item.backend_version = ""
    item.bound_factor = 0.0
    item.unbounded_fallback = False
    item.attempts = 0
    item.elapsed_s = 0.0
    item.surface_mode = ""
    item.result_object = ""
    item.result_geometry_hash = ""
    item.result_metric_tensor = (0.0,) * 9


def invalidate_measurement_result(item, reason):
    """Drop a stored result because a dependency changed (sect. 12).

    Never leaves a number behind: a distance whose landmarks, geometry or
    metric have changed is not a measurement of anything. The cached surface
    path goes with it - it was solved against the same configuration.
    """
    # clear_measurement_result() drops the cached path too, but a
    # measurement with no result can still hold one, so it is cleared here
    # unconditionally.
    clear_measurement_path(item)
    if item.has_result:
        clear_measurement_result(item)
        item.status = measurements.STATUS_NOT_READY
        item.status_detail = "invalidated: %s" % reason
    return item


def result_is_displayable(item):
    """True only when a stored number is genuinely current.

    The one place the UI asks "may I show this number". Numbers are cleared
    whenever a dependency changes, so has_result already implies currency -
    but pairing it with the status makes a stale value impossible to render
    as a live one even if a future path forgets to clear.
    """
    return bool(item.has_result and item.status == measurements.STATUS_VALID)


def measurement_by_stable_id(collection, stable_id):
    """The measurement with this stable id, or None. Never a substitute."""
    wanted = int(stable_id or 0)
    if not wanted:
        return None
    for item in (collection or ()):
        if int(item.stable_id) == wanted:
            return item
    return None


def measurements_referencing(collection, landmark_stable_id):
    """Definitions that use a landmark. Cheap: one pass, integer compares."""
    if not collection:
        return []
    wanted = int(landmark_stable_id)
    return [
        item for item in collection
        if item.source_stable_id == wanted or item.target_stable_id == wanted
    ]


def invalidate_measurements_for_landmark(context, landmark_stable_id, reason):
    """Invalidate only the definitions that reference this landmark (sect. 13).

    Targeted rather than a sweep: a landmark change touches the measurements
    that depend on it and nothing else, so 100 definitions stay responsive.
    """
    collection = get_measurements(context)
    affected = measurements_referencing(collection, landmark_stable_id)
    for item in affected:
        invalidate_measurement_result(item, reason)
    # Regions depend on the LANDMARK directly, not on the measurements that
    # happen to use it, so they are restated by
    # `invalidate_regions_for_landmark` on the same landmark change - not
    # here, and not only when some measurement also referenced it.
    return len(affected)


def resolve_measurement_landmarks(context, item):
    """(source, target) landmark objects, either possibly None."""
    collection = get_landmarks(context)
    if collection is None:
        return None, None
    return (
        landmark_by_stable_id(collection, item.source_stable_id)
        if item.source_stable_id else None,
        landmark_by_stable_id(collection, item.target_stable_id)
        if item.target_stable_id else None,
    )


def refresh_measurement_status(context, item, canonical=None,
                               matrix_world=None):
    """Recompute one measurement's status. Returns it.

    A stored result stays VALID only while every dependency it was computed
    against still matches. Translation and rotation cannot reach the failing
    branch: the metric tensor is rotation invariant and carries no
    translation (sect. 6.4).
    """
    source, target = resolve_measurement_landmarks(context, item)
    status, detail = measurements.readiness(
        source, target, item.source_stable_id, item.target_stable_id,
        item.source_name or item.source_protocol_id,
        item.target_name or item.target_protocol_id,
    )

    if not item.has_result:
        item.status = status
        item.status_detail = detail
        return item

    if status == measurements.STATUS_INVALID_REFERENCE:
        clear_measurement_result(item)
        item.status = status
        item.status_detail = detail
        return item
    if status == measurements.STATUS_STALE:
        clear_measurement_result(item)
        item.status = measurements.STATUS_STALE
        item.status_detail = detail
        return item

    if canonical is not None:
        if canonical.geometry_hash != item.result_geometry_hash:
            clear_measurement_result(item)
            item.status = measurements.STATUS_STALE
            item.status_detail = "the mesh geometry changed since this was calculated"
            return item
        if matrix_world is not None:
            live = metric_tensor(matrix_world, canonical.unit_multiplier)
            if not metric_tensors_match(live, item.result_metric_tensor):
                clear_measurement_result(item)
                item.status = measurements.STATUS_STALE
                item.status_detail = (
                    "the object scale or coordinate unit changed since this "
                    "was calculated"
                )
                return item

    item.status = measurements.STATUS_VALID
    item.status_detail = ""
    return item


# ---------------------------------------------------------------------------
# Surface Regions (Milestone 3.31)
# ---------------------------------------------------------------------------
#
# A region is a CLOSED BOUNDARY defined by ORDERED LANDMARKS. Everything in
# this section is bounded by one rule, which is the reason the section can be
# read at all: NOTHING HERE CALLS THE SOLVER. Facts are read from properties
# and from `pathcache`, the verdict is computed by the pure rules in
# `regions.py`, and a boundary is drawn from cached polylines. The one route
# to the solver is the Compute Boundary operator, and it is the only thing a
# researcher ever waits for.
#
# A region does NOT reference measurements. It shares the geodesic backend
# with them and nothing else: deleting, renaming or editing a measurement
# cannot reach a region, and a region can be defined and computed in a file
# that has no measurements at all.


def get_regions(context):
    scene = getattr(context, "scene", None)
    return getattr(scene, "bsmt_regions", None) if scene is not None else None


def region_names(collection):
    return [item.name for item in (collection or ())]


def region_protocol_ids(collection):
    return [item.protocol_id for item in (collection or ())]


def region_by_stable_id(collection, stable_id):
    wanted = int(stable_id or 0)
    for item in (collection or ()):
        if int(item.stable_id) == wanted:
            return item
    return None


def active_region(context, props=None):
    collection = get_regions(context)
    if not collection:
        return None
    if props is None:
        props = get_props(context)
    index = int(getattr(props, "region_index", 0) or 0) if props else 0
    if 0 <= index < len(collection):
        return collection[index]
    return None


def add_region(context, props, name="", protocol_id="", notes=""):
    """Append an empty Surface Region. Returns it."""
    collection = get_regions(context)
    if collection is None:
        raise regions.RegionError("region collection is not registered")
    item = collection.add()
    item.stable_id = props.region_next_id
    props.region_next_id += 1
    item.protocol_id = (str(protocol_id).strip()
                        or regions.next_protocol_id(
                            region_protocol_ids(collection)))
    item.name = (" ".join(str(name or "").split())
                 or regions.default_name(region_names(collection)))
    item.notes = str(notes or "")
    item.status = regions.STATUS_DRAFT
    item.status_code = regions.CODE_EMPTY
    item.status_detail = ("Add boundary landmarks, in the order they run "
                          "round the region.")
    props.region_index = len(collection) - 1
    return item


def remove_region(context, props, index):
    """Delete one region. Landmarks and measurements are KEPT.

    A region owns its boundary CACHE and its helper, and nothing else. It
    holds references to landmarks; deleting a boundary must never take one of
    those with it, nor any measurement, nor any measurement's solved path.
    """
    collection = get_regions(context)
    if collection is None or not 0 <= index < len(collection):
        return None
    stable_id = int(collection[index].stable_id)
    for drop in (visualization.remove_region_boundary,
                 visualization.remove_region_fill,
                 visualization.remove_region_panel):
        try:
            drop(stable_id)
        except Exception:                             # pragma: no cover
            pass
    for drop in (pathcache.drop_region, interiorcache.drop):
        try:
            drop(stable_id)
        except Exception:                             # pragma: no cover
            pass
    collection.remove(index)
    if props is not None:
        props.region_index = max(0, min(int(props.region_index),
                                        len(collection) - 1))
    return stable_id


# ---------------------------------------------------------------------------
# editing the definition - none of which computes anything
# ---------------------------------------------------------------------------

def append_region_landmark(item, landmark):
    """Add one landmark to the end of a region's boundary. Returns the entry."""
    entry = item.landmarks.add()
    entry.landmark_stable_id = int(landmark.stable_id)
    entry.landmark_protocol_id = landmark.protocol_id
    entry.landmark_name = landmark.name
    item.landmark_index = len(item.landmarks) - 1
    return entry


def remove_region_landmark(item, index):
    """Drop one boundary landmark. The cached boundary becomes stale."""
    if not 0 <= index < len(item.landmarks):
        return False
    item.landmarks.remove(index)
    item.landmark_index = max(0, min(int(item.landmark_index),
                                     len(item.landmarks) - 1))
    return True


def move_region_landmark(item, index, offset):
    """Reorder one boundary landmark. Returns the new index, or None.

    Order IS the boundary: A-B-C-D and A-C-B-D are different loops, so this
    is a real edit and the cached boundary stops matching the definition.
    Nothing is recomputed - see `regions.definition_key`.
    """
    count = len(item.landmarks)
    target = int(index) + int(offset)
    if not (0 <= index < count and 0 <= target < count):
        return None
    item.landmarks.move(index, target)
    item.landmark_index = target
    return target


def regions_referencing_landmark(collection, landmark_stable_id):
    """Regions whose boundary uses this landmark. One pass, integer compares."""
    wanted = int(landmark_stable_id or 0)
    if not wanted:
        return []
    found = []
    for item in (collection or ()):
        if any(int(entry.landmark_stable_id) == wanted
               for entry in item.landmarks):
            found.append(item)
    return found


# ---------------------------------------------------------------------------
# reading a region's live facts - the adapter to the pure rules
# ---------------------------------------------------------------------------

def region_definition_facts(context, item, canonical=None):
    """Live facts for one region's definition and cached boundary.

    The adapter between Blender state and the pure rules in `regions.py`.
    Every value is read from a property; no geometry is evaluated, no
    canonical mesh is built and the solver is never reached, so this is safe
    from a panel draw.
    """
    landmark_collection = get_landmarks(context)
    landmark_facts = []
    for entry in item.landmarks:
        stable_id = int(entry.landmark_stable_id)
        landmark = landmark_by_stable_id(landmark_collection, stable_id)
        fact = {
            "stable_id": stable_id,
            "exists": landmark is not None,
            "label": (entry.landmark_name or entry.landmark_protocol_id
                      or "landmark %d" % stable_id),
        }
        if landmark is not None:
            point = landmark.surface_point
            fact.update({
                "label": landmark.label,
                "protocol_id": landmark.protocol_id,
                "picked": bool(point.valid),
                "status": landmark.status,
                "component_id": int(point.component_id) if point.valid else 0,
                "triangle": int(point.triangle_index) if point.valid else -1,
                "source_object": point.source_object,
                "geometry_hash": point.geometry_hash,
            })
        landmark_facts.append(fact)

    segment_facts = []
    for position, segment in enumerate(item.segments):
        segment_facts.append({
            "position": position,
            "from_landmark": int(segment.from_landmark),
            "to_landmark": int(segment.to_landmark),
            # "computed" means BOTH the record and the polyline are present.
            # A record whose cache datablock has gone - a purged file, a
            # hand-edited scene - is not a computed segment, and saying it is
            # would let a boundary be drawn from nothing.
            "computed": bool(segment.computed)
                        and pathcache.region_segment_exists(item.stable_id,
                                                            position),
            "point_count": int(segment.point_count),
            "length_mm": float(segment.length_mm),
            "object_name": segment.object_name,
            "geometry_hash": segment.geometry_hash,
            "from_triangle": int(segment.from_triangle),
            "to_triangle": int(segment.to_triangle),
            "component_id": int(segment.component_id),
        })

    return {
        "landmarks": landmark_facts,
        "segments": segment_facts,
        "cached_definition": item.cached_definition,
        "live_geometry_hash": (canonical.geometry_hash
                               if canonical is not None else ""),
        # A region loaded from a file written by the measurement-path model
        # has no landmarks and a non-empty segment collection. Detected so it
        # can be REFUSED BY NAME rather than read as an empty definition.
        "legacy_segment_count": (len(item.segments)
                                 if not len(item.landmarks) else 0),
    }


def region_polylines(item):
    """Cached polylines for a region's boundary, in loop order, or None each.

    THE one place a region's cached geometry is loaded. Read straight out of
    `pathcache` in the region's own name space - the same local-space arrays
    the boundary helper is drawn from - so the self-intersection test and the
    drawn boundary can never disagree about where the boundary runs.

    Nothing is solved, and nothing is projected.
    """
    loaded = []
    for position in range(len(item.segments)):
        cached = None
        try:
            cached = pathcache.load_region_segment(item.stable_id, position)
        except Exception:                             # pragma: no cover
            cached = None
        loaded.append(None if cached is None else cached[0])
    return loaded


#: How close two boundary points must be to count as the SAME place, as a
#: fraction of the mesh's own bounding-box diagonal. Deliberately tiny: this
#: is looking for polylines that genuinely share a point, not for ones that
#: merely pass near each other, and a loose value here would refuse valid
#: boundaries that run close together round a limb.
TOUCH_TOLERANCE_FRACTION = 1e-6


def region_touch_tolerance(canonical):
    """The shared-point tolerance in OBJECT-LOCAL units, or 0 when unknown."""
    if canonical is None:
        return 0.0
    report = getattr(canonical, "topology", None) or {}
    diagonal = float(report.get("bbox_diagonal", 0.0) or 0.0)
    if diagonal <= 0.0:
        return 0.0
    multiplier = float(getattr(canonical, "unit_multiplier", 1.0) or 1.0)
    return diagonal * TOUCH_TOLERANCE_FRACTION / max(multiplier, 1e-12)


def region_object_name(context, item):
    """The scan a region belongs to: its cache's, else its landmarks'."""
    for segment in item.segments:
        if segment.object_name:
            return segment.object_name
    landmark_collection = get_landmarks(context)
    for entry in item.landmarks:
        landmark = landmark_by_stable_id(landmark_collection,
                                         int(entry.landmark_stable_id))
        if landmark is not None and landmark.surface_point.valid:
            return landmark.surface_point.source_object
    return ""


def validate_region(context, item, canonical=None, check_touching=False):
    """The live verdict for one region. Pure inspection; no solver, no writes.

    `check_touching` adds the shared-point half of the self-intersection test,
    which reads every cached polyline. That is cheap next to a solve but not
    free, so it belongs to an explicit Validate press rather than to a redraw.
    """
    facts = region_definition_facts(context, item, canonical)
    polylines = None
    tolerance = 0.0
    if check_touching:
        tolerance = region_touch_tolerance(canonical)
        if tolerance > 0.0:
            polylines = region_polylines(item)
    return regions.validate(facts, polylines=polylines,
                            touch_tolerance=tolerance), facts


def region_fingerprint(item, result):
    """What an explicit Validate was run AGAINST, as one comparable string.

    The ordered landmark definition plus the verdict the same pass derived.
    Any edit to the boundary, and any change in what the landmarks or the
    cache are worth, moves this value - which is what lets `validated` be
    cleared automatically instead of being remembered until somebody notices
    it is wrong.
    """
    return "|".join((regions.definition_key(item.landmark_ids),
                     str(item.cached_definition or ""),
                     str(result.get("status", "")),
                     str(result.get("code", "")),
                     str(result.get("geometry_hash", "")),
                     "%.6f" % float(result.get("length_mm", 0.0) or 0.0)))


def store_region_status(item, result):
    """WRITE a derived verdict onto the region. Never call this from a draw.

    Split out from `refresh_region_status` deliberately. Blender forbids
    writing to ID-backed data while the UI is drawing - a panel that assigns
    to a PropertyGroup field raises

        AttributeError: Writing to ID classes in this context is not allowed

    and the whole panel disappears behind an error. So the DERIVATION
    (`validate_region`, pure) and the RECORD (this, a write) are two
    functions, and only operators and invalidation call this one.
    """
    item.status = result["status"]
    item.status_code = result["code"]
    item.status_detail = result["detail"]
    item.boundary_closed = bool(result["closed"])
    item.boundary_length_mm = float(result["length_mm"])
    item.boundary_point_count = int(result["point_count"])
    item.boundary_object = result["object_name"]
    item.boundary_geometry_hash = result["geometry_hash"]
    item.boundary_component_id = int(result["component_id"])
    # A recorded "validated" that no longer describes this region is worse
    # than none: it is the stale authoritative-looking claim this milestone
    # exists to refuse. Derive it, do not remember it.
    if item.validated and (region_fingerprint(item, result)
                           != item.validated_fingerprint):
        item.validated = False
        item.report = ""
    return result


def refresh_region_status(context, item, canonical=None, check_touching=False):
    """Re-derive AND store one region's status. Returns the result dict.

    The mutating entry point: every region operator and every invalidation
    path calls this, so a stored verdict can never outlive the definition
    that produced it. **A panel draw must not**, because storing is a write -
    see `store_region_status`. Draw code wants `validate_region`, which
    derives exactly the same answer and keeps none of it.
    """
    result, _facts = validate_region(context, item, canonical, check_touching)
    store_region_status(item, result)
    # The interior hangs off the boundary, so it is restated in the same
    # pass. Nothing is recomputed and no classified geometry is discarded: a
    # boundary that moved makes its interior STALE, which is a thing the
    # researcher can see and fix.
    refresh_interior_status(item, result, canonical)
    return result


def refresh_all_region_statuses(context, canonical=None):
    """Restate every region. Returns how many changed status."""
    collection = get_regions(context)
    if not collection:
        return 0
    changed = 0
    for item in collection:
        before = item.status
        refresh_region_status(context, item, canonical)
        if item.status != before:
            changed += 1
    return changed


def clear_region_boundary(item):
    """Throw away one region's computed boundary. The DEFINITION is kept.

    Used when a compute is abandoned and when a region's cache is known to
    describe something the region no longer is. The landmarks - the thing the
    researcher actually typed - are never touched by this.
    """
    try:
        pathcache.drop_region(item.stable_id)
    except Exception:                                 # pragma: no cover
        pass
    item.segments.clear()
    item.cached_definition = ""
    item.boundary_closed = False
    item.boundary_length_mm = 0.0
    item.boundary_point_count = 0
    item.boundary_object = ""
    item.boundary_geometry_hash = ""
    item.boundary_component_id = 0
    item.validated = False
    item.validated_fingerprint = ""
    item.report = ""


# ---------------------------------------------------------------------------
# Surface Interior: which side of the boundary is the region
# ---------------------------------------------------------------------------
#
# The interior is derived from the boundary exactly as the boundary is derived
# from the landmarks: an explicit press computes it, anything upstream
# changing makes it STALE, and nothing is recomputed unasked. Unlike the
# boundary it costs NO SOLVER - it is topology and geometry analysis on a mesh
# BSMT already has - which is why switching side is allowed to recompute it
# outright rather than needing a second cached copy.


def interior_fingerprint(item):
    """What an interior would have to match to still be current."""
    return "%s|%s" % (item.cached_definition or "", item.interior_side)


def interior_is_current(item, canonical=None):
    """(status, code, detail) for the stored interior, derived not trusted."""
    if not item.interior_definition:
        return (interior.STATUS_NONE, interior.CODE_NOT_COMPUTED,
                "The interior has not been computed yet.")
    if item.interior_definition != interior_fingerprint(item):
        return (interior.STATUS_STALE, interior.CODE_BOUNDARY_CHANGED,
                "The boundary or the chosen side changed after the interior "
                "was computed. Press Compute Interior again.")
    if canonical is not None and item.interior_geometry_hash and \
            canonical.geometry_hash != item.interior_geometry_hash:
        return (interior.STATUS_STALE, interior.CODE_GEOMETRY_MISMATCH,
                "The mesh geometry changed after the interior was computed. "
                "Press Compute Interior again.")
    return (interior.STATUS_VALID, interior.CODE_NONE, item.interior_detail)


def refresh_interior_status(item, boundary_result, canonical=None):
    """Re-derive and STORE the interior's status. Never call from a draw.

    An interior can never outlive the boundary under it, so a boundary that
    is not currently VALID drags the interior to stale with it - without
    touching the classified geometry, which stays exactly as computed until
    something explicitly replaces it.
    """
    if not item.interior_definition:
        status, code = interior.STATUS_NONE, interior.CODE_NOT_COMPUTED
        detail = "The interior has not been computed yet."
    elif boundary_result is not None and \
            boundary_result["status"] not in regions.TRUSTED:
        status, code = interior.STATUS_STALE, interior.CODE_BOUNDARY_NOT_VALID
        detail = ("The region boundary is %s, so its interior is no longer "
                  "the answer." % regions.STATUS_SHORT.get(
                      boundary_result["status"], boundary_result["status"]))
    else:
        status, code, detail = interior_is_current(item, canonical)
    item.interior_status = status
    item.interior_code = code
    item.interior_detail = detail
    result = {"status": status, "code": code, "detail": detail}
    # The area hangs off the interior, so it is restated in the same pass.
    refresh_area_status(item, result)
    return result


def panel_fingerprint(item):
    """What a drawn thickness preview was built from.

    The interior it belongs to AND the thickness it was given, so a thickness
    change rebuilds the preview geometry while leaving the interior - and the
    boundary under it - completely alone.
    """
    return "%s|%.6f" % (item.interior_definition or "",
                        float(item.panel_thickness_mm))


def panel_is_current(item):
    return bool(item.panel_built) and item.panel_built == panel_fingerprint(item)


def clear_region_area(item):
    """Throw away one region's area result. The interior is kept."""
    item.area_mm2 = 0.0
    item.area_full_mm2 = 0.0
    item.area_partial_mm2 = 0.0
    item.area_component_mm2 = 0.0
    item.area_status = surfacearea.STATUS_NONE
    item.area_code = surfacearea.CODE_NOT_COMPUTED
    item.area_detail = ""
    item.area_method = ""
    item.area_interior_key = ""
    item.area_geometry_hash = ""
    item.area_side = ""
    item.area_elapsed_s = 0.0


def area_fingerprint(item):
    """What a stored area was computed against."""
    return "%s|%s" % (item.interior_definition or "",
                      item.interior_geometry_hash or "")


def refresh_area_status(item, interior_result=None):
    """Re-derive and STORE the area's status. Never call from a draw.

    An area can never outlive the interior under it. Nothing is recomputed
    and the stored number is not cleared - a stale area is shown AS stale,
    which is more useful than a blank, and is never shown as a result.
    """
    if not item.area_interior_key:
        status, code = surfacearea.STATUS_NONE, surfacearea.CODE_NOT_COMPUTED
        detail = "The area has not been computed yet."
    elif interior_result is not None and \
            interior_result["status"] not in interior.TRUSTED:
        status, code = surfacearea.STATUS_STALE, surfacearea.CODE_INTERIOR_NOT_VALID
        detail = ("The interior is %s, so its area is no longer the answer."
                  % interior.STATUS_SHORT.get(interior_result["status"],
                                              interior_result["status"]))
    elif item.area_interior_key != area_fingerprint(item):
        status, code = surfacearea.STATUS_STALE, surfacearea.CODE_INTERIOR_CHANGED
        detail = ("The interior changed after the area was computed. Press "
                  "Compute Area again.")
    else:
        status, code, detail = (surfacearea.STATUS_VALID, surfacearea.CODE_NONE,
                                item.area_detail)
    item.area_status = status
    item.area_code = code
    item.area_detail = detail
    return {"status": status, "code": code, "detail": detail}


def clear_region_panel(item):
    """Remove one region's thickness preview. The interior is kept."""
    try:
        visualization.remove_region_panel(item.stable_id)
    except Exception:                                 # pragma: no cover
        pass
    item.panel_built = ""
    item.panel_detail = ""
    item.panel_folded_faces = 0
    item.show_panel = False


def clear_region_interior(item):
    """Throw away one region's computed interior. The BOUNDARY is kept."""
    try:
        visualization.remove_region_fill(item.stable_id)
    except Exception:                                 # pragma: no cover
        pass
    item.interior_definition = ""
    item.interior_geometry_hash = ""
    item.interior_status = interior.STATUS_NONE
    item.interior_code = interior.CODE_NOT_COMPUTED
    item.interior_detail = "The interior has not been computed yet."
    item.interior_full_count = 0
    item.interior_partial_count = 0
    item.interior_component_id = 0
    item.show_fill = False
    # The preview hangs off the interior, so it goes with it. A preview of an
    # interior that no longer exists is the clearest possible wrong picture.
    clear_region_panel(item)
    clear_region_area(item)
    try:
        interiorcache.drop(item.stable_id)
    except Exception:                                 # pragma: no cover
        pass


def interior_fill_geometry(vertices, triangles, side):
    """(vertices, faces) for the fill helper, from one side of an analysis.

    One face per full interior triangle and one per exactly-clipped partial
    piece - the classification itself, drawn. Vertices are duplicated per
    face on purpose: this is analysis geometry read once per rebuild, and
    welding it would merge pieces that a later area sum must keep apart.
    """
    points = []
    faces = []
    for triangle in side["full_triangles"]:
        base = len(points)
        points.extend(vertices[index] for index in triangles[triangle])
        faces.append(tuple(range(base, base + 3)))
    for entry in side["partial"]:
        polygon = entry["polygon"]
        if polygon.shape[0] < 3:
            continue
        base = len(points)
        points.extend(polygon)
        faces.append(tuple(range(base, base + polygon.shape[0])))
    return points, faces


def invalidate_regions_for_landmark(context, landmark_stable_id, reason):
    """Restate the regions that use this landmark. Targeted, not a sweep.

    Called when a landmark is re-picked or deleted. Nothing is recomputed and
    no cache is thrown away: a boundary whose landmark moved is STALE, which
    is a thing the researcher can see and fix, and silently re-solving it
    would cost minutes without being asked.
    """
    collection = get_regions(context)
    affected = regions_referencing_landmark(collection, landmark_stable_id)
    for item in affected:
        refresh_region_status(context, item)
    return len(affected)


def invalidate_all_measurement_results(context, reason):
    """Drop every stored measurement result. Definitions are untouched."""
    collection = get_measurements(context)
    if not collection:
        return 0
    count = 0
    for item in collection:
        if item.has_result:
            invalidate_measurement_result(item, reason)
            count += 1
    return count


# ---------------------------------------------------------------------------
# Export collectors (Milestone 3.11)
# ---------------------------------------------------------------------------
#
# These read Blender data into PLAIN DICTS. Every decision about what a row
# contains then happens in export.py, which imports no bpy and can be tested
# directly - including the decisions that matter most, like whether a number
# is written at all.


def session_metadata(props):
    """The researcher's session fields. Metadata only, never read elsewhere.

    The two protocol names are included because an exported measurement is
    only reproducible if the reader knows WHICH protocol produced it - a
    landmark called "Acromion" means one thing under one protocol and
    something slightly different under another. They are recorded when a
    protocol or template is loaded, so nothing here is invented.
    """
    return {
        "subject_id": props.session_subject_id.strip(),
        "condition": props.session_condition.strip(),
        "scan_id": props.session_scan_id.strip(),
        "notes": props.session_notes.strip(),
        "landmark_protocol": props.protocol_name.strip(),
        "measurement_protocol": props.measurement_protocol_name.strip(),
    }


def mesh_provenance(obj):
    """What is reproducible about the mesh a measurement was taken on.

    Reads only fields BSMT itself recorded when the measurement mesh was
    created. Anything absent stays absent rather than being guessed at.
    """
    record = {
        "measurement_mesh": "",
        "source_mesh": "",
        "representation": "",
        "source_triangles": 0,
        "measurement_triangles": 0,
        "preprocessing_method": "",
    }
    if obj is None:
        return record
    record["measurement_mesh"] = obj.name
    provenance = getattr(obj, "bsmt_scan", None)
    if provenance is None or not provenance.is_measurement_copy:
        # Measuring directly on an imported scan is legitimate; there is
        # simply no preprocessing provenance to report.
        return record
    source = provenance.source
    record["source_mesh"] = (source.name if source is not None
                             else provenance.source_name)
    record["representation"] = provenance.representation
    record["source_triangles"] = int(provenance.original_triangles or 0)
    record["measurement_triangles"] = int(provenance.actual_triangles or 0)
    record["preprocessing_method"] = provenance.method
    return record


def measurement_export_record(context, item):
    """One measurement as plain values, ready for export.measurement_row.

    `straight_valid` and `surface_valid` are passed through untouched. They
    are what decides whether a number is written at all, and BSMT already
    clears them the moment a dependency changes - so a STALE measurement
    arrives here with no number to write, which is the point.
    """
    source, target = resolve_measurement_landmarks(context, item)
    return {
        "stable_id": int(item.stable_id),
        "protocol_id": item.protocol_id,
        "name": item.name,
        "notes": item.notes,
        # The stable id is read from the DEFINITION, not from the resolved
        # landmark: it is what the definition points at, and it stays
        # meaningful even when the landmark it names has been deleted.
        "from_landmark_stable_id": int(item.source_stable_id),
        "to_landmark_stable_id": int(item.target_stable_id),
        "from_landmark_id": (source.protocol_id if source is not None
                             else item.source_protocol_id),
        "from_landmark_name": (source.label if source is not None
                               else item.source_name),
        "to_landmark_id": (target.protocol_id if target is not None
                           else item.target_protocol_id),
        "to_landmark_name": (target.label if target is not None
                             else item.target_name),
        "measurement_type": item.measurement_type,
        "enabled": bool(item.enabled),
        "straight_valid": bool(item.straight_valid),
        "straight_mm": float(item.straight_mm),
        "surface_valid": bool(item.surface_valid),
        "surface_mm": float(item.surface_mm),
        "ratio": float(item.ratio),
        "status": item.status,
        "result_geometry_hash": item.result_geometry_hash,
        "result_object": item.result_object,
        "backend_name": item.backend_name,
        "backend_version": item.backend_version,
    }


def landmark_export_record(item):
    """One landmark as plain values, ready for export.landmark_row."""
    point = item.surface_point
    return {
        "stable_id": int(item.stable_id),
        "protocol_id": item.protocol_id,
        "name": item.label,
        "status": item.status,
        "notes": item.notes,
        "valid": bool(point.valid),
        "triangle_index": int(point.triangle_index),
        "barycentric": tuple(float(v) for v in point.barycentric),
        "component_id": int(point.component_id),
        "world_xyz": tuple(float(v) for v in point.world_xyz),
        "physical_mm_xyz": tuple(float(v) for v in point.physical_mm_xyz),
        "source_object": point.source_object,
        "geometry_hash": point.geometry_hash,
    }


def export_object(context, props=None):
    """The mesh an export should report as the measurement mesh.

    The same rule the readiness line uses: the landmarks decide. A result
    belongs to the mesh it was computed on, not to whatever is selected when
    the researcher presses Export.
    """
    obj, _reason = measurement_target(context, props)
    return obj


def protocol_entries(context, props=None):
    """(landmark entries, measurement entries) for a unified protocol file.

    Definitions only. Nothing here reads a surface point, a result or a
    scan name - the shapes are exactly what protocol.build_protocol takes,
    and protocol.py refuses anything else.
    """
    landmark_collection = get_landmarks(context) or ()
    measurement_collection = get_measurements(context) or ()
    landmark_entries = [
        (int(item.stable_id), item.protocol_id, item.label, item.notes)
        for item in landmark_collection
    ]
    measurement_entries = [
        (int(item.stable_id), item.protocol_id, item.label,
         int(item.source_stable_id), int(item.target_stable_id),
         item.measurement_type, bool(item.enabled), item.notes)
        # A draft has no complete pair of landmarks, so there is no definition
        # to save. Writing one would produce a protocol that cannot be loaded.
        for item in measurements.defined(measurement_collection)
    ]
    return landmark_entries, measurement_entries


def apply_protocol(context, props, name, landmark_entries,
                   measurement_entries):
    """Replace the scene's definitions with a protocol's. Returns a report.

    REPLACE, not merge (sect. 8). Merging two protocols means deciding what a
    collision is - same name, same id, same stable id? - and every answer
    silently produces duplicates or silently discards a definition. Replacing
    is one rule the researcher can predict, and the file they loaded from is
    still on disk if they wanted the other one.

    Stable ids are RESTORED from the file, which is what makes a measurement's
    reference survive the trip (sect. 7). The next-id counters are advanced
    past everything loaded, so a landmark added afterwards cannot collide with
    one the protocol brought in.

    Nothing arrives positioned or calculated. A protocol says what to measure;
    this scan has not been measured yet.
    """
    landmark_collection = get_landmarks(context)
    measurement_collection = get_measurements(context)
    if landmark_collection is None or measurement_collection is None:
        raise landmarks.LandmarkError("BSMT collections are not registered")

    # Cached paths live in helper objects, so they are removed explicitly
    # rather than left orphaned when the definitions go.
    for item in measurement_collection:
        clear_measurement_path(item)
    clear_measurements(context, props)
    clear_landmarks(context, props)
    visualization.clear_landmark_markers()

    highest_landmark = 0
    for stable_id, protocol_id, landmark_name, notes in landmark_entries:
        item = landmark_collection.add()
        item.stable_id = int(stable_id)
        item.protocol_id = protocol_id
        item.name = landmark_name
        item.notes = notes
        item.status = landmarks.STATUS_NOT_PICKED
        item.status_detail = "loaded from a protocol; not picked yet"
        clear_surface_point(item.surface_point)
        highest_landmark = max(highest_landmark, int(stable_id))
    props.landmark_next_id = highest_landmark + 1
    props.landmark_index = 0
    props.protocol_name = name

    known = {int(item.stable_id) for item in landmark_collection}
    unresolved = []
    highest_measurement = 0
    for entry in measurement_entries:
        (stable_id, protocol_id, measurement_name, from_stable, to_stable,
         measurement_type, enabled, notes) = entry
        item = measurement_collection.add()
        item.stable_id = int(stable_id)
        item.protocol_id = protocol_id
        item.name = measurement_name
        item.auto_name = False
        item.notes = notes
        item.measurement_type = measurement_type
        item.enabled = bool(enabled)
        item.source_stable_id = int(from_stable)
        item.target_stable_id = int(to_stable)
        highest_measurement = max(highest_measurement, int(stable_id))

        # The cached names travel with the reference so an unresolved one can
        # SAY which landmark is missing. They are never used to find a
        # substitute (sect. 7).
        for slot, reference in (("source", from_stable), ("target", to_stable)):
            landmark = landmark_by_stable_id(landmark_collection, reference)
            if landmark is None:
                unresolved.append((protocol_id, slot, int(reference)))
                continue
            setattr(item, slot + "_protocol_id", landmark.protocol_id)
            setattr(item, slot + "_name", landmark.name)
        clear_measurement_result(item)
        refresh_measurement_status(context, item)
    props.measurement_next_id = highest_measurement + 1
    props.measurement_index = 0
    props.measurement_protocol_name = name
    props.measurement_summary = ""

    lines = ["loaded '%s': %d landmark(s), %d measurement(s)"
             % (name, len(landmark_entries), len(measurement_entries)),
             "every landmark is NOT PICKED; no result or path was loaded"]
    for protocol_id, slot, reference in unresolved:
        lines.append("UNRESOLVED: measurement %s references %s landmark "
                     "stable id %d, which the protocol does not define"
                     % (protocol_id, slot, reference))
    return {
        "landmarks": len(landmark_entries),
        "measurements": len(measurement_entries),
        "unresolved": unresolved,
        "lines": lines,
    }


# ---------------------------------------------------------------------------
# Global readiness (Milestone 3.7, sect. 13)
# ---------------------------------------------------------------------------

def measurement_target(context, props=None):
    """The object measurements would actually run on, and why.

    Returns (object-or-None, reason). The landmarks decide: a measurement is
    computed on the mesh its landmarks were picked on, never on whatever
    happens to be selected. Only when no landmark has been picked does the
    active object stand in - and then it is labelled as a guess.
    """
    names = set()
    collection = get_landmarks(context)
    for item in (collection or ()):
        point = item.surface_point
        if point.valid and point.source_object:
            names.add(point.source_object)
    if len(names) == 1:
        name = names.pop()
        obj = bpy.data.objects.get(name)
        if obj is not None:
            return obj, "the mesh the landmarks were picked on"
    if len(names) > 1:
        return None, "landmarks are spread across %d meshes" % len(names)

    obj = getattr(context, "active_object", None)
    if obj is not None and obj.type == 'MESH' and not visualization.is_helper(obj):
        return obj, "the active object"
    return None, "no mesh selected"


#: What `mesh_verdict` reports when nothing has been analysed yet. Distinct
#: from every classify_ready state on purpose: "not analysed" is not an
#: answer about the mesh, and rendering it as one is how a stale or absent
#: diagnostic turns into a confident status line.
MESH_NOT_ANALYSED = 'NOT_ANALYSED'


def mesh_verdict(context, props=None, obj=None):
    """THE readiness answer for the measurement target's mesh.

    One source, used by every surface that shows a mesh status: the Scan
    Setup topology line, the readiness headline, and the preprocessing
    report. It does not decide anything itself - it locates the current
    topology report and hands it to `preprocess.classify_ready`, which is
    where the policy lives and the only place it lives.

    This exists because two panels used to carry their OWN rule - "Ready if
    non-manifold == 0" - which ignored degenerate triangles and therefore
    reported Ready on a mesh that classify_ready calls NOT READY.

    Panel-draw safe: `peek_current` never builds a canonical mesh, and a
    report that no longer describes the live object is treated as absent
    rather than shown.

    Returns a dict with `analysed`, `state`, `reasons`, `report` and `object`.
    """
    if props is None:
        props = get_props(context)
    if obj is None and props is not None:
        obj, _reason = measurement_target(context, props)

    blank = {
        "analysed": False,
        "state": MESH_NOT_ANALYSED,
        "reasons": [],
        "report": {},
        "object": obj.name if obj is not None else "",
    }
    if obj is None or not geodesic.MESHCACHE_AVAILABLE:
        return blank

    canonical = geodesic.meshcache.peek_current(obj)
    if canonical is None:
        return blank

    report = dict(canonical.topology or {})
    verdict, reasons = preprocess.classify_ready(
        report,
        canonical_built=True,
        # Appearance is a preprocessing concern and is judged there, against
        # the facts recorded at copy time. Nothing about it can be
        # rediscovered from a topology report, so it is not guessed at here.
        appearance_ok=True,
        dense_threshold=(props.dense_threshold_triangles
                         if props is not None else
                         preprocess.DEFAULT_DENSE_THRESHOLD),
    )
    return {
        "analysed": True,
        "state": verdict,
        "reasons": list(reasons),
        "report": report,
        "object": obj.name,
    }


def workflow_facts(context, props=None):
    """The handful of numbers the per-stage guidance needs (sect. 10).

    Safe from a panel draw by construction: it reads stored properties and
    peeks at the canonical mesh cache, so it never builds one and never
    touches the solver. A scan that has not been analysed reports
    ``analysed=False`` rather than an invented topology.
    """
    if props is None:
        props = get_props(context)
    if props is None:
        return {
            "has_mesh": False, "analysed": False, "copy_status": "",
            "landmark_total": 0, "landmarks_picked": 0,
            "measurements_defined": 0, "results_available": 0,
            "non_manifold": 0,
        }

    obj, _reason = measurement_target(context, props)
    # Same accessor as everything else, so "analysed" means the same thing
    # here as it does in the Scan Setup topology line.
    verdict = mesh_verdict(context, props, obj)
    analysed = verdict["analysed"]
    non_manifold = int(
        verdict["report"].get("nonmanifold_edge_count", 0) or 0
    )

    landmark_collection = get_landmarks(context) or ()
    picked = sum(1 for item in landmark_collection
                 if item.surface_point.valid)

    measurement_collection = get_measurements(context) or ()
    defined = defined_measurements(measurement_collection)
    results = sum(1 for item in defined if result_is_displayable(item))

    return {
        "has_mesh": obj is not None,
        "analysed": analysed,
        # The verdict from the LAST preprocessing run, not a fresh one: a
        # panel draw must never re-classify anything.
        "copy_status": props.preprocess_status if props.preprocess_valid else "",
        "landmark_total": len(landmark_collection),
        "landmarks_picked": picked,
        "measurements_defined": len(defined),
        "results_available": results,
        "non_manifold": non_manifold,
    }


def stage_hint(context, stage, props=None):
    """The one-line hint for a workflow stage, or "". Panel-draw safe."""
    return readiness.stage_hint(stage, **workflow_facts(context, props))


def readiness_snapshot(context, props=None):
    """The compact "can I measure yet" answer (sect. 13).

    Safe to call from a panel draw: it reads only cached values. The canonical
    mesh is consulted with peek(), which never builds one, so opening a panel
    can never trigger a rebuild - and when nothing is cached the result says
    "not analyzed yet" rather than inventing an answer.
    """
    if props is None:
        props = get_props(context)
    if props is None:
        return readiness.evaluate()

    obj, _reason = measurement_target(context, props)
    # The mesh half of the answer comes from the ONE authoritative verdict,
    # never from a rule re-derived here.
    verdict = mesh_verdict(context, props, obj)
    analysed = verdict["analysed"]
    triangle_count = int(verdict["report"].get("triangle_count", 0) or 0)
    mesh_reasons = (verdict["reasons"]
                    if verdict["state"] == preprocess.MEASUREMENT_NOT_READY
                    else [])

    scale_uniform = True
    if obj is not None:
        scale_uniform = bool(alignment.scale_report(obj.matrix_world)["uniform"])

    unpicked = stale = 0
    landmark_collection = get_landmarks(context) or ()
    for item in landmark_collection:
        if item.status == landmarks.STATUS_NOT_PICKED:
            unpicked += 1
        elif item.status in (landmarks.STATUS_STALE, landmarks.STATUS_INVALID):
            stale += 1

    measurement_collection = get_measurements(context) or ()
    return readiness.evaluate(
        mesh_name=obj.name if obj is not None else "",
        triangle_count=triangle_count,
        mesh_reasons=mesh_reasons,
        analysed=analysed,
        dense_threshold=props.dense_threshold_triangles,
        guard_dense=props.guard_dense_solve,
        scale_uniform=scale_uniform,
        landmark_total=len(landmark_collection),
        landmarks_unpicked=unpicked,
        landmarks_stale=stale,
        measurements_defined=len(defined_measurements(measurement_collection)),
    )


# ---------------------------------------------------------------------------
# Alignment (Milestone 3.6)
# ---------------------------------------------------------------------------

ALIGN_SLOTS = ('LEFT', 'RIGHT', 'SUPERIOR', 'INFERIOR')


def align_point(props, slot):
    """The alignment reference SurfacePoint for one slot.

    These are alignment references, deliberately separate from the research
    landmarks of the Landmark Manager - but they reuse BSMT_SurfacePoint, so
    there is still exactly one surface-location representation.

    Returns None if the property is absent. That is not a hypothetical: the
    depsgraph handler that follows transforms can fire against a scene whose
    property group has not finished (re)registering, and a missing alignment
    reference must never take the A/B refresh down with it.
    """
    return getattr(props, {
        'LEFT': "align_left",
        'RIGHT': "align_right",
        'SUPERIOR': "align_superior",
        'INFERIOR': "align_inferior",
    }[slot], None)


def _align_valid(point):
    return point is not None and point.valid


def align_points_ready(props):
    """(ready, missing) for the four references."""
    missing = [slot for slot in ALIGN_SLOTS
               if not _align_valid(align_point(props, slot))]
    return (not missing), missing


def align_objects(props):
    """The set of objects the four references were picked on."""
    names = set()
    for slot in ALIGN_SLOTS:
        point = align_point(props, slot)
        if _align_valid(point) and point.source_object:
            names.add(point.source_object)
    return names


def _copy_surface_point(source):
    """A plain snapshot of a SurfacePoint's fields, for swapping slots."""
    return {
        "valid": bool(source.valid),
        "source_object": source.source_object,
        "geometry_hash": source.geometry_hash,
        "triangle_index": int(source.triangle_index),
        "barycentric": tuple(float(v) for v in source.barycentric),
        "component_id": int(source.component_id),
        "kind": source.kind,
        "local_xyz": tuple(float(v) for v in source.local_xyz),
        "world_xyz": tuple(float(v) for v in source.world_xyz),
        "physical_mm_xyz": tuple(float(v) for v in source.physical_mm_xyz),
        "reconstruction_error": float(source.reconstruction_error),
    }


def _restore_surface_point(point, data):
    if not data["valid"]:
        clear_surface_point(point)
        return point
    return fill_surface_point(
        point, data["source_object"], data["geometry_hash"],
        data["triangle_index"], data["barycentric"], data["component_id"],
        data["kind"], data["local_xyz"], data["world_xyz"],
        data["physical_mm_xyz"], data["reconstruction_error"])


def swap_align_points(props, first, second):
    """Exchange two alignment reference slots.

    Flip Front/Back exists because the subject's left and right are easy to
    label the wrong way round. Turning the body without also exchanging the
    two LABELS would leave the stored LEFT reference sitting on the subject's
    right, so every later check - including the applied-frame validation -
    would measure the correction as a 180 degree error.
    """
    a = align_point(props, first)
    b = align_point(props, second)
    a_data, b_data = _copy_surface_point(a), _copy_surface_point(b)
    _restore_surface_point(a, b_data)
    _restore_surface_point(b, a_data)
    return True


def clear_align_points(props):
    for slot in ALIGN_SLOTS:
        point = align_point(props, slot)
        if point is not None:
            clear_surface_point(point)


def clear_alignment_state(props, keep_points=True):
    """Forget the alignment record. No object or mesh is touched."""
    if not keep_points:
        clear_align_points(props)
    props.align_applied = False
    props.align_moved = False
    props.align_object = ""
    props.align_method = ""
    props.align_created = ""
    props.align_report = ""
    props.align_residual_degrees = 0.0
    props.align_validation = ""
    props.align_origin_requested = False
    props.align_validation_report = ""
    props.align_refusal_report = ""
    props.align_lr_dot_x = 0.0
    props.align_si_dot_z = 0.0
    props.align_axis_error_degrees = 0.0
    props.align_orthogonality_error = 0.0
    props.align_preview = False


def matrix_to_flat(matrix):
    return tuple(float(matrix[row][col]) for row in range(4) for col in range(4))


def flat_to_rows(flat):
    values = [float(v) for v in flat]
    return [values[0:4], values[4:8], values[8:12], values[12:16]]


def degenerate_defect_rows(props):
    """The stored degenerate defects, as a plain list. Display only."""
    return list(props.repair_degenerates)


def active_degenerate_defect(props):
    """The selected degenerate defect, or None."""
    index = props.repair_degenerate_index
    if 0 <= index < len(props.repair_degenerates):
        return props.repair_degenerates[index]
    return None


def active_nonmanifold_defect(props):
    """The FOCUSED non-manifold defect, or None.

    "Focused" is the defect the researcher stepped to and inspected with Show
    Edges / Focus. It is the only defect any component-scoped deletion is ever
    allowed to act on, so there is exactly one place that decides which it is.
    """
    if props is None:
        return None
    index = props.repair_nonmanifold_index
    if 0 <= index < len(props.repair_nonmanifold_defects):
        return props.repair_nonmanifold_defects[index]
    return None


def clear_focused_defect(props):
    """Forget the focused non-manifold defect and its artifact preview.

    Called after a geometry edit: the stored region id, component number and
    centre all describe a mesh that no longer exists, and a dangling reference
    to deleted geometry is exactly what must not survive an edit.
    """
    if props is None:
        return
    props.repair_nonmanifold_defects.clear()
    props.repair_nonmanifold_index = 0
    props.repair_artifact_preview = ""
    clear_local_repair(props)


def clear_local_repair(props):
    """Forget the local-repair inspection. No object or mesh is touched.

    Called whenever the focused defect changes or the geometry does: an
    inspection names a candidate by the topology of ONE mesh state, and
    leaving it on screen beside a different defect - or a different mesh - is
    exactly the dangling reference sect. 11 of the brief forbids.
    """
    if props is None:
        return
    props.repair_local_report = ""
    props.repair_local_classification = ""
    props.repair_local_removable = False
    props.repair_local_hash = ""
    props.repair_local_region_id = 0
    props.repair_local_face_count = 0
    props.repair_local_vertex_count = 0
    props.repair_local_area_mm2 = 0.0


def clear_repair_lists(props):
    props.boundary_loops.clear()
    props.repair_components.clear()
    props.repair_degenerates.clear()
    props.repair_degenerate_index = 0
    props.repair_degenerate_preview = ""
    props.boundary_loop_index = 0
    props.repair_component_index = 0
    clear_focused_defect(props)


def clear_repair_state(props):
    """Forget the repair analysis. No object or mesh is touched."""
    clear_repair_lists(props)
    props.repair_report = ""
    props.repair_readiness = ""
    props.repair_valid = False
    props.repair_object = ""
    props.repair_geometry_hash = ""


def active_boundary_loop(props):
    index = props.boundary_loop_index
    if 0 <= index < len(props.boundary_loops):
        return props.boundary_loops[index]
    return None


def active_repair_component(props):
    index = props.repair_component_index
    if 0 <= index < len(props.repair_components):
        return props.repair_components[index]
    return None


def metric_tensor(matrix_world, multiplier):
    """(L^T L) * multiplier^2 as 9 floats, row major.

    This is the sect. 6.4 metric, computed without hashing so it is cheap
    enough to evaluate in a panel draw. L^T L is the right Cauchy-Green
    tensor: for the polar decomposition L = R*S it equals S^2, so it is
    exactly invariant to rotation and contains no translation at all.
    Folding in the unit multiplier squared makes it the physical metric in
    mm^2 per squared local unit, so a unit change registers as a metric
    change while a rotation does not.

    Pure Python on purpose: three dot products, no numpy import in state.
    """
    linear = [[float(matrix_world[row][col]) for col in range(3)]
              for row in range(3)]
    squared = float(multiplier) * float(multiplier)
    flat = []
    for i in range(3):
        for j in range(3):
            total = 0.0
            for k in range(3):
                total += linear[k][i] * linear[k][j]
            flat.append(total * squared)
    return tuple(flat)


#: Relative tolerance when comparing two metric tensors.
#:
#: Set by the precision of Blender's own transform, not by float64. An
#: ``Object.matrix_world`` is stored in SINGLE precision, so for a pure
#: rotation R the product L^T L differs from the identity by about 3.6e-8
#: relative - measured on Blender 4.5.13, not assumed. A tolerance tighter
#: than that reports every rotation as a metric change and invalidates a
#: surface distance that sect. 6.3 requires to stay valid. That is exactly
#: what happened with 1e-9 before this constant existed.
#:
#: The discrimination gap is wide: the float32 noise floor is ~4e-8 relative,
#: while the smallest scale change anyone would apply on purpose - a factor of
#: 1.0001 - moves the tensor by ~2e-4. 1e-6 sits ~25x above the noise and
#: ~200x below the smallest real signal.
METRIC_RELATIVE_TOLERANCE = 1e-6


def metric_tensors_match(first, second, relative=METRIC_RELATIVE_TOLERANCE):
    """True when two metric tensors describe the same physical metric."""
    first = tuple(float(v) for v in first)
    second = tuple(float(v) for v in second)
    scale = max(abs(v) for v in first + second) if first or second else 0.0
    if scale == 0.0:
        return all(v == 0.0 for v in first + second)
    tolerance = relative * scale
    return all(abs(a - b) <= tolerance for a, b in zip(first, second))


def clear_surface_result(props):
    """Forget the surface distance. Never touches the straight distance."""
    props.surface_valid = False
    props.surface_distance_mm = 0.0
    props.surface_straight_mm = 0.0
    props.surface_ratio = 0.0
    props.surface_summary = ""
    props.surface_mode = ""
    props.surface_object = ""
    props.surface_geometry_hash = ""
    props.surface_metric_key = ""
    props.surface_metric_tensor = (0.0,) * 9
    props.surface_component_id = 0
    props.surface_backend_name = ""
    props.surface_backend_version = ""
    props.surface_algorithm_version = ""
    props.surface_bound_factor = 0.0
    props.surface_unbounded_fallback = False
    props.surface_attempts = 0
    props.surface_seconds = 0.0
    props.surface_insertion_seconds = 0.0
    props.surface_solver_seconds = 0.0
    props.surface_provenance = ""


def set_surface_failure(props, message):
    """Record a failure. No numeric surface distance is stored (sect. 5.2)."""
    clear_surface_result(props)
    props.surface_status = message


def surface_result_problem(props, canonical=None, matrix_world=None):
    """Why the stored surface distance is no longer current, or ''.

    `canonical` is the live CanonicalMesh when one is cached, or None when it
    is not. A cleared cache is the project's established "geometry changed"
    signal (Milestone 2.1a), so it is reported as needing recomputation rather
    than silently trusted: displaying a distance that may have been computed
    on different geometry is exactly what sect. 5.2 forbids.
    """
    if not props.surface_valid:
        return ""

    for slot in ('A', 'B'):
        point = surface_point(props, slot)
        if not point.valid:
            return "Point %s was cleared" % slot
        if point.status != "VALID":
            return "Point %s is %s" % (slot, point.status)
        if point.source_object != props.surface_object:
            return "Point %s is now on a different object" % slot
        if point.geometry_hash != props.surface_geometry_hash:
            return "Point %s refers to different geometry" % slot

    if canonical is None:
        return "canonical mesh not loaded - recompute to confirm"

    if canonical.geometry_hash != props.surface_geometry_hash:
        return "the mesh geometry changed since this was computed"

    if matrix_world is not None:
        live = metric_tensor(matrix_world, canonical.unit_multiplier)
        if not metric_tensors_match(live, props.surface_metric_tensor):
            # Translation and rotation cannot reach here: the tensor is
            # rotation invariant and has no translation term. A scale or a
            # unit change does, and a geodesic cannot be rescaled from a
            # previous answer because a non-uniform scale moves the path.
            return "the object scale or coordinate unit changed - recompute"

    return ""


def set_point(props, slot, location):
    """Store a picked world-space location into slot 'A' or 'B'."""
    if slot == 'A':
        props.point_a = (location[0], location[1], location[2])
        props.point_a_valid = True
    else:
        props.point_b = (location[0], location[1], location[2])
        props.point_b_valid = True
    # A new point makes any previous result stale - both of them.
    props.distance_valid = False
    props.distance_mm = 0.0
    clear_surface_result(props)
    props.surface_status = ""


def clear_component_preview_state(props):
    """Forget the component preview list. Does not delete any object."""
    props.components.clear()
    props.component_preview_valid = False
    props.component_preview_object = ""
    props.component_isolate = 0
    props.component_report = ""
    props.component_report_valid = False


def clear_topology(props):
    """Forget the topology report. Independent of the measurement points."""
    props.topology_valid = False
    props.topology_object = ""
    props.topology_report = ""


def clear_backend_reports(props):
    """Forget the Milestone 2.2 diagnostics output.

    Separate from clear_topology() and from reset(): the backend proof has
    nothing to do with the measurement points and must not be cleared by
    Clear Points, nor clear them.
    """
    props.env_report = ""
    props.env_report_valid = False
    props.backend_test_report = ""
    props.backend_test_valid = False


def reset(props):
    """Forget both points and the result, Phase 1 and Phase 2 together."""
    clear_surface_point(props.surface_a)
    clear_surface_point(props.surface_b)
    props.point_a_valid = False
    props.point_b_valid = False
    props.point_a = (0.0, 0.0, 0.0)
    props.point_b = (0.0, 0.0, 0.0)
    props.distance_valid = False
    props.distance_mm = 0.0
    clear_surface_result(props)
    props.surface_status = ""


classes = (
    # Order matters twice over: BSMT_SurfacePoint must exist before
    # BSMT_Landmark can point at it, and BSMT_Landmark before the Scene
    # collection that holds it.
    BSMT_SurfacePoint,
    BSMT_Landmark,
    BSMT_Measurement,
    # Both must exist before the region whose collections hold them.
    BSMT_RegionLandmark,
    BSMT_RegionSegment,
    BSMT_SurfaceRegion,
    BSMT_ScanProvenance,
    BSMT_BoundaryLoop,
    BSMT_RepairComponent,
    BSMT_NonManifoldDefect,
    BSMT_DegenerateDefect,
    BSMT_ComponentInfo,
    BSMT_Properties,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bsmt = bpy.props.PointerProperty(type=BSMT_Properties)
    # Scene-level, as specified in the Milestone 3.0 brief.
    bpy.types.Scene.bsmt_landmarks = CollectionProperty(type=BSMT_Landmark)
    bpy.types.Scene.bsmt_measurements = CollectionProperty(type=BSMT_Measurement)
    # Milestone 3.29. Additive: a .blend saved before this existed simply has
    # an empty collection, which is exactly "no regions defined yet".
    bpy.types.Scene.bsmt_regions = CollectionProperty(type=BSMT_SurfaceRegion)
    # Provenance lives on the generated object itself, so it travels with the
    # .blend and cannot drift from the object it describes.
    bpy.types.Object.bsmt_scan = PointerProperty(type=BSMT_ScanProvenance)


def unregister():
    if hasattr(bpy.types.Object, "bsmt_scan"):
        del bpy.types.Object.bsmt_scan
    if hasattr(bpy.types.Scene, "bsmt_regions"):
        del bpy.types.Scene.bsmt_regions
    if hasattr(bpy.types.Scene, "bsmt_measurements"):
        del bpy.types.Scene.bsmt_measurements
    if hasattr(bpy.types.Scene, "bsmt_landmarks"):
        del bpy.types.Scene.bsmt_landmarks
    if hasattr(bpy.types.Scene, "bsmt"):
        del bpy.types.Scene.bsmt
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
