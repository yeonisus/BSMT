"""Operators: modal point picking, distance calculation, clearing."""

import time
import traceback

import numpy as np

import bpy
from bpy.props import EnumProperty, IntProperty

from . import attach, geodesic, measurement, picking, state, visualization

# Events that must keep working while the modal picker is active, so the user
# can orbit / zoom / change the view before committing to a click.
_NAVIGATION_EVENTS = {
    'MIDDLEMOUSE',
    'WHEELUPMOUSE',
    'WHEELDOWNMOUSE',
    'WHEELINMOUSE',
    'WHEELOUTMOUSE',
    'TRACKPADPAN',
    'TRACKPADZOOM',
    'MOUSEROTATE',
    'MOUSESMARTZOOM',
    'NUMPAD_0', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3', 'NUMPAD_4',
    'NUMPAD_5', 'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8', 'NUMPAD_9',
    'NUMPAD_PERIOD', 'NUMPAD_PLUS', 'NUMPAD_MINUS',
}


class BSMT_OT_pick_point(bpy.types.Operator):
    """Click on the mesh surface to store a measurement point"""

    bl_idname = "bsmt.pick_point"
    bl_label = "Pick Point"
    bl_options = {'REGISTER'}

    point: EnumProperty(
        name="Point",
        items=(
            ('A', "Point A", "Pick Point A"),
            ('B', "Point B", "Pick Point B"),
        ),
        default='A',
        options={'SKIP_SAVE'},
    )

    # Runtime-only handles, not properties.
    _area = None
    _region = None
    _space = None

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "BSMT: run this from the 3D Viewport")
            return {'CANCELLED'}

        self._area = context.area
        self._space = context.space_data
        # A button in the sidebar gives us the UI region, not the 3D view.
        self._region = picking.region_from_area(self._area)
        if self._region is None or self._space.region_3d is None:
            self.report({'ERROR'}, "BSMT: could not find the 3D view region")
            return {'CANCELLED'}

        self._set_status(context)
        context.window.cursor_modal_set('EYEDROPPER')
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in _NAVIGATION_EVENTS:
            return {'PASS_THROUGH'}

        if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self._restore(context)
            self.report({'INFO'}, "BSMT: picking cancelled, Point %s unchanged" % self.point)
            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            return self._try_pick(context, event)

        return {'RUNNING_MODAL'}

    def cancel(self, context):
        # Called by Blender if the modal state is aborted externally.
        self._restore(context)

    # -- internals ---------------------------------------------------------

    def _try_pick(self, context, event):
        coord = picking.coord_in_region(self._region, event)
        if coord is None:
            self.report({'WARNING'}, "BSMT: click inside the 3D viewport")
            return {'RUNNING_MODAL'}

        rv3d = self._space.region_3d
        hit = picking.ray_cast_surface(context, self._region, rv3d, coord)
        if hit is None:
            self.report(
                {'WARNING'},
                "BSMT: no mesh surface under the cursor - click on the mesh "
                "(ESC or right click to cancel)",
            )
            return {'RUNNING_MODAL'}

        location, _normal, obj = hit
        props = state.get_props(context)
        if props is None:
            self._restore(context)
            self.report({'ERROR'}, "BSMT: add-on properties are not registered")
            return {'CANCELLED'}

        # Phase 1 state: the exact ray/surface intersection, unchanged.
        state.set_point(props, self.point, location)

        # Phase 2 state: the canonical surface location for the same click.
        # One pick populates both; there is never a separately picked landmark.
        surface_note = self._attach_surface_point(context, props, obj, coord)

        visualization.update_marker(context, props, self.point, location)
        # The previous line (if any) no longer matches the points.
        visualization.remove_line(context)

        self._restore(context)
        self.report(
            {'INFO'} if surface_note.startswith("triangle") else {'WARNING'},
            "BSMT: Point %s set on '%s' at (%.4f, %.4f, %.4f) - %s"
            % (
                self.point,
                obj.name,
                location[0],
                location[1],
                location[2],
                surface_note,
            ),
        )
        return {'FINISHED'}

    def _attach_surface_point(self, context, props, obj, coord):
        """Capture the canonical surface location for this click.

        Uses the canonical BVH, so the triangle index returned directly
        indexes canonical_triangles. Never snaps to a vertex: the stored
        barycentrics reproduce the exact ray/surface intersection.
        """
        state.clear_surface_point(state.surface_point(props, self.point))

        unavailable = geodesic.ensure_loaded()
        if unavailable:
            return "no surface attachment (%s)" % unavailable

        meshcache = geodesic.meshcache
        surface_module = geodesic.surface_point
        try:
            canonical = meshcache.get(context, obj, props.unit)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            return "no surface attachment (canonical mesh failed: %s)" % exc

        rv3d = self._space.region_3d
        origin, direction = picking.world_ray(self._region, rv3d, coord)
        result = meshcache.ray_cast_local(
            canonical,
            canonical.matrix_world,
            np.array(origin, dtype=np.float64),
            np.array(direction, dtype=np.float64),
        )
        if result is None:
            return "no surface attachment (canonical ray cast missed)"

        triangle_index, local_xyz, world_xyz, bary = result
        bary, kind, _index = surface_module.snap(bary)

        reconstructed = canonical.local_from(triangle_index, bary)
        error = float(np.linalg.norm(reconstructed - local_xyz))

        state.set_surface_point(
            props,
            self.point,
            canonical.source_object,
            canonical.geometry_hash,
            triangle_index,
            bary,
            canonical.component_of(triangle_index),
            kind,
            local_xyz,
            world_xyz,
            np.asarray(world_xyz, dtype=np.float64) * canonical.unit_multiplier,
            error,
        )
        return "triangle %d, component %d, %s" % (
            triangle_index,
            canonical.component_of(triangle_index),
            kind,
        )

    def _set_status(self, context):
        text = (
            "BSMT: Left click on the mesh to set Point %s   |   "
            "ESC or Right click: cancel" % self.point
        )
        context.workspace.status_text_set(text)
        if self._area is not None:
            self._area.header_text_set(text)

    def _restore(self, context):
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)
        if self._area is not None:
            self._area.header_text_set(None)
            self._area.tag_redraw()


