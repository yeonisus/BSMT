"""Sidebar UI: View3D > Sidebar (N) > BSMT.

User-visible wording follows one vocabulary throughout (Milestone 3.7):

    Landmark            a named point the researcher places on the surface
    Reference Point     one of the four anatomical references used to align
    Point A / Point B   the two ad-hoc points of Quick Measure, which is a
                        separate tool from the Landmark Manager
    Source Mesh         the imported scan, which BSMT never modifies
    Measurement Mesh    the lighter textured copy measurements run on
    Straight Distance   the straight line between two landmarks
    Surface Distance    the exact geodesic distance across the surface
    Surface Path        the polyline that distance follows
    Calculate           produce a distance
    Compute             produce a surface path

Spelling is US English in the UI ("Analyze", "Color", "Visualization") even
where the code around it is written in British English.
"""

import math

import bpy

from . import (alignment, export, geodesic, landmarks, measurement,
               measurements, overlay, preprocess, repair, scancopy, state,
               timing, visualization, viz)


class BSMT_PT_body_measurement(bpy.types.Panel):
    bl_label = "Quick Measure (A to B)"
    bl_idname = "BSMT_PT_body_measurement"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BSMT"

    def draw(self, context):
        layout = self.layout
        props = state.get_props(context)
        if props is None:
            layout.label(text="BSMT is not registered", icon='ERROR')
            return

        _draw_readiness(context, layout, props)

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
                text="Straight Distance:  %s" % measurement.format_mm(props.distance_mm)
            )
        else:
            box.label(text="Straight Distance:  not calculated")

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
            column.label(text="Surface Distance:   recalculate needed",
                         icon='ERROR')
            for line in _wrap(problem, 44):
                column.label(text="   " + line)
        elif props.surface_status:
            column = box.column(align=True)
            column.scale_y = 0.7
            for line in _wrap(props.surface_status, 44):
                column.label(text=line)
        else:
            box.label(text="Surface Distance:   not calculated")

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
        row.operator("bsmt.validate_surface_points", text="Validate Points",
                     icon='FILE_REFRESH')
        row.operator("bsmt.refresh_helpers", text="Refresh Markers",
                     icon='CON_LOCLIKE')
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
            text="Point %s: %s" % (slot, "picked" if valid else "not picked"),
            icon='CHECKMARK' if valid else 'BLANK1',
        )
        if valid:
            sub = layout.row()
            sub.enabled = False
            sub.label(
                text="   (%.3f, %.3f, %.3f)"
                % (location[0], location[1], location[2])
            )


def _addon_version():
    from . import VERSION
    return ".".join(str(part) for part in VERSION)


def _about_lines():
    """Environment summary, or a one-line reason it could not be read."""
    try:
        return geodesic.envreport.about_lines()
    except Exception as exc:                          # pragma: no cover
        return ["environment unavailable: %s" % exc]


def _draw_readiness(context, layout, props):
    """One compact line: can this scan be measured, and if not, why (sect. 13).

    Deliberately a pointer, not a second diagnostics report. It names the
    first blocker and the panel that explains it; the detail stays where it
    already lives.
    """
    result = state.readiness_snapshot(context, props)
    row = layout.row(align=True)
    row.alert = result["blocked"]
    row.prop(
        props, "show_readiness",
        icon='TRIA_DOWN' if props.show_readiness else 'TRIA_RIGHT',
        emboss=False, text="",
    )
    row.label(text=result["headline"], icon=result["icon"])
    if not props.show_readiness:
        return result
    detail = layout.box().column(align=True)
    detail.scale_y = 0.75
    if not result["reasons"]:
        detail.label(text="Topology, landmarks and measurements are all in "
                          "order.")
    for entry in result["reasons"]:
        line = detail.row()
        line.alert = entry["blocking"]
        line.label(text="%s%s" % (entry["text"],
                                  (" - see %s" % entry["panel"])
                                  if entry["panel"] else ""),
                   icon='ERROR' if entry["blocking"] else 'DOT')
    return result


