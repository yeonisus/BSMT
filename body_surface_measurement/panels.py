"""Sidebar UI: View3D > Sidebar (N) > BSMT > Body Measurement."""

import bpy

from . import (geodesic, landmarks, measurement, measurements, preprocess,
               repair, scancopy, state, visualization)


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
        column.operator("bsmt.calculate_surface_distance", icon='MOD_SIMPLIFY')
        column.operator("bsmt.clear_points", icon='TRASH')

        self._draw_surface_debug(layout, props)
        self._draw_results(context, layout, props)

    @staticmethod
    def _draw_results(context, layout, props):
        """Straight and surface distance, plus a compact query status.

        The surface result is checked against the live canonical mesh before
        it is shown. A distance computed under different geometry or a
        different metric is never displayed as current (sect. 5.2, 6.5).
        """
        box = layout.box()
        if props.distance_valid:
            box.label(
                text="Straight Distance: %s" % measurement.format_mm(props.distance_mm)
            )
        else:
            box.label(text="Straight Distance: --")

        problem = ""
        if props.surface_valid:
            canonical = None
            matrix = None
            meshcache = getattr(geodesic, "meshcache", None)
            if meshcache is not None and props.surface_object:
                # peek() never builds, so it is safe inside draw().
                canonical = meshcache.peek(props.surface_object)
                obj = bpy.data.objects.get(props.surface_object)
                if obj is not None:
                    matrix = obj.matrix_world
            problem = state.surface_result_problem(props, canonical, matrix)

        if props.surface_valid and not problem:
            box.label(
                text="Surface Distance:  %s"
                % measurement.format_mm(props.surface_distance_mm)
            )
            box.label(text="Surface / Straight: %.3f" % props.surface_ratio)
            status = box.column(align=True)
            status.scale_y = 0.7
            status.label(text=props.surface_backend_name or "Exact MMP")
            if props.surface_mode == 'SOLVER':
                status.label(
                    text="Bound: %s"
                    % ("unbounded fallback" if props.surface_unbounded_fallback
                       else "%.2fx" % props.surface_bound_factor)
                )
                status.label(text="Attempts: %d" % props.surface_attempts)
            else:
                status.label(text="Analytic: %s" % props.surface_mode)
            status.label(text="Elapsed: %.2f s" % props.surface_seconds)
        elif props.surface_valid and problem:
            column = box.column(align=True)
            column.scale_y = 0.7
            column.label(text="Surface Distance:  recompute required", icon='ERROR')
            for line in _wrap(problem, 44):
                column.label(text="   " + line)
        elif props.surface_status:
            column = box.column(align=True)
            column.scale_y = 0.7
            for line in _wrap(props.surface_status, 44):
                column.label(text=line)
        else:
            box.label(text="Surface Distance:  --")

        if props.surface_valid or props.surface_status:
            box.operator("bsmt.clear_surface_distance", text="", icon='X')

        if props.surface_valid and props.surface_provenance:
            header = layout.row(align=True)
            header.prop(
                props,
                "show_surface_provenance",
                icon='TRIA_DOWN' if props.show_surface_provenance else 'TRIA_RIGHT',
                emboss=False,
            )
            if props.show_surface_provenance:
                column = layout.box().column(align=True)
                column.scale_y = 0.7
                for line in props.surface_provenance.split("\n"):
                    if line.strip():
                        column.label(text=line)
                    else:
                        column.separator()

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