class BSMT_OT_calculate_distance(bpy.types.Operator):
    """Calculate the straight-line distance between Point A and Point B"""

    bl_idname = "bsmt.calculate_distance"
    bl_label = "Calculate Distance"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return bool(props and props.point_a_valid and props.point_b_valid)

    def execute(self, context):
        props = state.get_props(context)
        if props is None or not (props.point_a_valid and props.point_b_valid):
            self.report({'WARNING'}, "BSMT: pick both Point A and Point B first")
            return {'CANCELLED'}

        props.distance_mm = measurement.straight_distance_mm(
            props.point_a, props.point_b, props.unit
        )
        props.distance_valid = True

        visualization.update_line(context, props, props.point_a, props.point_b)

        self.report(
            {'INFO'},
            "BSMT: straight distance = %s" % measurement.format_mm(props.distance_mm),
        )
        return {'FINISHED'}


class BSMT_OT_validate_surface_points(bpy.types.Operator):
    """Re-validate Point A/B against the current canonical mesh.

    Rebuilds the canonical mesh, compares geometry hashes, refreshes derived
    positions and moves the markers to the stored surface locations
    """

    bl_idname = "bsmt.validate_surface_points"
    bl_label = "Validate / Refresh"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        if props is None:
            return {'CANCELLED'}

        unavailable = geodesic.ensure_loaded()
        if unavailable:
            self.report({'ERROR'}, "BSMT: " + unavailable)
            return {'CANCELLED'}

        meshcache = geodesic.meshcache
        checked = 0
        stale = 0
        moved = False

        for slot in ('A', 'B'):
            point = state.surface_point(props, slot)
            if not point.valid:
                continue
            checked += 1

            obj = bpy.data.objects.get(point.source_object)
            if obj is None or obj.type != 'MESH':
                point.status = "STALE: source object '%s' is missing" % point.source_object
                stale += 1
                continue

            try:
                canonical = meshcache.get(context, obj, props.unit, rebuild=True)
            except Exception as exc:                  # noqa: BLE001
                traceback.print_exc()
                point.status = "STALE: canonical mesh failed (%s)" % exc
                stale += 1
                continue

            if canonical.geometry_hash != point.geometry_hash:
                # Never reproject onto a changed surface.
                point.status = "STALE: geometry changed (%s -> %s)" % (
                    point.geometry_hash[:8], canonical.geometry_hash[:8]
                )
                stale += 1
                continue

            if not 0 <= point.triangle_index < canonical.triangle_count:
                point.status = "STALE: triangle index out of range"
                stale += 1
                continue

            bary = np.array(point.barycentric, dtype=np.float64)
            world = canonical.world_from(point.triangle_index, bary)
            point.world_xyz = tuple(float(v) for v in world)
            point.local_xyz = tuple(
                float(v) for v in canonical.local_from(point.triangle_index, bary)
            )
            point.physical_mm_xyz = tuple(
                float(v) * canonical.unit_multiplier for v in world
            )
            point.component_id = canonical.component_of(point.triangle_index)
            point.status = "VALID"

            # Phase 1 world position follows the surface location, so a marker
            # stays on the same anatomical spot when the scan is transformed.
            state.set_point(props, slot, world)
            visualization.update_marker(context, props, slot, world)
            moved = True

        if moved:
            visualization.remove_line(context)
            if props.point_a_valid and props.point_b_valid:
                props.distance_mm = measurement.straight_distance_mm(
                    props.point_a, props.point_b, props.unit
                )
                props.distance_valid = True
                visualization.update_line(
                    context, props, props.point_a, props.point_b
                )

        if checked == 0:
            self.report({'INFO'}, "BSMT: no surface points to validate")
        else:
            self.report(
                {'WARNING'} if stale else {'INFO'},
                "BSMT: validated %d surface point(s), %d stale" % (checked, stale),
            )
        return {'FINISHED'}