def _draw_measurement_target(context, layout, props):
    """Which mesh measurements actually run on (sect. 11).

    A measurement mesh sits exactly on top of the scan it was copied from, so
    the two are visually indistinguishable in the viewport. Naming the target
    here is the only way a researcher can tell which one a result belongs to.
    """
    obj, reason = state.measurement_target(context, props)
    box = layout.box()
    column = box.column(align=True)
    column.scale_y = 0.75
    if obj is None:
        column.label(text="Measurement Mesh: %s" % reason, icon='ERROR')
        return

    provenance = getattr(obj, "bsmt_scan", None)
    is_copy = bool(provenance is not None and provenance.is_measurement_copy)
    column.label(text="Measurement Mesh: %s" % obj.name,
                 icon='DUPLICATE' if is_copy else 'MESH_DATA')
    if is_copy and provenance.source_name:
        column.label(text="Source Mesh:      %s" % provenance.source_name)

    cached = (geodesic.meshcache.peek(obj.name)
              if geodesic.MESHCACHE_AVAILABLE else None)
    if cached is None:
        column.label(text="Topology:         not analyzed yet")
        return
    report = cached.topology or {}
    column.label(text="Triangles:        {:,}".format(
        int(report.get("triangle_count", 0) or 0)))
    non_manifold = int(report.get("nonmanifold_edge_count", 0) or 0)
    status = column.row()
    status.alert = non_manifold > 0
    status.label(
        text="Topology:         %s"
             % ("Ready" if non_manifold == 0
                else "%d non-manifold edge(s)" % non_manifold)
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

    bl_label = "Mesh Diagnostics"
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
            layout.label(text="BSMT is not registered", icon='ERROR')
            return

        obj = context.active_object
        name = obj.name if obj is not None else "-"
        layout.label(text="Selected: %s" % name)

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
            layout.label(text="No topology report yet")
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
        layout.label(text="Connected Components")

        preview_problem = geodesic.preview_error()
        if preview_problem:
            column = layout.box().column(align=True)
            column.scale_y = 0.7
            column.label(text="Unavailable", icon='ERROR')
            for line in _wrap(preview_problem, 46):
                column.label(text=line)
            return

        row = layout.row(align=True)
        row.operator("bsmt.visualize_components", text="Show Components",
                     icon='COLOR')
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
        box.label(text="Showing: %s" % props.component_preview_object)

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

    bl_label = "Geodesic Backend (Developer)"
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
            column.label(text="Straight-line measurement still works.")
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
                warn.label(text="The dense benchmark freezes Blender while it runs.",
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


#: Short status text for the landmark list. Defined once in landmarks.py so
#: the panel and the viewport overlay cannot disagree about what a status is
#: called.
_STATUS_SHORT = landmarks.STATUS_SHORT


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
            layout.label(text="BSMT is not registered", icon='ERROR')
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
        row.operator("bsmt.add_landmark", text="Add Landmark", icon='ADD')
        delete = row.row(align=True)
        delete.scale_x = 0.35
        delete.operator("bsmt.remove_landmark", text="", icon='REMOVE')

        if not len(collection):
            empty = layout.box().column(align=True)
            empty.scale_y = 0.8
            empty.label(text="No landmarks defined.", icon='INFO')
            empty.label(text="Add a landmark, then pick it on the surface.")

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
        layout.operator("bsmt.clear_landmarks", text="Delete All Landmarks",
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
            text="Repick Landmark" if item.surface_point.valid
                 else "Pick Landmark",
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
            column.label(text="   Mesh:      %s" % point.source_object)
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
        box.label(text="Picking %d of %d: %s"
                       % (props.guided_index + 1, total, label),
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
        """Landmark Display (Milestone 3.8). Every control here is cosmetic.

        Nothing in this section can move a landmark, change a distance or
        touch the mesh - which is why it is safe to leave open while working.
        """
        box = layout.box()
        header = box.row(align=True)
        header.prop(
            props, "show_landmark_display",
            icon=('TRIA_DOWN' if props.show_landmark_display
                  else 'TRIA_RIGHT'),
            emboss=False, text="Landmark Display",
        )
        if not props.show_landmark_display:
            return

        row = box.row(align=True)
        row.prop(props, "show_landmarks", toggle=False)
        row.prop(props, "show_landmark_labels", toggle=False)

        box.prop(props, "landmark_visibility", text="Visibility")

        marker = box.column(align=True)
        marker.enabled = bool(props.show_landmarks)
        marker.prop(props, "landmark_marker_color")
        marker.prop(props, "landmark_marker_size_px")

        label = box.column(align=True)
        label.enabled = bool(props.show_landmark_labels)
        label.prop(props, "landmark_label_color")
        label.prop(props, "landmark_label_size")
        label.prop(props, "landmark_label_offset")
        label.prop(props, "landmark_label_shadow")
        label.prop(props, "landmark_label_scope")

        note = box.column(align=True)
        note.scale_y = 0.7
        note.enabled = False
        for line in _wrap("Marker and label sizes are in screen pixels, so "
                          "every landmark stays the same size at any zoom.",
                          44):
            note.label(text=line)
        if props.landmark_visibility == 'OCCLUDED':
            for line in _wrap("Visible Surface Only hides a landmark while "
                              "the mesh is in front of it.", 44):
                note.label(text=line)
        if ((props.show_landmarks or props.show_landmark_labels)
                and not overlay.is_registered()):
            warn = box.row()
            warn.alert = True
            warn.label(text="Landmark overlay is not running", icon='ERROR')

    @staticmethod
    def _draw_protocol(layout, props):
        layout.separator()
        layout.label(text="Landmark Protocol (names and order only)")
        row = layout.row(align=True)
        row.operator("bsmt.load_protocol", text="Load", icon='IMPORT')
        row.operator("bsmt.save_protocol", text="Save", icon='EXPORT')
        note = layout.column(align=True)
        note.scale_y = 0.7
        note.enabled = False
        note.label(text="A protocol carries names, not positions.")


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
        label.label(text=item.name or "(new measurement)")
        kind = top.row()
        kind.alignment = 'RIGHT'
        kind.scale_x = 0.55
        kind.enabled = False
        kind.label(text=measurements.TYPE_LABELS.get(
            item.measurement_type, item.measurement_type))
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
        if item.status == measurements.STATUS_DRAFT:
            # A draft has no pair to name yet. "? -> ?" would read like a
            # broken measurement rather than an unfinished one.
            pair.label(text="   not defined yet")
        else:
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
            layout.label(text="BSMT is not registered", icon='ERROR')
            return

        if props.measurement_protocol_name:
            row = layout.row()
            row.enabled = False
            row.label(text="Template: %s" % props.measurement_protocol_name)

        if props.measurement_running and props.measurement_progress:
            box = layout.box()
            box.label(text=props.measurement_progress, icon='TIME')

        _draw_measurement_target(context, layout, props)

        layout.template_list(
            "BSMT_UL_measurements", "",
            context.scene, "bsmt_measurements",
            props, "measurement_index",
            rows=6 if len(collection) > 3 else 3,
        )

        row = layout.row(align=True)
        row.operator("bsmt.add_measurement", text="Add Measurement",
                     icon='ADD')
        delete = row.row(align=True)
        delete.scale_x = 0.35
        delete.operator("bsmt.remove_measurement", text="", icon='REMOVE')

        if not len(collection):
            # Sect. 9: an empty list says so. A blank row is never created
            # just to give the panel something to draw.
            empty = layout.box().column(align=True)
            empty.scale_y = 0.8
            empty.label(text="No measurements defined.", icon='INFO')
            empty.label(text="Add one, then choose its From and To landmarks.")
            self._draw_template(layout)
            return

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
        if plan["drafts"]:
            info.label(text="%d unfinished, will be skipped" % plan["drafts"])

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
                     text="Delete Unresolved", icon='CANCEL')
        row.operator("bsmt.clear_measurements", text="Delete All",
                     icon='TRASH')

    @staticmethod
    def _draw_selected(context, layout, props, collection):
        index = props.measurement_index
        if not 0 <= index < len(collection):
            layout.box().label(text="No measurement selected")
            return
        item = collection[index]
        draft = state.measurement_is_draft(item)
        box = layout.box()

        header = box.row(align=True)
        header.prop(
            props, "show_measurement_detail",
            icon='TRIA_DOWN' if props.show_measurement_detail else 'TRIA_RIGHT',
            emboss=False,
            text="%s: %s" % ("New measurement" if draft else "Selected",
                             item.protocol_id if draft else item.label),
        )
        if not props.show_measurement_detail:
            return

        if draft:
            # Sect. 6/7: this row is not a measurement yet, and the panel says
            # so plainly rather than letting an unfinished entry look real.
            hint = box.column(align=True)
            hint.scale_y = 0.8
            hint.label(text="Not defined yet.", icon='GREASEPENCIL')
            for line in _wrap(item.status_detail
                              or "Choose a From and a To landmark.", 42):
                hint.label(text=line)

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
            hint.label(text="   Auto Name follows From and To")

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

        if draft:
            # Sect. 8: an unfinished row can be discarded in one click, so
            # nothing has to be hunted down in the list and deleted.
            box.operator("bsmt.cancel_measurement_draft", icon='X')
        else:
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
            detail.label(text="Straight Distance: %s"
                              % measurement.format_mm(item.straight_mm))
        if item.surface_valid:
            detail.label(text="Surface Distance:  %s"
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
        rows = state.defined_measurements(collection)
        if not rows:
            # Sect. 6/9: a draft is not a measurement, so it has no result to
            # show. An empty results list says exactly that.
            note = layout.box().column(align=True)
            note.scale_y = 0.8
            note.label(text="No measurements defined.", icon='INFO')
            drafts = len(collection) - len(rows)
            if drafts:
                note.label(text="%d unfinished - choose From and To to "
                                "complete %s"
                                % (drafts, "it" if drafts == 1 else "them"))
            return

        box = layout.box()
        for item in rows:
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
            kind.label(text=measurements.TYPE_LABELS.get(
                item.measurement_type, item.measurement_type))

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
                    entry.label(text="   Straight  %s"
                                     % measurement.format_mm(item.straight_mm))
                if item.surface_valid:
                    entry.label(text="   Surface   %s"
                                     % measurement.format_mm(item.surface_mm))
                if (item.straight_valid and item.surface_valid
                        and item.ratio):
                    entry.label(text="   Ratio     %.4f" % item.ratio)
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
        note.label(text="A template carries definitions, not results.")


class BSMT_PT_session(bpy.types.Panel):
    """Session metadata, CSV export and protocol reuse (Milestone 3.11).

    Everything here is about the RECORD, not the measurement. The session
    fields are metadata that no geometry or calculation reads; the exports
    write what has already been computed; the protocol carries definitions
    between subjects. Nothing in this panel can change a number.
    """

    bl_label = "Session and Export"
    bl_idname = "BSMT_PT_session"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BSMT"
    bl_parent_id = "BSMT_PT_measurements"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = state.get_props(context)
        if props is None:
            layout.label(text="BSMT is not registered", icon='ERROR')
            return

        box = layout.box()
        box.label(text="Session Info")
        column = box.column(align=True)
        column.prop(props, "session_subject_id")
        column.prop(props, "session_condition")
        column.prop(props, "session_scan_id")
        box.prop(props, "session_notes")
        note = box.row()
        note.enabled = False
        note.scale_y = 0.7
        note.label(text="Metadata only. Never affects a measurement.")

        box = layout.box()
        box.label(text="Export")
        row = box.row(align=True)
        row.operator("bsmt.export_measurements", icon='EXPORT')
        row.operator("bsmt.export_landmarks", icon='EXPORT')
        preview = box.column(align=True)
        preview.scale_y = 0.7
        preview.enabled = False
        obj = state.export_object(context, props)
        preview.label(text=export.default_filename(
            "measurements",
            subject_id=props.session_subject_id,
            condition=props.session_condition,
            scan_id=props.session_scan_id,
            fallback=obj.name if obj is not None else ""))
        if props.export_report:
            report = box.column(align=True)
            report.scale_y = 0.7
            for line in props.export_report.split("\n"):
                if line.strip():
                    for wrapped in _wrap(line, 44):
                        report.label(text=wrapped)

        box = layout.box()
        header = box.row(align=True)
        header.prop(
            props, "show_about",
            icon='TRIA_DOWN' if props.show_about else 'TRIA_RIGHT',
            emboss=False, text="About BSMT")
        if props.show_about:
            column = box.column(align=True)
            column.scale_y = 0.75
            column.label(text="BSMT %s" % _addon_version())
            for line in _about_lines():
                column.label(text=line)
            note = box.column(align=True)
            note.scale_y = 0.7
            note.enabled = False
            for line in _wrap("Copy these lines into a bug report. They say "
                              "which platform and solver build produced a "
                              "measurement.", 44):
                note.label(text=line)

        box = layout.box()
        box.label(text="Protocol")
        if props.protocol_name:
            current = box.row()
            current.enabled = False
            current.label(text=props.protocol_name)
        row = box.row(align=True)
        row.operator("bsmt.save_study_protocol", icon='EXPORT')
        row.operator("bsmt.load_study_protocol", icon='IMPORT')
        note = box.column(align=True)
        note.scale_y = 0.7
        note.enabled = False
        for line in _wrap("A protocol holds definitions only, and replaces "
                          "the current ones. Nothing arrives positioned.", 44):
            note.label(text=line)


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
            layout.label(text="BSMT is not registered", icon='ERROR')
            return

        item = state.active_measurement(context, props)
        box = layout.box()
        if item is None:
            box.label(text="No measurement selected")
            return
        # The Measurement Manager's selection IS the visualisation target.
        # There is deliberately no second selection system.
        if state.measurement_is_draft(item):
            box.label(text="%s is not defined yet" % item.protocol_id,
                      icon='GREASEPENCIL')
        else:
            box.label(text="Selected: %s  %s \u2192 %s" % (
                item.protocol_id,
                item.source_name or item.source_protocol_id or "?",
                item.target_name or item.target_protocol_id or "?",
            ))

        layout.prop(props, "viz_scope")
        layout.prop(props, "viz_mode")
        if props.viz_scope == 'TICKED':
            layout.prop(item, "show_visualization",
                        text="Show This Measurement")

        self._draw_scope_report(context, layout, props)
        self._draw_path_controls(context, layout, props, item)

        style = layout.box()
        style.label(text="Straight Distance")
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
    def _draw_scope_report(context, layout, props):
        """What is on screen right now, and what has no path yet (sect. 10).

        Nothing here computes anything. A measurement without a cached path is
        NAMED rather than solved for, so widening the scope to ten
        measurements can never start ten solves - and the ones that do have a
        cached path are still drawn.
        """
        report = viz.display_report(context, props)
        box = layout.box().column(align=True)
        box.scale_y = 0.75
        if not report["count"]:
            box.label(text="Nothing to display in this scope.", icon='INFO')
            return
        box.label(text="Displaying %d measurement%s"
                       % (report["count"],
                          "" if report["count"] == 1 else "s"),
                  icon='HIDE_OFF')
        if not report["path_wanted"]:
            return
        if report["stale"]:
            stale = box.column(align=True)
            stale.alert = True
            stale.label(text="Path stale (not recomputed):", icon='ERROR')
            for label in report["stale"][:6]:
                stale.label(text="   %s" % label)
            if len(report["stale"]) > 6:
                stale.label(text="   and %d more" % (len(report["stale"]) - 6))
        if report["without_path"]:
            missing = box.column(align=True)
            missing.label(text="Path not computed:", icon='INFO')
            for label in report["without_path"][:6]:
                missing.label(text="   %s" % label)
            if len(report["without_path"]) > 6:
                missing.label(text="   and %d more"
                                   % (len(report["without_path"]) - 6))
        if not report["stale"] and not report["without_path"]:
            return
        note = box.column(align=True)
        note.enabled = False
        for line in _wrap("Select one and press Compute Surface Path. "
                          "Nothing is computed automatically, and a stale "
                          "path is never recomputed behind your back.", 42):
            note.label(text=line)

    @staticmethod
    def _draw_path_controls(context, layout, props, item):
        """The surface path's state, in words, and the three buttons.

        The state is always NAMED - NOT COMPUTED, CACHED, STALE or INVALID -
        because a path is expensive enough that "why is nothing drawn" must
        never need guessing, and because a stale path has to be visibly stale
        rather than quietly re-solved (sect. 9).
        """
        box = layout.box()
        if props.viz_running:
            box.label(text="Computing the surface path...", icon='TIME')
            return

        current, reason = viz.path_state(context, props, item)
        label = state.PATH_STATE_LABELS.get(current, current)
        icon = {
            state.PATH_CACHED: 'CHECKMARK',
            state.PATH_STALE: 'ERROR',
            state.PATH_INVALID: 'CANCEL',
        }.get(current, 'INFO')
        header = box.row()
        header.alert = current in (state.PATH_STALE, state.PATH_INVALID)
        header.label(text="Surface Path:  %s" % label, icon=icon)
        if reason:
            detail = box.column(align=True)
            detail.scale_y = 0.7
            detail.enabled = False
            for line in _wrap(reason, 44):
                detail.label(text=line)

        if current == state.PATH_CACHED:
            column = box.column(align=True)
            column.scale_y = 0.75
            column.label(text="Points:          %d" % item.path_point_count)
            column.label(text="Path length:     %s"
                              % measurement.format_mm(item.path_length_mm))
            column.label(text="Solver distance: %s"
                              % measurement.format_mm(item.path_distance_mm))
            column.label(text="Stored surface:  %s"
                              % measurement.format_mm(item.surface_mm))
            column.label(text="Agreement:       %.3e mm"
                              % item.path_agreement_mm)
            column.label(text="Solve time:      %.2f s" % item.path_elapsed_s)
        elif current == state.PATH_NOT_COMPUTED:
            note = box.column(align=True)
            note.scale_y = 0.75
            if not item.surface_valid:
                note.label(text="Calculate the surface distance first.")
            else:
                for line in _wrap("Computing the path runs the unbounded "
                                  "exact solve and may freeze Blender for "
                                  "tens of seconds.", 42):
                    note.label(text=line)
        elif current == state.PATH_STALE:
            note = box.column(align=True)
            note.scale_y = 0.75
            for line in _wrap("The cached path has been kept but is not "
                              "drawn. Nothing was recomputed - press "
                              "Compute Surface Path if you want it solved "
                              "again.", 42):
                note.label(text=line)

        # Three buttons, three separate decisions. Computing is the only one
        # that can ever reach the solver; the other two are display and
        # cache management and are instant whatever the mesh size.
        row = box.row(align=True)
        row.operator(
            "bsmt.compute_surface_path",
            text=("Recompute Surface Path" if current != state.PATH_NOT_COMPUTED
                  else "Compute Surface Path"),
            icon=('FILE_REFRESH' if current != state.PATH_NOT_COMPUTED
                  else 'PLAY'),
        )
        row = box.row(align=True)
        row.operator(
            "bsmt.toggle_surface_path",
            text="Show Path" if not item.path_shown else "Hide Path",
            icon='HIDE_OFF' if not item.path_shown else 'HIDE_ON',
        )
        row.operator("bsmt.clear_cached_path", icon='TRASH')

        if props.viz_status:
            status = box.column(align=True)
            status.scale_y = 0.7
            for line in _wrap(props.viz_status, 42):
                status.label(text=line)

        BSMT_PT_measurement_visualization._draw_timing(box, props)

    @staticmethod
    def _draw_timing(layout, props):
        """Where the time actually went. Read from the ring buffer only.

        Nothing is measured by drawing this - the samples were recorded when
        the work happened - so opening the section cannot itself cost
        anything, and it is what distinguishes a slow solver from a slow
        curve rebuild from handler churn.
        """
        box = layout.box()
        box.prop(props, "show_timing",
                 icon='TRIA_DOWN' if props.show_timing else 'TRIA_RIGHT',
                 emboss=False)
        if not props.show_timing:
            return
        box.prop(props, "timing_debug")
        totals = timing.totals()
        if not totals:
            box.label(text="Nothing timed yet.", icon='INFO')
            return
        column = box.column(align=True)
        column.scale_y = 0.7
        column.enabled = False
        for label in sorted(totals, key=lambda key: -totals[key][1]):
            count, total = totals[label]
            column.label(text="%-16s %4dx %9.2f ms" % (label, count, total))
        box.operator("bsmt.path_timing_report", icon='CONSOLE')


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
            layout.label(text="BSMT is not registered", icon='ERROR')
            return

        obj = context.active_object
        info = scancopy.describe(obj) if obj is not None else None

        box = layout.box()
        if info is None:
            box.label(text="Select a mesh scan to preprocess", icon='INFO')
        else:
            column = box.column(align=True)
            column.scale_y = 0.75
            column.label(text="Selected: %s" % info["name"])
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
                column.label(text="This is a measurement mesh", icon='DUPLICATE')
                column.label(text="Source Mesh: %s" % provenance.source_name)
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
        note.label(text="A non-manifold mesh is always refused,")
        note.label(text="whatever this threshold is set to.")

        if props.preprocess_valid and props.preprocess_report:
            layout.separator()
            row = layout.row(align=True)
            row.label(text="Preprocessing Report")
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
            layout.label(text="BSMT is not registered", icon='ERROR')
            return

        obj = context.active_object
        provenance = getattr(obj, "bsmt_scan", None) if obj is not None else None
        is_copy = provenance is not None and provenance.is_measurement_copy

        box = layout.box()
        if obj is None or obj.type != 'MESH':
            box.label(text="Select a measurement mesh", icon='INFO')
            return
        if not is_copy:
            column = box.column(align=True)
            column.scale_y = 0.75
            column.label(text="'%s' is not a measurement mesh" % obj.name,
                         icon='ERROR')
            for line in _wrap("Repairs run only on a mesh created by Scan "
                              "Preprocessing, so the source mesh is never "
                              "modified.", 44):
                column.label(text=line)
            return

        box.label(text="Measurement Mesh: %s" % obj.name, icon='DUPLICATE')
        sub = box.row()
        sub.enabled = False
        sub.label(text="Source Mesh: %s" % provenance.source_name)

        layout.operator("bsmt.analyse_repair", icon='VIEWZOOM')
        if not props.repair_valid or props.repair_object != obj.name:
            layout.label(text="Analyze the mesh to see its diagnostics")
            return

        # Automatic repair. Nothing here runs by opening the panel: geometry
        # changes only on an explicit press.
        auto = layout.box()
        auto.label(text="Automatic Repair", icon='SHADERFX')
        auto.operator("bsmt.auto_repair_local", icon='MODIFIER')
        auto.operator("bsmt.auto_repair_boundaries", icon='MOD_TRIANGULATE')
        note = auto.column(align=True)
        note.scale_y = 0.7
        note.enabled = False
        for line in _wrap("Only small localised artefacts. Every attempt is "
                          "validated and reverted if it does not improve the "
                          "topology.", 44):
            note.label(text=line)

        self._draw_diagnostics(layout, props)
        self._draw_non_manifold(layout, props)
        self._draw_boundaries(layout, props)
        self._draw_components(layout, props)

        layout.separator()
        row = layout.row(align=True)
        row.operator("bsmt.restore_repair_backup", text="Undo Repair",
                     icon='LOOP_BACK')
        row.operator("bsmt.clear_repair_report", text="Clear Log", icon='X')

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
        row.operator("bsmt.show_non_manifold", text="Show Edges",
                     icon='HIDE_OFF')
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
        row.operator("bsmt.show_boundary_loop", text="Show Loop",
                     icon='HIDE_OFF')
        row.operator("bsmt.fill_boundary_loop", text="Fill Loop",
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
            row.label(text="one component - nothing to remove")
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


class BSMT_PT_alignment(bpy.types.Panel):
    """Rigid anatomical alignment (Milestone 3.6).

    Object transforms only - the mesh is never touched, so landmarks and
    stored distances stay valid through any alignment.
    """

    bl_label = "Alignment"
    bl_idname = "BSMT_PT_alignment"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BSMT"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = state.get_props(context)
        if props is None:
            layout.label(text="BSMT is not registered", icon='ERROR')
            return

        obj = context.active_object
        names = state.align_objects(props)
        target_name = (sorted(names)[0] if len(names) == 1
                       else (obj.name if obj is not None else "-"))

        box = layout.box()
        if obj is None or obj.type != 'MESH':
            box.label(text="Select a mesh object to align", icon='INFO')
            return
        column = box.column(align=True)
        column.scale_y = 0.75
        column.label(text="Object: %s" % target_name)
        column.label(text="Location: %.1f, %.1f, %.1f"
                          % tuple(obj.location))
        column.label(text="Rotation: %.1f, %.1f, %.1f deg"
                          % tuple(math.degrees(v) for v in obj.rotation_euler))
        column.label(text="Scale:    %.4f, %.4f, %.4f" % tuple(obj.scale))
        report = alignment.scale_report(obj.matrix_world)
        if not report["unity"]:
            warn = box.column(align=True)
            warn.scale_y = 0.75
            warn.alert = not report["uniform"]
            for line in _wrap(report["message"], 44):
                warn.label(text=line, icon='ERROR')
            if not report["uniform"]:
                for line in _wrap("Alignment is refused on a non-uniformly "
                                  "scaled scan. Apply the scale first.", 44):
                    warn.label(text=line)

        note = box.column(align=True)
        note.scale_y = 0.7
        note.enabled = False
        for line in _wrap(alignment.AXIS_DESCRIPTION, 44):
            note.label(text=line)

        layout.prop(props, "align_mode", text="Mode")
        if props.align_mode == 'MANUAL':
            self._draw_manual(layout, props)
        else:
            self._draw_landmark(layout, props)

        layout.separator()
        row = layout.row(align=True)
        row.operator("bsmt.flip_front_back", icon='ARROW_LEFTRIGHT')
        row.operator("bsmt.reset_alignment", icon='LOOP_BACK')

        status = layout.box()
        status.label(text="Status: %s"
                          % ("Aligned (%s)" % props.align_method.title()
                             if props.align_applied else "Not aligned"),
                     icon='CHECKMARK' if props.align_applied else 'BLANK1')
        if props.align_report:
            column = status.column(align=True)
            column.scale_y = 0.7
            for line in props.align_report.split("\n"):
                if line.strip():
                    column.label(text=line)

    @staticmethod
    def _draw_manual(layout, props):
        box = layout.box()
        box.label(text="Rotate by 90 degrees")
        for axis in ('X', 'Y', 'Z'):
            row = box.row(align=True)
            minus = row.operator("bsmt.manual_align", text="%s -90" % axis)
            minus.axis = axis
            minus.degrees = -90.0
            minus.action = 'ROTATE'
            plus = row.operator("bsmt.manual_align", text="%s +90" % axis)
            plus.axis = axis
            plus.degrees = 90.0
            plus.action = 'ROTATE'

        box.separator()
        box.prop(props, "align_fine_degrees")
        row = box.row(align=True)
        for axis in ('X', 'Y', 'Z'):
            fine = row.operator("bsmt.manual_align", text="%s" % axis)
            fine.axis = axis
            fine.degrees = props.align_fine_degrees
            fine.action = 'ROTATE'

        origin = box.operator("bsmt.manual_align", text="Move To Origin",
                              icon='OBJECT_ORIGIN')
        origin.action = 'ORIGIN'

    @staticmethod
    def _draw_landmark(layout, props):
        box = layout.box()
        box.label(text="Reference Points")
        for slot, label in (('LEFT', "LEFT"), ('RIGHT', "RIGHT"),
                            ('SUPERIOR', "SUPERIOR"),
                            ('INFERIOR', "INFERIOR")):
            point = state.align_point(props, slot)
            row = box.row(align=True)
            name = row.row()
            name.scale_x = 0.9
            name.label(text=label,
                       icon='CHECKMARK' if point.valid else 'BLANK1')
            pick = row.operator("bsmt.pick_alignment_reference",
                                text="Repick" if point.valid else "Pick",
                                icon='EYEDROPPER')
            pick.slot = slot

        # Sect. 5: short enough to fit the sidebar, and it says what to click
        # rather than restating the theory.
        help_text = box.column(align=True)
        help_text.scale_y = 0.7
        help_text.enabled = False
        for line in _wrap("LEFT / RIGHT: matching points on each side, at "
                          "about the same height.", 44):
            help_text.label(text=line)
        for line in _wrap("SUPERIOR / INFERIOR: an upper and a lower point, "
                          "near the body midline.", 44):
            help_text.label(text=line)
        for line in _wrap("Left and right are the SUBJECT'S, not the "
                          "viewer's.", 44):
            help_text.label(text=line)

        ready, missing = state.align_points_ready(props)
        if not ready:
            hint = box.row()
            hint.enabled = False
            hint.label(text="Still to pick: %s" % ", ".join(missing))
        box.operator("bsmt.clear_alignment_references",
                     text="Clear Reference Points", icon='X')

        column = layout.column(align=True)
        row = column.row(align=True)
        row.operator("bsmt.preview_alignment", text="Preview Axes",
                     icon='HIDE_OFF')
        row.operator("bsmt.clear_alignment_preview", text="Clear Preview",
                     icon='X')
        column.prop(props, "align_move_to_origin")
        column.operator("bsmt.apply_alignment", icon='CON_ROTLIKE')
        if props.align_residual_degrees:
            BSMT_PT_alignment._draw_residual(column,
                                             props.align_residual_degrees)

    @staticmethod
    def _draw_residual(layout, degrees):
        """The residual, with a plain-language reading (sect. 12).

        Labelled as guidance on the face of it. The bands are a usability aid,
        not a validated anthropometric criterion, and nothing is refused or
        adjusted because of them - so the panel says so rather than letting
        three tidy verdicts imply a standard that does not exist.
        """
        verdict, advice, severity = alignment.quality(degrees)
        box = layout.box().column(align=True)
        box.scale_y = 0.75
        row = box.row()
        row.alert = severity >= 2
        row.label(text="Reference angle: %.1f deg off perpendicular" % degrees,
                  icon=('CHECKMARK' if severity == 0
                        else 'INFO' if severity == 1 else 'ERROR'))
        box.label(text="   %s - %s" % (verdict, advice))
        note = box.row()
        note.enabled = False
        note.label(text="   (UI guidance, not a validated threshold)")


classes = (
    BSMT_PT_body_measurement,
    BSMT_PT_diagnostics,
    BSMT_PT_geodesic_backend,
    BSMT_UL_landmarks,
    BSMT_PT_landmarks,
    BSMT_UL_measurements,
    BSMT_PT_measurements,
    BSMT_PT_session,
    BSMT_PT_measurement_visualization,
    BSMT_PT_preprocessing,
    BSMT_PT_alignment,
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