class BSMT_PT_geodesic_backend(bpy.types.Panel):
    """Milestone 2.2 development panel: exact geodesic backend proof.

    Deliberately NOT part of the Body Measurement result area. Nothing here
    produces or displays a research measurement; Surface Distance arrives in
    Milestone 2.3.
    """

    bl_label = "Geodesic Backend (dev)"
    bl_idname = "BSMT_PT_geodesic_backend"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BSMT"
    bl_parent_id = "BSMT_PT_body_measurement"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = state.get_props(context)

        status = geodesic.backend_status()
        available = bool(status.get("available"))

        box = layout.box()
        column = box.column(align=True)
        column.scale_y = 0.7
        column.label(
            text="pygeodesic: %s" % ("Available" if available else "Unavailable"),
            icon='CHECKMARK' if available else 'ERROR',
        )
        column.label(text="Version: %s" % (status.get("version") or "-"))
        path = status.get("module_path") or "-"
        column.label(text="Import path:")
        for line in _wrap(path, 46):
            column.label(text="  " + line)

        if not available:
            if status.get("wrapper_error"):
                column.separator()
                column.label(text="BSMT wrapper error:")
                for line in _wrap(status["wrapper_error"], 46):
                    column.label(text="  " + line)
            if status.get("import_error"):
                column.separator()
                column.label(text="Import error:")
                for line in _wrap(status["import_error"], 46):
                    column.label(text="  " + line)
                column.label(text="Full traceback: system console")
            column.separator()
            column.label(text="BSMT and Phase 1 measurement are unaffected.")
            column.label(text="Run Check Environment for the install command.")

        layout.operator("bsmt.check_geodesic_env", icon='CONSOLE')

        run = layout.column(align=True)
        run.enabled = available
        run.operator("bsmt.run_backend_selftest", icon='PLAY')
        if props is not None:
            run.prop(props, "backend_test_dijkstra")
            run.prop(props, "backend_test_dense")
            dense = run.row()
            dense.enabled = props.backend_test_dense
            dense.prop(props, "backend_test_triangles")
            if props.backend_test_dense:
                warn = layout.column(align=True)
                warn.scale_y = 0.7
                warn.label(text="Dense benchmark blocks the UI while it runs.",
                           icon='INFO')

        if props is None:
            return

        if props.env_report_valid or props.backend_test_valid:
            layout.operator("bsmt.clear_backend_reports", text="Clear Reports",
                            icon='X')

        self._draw_report(layout, "Environment", props.env_report_valid,
                          props.env_report)
        self._draw_report(layout, "Synthetic Backend Tests",
                          props.backend_test_valid, props.backend_test_report)

    @staticmethod
    def _draw_report(layout, title, valid, text):
        if not valid or not text:
            return
        layout.separator()
        layout.label(text=title)
        column = layout.box().column(align=True)
        column.scale_y = 0.7
        for line in text.split("\n"):
            if line.strip():
                column.label(text=line)
            else:
                column.separator()


class BSMT_UL_landmarks(bpy.types.UIList):
    """Landmark rows: name and a concise status.

    A UIList because the manager has to stay usable at 20-50 landmarks: it
    scrolls, filters and sorts without the panel growing without bound, and it
    draws only the visible rows. Per-landmark detail belongs in the panel
    below the list, not in every row (sect. 3).
    """

    bl_idname = "BSMT_UL_landmarks"

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index=0, flt_flag=0):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            identifier = row.row()
            identifier.scale_x = 0.35
            identifier.enabled = False
            identifier.label(text=item.protocol_id or "-")
            row.label(text=item.label)
            status = row.row()
            status.alignment = 'RIGHT'
            status.label(
                text=_STATUS_SHORT.get(item.status, item.status),
                icon=landmarks.STATUS_ICONS.get(item.status, 'BLANK1'),
            )
        else:
            layout.alignment = 'CENTER'
            layout.label(
                text="", icon=landmarks.STATUS_ICONS.get(item.status, 'BLANK1')
            )


_STATUS_SHORT = {
    landmarks.STATUS_NOT_PICKED: "NOT PICKED",
    landmarks.STATUS_VALID: "VALID",
    landmarks.STATUS_NEEDS_REFRESH: "REFRESH",
    landmarks.STATUS_STALE: "STALE",
    landmarks.STATUS_INVALID: "INVALID",
}