class BSMT_OT_transform_handler_status(bpy.types.Operator):
    """Report whether the transform-following handlers are registered"""

    bl_idname = "bsmt.transform_handler_status"
    bl_label = "Transform Handler Status"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        lines = ["BSMT Transform Handler Status"]

        attach_exact, attach_named = attach.handler_count()
        lines.append(
            "  attach handler:    registered=%s instances=%d name_matches=%d"
            % ("YES" if attach_exact else "NO", attach_exact, attach_named)
        )
        lines.append(
            "  attach persistent: %s"
            % bool(getattr(attach._on_depsgraph_update, "_bpy_persistent", True))
        )

        if geodesic.MESHCACHE_AVAILABLE and geodesic.meshcache is not None:
            cache_exact, cache_named = geodesic.meshcache.handler_count()
            lines.append(
                "  cache handler:     registered=%s instances=%d name_matches=%d"
                % ("YES" if cache_exact else "NO", cache_exact, cache_named)
            )
        else:
            lines.append("  cache handler:     meshcache unavailable")

        total = len(bpy.app.handlers.depsgraph_update_post)
        lines.append("  depsgraph_update_post total handlers: %d" % total)

        stats = attach.STATS
        age = (
            "%.1f s ago" % (time.monotonic() - stats["last_fire"])
            if stats["last_fire"] else "never"
        )
        lines.append("  fires=%d refreshes=%d last_fire=%s"
                     % (stats["fire_count"], stats["refresh_count"], age))
        lines.append("  last outcome: %s" % stats["last_outcome"])
        if stats["last_error"]:
            lines.append("  last error:   %s" % stats["last_error"])

        if props is not None:
            lines.append("  debug logging: %s" % props.transform_debug)
            for slot in ('A', 'B'):
                point = state.surface_point(props, slot)
                if not point.valid:
                    lines.append("  Point %s: not picked" % slot)
                    continue
                obj = bpy.data.objects.get(point.source_object)
                cached = None
                if geodesic.MESHCACHE_AVAILABLE and geodesic.meshcache is not None:
                    cached = geodesic.meshcache.peek(point.source_object)
                lines.append(
                    "  Point %s: object='%s' present=%s canonical_cached=%s "
                    "tri=%d status=%s"
                    % (
                        slot,
                        point.source_object,
                        obj is not None,
                        cached is not None,
                        point.triangle_index,
                        point.status,
                    )
                )

        text = "\n".join(lines)
        print("\n" + text + "\n")
        if props is not None:
            props.component_report = text
            props.component_report_valid = True

        self.report(
            {'WARNING'} if not attach_exact else {'INFO'},
            "BSMT: attach handler %s (%d registered) - details in the system console"
            % ("REGISTERED" if attach_exact else "NOT REGISTERED", attach_exact),
        )
        return {'FINISHED'}


