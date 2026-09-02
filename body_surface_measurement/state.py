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

from . import (alignment, export, geodesic, landmarks, measurement,
               measurements, overlay, preprocess, readiness, visualization)


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
_on_landmark_label_changed = _on_landmark_display_changed


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


def _on_visualization_style_changed(self, context):
    """Colour or thickness changed. Cosmetic: no rebuild, no recomputation."""
    try:
        visualization.apply_measurement_display(context, self)
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
    # The polyline itself lives in the helper curve, in the scan's local
    # space. These fields are the provenance that says whether it is still
    # the path for the current landmarks, geometry and metric.
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
    path_source_stable_id: IntProperty(default=0)
    path_target_stable_id: IntProperty(default=0)

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
        # Blender would repaint on its own.
        update=_on_landmark_display_changed,
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
        update=_on_landmark_display_changed,
    )
    landmark_marker_size_px: IntProperty(
        name="Marker Size (px)",
        description="Diameter of a landmark marker in SCREEN PIXELS, so every "
                    "landmark reads at exactly the same size however far you "
                    "zoom. The marker is an annotation; changing it never "
                    "moves a landmark",
        default=overlay.DEFAULT_MARKER_SIZE,
        min=2, max=20,
        update=_on_landmark_display_changed,
    )
    landmark_marker_color: FloatVectorProperty(
        name="Marker Color",
        description="Color of a VALID landmark marker. A stale or invalid "
                    "landmark keeps its status color instead, so a marker "
                    "that cannot be trusted never looks like one that can",
        subtype='COLOR', size=4,
        default=visualization.LANDMARK_COLOR,
        min=0.0, max=1.0,
        update=_on_landmark_display_changed,
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
        update=_on_landmark_display_changed,
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
    if props.measurement_index >= len(collection):
        props.measurement_index = max(0, len(collection) - 1)
    return stable_id


def clear_measurements(context, props):
    collection = get_measurements(context)
    if collection is None:
        return 0
    count = len(collection)
    collection.clear()
    props.measurement_index = 0
    props.measurement_summary = ""
    props.measurement_progress = ""
    return count


def clear_measurement_path(item, remove_helper=True):
    """Forget a cached surface path. The measurement result is untouched.

    The polyline lives in the helper curve, so dropping the cache removes it:
    a path that is no longer known to be current must not stay on screen.
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
    item.path_source_stable_id = 0
    item.path_target_stable_id = 0
    if remove_helper:
        try:
            visualization.remove_measurement_helper(item.stable_id, 'PATH')
        except Exception:                             # pragma: no cover
            pass
    return had


def path_is_current(item, canonical=None, matrix_world=None):
    """Whether a cached path still describes the current configuration.

    A rigid transform deliberately cannot fail this: the metric tensor is
    rotation invariant and carries no translation, so a translated or rotated
    scan keeps its path and simply follows (sect. 11).
    """
    if not item.path_valid:
        return False
    if item.path_source_stable_id != item.source_stable_id:
        return False
    if item.path_target_stable_id != item.target_stable_id:
        return False
    if not visualization.measurement_helper_exists(item.stable_id, 'PATH'):
        return False
    if canonical is not None:
        if canonical.geometry_hash != item.path_geometry_hash:
            return False
        if matrix_world is not None:
            live = metric_tensor(matrix_world, canonical.unit_multiplier)
            if not metric_tensors_match(live, item.path_metric_tensor):
                return False
    return True


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
    """The researcher's session fields. Metadata only, never read elsewhere."""
    return {
        "subject_id": props.session_subject_id.strip(),
        "condition": props.session_condition.strip(),
        "scan_id": props.session_scan_id.strip(),
        "notes": props.session_notes.strip(),
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
        "protocol_id": item.protocol_id,
        "name": item.name,
        "notes": item.notes,
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
    triangle_count = 0
    non_manifold = 0
    analysed = False
    if obj is not None and geodesic.MESHCACHE_AVAILABLE:
        cached = geodesic.meshcache.peek(obj.name)
        if cached is not None:
            report = cached.topology or {}
            triangle_count = int(report.get("triangle_count", 0) or 0)
            non_manifold = int(report.get("nonmanifold_edge_count", 0) or 0)
            analysed = True

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
        non_manifold=non_manifold,
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
    props.align_object = ""
    props.align_method = ""
    props.align_created = ""
    props.align_report = ""
    props.align_residual_degrees = 0.0
    props.align_preview = False


def matrix_to_flat(matrix):
    return tuple(float(matrix[row][col]) for row in range(4) for col in range(4))


def flat_to_rows(flat):
    values = [float(v) for v in flat]
    return [values[0:4], values[4:8], values[8:12], values[12:16]]


def clear_repair_lists(props):
    props.boundary_loops.clear()
    props.repair_components.clear()
    props.boundary_loop_index = 0
    props.repair_component_index = 0


def clear_repair_state(props):
    """Forget the repair analysis. No object or mesh is touched."""
    clear_repair_lists(props)
    props.repair_report = ""
    props.repair_readiness = ""
    props.repair_valid = False
    props.repair_object = ""


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
    BSMT_ScanProvenance,
    BSMT_BoundaryLoop,
    BSMT_RepairComponent,
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
    # Provenance lives on the generated object itself, so it travels with the
    # .blend and cannot drift from the object it describes.
    bpy.types.Object.bsmt_scan = PointerProperty(type=BSMT_ScanProvenance)


def unregister():
    if hasattr(bpy.types.Object, "bsmt_scan"):
        del bpy.types.Object.bsmt_scan
    if hasattr(bpy.types.Scene, "bsmt_measurements"):
        del bpy.types.Scene.bsmt_measurements
    if hasattr(bpy.types.Scene, "bsmt_landmarks"):
        del bpy.types.Scene.bsmt_landmarks
    if hasattr(bpy.types.Scene, "bsmt"):
        del bpy.types.Scene.bsmt
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