class BSMT_PT_landmarks(bpy.types.Panel):
    """Named research landmarks (Milestone 3.0).

    A separate layer from the A/B workflow above, which stays available for
    quick ad-hoc measurement and is not affected by anything here.
    """

    bl_label = "Landmark Manager"
    bl_idname = "BSMT_PT_landmarks"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BSMT"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = state.get_props(context)
        collection = state.get_landmarks(context)
        if props is None or collection is None:
            layout.label(text="Add-on state unavailable", icon='ERROR')
            return

        if props.protocol_name:
            row = layout.row()
            row.enabled = False
            row.label(text="Protocol: %s" % props.protocol_name)

        layout.template_list(
            "BSMT_UL_landmarks", "",
            context.scene, "bsmt_landmarks",
            props, "landmark_index",
            rows=6 if len(collection) > 3 else 3,
        )

        row = layout.row(align=True)
        row.operator("bsmt.add_landmark", text="Add", icon='ADD')
        row.operator("bsmt.remove_landmark", text="Delete", icon='REMOVE')

        self._draw_selected(layout, props, collection)
        self._draw_guided(layout, props, collection)

        layout.separator()
        column = layout.column(align=True)
        column.operator("bsmt.validate_landmarks", icon='CHECKMARK')
        if props.landmark_summary:
            info = column.row()
            info.enabled = False
            info.label(text=props.landmark_summary)

        self._draw_display(layout, props)
        self._draw_protocol(layout, props)

        layout.separator()
        layout.operator("bsmt.clear_landmarks", text="Clear Landmark Data",
                        icon='TRASH')

    @staticmethod
    def _draw_selected(layout, props, collection):
        box = layout.box()
        index = props.landmark_index
        if not 0 <= index < len(collection):
            box.label(text="No landmark selected")
            return
        item = collection[index]

        box.label(text="Selected: %s" % item.label)
        box.prop(item, "name", text="Name")
        box.prop(item, "notes", text="Notes")

        row = box.row(align=True)
        row.operator(
            "bsmt.pick_landmark",
            text="Re-pick" if item.surface_point.valid else "Pick Selected",
            icon='EYEDROPPER',
        ).index = index
        row.operator("bsmt.clear_landmark_position", text="Clear Position",
                     icon='X')

        column = box.column(align=True)
        column.scale_y = 0.7
        column.label(
            text="Status: %s" % _STATUS_SHORT.get(item.status, item.status),
            icon=landmarks.STATUS_ICONS.get(item.status, 'BLANK1'),
        )
        if item.status_detail:
            for line in _wrap(item.status_detail, 42):
                column.label(text="   " + line)
        point = item.surface_point
        if point.valid:
            column.label(text="   Object:    %s" % point.source_object)
            column.label(text="   Triangle:  %d" % point.triangle_index)
            column.label(text="   Component: %d" % point.component_id)
            column.label(
                text="   Physical mm: %.1f, %.1f, %.1f"
                % (point.physical_mm_xyz[0], point.physical_mm_xyz[1],
                   point.physical_mm_xyz[2])
            )

    @staticmethod
    def _draw_guided(layout, props, collection):
        box = layout.box()
        if not props.guided_active:
            box.operator("bsmt.guided_picking", text="Start Guided Picking",
                         icon='PLAY').action = 'START'
            box.prop(props, "guided_skip_valid")
            return

        total = sum(
            1 for item in collection
            if not props.guided_skip_valid or item.status != landmarks.STATUS_VALID
        )
        index = props.landmark_index
        label = collection[index].label if 0 <= index < len(collection) else "-"
        box.label(text="Pick %d/%d: %s" % (props.guided_index + 1, total, label),
                  icon='EYEDROPPER')

        row = box.row(align=True)
        row.operator("bsmt.guided_picking", text="Previous",
                     icon='TRIA_LEFT').action = 'PREVIOUS'
        row.operator("bsmt.pick_landmark", text="Pick",
                     icon='EYEDROPPER').index = index
        row.operator("bsmt.guided_picking", text="Next",
                     icon='TRIA_RIGHT').action = 'NEXT'
        box.operator("bsmt.guided_picking", text="Cancel",
                     icon='X').action = 'CANCEL'

    @staticmethod
    def _draw_display(layout, props):
        box = layout.box()
        box.prop(props, "show_landmarks")
        box.prop(props, "landmark_marker_size_mm")

    @staticmethod
    def _draw_protocol(layout, props):
        layout.separator()
        layout.label(text="Protocol (names and order only)")
        row = layout.row(align=True)
        row.operator("bsmt.load_protocol", text="Load", icon='IMPORT')
        row.operator("bsmt.save_protocol", text="Save", icon='EXPORT')
        note = layout.column(align=True)
        note.scale_y = 0.7
        note.enabled = False
        note.label(text="Protocols carry names, not scan positions.")


class BSMT_UL_measurements(bpy.types.UIList):
    """Measurement rows: id, From to To, type, result and status.

    Compact on purpose (sect. 11): the numbers a researcher scans down the
    list, with the detail for the selected definition drawn below.
    """

    bl_idname = "BSMT_UL_measurements"

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index=0, flt_flag=0):
        if self.layout_type not in {'DEFAULT', 'COMPACT'}:
            layout.alignment = 'CENTER'
            layout.label(
                text="",
                icon=measurements.STATUS_ICONS.get(item.status, 'BLANK1'),
            )
            return

        column = layout.column(align=True)

        current = state.result_is_displayable(item)

        top = column.row(align=True)
        top.prop(item, "enabled", text="")
        identifier = top.row()
        identifier.scale_x = 0.35
        identifier.enabled = False
        identifier.label(text=item.protocol_id or "-")
        label = top.row()
        label.enabled = bool(item.enabled)
        label.label(text=item.label)
        kind = top.row()
        kind.alignment = 'RIGHT'
        kind.scale_x = 0.55
        kind.enabled = False
        kind.label(text=item.measurement_type)
        mark = top.row()
        mark.alignment = 'RIGHT'
        mark.scale_x = 0.2
        if item.status in (measurements.STATUS_FAILED,
                           measurements.STATUS_INVALID_REFERENCE):
            mark.alert = True
        mark.label(text="", icon=measurements.STATUS_ICONS.get(
            item.status, 'BLANK1'))

        bottom = column.row(align=True)
        bottom.scale_y = 0.75
        pair = bottom.row()
        pair.enabled = False
        pair.label(text="   %s \u2192 %s" % (
            item.source_name or item.source_protocol_id or "?",
            item.target_name or item.target_protocol_id or "?",
        ))
        result = bottom.row()
        result.alignment = 'RIGHT'
        if not item.enabled:
            # Still listed, but unmistakably not part of a batch run.
            result.enabled = False
            result.label(text="DISABLED")
        elif current:
            text = measurements.format_result(
                item.straight_mm, item.straight_valid,
                item.surface_mm, item.surface_valid,
            )
            if item.surface_valid and item.straight_valid and item.ratio:
                text += "  r %.3f" % item.ratio
            result.label(text=text)
        else:
            # No number is ever shown next to a status that is not VALID:
            # a stale value must not be readable as a current one.
            if item.status in (measurements.STATUS_STALE,
                               measurements.STATUS_FAILED,
                               measurements.STATUS_INVALID_REFERENCE):
                result.alert = True
            result.label(text=measurements.STATUS_SHORT.get(
                item.status, item.status))