class BSMT_OT_refresh_helpers(bpy.types.Operator):
    """Re-derive helper positions from the stored surface locations"""

    bl_idname = "bsmt.refresh_helpers"
    bl_label = "Refresh Helpers"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        if props is None:
            return {'CANCELLED'}
        outcome = attach.refresh(props, reason="manual")
        self.report({'INFO'}, "BSMT: %s" % outcome)
        return {'FINISHED'}


class BSMT_OT_clear_points(bpy.types.Operator):
    """Remove BSMT markers and the measurement line, and forget both points"""

    bl_idname = "bsmt.clear_points"
    bl_label = "Clear Points"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        removed = visualization.clear_all(context)
        props = state.get_props(context)
        if props is not None:
            state.reset(props)
        self.report({'INFO'}, "BSMT: cleared %d helper object(s)" % removed)
        return {'FINISHED'}


class BSMT_OT_diagnose_topology(bpy.types.Operator):
    """Report the topology of the active mesh. Read-only: never modifies it"""

    bl_idname = "bsmt.diagnose_topology"
    bl_label = "Diagnose Topology"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        props = state.get_props(context)
        obj = context.active_object

        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "BSMT: select a mesh object first")
            return {'CANCELLED'}
        if visualization.is_helper(obj):
            self.report(
                {'ERROR'},
                "BSMT: '%s' is a BSMT helper object, not a scan" % obj.name,
            )
            return {'CANCELLED'}
        # Checked before touching any submodule attribute, so a failed import
        # can never turn into a secondary AttributeError on None.
        # ensure_loaded() also repairs a package left partially initialised by
        # an add-on disable/enable or a Reload Scripts cycle.
        unavailable = geodesic.ensure_loaded()
        if unavailable:
            print("[BSMT] " + unavailable)
            original = geodesic.import_traceback()
            if original:
                print(original)
            self.report({'ERROR'}, "BSMT: " + unavailable)
            return {'CANCELLED'}

        # Past the guard both modules are real; bind them locally so the
        # exception handler never dereferences a possibly-None attribute.
        topology_module = geodesic.topology

        started = time.perf_counter()
        try:
            canonical = geodesic.meshcache.get(context, obj, props.unit)
        except Exception as exc:                      # noqa: BLE001
            if isinstance(exc, geodesic.extract.ExtractionError):
                self.report({'ERROR'}, "BSMT: %s" % exc)
            else:
                traceback.print_exc()
                self.report(
                    {'ERROR'},
                    "BSMT: mesh extraction failed (%s: %s) - traceback in the "
                    "system console" % (type(exc).__name__, exc),
                )
            return {'CANCELLED'}

        report = topology_module.analyse(
            canonical.vertices_solver,
            canonical.triangles,
            near_tolerance=props.near_coincident_mm,
        )
        elapsed = time.perf_counter() - started

        header = [
            "BSMT Topology Diagnostics",
            "  Object:          %s" % canonical.source_object,
            "  Coordinate unit: %s (1 unit = %g mm)"
            % (props.unit, canonical.unit_multiplier),
            "  Solver space:    millimetres, centered, float64",
            "  Canonical triangles: %d" % canonical.triangle_count,
            "  Geometry hash:   %s" % canonical.geometry_hash,
            "  Metric key:      %s" % canonical.metric_key,
        ]
        lines = topology_module.format_report(report, header)
        lines.append("")
        lines.append("Elapsed: %.2f s" % elapsed)
        text = "\n".join(lines)

        props.topology_report = text
        props.topology_object = canonical.source_object
        props.topology_valid = True

        print("\n" + text + "\n")
        warnings = topology_module.warnings_for(report)
        self.report(
            {'WARNING'} if warnings else {'INFO'},
            "BSMT: diagnosed '%s' - %d vertices, %d triangles, %d component(s), "
            "%d warning(s). Full report in the panel and the system console."
            % (
                canonical.source_object,
                report["vertex_count"],
                report["triangle_count"],
                report["component_count"],
                len(warnings),
            ),
        )
        return {'FINISHED'}


