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

from . import geodesic, measurement, visualization


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


def set_surface_point(props, slot, source_object, geometry_hash, triangle_index,
                      barycentric, component_id, kind, local_xyz, world_xyz,
                      physical_mm_xyz, reconstruction_error):
    """Store the canonical surface location for slot 'A' or 'B'."""
    point = surface_point(props, slot)
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


def set_point(props, slot, location):
    """Store a picked world-space location into slot 'A' or 'B'."""
    if slot == 'A':
        props.point_a = (location[0], location[1], location[2])
        props.point_a_valid = True
    else:
        props.point_b = (location[0], location[1], location[2])
        props.point_b_valid = True
    # A new point makes any previous result stale.
    props.distance_valid = False
    props.distance_mm = 0.0


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


classes = (
    BSMT_SurfacePoint,
    BSMT_ComponentInfo,
    BSMT_Properties,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bsmt = bpy.props.PointerProperty(type=BSMT_Properties)


def unregister():
    if hasattr(bpy.types.Scene, "bsmt"):
        del bpy.types.Scene.bsmt
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