class BSMT_PT_measurements(bpy.types.Panel):
    """User-defined measurements between named landmarks (Milestone 3.1).

    Only the definitions the researcher writes are ever calculated. There is
    no all-pairs path: the workflow is protocol-driven, not combinatorial.
    """

    bl_label = "Measurement Manager"
    bl_idname = "BSMT_PT_measurements"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BSMT"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = state.get_props(context)
        collection = state.get_measurements(context)
        if props is None or collection is None:
            layout.label(text="Add-on state unavailable", icon='ERROR')
            return

        if props.measurement_protocol_name:
            row = layout.row()
            row.enabled = False
            row.label(text="Template: %s" % props.measurement_protocol_name)

        if props.measurement_running and props.measurement_progress:
            box = layout.box()
            box.label(text=props.measurement_progress, icon='TIME')

        layout.template_list(
            "BSMT_UL_measurements", "",
            context.scene, "bsmt_measurements",
            props, "measurement_index",
            rows=6 if len(collection) > 3 else 3,
        )

        row = layout.row(align=True)
        row.operator("bsmt.add_measurement", text="Add", icon='ADD')
        row.operator("bsmt.remove_measurement", text="Delete", icon='REMOVE')

        self._draw_selected(context, layout, props, collection)

        layout.separator()
        column = layout.column(align=True)
        column.operator("bsmt.calculate_all_measurements", icon='PLAY')
        plan = measurements.batch_plan(collection)
        info = column.column(align=True)
        info.scale_y = 0.7
        info.enabled = False
        info.label(text=plan["summary"])
        if plan["disabled"]:
            info.label(text="%d disabled, will be skipped" % plan["disabled"])

        if props.measurement_summary:
            box = layout.box()
            box.label(text="Last Run", icon='INFO')
            lines = box.column(align=True)
            lines.scale_y = 0.7
            for line in props.measurement_summary.split("\n"):
                if line.strip():
                    lines.label(text=line)

        row = layout.row(align=True)
        row.operator("bsmt.refresh_measurements", text="Refresh",
                     icon='FILE_REFRESH')
        row.operator("bsmt.clear_measurement_results", text="Clear Results",
                     icon='X')

        self._draw_results(context, layout, props, collection)
        self._draw_template(layout)

        layout.separator()
        row = layout.row(align=True)
        row.operator("bsmt.remove_invalid_measurements",
                     text="Remove Invalid", icon='CANCEL')
        row.operator("bsmt.clear_measurements", text="Clear All", icon='TRASH')

    @staticmethod
    def _draw_selected(context, layout, props, collection):
        index = props.measurement_index
        if not 0 <= index < len(collection):
            layout.box().label(text="No measurement selected")
            return
        item = collection[index]
        box = layout.box()

        header = box.row(align=True)
        header.prop(
            props, "show_measurement_detail",
            icon='TRIA_DOWN' if props.show_measurement_detail else 'TRIA_RIGHT',
            emboss=False, text="Selected: %s" % item.label,
        )
        if not props.show_measurement_detail:
            return

        row = box.row(align=True)
        sub = row.row()
        sub.enabled = not item.auto_name
        sub.prop(item, "name", text="Name")
        row.prop(item, "auto_name", text="", icon='SYNTAX_OFF',
                 toggle=True)
        if item.auto_name:
            hint = box.row()
            hint.enabled = False
            hint.scale_y = 0.7
            hint.label(text="   Auto Name follows From / To")

        # From / To. These pickers WRITE the authoritative stable id through
        # their update callbacks and are never read back for identity: a
        # dynamic enum remaps by index when the landmark list changes, which
        # would silently repoint a measurement (see state.landmark_enum_items).
        source, target = state.resolve_measurement_landmarks(context, item)
        for slot, picker, resolved, cached_id, cached_name in (
            ("From", "source_picker", source,
             item.source_protocol_id, item.source_name),
            ("To", "target_picker", target,
             item.target_protocol_id, item.target_name),
        ):
            row = box.row(align=True)
            row.prop(item, picker, text=slot)
            if resolved is None:
                warn = box.row()
                warn.alert = True
                warn.label(
                    text="   %s unresolved: %s %s" % (
                        slot, cached_id or "?", cached_name or ""
                    ),
                    icon='CANCEL',
                )

        box.prop(item, "measurement_type", text="Type")
        box.prop(item, "enabled")
        box.prop(item, "notes", text="Notes")

        box.operator("bsmt.calculate_measurement", icon='PLAY')

        detail = box.column(align=True)
        detail.scale_y = 0.7
        detail.label(
            text="Status: %s" % measurements.STATUS_SHORT.get(
                item.status, item.status),
            icon=measurements.STATUS_ICONS.get(item.status, 'BLANK1'),
        )
        if item.status_detail:
            for line in _wrap(item.status_detail, 42):
                detail.label(text="   " + line)

        if not item.has_result:
            return
        detail.separator()
        if item.straight_valid:
            detail.label(text="Straight: %s"
                              % measurement.format_mm(item.straight_mm))
        if item.surface_valid:
            detail.label(text="Surface:  %s"
                              % measurement.format_mm(item.surface_mm))
            if item.straight_valid and item.ratio:
                detail.label(text="Surface / Straight: %.4f" % item.ratio)
            detail.label(text="Backend:  %s %s" % (item.backend_name,
                                                   item.backend_version))
            detail.label(text="Bound:    %s" % (
                "unbounded fallback" if item.unbounded_fallback
                else "%.2fx" % item.bound_factor))
            detail.label(text="Attempts: %d" % item.attempts)
        detail.label(text="Elapsed:  %.3f s" % item.elapsed_s)

    @staticmethod
    def _draw_results(context, layout, props, collection):
        """Every DEFINED measurement's result, in order, without selecting
        each one in turn.

        Only user-defined measurements appear. Nothing here enumerates
        landmark pairs.
        """
        layout.separator()
        header = layout.row(align=True)
        header.prop(
            props, "show_measurement_results",
            icon='TRIA_DOWN' if props.show_measurement_results else 'TRIA_RIGHT',
            emboss=False, text="Measurement Results",
        )
        if not props.show_measurement_results:
            return
        if not len(collection):
            layout.box().label(text="No measurements defined")
            return

        box = layout.box()
        for item in collection:
            entry = box.column(align=True)
            entry.scale_y = 0.75

            title = entry.row(align=True)
            title.label(
                text="%s  %s \u2192 %s" % (
                    item.protocol_id,
                    item.source_name or item.source_protocol_id or "?",
                    item.target_name or item.target_protocol_id or "?",
                ),
                icon=measurements.STATUS_ICONS.get(item.status, 'BLANK1'),
            )
            kind = title.row()
            kind.alignment = 'RIGHT'
            kind.enabled = False
            kind.label(text=item.measurement_type)

            if item.name and item.name != state.auto_name_for(context, item):
                named = entry.row()
                named.enabled = False
                named.label(text="   %s" % item.name)

            if not item.enabled:
                skipped = entry.row()
                skipped.enabled = False
                skipped.label(text="   DISABLED - skipped by Calculate All")

            if state.result_is_displayable(item):
                if item.straight_valid:
                    entry.label(text="   Straight %s"
                                     % measurement.format_mm(item.straight_mm))
                if item.surface_valid:
                    entry.label(text="   Surface  %s"
                                     % measurement.format_mm(item.surface_mm))
                if (item.straight_valid and item.surface_valid
                        and item.ratio):
                    entry.label(text="   Ratio    %.4f" % item.ratio)
            else:
                # Deliberately no numbers: a value that is not current must
                # never be readable as though it were.
                status_row = entry.row()
                if item.status in (measurements.STATUS_STALE,
                                   measurements.STATUS_FAILED,
                                   measurements.STATUS_INVALID_REFERENCE):
                    status_row.alert = True
                status_row.label(text="   %s" % measurements.STATUS_SHORT.get(
                    item.status, item.status))
                if item.status_detail:
                    for line in _wrap(item.status_detail, 40):
                        detail = entry.row()
                        detail.enabled = False
                        detail.label(text="      " + line)
            box.separator()

    @staticmethod
    def _draw_template(layout):
        layout.separator()
        layout.label(text="Measurement Template (definitions only)")
        row = layout.row(align=True)
        row.operator("bsmt.load_measurement_template", text="Load",
                     icon='IMPORT')
        row.operator("bsmt.save_measurement_template", text="Save",
                     icon='EXPORT')
        note = layout.column(align=True)
        note.scale_y = 0.7
        note.enabled = False
        note.label(text="Templates carry definitions, not results.")