class BSMT_OT_visualize_components(bpy.types.Operator):
    """Colour each connected component with temporary BSMT helper objects.

    Diagnostics only: the scan is never modified and components are never
    welded, merged or repaired
    """

    bl_idname = "bsmt.visualize_components"
    bl_label = "Visualize Components"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        props = state.get_props(context)
        obj = context.active_object

        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "BSMT: select a mesh object first")
            return {'CANCELLED'}
        if visualization.is_helper(obj):
            self.report(
                {'ERROR'},
                "BSMT: '%s' is a BSMT helper object, not a scan" % obj.name,
            )
            return {'CANCELLED'}

        unavailable = geodesic.ensure_loaded() or geodesic.preview_error()
        if unavailable:
            print("[BSMT] " + unavailable)
            original = geodesic.import_traceback()
            if original:
                print(original)
            self.report({'ERROR'}, "BSMT: " + unavailable)
            return {'CANCELLED'}

        topology_module = geodesic.topology
        preview_module = geodesic.preview

        started = time.perf_counter()
        try:
            canonical = geodesic.meshcache.get(context, obj, props.unit)
        except Exception as exc:                      # noqa: BLE001
            if isinstance(exc, geodesic.extract.ExtractionError):
                self.report({'ERROR'}, "BSMT: %s" % exc)
            else:
                traceback.print_exc()
                self.report(
                    {'ERROR'},
                    "BSMT: mesh extraction failed (%s: %s) - traceback in the "
                    "system console" % (type(exc).__name__, exc),
                )
            return {'CANCELLED'}

        # Labels come from the canonical mesh: same triangle array, no remap.
        triangle_labels = canonical.triangle_components
        triangle_counts = canonical.component_triangle_counts
        component_count = len(triangle_counts)
        if component_count == 0:
            self.report({'WARNING'}, "BSMT: no triangles to visualise")
            return {'CANCELLED'}

        # Independently re-verify the labelling before drawing anything.
        # Misleading colours are worse than no colours.
        rows, problems = topology_module.verify_component_labels(
            canonical.vertices_solver,
            canonical.triangles,
            triangle_labels,
            triangle_counts,
        )
        report_lines = topology_module.format_verification(
            rows,
            problems,
            ["BSMT Component Integrity", "  Object: %s" % canonical.source_object],
        )
        props.component_report = "\n".join(report_lines)
        props.component_report_valid = True
        print("\n" + props.component_report + "\n")

        if problems:
            self.report(
                {'ERROR'},
                "BSMT: component integrity check failed (%d problem(s)) - "
                "preview refused. See the Diagnostics panel and the system "
                "console." % len(problems),
            )
            return {'CANCELLED'}

        # Helper geometry is placed in world space with an identity transform,
        # so it overlays the scan whatever the scan's own transform is.
        world_vertices = canonical.world_vertices()

        try:
            created = preview_module.build(
                context,
                world_vertices,
                canonical.triangles,
                triangle_labels,
                component_count,
                expected_counts=triangle_counts,
            )
        except preview_module.PreviewIntegrityError as exc:
            self.report(
                {'ERROR'}, "BSMT: preview integrity check failed - %s" % exc
            )
            return {'CANCELLED'}
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report(
                {'ERROR'},
                "BSMT: component preview failed (%s: %s) - traceback in the "
                "system console" % (type(exc).__name__, exc),
            )
            return {'CANCELLED'}

        state.clear_component_preview_state(props)
        for index, triangles, vertices, color in created:
            item = props.components.add()
            item.index = index + 1            # displayed 1-based
            item.triangle_count = triangles
            item.vertex_count = vertices
            item.color = color
        props.component_preview_valid = True
        props.component_preview_object = canonical.source_object
        props.component_isolate = 0

        elapsed = time.perf_counter() - started
        lines = ["BSMT Component Preview", "  Object: %s" % canonical.source_object]
        for index, triangles, vertices, _color in created:
            lines.append(
                "  Component %d: %d triangles, %d vertices"
                % (index + 1, triangles, vertices)
            )
        lines.append("  Elapsed: %.2f s" % elapsed)
        print("\n" + "\n".join(lines) + "\n")

        self.report(
            {'INFO'},
            "BSMT: previewing %d component(s) of '%s' in %.2f s"
            % (len(created), canonical.source_object, elapsed),
        )
        return {'FINISHED'}


