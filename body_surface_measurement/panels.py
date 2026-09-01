"""Sidebar UI: View3D > Sidebar (N) > BSMT > Body Measurement."""

import bpy

from . import geodesic, measurement, state


class BSMT_PT_body_measurement(bpy.types.Panel):
    bl_label = "Body Measurement"
    bl_idname = "BSMT_PT_body_measurement"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BSMT"

    def draw(self, context):
        layout = self.layout
        props = state.get_props(context)
        if props is None:
            layout.label(text="Add-on state unavailable", icon='ERROR')
            return

        layout.prop(props, "unit")

        display = layout.box()
        display.prop(props, "marker_size_mm")
        display.prop(props, "line_thickness_mm")
        row = display.row(align=True)
        row.prop(props, "show_markers", toggle=False)
        row.prop(props, "show_line", toggle=False)

        box = layout.box()
        self._draw_point(box, props, 'A')
        self._draw_point(box, props, 'B')

        column = layout.column(align=True)
        column.operator(
            "bsmt.pick_point", text="Pick Point A", icon='EYEDROPPER'
        ).point = 'A'
        column.operator(
            "bsmt.pick_point", text="Pick Point B", icon='EYEDROPPER'
        ).point = 'B'

        column = layout.column(align=True)
        column.operator("bsmt.calculate_distance", icon='ARROW_LEFTRIGHT')
        column.operator("bsmt.clear_points", icon='TRASH')

        self._draw_surface_debug(layout, props)

        box = layout.box()
        if props.distance_valid:
            box.label(
                text="Straight Distance: %s" % measurement.format_mm(props.distance_mm)
            )
        else:
            box.label(text="Straight Distance: --")

    @staticmethod
    def _draw_surface_debug(layout, props):
        """Collapsed canonical-attachment detail. Research/validation aid."""
        header = layout.row(align=True)
        header.prop(
            props,
            "show_surface_debug",
            icon='TRIA_DOWN' if props.show_surface_debug else 'TRIA_RIGHT',
            emboss=False,
        )
        if not props.show_surface_debug:
            return

        box = layout.box()
        row = box.row(align=True)
        row.operator("bsmt.validate_surface_points", text="Validate", icon='FILE_REFRESH')
        row.operator("bsmt.refresh_helpers", text="Refresh", icon='CON_LOCLIKE')
        box.prop(props, "transform_debug")
        box.operator("bsmt.transform_handler_status", icon='INFO')

        for slot in ('A', 'B'):
            point = props.surface_a if slot == 'A' else props.surface_b
            column = box.column(align=True)
            column.scale_y = 0.7
            column.label(text="Point %s" % slot)
            if not point.valid:
                column.label(text="   not picked")
                continue
            column.label(text="   Object:     %s" % point.source_object)
            column.label(text="   Triangle:   %d" % point.triangle_index)
            column.label(
                text="   Barycentric: %.6f, %.6f, %.6f"
                % (point.barycentric[0], point.barycentric[1], point.barycentric[2])
            )
            column.label(text="   Component:  %d" % point.component_id)
            column.label(text="   Position:   %s" % point.kind)
            column.label(
                text="   Physical mm: %.3f, %.3f, %.3f"
                % (
                    point.physical_mm_xyz[0],
                    point.physical_mm_xyz[1],
                    point.physical_mm_xyz[2],
                )
            )
            column.label(text="   Geom hash:  %s" % point.geometry_hash[:16])
            column.label(text="   Recon err:  %.3e" % point.reconstruction_error)
            column.label(text="   Status:     %s" % point.status)

    @staticmethod
    def _draw_point(layout, props, slot):
        valid = props.point_a_valid if slot == 'A' else props.point_b_valid
        location = props.point_a if slot == 'A' else props.point_b
        row = layout.row()
        row.label(
            text="Point %s: %s" % (slot, "Selected" if valid else "Not Selected"),
            icon='CHECKMARK' if valid else 'BLANK1',
        )
        if valid:
            sub = layout.row()
            sub.enabled = False
            sub.label(
                text="   (%.3f, %.3f, %.3f)"
                % (location[0], location[1], location[2])
            )


def _wrap(text, width):
    """Naive word wrap; Blender panel labels do not wrap by themselves."""
    lines = []
    current = ""
    for word in text.split():
        candidate = (current + " " + word).strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


class BSMT_PT_diagnostics(bpy.types.Panel):
    """Read-only mesh topology diagnostics (Phase 2, Milestone 2.0)."""

    bl_label = "Diagnostics"
    bl_idname = "BSMT_PT_diagnostics"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BSMT"
    bl_parent_id = "BSMT_PT_body_measurement"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = state.get_props(context)
        if props is None:
            layout.label(text="Add-on state unavailable", icon='ERROR')
            return

        obj = context.active_object
        name = obj.name if obj is not None else "-"
        layout.label(text="Active: %s" % name)

        unavailable = geodesic.diagnostics_error()
        if unavailable:
            box = layout.box()
            column = box.column(align=True)
            column.scale_y = 0.7
            column.label(text="Diagnostics unavailable", icon='ERROR')
            for line in _wrap(unavailable, 46):
                column.label(text=line)
            column.label(text="Traceback: system console")
            return

        layout.prop(props, "near_coincident_mm")
        layout.operator("bsmt.diagnose_topology", icon='VIEWZOOM')

        if not props.topology_valid:
            layout.label(text="No report yet")
            return

        row = layout.row()
        row.label(text="Report for: %s" % props.topology_object)
        row.operator("bsmt.clear_topology", text="", icon='X')

        box = layout.box()
        column = box.column(align=True)
        column.scale_y = 0.7
        for line in props.topology_report.split("\n"):
            if line.strip():
                column.label(text=line)
            else:
                column.separator()

        self._draw_component_preview(layout, props)

    @staticmethod
    def _draw_component_preview(layout, props):
        layout.separator()
        layout.label(text="Component Preview")

        preview_problem = geodesic.preview_error()
        if preview_problem:
            column = layout.box().column(align=True)
            column.scale_y = 0.7
            column.label(text="Unavailable", icon='ERROR')
            for line in _wrap(preview_problem, 46):
                column.label(text=line)
            return

        row = layout.row(align=True)
        row.operator("bsmt.visualize_components", text="Visualize", icon='COLOR')
        row.operator("bsmt.clear_component_preview", text="Clear", icon='X')
        layout.operator("bsmt.verify_components", icon='CHECKMARK')

        if props.component_report_valid and props.component_report:
            column = layout.box().column(align=True)
            column.scale_y = 0.7
            for line in props.component_report.split("\n"):
                if line.strip():
                    column.label(text=line)
                else:
                    column.separator()

        if not props.component_preview_valid or not props.components:
            return

        box = layout.box()
        box.label(text="Preview of: %s" % props.component_preview_object)

        row = box.row(align=True)
        show_all = row.operator("bsmt.isolate_component", text="Show All")
        show_all.index = 0
        row.prop(props, "component_isolate")

        for item in props.components:
            row = box.row(align=True)
            swatch = row.row(align=True)
            swatch.enabled = False                 # read-only colour key
            swatch.prop(item, "color", text="")
            row.label(
                text="C%d  %d tris  %d verts"
                % (item.index, item.triangle_count, item.vertex_count)
            )
            isolate = row.operator(
                "bsmt.isolate_component", text="", icon='HIDE_OFF'
            )
            isolate.index = item.index


classes = (
    BSMT_PT_body_measurement,
    BSMT_PT_diagnostics,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