class BSMT_PT_measurement_visualization(bpy.types.Panel):
    """Draw the selected measurement as a chord, a surface path, or both.

    Display only. Nothing here computes a distance, and the surface path is
    computed only when its button is pressed.
    """

    bl_label = "Measurement Visualization"
    bl_idname = "BSMT_PT_measurement_visualization"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BSMT"
    bl_parent_id = "BSMT_PT_measurements"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = state.get_props(context)
        collection = state.get_measurements(context)
        if props is None or collection is None:
            layout.label(text="Add-on state unavailable", icon='ERROR')
            return

        item = state.active_measurement(context, props)
        box = layout.box()
        if item is None:
            box.label(text="No measurement selected")
            return
        # The Measurement Manager's selection IS the visualisation target.
        # There is deliberately no second selection system.
        box.label(text="Selected: %s  %s \u2192 %s" % (
            item.protocol_id,
            item.source_name or item.source_protocol_id or "?",
            item.target_name or item.target_protocol_id or "?",
        ))
        drawn = []
        if visualization.measurement_helper_exists(item.stable_id, 'STRAIGHT'):
            drawn.append("LINE")
        if visualization.measurement_helper_exists(item.stable_id, 'PATH'):
            drawn.append("PATH")
        if drawn:
            state_row = box.row()
            state_row.enabled = False
            state_row.label(text="Showing: %s" % ("BOTH" if len(drawn) > 1
                                                  else drawn[0]))

        layout.prop(props, "viz_mode", text="Mode")
        row = layout.row(align=True)
        row.prop(props, "viz_selected_only")
        if not props.viz_selected_only:
            layout.prop(item, "show_visualization", text="Show This Measurement")

        self._draw_path_controls(layout, props, item)

        style = layout.box()
        style.label(text="Straight")
        style.prop(props, "viz_straight_color", text="Color")
        style.prop(props, "viz_straight_thickness_mm", text="Thickness (mm)")
        style.label(text="Surface Path")
        style.prop(props, "viz_path_color", text="Color")
        style.prop(props, "viz_path_thickness_mm", text="Thickness (mm)")
        style.prop(props, "viz_surface_offset")

        layout.separator()
        layout.operator("bsmt.refresh_visualization", icon='FILE_REFRESH')
        row = layout.row(align=True)
        row.operator("bsmt.clear_visualization", text="Clear Selected",
                     icon='X')
        row.operator("bsmt.clear_all_visualizations", text="Clear All",
                     icon='TRASH')

    @staticmethod
    def _draw_path_controls(layout, props, item):
        box = layout.box()
        if props.viz_running:
            box.label(text="Computing exact surface path...", icon='TIME')
            return

        if item.path_valid:
            column = box.column(align=True)
            column.scale_y = 0.75
            column.label(text="Path computed", icon='CHECKMARK')
            column.label(text="Elapsed: %.2f s" % item.path_elapsed_s)
            column.label(text="Points:  %d" % item.path_point_count)
            column.label(text="Path length:   %s"
                              % measurement.format_mm(item.path_length_mm))
            column.label(text="Solver path:   %s"
                              % measurement.format_mm(item.path_distance_mm))
            column.label(text="Stored surface: %s"
                              % measurement.format_mm(item.surface_mm))
            column.label(text="Agreement: %.3e mm" % item.path_agreement_mm)
            box.operator("bsmt.compute_surface_path", text="Recompute Path",
                         icon='FILE_REFRESH')
            return

        if props.viz_mode in ('SURFACE', 'BOTH'):
            note = box.column(align=True)
            note.scale_y = 0.75
            note.label(text="Surface path not computed", icon='INFO')
            if not item.surface_valid:
                note.label(text="Calculate the surface distance first.")
            else:
                note.label(text="This runs the unbounded exact solve and")
                note.label(text="may block Blender for tens of seconds.")
        box.operator("bsmt.compute_surface_path", icon='PLAY')

        if props.viz_status:
            status = box.column(align=True)
            status.scale_y = 0.7
            for line in _wrap(props.viz_status, 42):
                status.label(text=line)