class BSMT_OT_verify_components(bpy.types.Operator):
    """Independently verify component labelling against the canonical triangles.

    Development diagnostic. Re-extracts each component from the canonical
    triangle array and recomputes its connectivity from scratch
    """

    bl_idname = "bsmt.verify_components"
    bl_label = "Verify Components"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        props = state.get_props(context)
        obj = context.active_object

        if obj is None or obj.type != 'MESH' or visualization.is_helper(obj):
            self.report({'ERROR'}, "BSMT: select the scan mesh first")
            return {'CANCELLED'}

        unavailable = geodesic.ensure_loaded()
        if unavailable:
            self.report({'ERROR'}, "BSMT: " + unavailable)
            return {'CANCELLED'}

        topology_module = geodesic.topology

        started = time.perf_counter()
        try:
            canonical = geodesic.meshcache.get(context, obj, props.unit, rebuild=True)
            labels = canonical.triangle_components
            counts = canonical.component_triangle_counts
            rows, problems = topology_module.verify_component_labels(
                canonical.vertices_solver, canonical.triangles, labels, counts
            )
            # Cross-check against the independent analyse() path.
            summary = topology_module.analyse(
                canonical.vertices_solver, canonical.triangles
            )
            if summary["component_triangle_counts"] != counts:
                problems.append(
                    "analyse() and component_labels() disagree: %s vs %s"
                    % (summary["component_triangle_counts"], counts)
                )
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report(
                {'ERROR'},
                "BSMT: verification failed (%s: %s) - traceback in the system "
                "console" % (type(exc).__name__, exc),
            )
            return {'CANCELLED'}

        elapsed = time.perf_counter() - started
        header = [
            "BSMT Component Integrity",
            "  Object:        %s" % canonical.source_object,
            "  Canonical triangles: %d" % canonical.triangle_count,
            "  Canonical vertices:  %d" % canonical.vertex_count,
            "  Geometry hash: %s" % canonical.geometry_hash,
        ]
        lines = topology_module.format_verification(rows, problems, header)
        lines.append("")
        lines.append("Elapsed: %.2f s" % elapsed)
        props.component_report = "\n".join(lines)
        props.component_report_valid = True
        print("\n" + props.component_report + "\n")

        self.report(
            {'ERROR'} if problems else {'INFO'},
            "BSMT: %d component(s) verified in %.2f s - %s"
            % (
                len(rows),
                elapsed,
                "%d PROBLEM(S)" % len(problems) if problems else "all checks passed",
            ),
        )
        return {'FINISHED'}


