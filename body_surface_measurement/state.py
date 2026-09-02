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

from . import geodesic, landmarks, measurement, visualization


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


def _on_landmark_display_changed(self, context):
    """Re-apply landmark marker size and visibility. Cosmetic only."""
    visualization.apply_landmark_display(context, self)


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
    show_landmarks: BoolProperty(
        name="Show Named Landmarks",
        description="Show the named landmark markers. Independent of the "
                    "A/B marker visibility",
        default=True,
        update=_on_landmark_display_changed,
    )
    landmark_marker_size_mm: FloatProperty(
        name="Landmark Size (mm)",
        description="Diameter of a named landmark marker, in millimetres",
        default=12.0,
        min=0.1,
        soft_max=100.0,
        update=_on_landmark_display_changed,
    )
    show_landmark_labels: BoolProperty(
        name="Show Landmark Labels",
        description="Draw the landmark name next to each marker in the viewport",
        default=False,
        update=_on_landmark_display_changed,
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
    collection.remove(index)
    if props.landmark_index >= len(collection):
        props.landmark_index = max(0, len(collection) - 1)
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


def clear_landmark_position(item):
    """Forget a landmark's surface location, keeping its name and stable id."""
    clear_surface_point(item.surface_point)
    item.status = landmarks.STATUS_NOT_PICKED
    item.status_detail = ""


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
    BSMT_ComponentInfo,
    BSMT_Properties,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bsmt = bpy.props.PointerProperty(type=BSMT_Properties)
    # Scene-level, as specified in the Milestone 3.0 brief.
    bpy.types.Scene.bsmt_landmarks = CollectionProperty(type=BSMT_Landmark)


def unregister():
    if hasattr(bpy.types.Scene, "bsmt_landmarks"):
        del bpy.types.Scene.bsmt_landmarks
    if hasattr(bpy.types.Scene, "bsmt"):
        del bpy.types.Scene.bsmt
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