class BSMT_PT_preprocessing(bpy.types.Panel):
    """Turn a dense textured scan into a lighter TEXTURED measurement copy.

    The source scan is never modified. Nothing is welded and no hole is
    filled: on a human scan those silently fuse anatomically distinct
    surfaces that happen to touch, and a fused surface produces a
    confidently wrong, systematically short geodesic.
    """

    bl_label = "Scan Preprocessing"
    bl_idname = "BSMT_PT_preprocessing"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BSMT"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = state.get_props(context)
        if props is None:
            layout.label(text="Add-on state unavailable", icon='ERROR')
            return

        obj = context.active_object
        info = scancopy.describe(obj) if obj is not None else None

        box = layout.box()
        if info is None:
            box.label(text="Select a mesh scan", icon='INFO')
        else:
            column = box.column(align=True)
            column.scale_y = 0.75
            column.label(text="Active: %s" % info["name"])
            column.label(text="Vertices:  {:,}".format(info["vertex_count"]))
            column.label(text="Triangles: {:,}".format(info["triangle_count"]))
            column.label(
                text="UV map: %s" % (", ".join(info["uv_layers"])
                                     if info["has_uv"] else "NONE"),
                icon='CHECKMARK' if info["has_uv"] else 'ERROR',
            )
            column.label(
                text="Materials: %s" % (", ".join(info["material_slots"])
                                        if info["has_material"] else "NONE"),
                icon='CHECKMARK' if info["has_material"] else 'ERROR',
            )
            column.label(
                text="Image texture: %s" % (", ".join(info["images"])
                                            if info["has_image"] else "NONE"),
                icon='CHECKMARK' if info["has_image"] else 'ERROR',
            )
            provenance = getattr(obj, "bsmt_scan", None)
            if provenance is not None and provenance.is_measurement_copy:
                column.separator()
                column.label(text="This IS a measurement copy of '%s'"
                                  % provenance.source_name, icon='DUPLICATE')
                column.label(text=provenance.representation
                                  or preprocess.REPRESENTATION)

            if info["triangle_count"] > props.dense_threshold_triangles:
                warn = box.column(align=True)
                warn.scale_y = 0.75
                warn.alert = True
                for line in _wrap(preprocess.WARN_DENSE % (
                    "{:,}".format(info["triangle_count"]),
                    "{:,}".format(props.dense_threshold_triangles)), 44
                ):
                    warn.label(text=line)

        layout.prop(props, "preprocess_preset", text="Preset")
        layout.prop(props, "preprocess_target_triangles", text="Target")
        if info is not None and info["triangle_count"] > 0:
            try:
                step = preprocess.plan(props.preprocess_target_triangles,
                                       info["triangle_count"])
                hint = layout.column(align=True)
                hint.scale_y = 0.7
                hint.enabled = False
                for line in _wrap(step["summary"], 46):
                    hint.label(text=line)
            except preprocess.PreprocessError:
                pass

        layout.operator("bsmt.create_measurement_copy", icon='MOD_DECIM')
        layout.operator("bsmt.toggle_measurement_copy", icon='HIDE_OFF')

        safety = layout.box()
        safety.label(text="Solver Safety Gate")
        safety.prop(props, "dense_threshold_triangles", text="Threshold")
        safety.prop(props, "guard_dense_solve")
        note = safety.column(align=True)
        note.scale_y = 0.7
        note.enabled = False
        note.label(text="Non-manifold edges always refuse exact")
        note.label(text="geodesic computation.")

        if props.preprocess_valid and props.preprocess_report:
            layout.separator()
            row = layout.row(align=True)
            row.label(text="Report")
            row.operator("bsmt.clear_preprocess_report", text="", icon='X')
            column = layout.box().column(align=True)
            column.scale_y = 0.7
            for line in props.preprocess_report.split("\n"):
                if line.strip():
                    column.label(text=line)
                else:
                    column.separator()