class BSMT_OT_clear_component_preview(bpy.types.Operator):
    """Remove the component preview helper objects. The scan is untouched"""

    bl_idname = "bsmt.clear_component_preview"
    bl_label = "Clear Component Preview"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        removed = 0
        preview_module = getattr(geodesic, "preview", None)
        if preview_module is not None:
            removed = preview_module.clear(context)
        if props is not None:
            state.clear_component_preview_state(props)
        self.report({'INFO'}, "BSMT: removed %d component preview object(s)" % removed)
        return {'FINISHED'}


class BSMT_OT_isolate_component(bpy.types.Operator):
    """Show only this component, or all of them when index is 0"""

    bl_idname = "bsmt.isolate_component"
    bl_label = "Isolate Component"
    bl_options = {'REGISTER'}

    index: IntProperty(
        name="Component",
        description="Component to isolate; 0 shows every component",
        default=0,
        min=0,
        options={'SKIP_SAVE'},
    )

    def execute(self, context):
        props = state.get_props(context)
        if props is None:
            return {'CANCELLED'}
        # Assignment triggers the property update, which applies visibility.
        props.component_isolate = self.index
        return {'FINISHED'}


class BSMT_OT_check_geodesic_env(bpy.types.Operator):
    """Report the Python environment Blender is actually running (dev tool).

    Milestone 2.2. Read-only: it inspects the interpreter and reports the
    exact pip command for it. It installs nothing and modifies nothing
    """

    bl_idname = "bsmt.check_geodesic_env"
    bl_label = "Check Environment"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        geodesic.ensure_loaded()

        unavailable = geodesic.environment_error()
        if unavailable:
            print("[BSMT] " + unavailable)
            self.report({'ERROR'}, "BSMT: " + unavailable)
            return {'CANCELLED'}

        try:
            info = geodesic.envreport.collect()
            lines = geodesic.envreport.format_report(info)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report(
                {'ERROR'},
                "BSMT: environment report failed (%s: %s) - traceback in the "
                "system console" % (type(exc).__name__, exc),
            )
            return {'CANCELLED'}

        text = "\n".join(lines)
        if props is not None:
            props.env_report = text
            props.env_report_valid = True

        print("\n" + text + "\n")

        # A failed backend import must reach the console complete, never
        # summarised: an ABI or architecture problem is only diagnosable from
        # the original traceback.
        original = geodesic.backend_import_traceback()
        if original:
            print("[BSMT] original backend import traceback:")
            print(original)

        available = geodesic.backend_available()
        level = 'INFO' if available and not info["mismatches"] else 'WARNING'
        self.report(
            {level},
            "BSMT: %s, Python %s, %s %s, numpy %s - pygeodesic %s. Full report "
            "in the panel and the system console."
            % (
                info["blender_version"] or "outside Blender",
                info["python_version"],
                info["system"],
                info["machine"],
                info["numpy_version"] or "MISSING",
                "available" if available else "UNAVAILABLE",
            ),
        )
        return {'FINISHED'}