class BSMT_UL_boundary_loops(bpy.types.UIList):
    bl_idname = "BSMT_UL_boundary_loops"

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index=0, flt_flag=0):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.label(text=item.label,
                         icon='MESH_CIRCLE' if item.closed else 'IPO_LINEAR')
        else:
            layout.label(text="", icon='MESH_CIRCLE')


class BSMT_UL_repair_components(bpy.types.UIList):
    bl_idname = "BSMT_UL_repair_components"

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index=0, flt_flag=0):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            if item.is_largest:
                row.label(text=item.label, icon='CHECKMARK')
            elif item.is_small:
                row.label(text=item.label, icon='ERROR')
            else:
                row.label(text=item.label)
        else:
            layout.label(text="", icon='MESH_DATA')


class BSMT_PT_repair(bpy.types.Panel):
    """Controlled repair of a measurement copy (Milestone 3.4).

    Runs only on a copy generated by Scan Preprocessing, so the source scan is
    never modified. Every repair is an explicit action on a region the
    researcher selected: there is no global cleanup, because a global weld or
    a fill-everything pass fuses anatomically distinct surfaces that touch and
    produces a confidently wrong, systematically short geodesic.
    """

    bl_label = "Mesh Repair"
    bl_idname = "BSMT_PT_repair"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BSMT"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = state.get_props(context)
        if props is None:
            layout.label(text="Add-on state unavailable", icon='ERROR')
            return

        obj = context.active_object
        provenance = getattr(obj, "bsmt_scan", None) if obj is not None else None
        is_copy = provenance is not None and provenance.is_measurement_copy

        box = layout.box()
        if obj is None or obj.type != 'MESH':
            box.label(text="Select a measurement copy", icon='INFO')
            return
        if not is_copy:
            column = box.column(align=True)
            column.scale_y = 0.75
            column.label(text="'%s' is not a measurement copy" % obj.name,
                         icon='ERROR')
            for line in _wrap("Repairs run only on a copy created by Scan "
                              "Preprocessing, so the source scan is never "
                              "modified.", 44):
                column.label(text=line)
            return

        box.label(text="Repairing: %s" % obj.name, icon='DUPLICATE')
        sub = box.row()
        sub.enabled = False
        sub.label(text="copy of '%s'" % provenance.source_name)

        layout.operator("bsmt.analyse_repair", icon='VIEWZOOM')
        if not props.repair_valid or props.repair_object != obj.name:
            layout.label(text="Analyse the mesh to see its diagnostics")
            return

        self._draw_diagnostics(layout, props)
        self._draw_non_manifold(layout, props)
        self._draw_boundaries(layout, props)
        self._draw_components(layout, props)

        layout.separator()
        row = layout.row(align=True)
        row.operator("bsmt.restore_repair_backup", icon='LOOP_BACK')
        row.operator("bsmt.clear_repair_report", text="Clear", icon='X')

        if props.repair_log:
            layout.separator()
            layout.label(text="Repair Log")
            column = layout.box().column(align=True)
            column.scale_y = 0.7
            for line in props.repair_log.split("\n"):
                if line.strip():
                    column.label(text=line)

    @staticmethod
    def _draw_diagnostics(layout, props):
        box = layout.box()
        column = box.column(align=True)
        column.scale_y = 0.75
        for line in props.repair_report.split("\n"):
            if not line.strip():
                column.separator()
                continue
            if line.strip().startswith("BLOCKED"):
                row = column.row()
                row.alert = True
                row.label(text=line)
            else:
                column.label(text=line)

    @staticmethod
    def _draw_non_manifold(layout, props):
        box = layout.box()
        box.label(text="Non-Manifold Edges")
        row = box.row(align=True)
        row.operator("bsmt.show_non_manifold", text="Show", icon='HIDE_OFF')
        row.operator("bsmt.clear_repair_highlight", text="Clear Highlight",
                     icon='X')
        box.operator("bsmt.remove_duplicate_faces", icon='TRASH')
        weld = box.column(align=True)
        weld.prop(props, "repair_weld_distance_mm")
        weld.operator("bsmt.weld_non_manifold", icon='AUTOMERGE_ON')
        note = box.column(align=True)
        note.scale_y = 0.7
        note.enabled = False
        for line in _wrap("The weld touches only the non-manifold edges' own "
                          "vertices. It is never a global merge.", 44):
            note.label(text=line)

    @staticmethod
    def _draw_boundaries(layout, props):
        box = layout.box()
        box.label(text="Boundary Loops (%d)" % len(props.boundary_loops))
        if not len(props.boundary_loops):
            row = box.row()
            row.enabled = False
            row.label(text="none - the surface is closed")
            return
        box.template_list(
            "BSMT_UL_boundary_loops", "",
            props, "boundary_loops", props, "boundary_loop_index",
            rows=4 if len(props.boundary_loops) > 2 else 2,
        )
        row = box.row(align=True)
        row.operator("bsmt.show_boundary_loop", text="Show", icon='HIDE_OFF')
        row.operator("bsmt.fill_boundary_loop", text="Fill Selected",
                     icon='MOD_TRIANGULATE')
        note = box.column(align=True)
        note.scale_y = 0.7
        note.enabled = False
        for line in _wrap("Only the selected loop is filled. A cropped scan's "
                          "open bottom is normal and should be left alone.",
                          44):
            note.label(text=line)

    @staticmethod
    def _draw_components(layout, props):
        box = layout.box()
        box.label(text="Connected Components (%d)"
                       % len(props.repair_components))
        if len(props.repair_components) < 2:
            row = box.row()
            row.enabled = False
            row.label(text="single component")
            return
        box.template_list(
            "BSMT_UL_repair_components", "",
            props, "repair_components", props, "repair_component_index",
            rows=4 if len(props.repair_components) > 2 else 2,
        )
        box.operator("bsmt.remove_small_component", icon='TRASH')
        note = box.column(align=True)
        note.scale_y = 0.7
        note.enabled = False
        for line in _wrap("Nothing is removed automatically. Hair, a garment "
                          "or an accessory can be a legitimate component.",
                          44):
            note.label(text=line)


classes = (
    BSMT_PT_body_measurement,
    BSMT_PT_diagnostics,
    BSMT_PT_geodesic_backend,
    BSMT_UL_landmarks,
    BSMT_PT_landmarks,
    BSMT_UL_measurements,
    BSMT_PT_measurements,
    BSMT_PT_measurement_visualization,
    BSMT_PT_preprocessing,
    BSMT_UL_boundary_loops,
    BSMT_UL_repair_components,
    BSMT_PT_repair,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