class BSMT_OT_run_backend_selftest(bpy.types.Operator):
    """Run the synthetic exact-geodesic backend proof (dev tool).

    Milestone 2.2. Uses generated meshes only. It does not read Point A or
    Point B, does not touch the scan, and produces no measurement result
    """

    bl_idname = "bsmt.run_backend_selftest"
    bl_label = "Run Synthetic Backend Tests"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        geodesic.ensure_loaded()

        if geodesic.backends is None:
            message = geodesic.backend_error()
            print("[BSMT] " + message)
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        blocked = geodesic.backends.selftest_error()
        if blocked:
            print("[BSMT] " + blocked)
            self.report({'ERROR'}, "BSMT: " + blocked)
            return {'CANCELLED'}

        if not geodesic.backend_available():
            # Not a crash and not a silent skip: the reason is reported with
            # the original import error, and no substitute result is produced.
            message = geodesic.backend_error()
            original = geodesic.backend_import_traceback()
            print("[BSMT] " + message)
            if original:
                print(original)
            if props is not None:
                props.backend_test_report = "\n".join([
                    "BSMT Milestone 2.2 - exact geodesic backend self-test",
                    "",
                    "NOT RUN: " + message,
                    "",
                    "BSMT loads and Phase 1 measurement works without this",
                    "backend. Install it with the command shown by",
                    "Check Environment, then run this again.",
                ])
                props.backend_test_valid = True
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        selftest = geodesic.backends.selftest
        dense = True if props is None else bool(props.backend_test_dense)
        triangles = (
            selftest.REFERENCE_SCAN_TRIANGLES if props is None
            else int(props.backend_test_triangles)
        )
        dijkstra = True if props is None else bool(props.backend_test_dijkstra)

        started = time.perf_counter()
        try:
            report = selftest.run_all(
                include_dense=dense,
                dense_triangles=triangles,
                include_dijkstra=dijkstra,
            )
            lines = selftest.format_report(report)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report(
                {'ERROR'},
                "BSMT: backend self-test failed (%s: %s) - traceback in the "
                "system console" % (type(exc).__name__, exc),
            )
            return {'CANCELLED'}
        elapsed = time.perf_counter() - started

        text = "\n".join(lines)
        if props is not None:
            props.backend_test_report = text
            props.backend_test_valid = True
        print("\n" + text + "\n")

        for suite in report.get("suites", []):
            if suite.get("traceback"):
                print("[BSMT] %s raised:" % suite.get("name"))
                print(suite["traceback"])

        passed = bool(report.get("pass"))
        self.report(
            {'INFO'} if passed else {'ERROR'},
            "BSMT: backend self-test %s in %.2f s (%d suites). Full report in "
            "the panel and the system console."
            % ("PASSED" if passed else "FAILED", elapsed,
               len(report.get("suites", []))),
        )
        return {'FINISHED'}


class BSMT_OT_clear_backend_reports(bpy.types.Operator):
    """Clear the Milestone 2.2 environment and backend test reports"""

    bl_idname = "bsmt.clear_backend_reports"
    bl_label = "Clear Backend Reports"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        if props is not None:
            state.clear_backend_reports(props)
        return {'FINISHED'}


class BSMT_OT_clear_topology(bpy.types.Operator):
    """Clear the topology diagnostics report"""

    bl_idname = "bsmt.clear_topology"
    bl_label = "Clear Report"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        if props is not None:
            state.clear_topology(props)
        return {'FINISHED'}


classes = (
    BSMT_OT_pick_point,
    BSMT_OT_calculate_distance,
    BSMT_OT_validate_surface_points,
    BSMT_OT_transform_handler_status,
    BSMT_OT_refresh_helpers,
    BSMT_OT_clear_points,
    BSMT_OT_diagnose_topology,
    BSMT_OT_clear_topology,
    BSMT_OT_visualize_components,
    BSMT_OT_verify_components,
    BSMT_OT_clear_component_preview,
    BSMT_OT_isolate_component,
    BSMT_OT_check_geodesic_env,
    BSMT_OT_run_backend_selftest,
    BSMT_OT_clear_backend_reports,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
