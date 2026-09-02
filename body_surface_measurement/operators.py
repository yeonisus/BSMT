"""Operators: modal point picking, distance calculation, clearing."""

import time
import traceback

import numpy as np

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import (attach, geodesic, landmarks, measurement, measurements,
               picking, protocol, state, visualization)

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

    # Milestone 3.0. Additive, and 'AB' is the default, so every existing
    # caller and the whole Phase 1 workflow behave exactly as before. The
    # picking ALGORITHM is untouched: the same viewport ray, the same
    # canonical BVH cast and the same SurfacePoint construction serve both
    # targets, so there is only one picking implementation (sect. 5).
    target: EnumProperty(
        name="Target",
        items=(
            ('AB', "A/B Point", "Store into the Phase 1 A/B slot"),
            ('LANDMARK', "Named Landmark",
             "Store into the selected named research landmark"),
        ),
        default='AB',
        options={'SKIP_SAVE'},
    )
    landmark_index: IntProperty(
        name="Landmark Index",
        description="Row in scene.bsmt_landmarks to store into",
        default=-1,
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
            self.report(
                {'INFO'},
                "BSMT: picking cancelled, %s unchanged"
                % ("the selected landmark" if self.target == 'LANDMARK'
                   else "Point %s" % self.point),
            )
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

        if self.target == 'LANDMARK':
            return self._pick_landmark(context, props, obj, coord, location)

        # Phase 1 state: the exact ray/surface intersection, unchanged.
        state.set_point(props, self.point, location)

        # Phase 2 state: the canonical surface location for the same click.
        # One pick populates both; there is never a separately picked landmark.
        surface_note = self._attach_surface_point(
            context, props, obj, coord, state.surface_point(props, self.point)
        )

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

    def _pick_landmark(self, context, props, obj, coord, location):
        """Store this click into the selected named landmark.

        Only the landmark is touched: A/B, the straight line and any stored
        surface distance are left exactly as they were (sect. 6, sect. 15).
        """
        collection = state.get_landmarks(context)
        index = self.landmark_index if self.landmark_index >= 0 else props.landmark_index
        if collection is None or not 0 <= index < len(collection):
            self._restore(context)
            self.report({'ERROR'}, "BSMT: no landmark selected to pick into")
            return {'CANCELLED'}

        item = collection[index]
        surface_note = self._attach_surface_point(
            context, props, obj, coord, item.surface_point
        )
        if not surface_note.startswith("triangle"):
            # A click that produced no canonical attachment is not a pick.
            # Nothing is stored and guided picking does not advance.
            state.clear_landmark_position(item, context)
            self.report({'WARNING'}, "BSMT: %s" % surface_note)
            return {'RUNNING_MODAL'}

        canonical = geodesic.meshcache.peek(item.surface_point.source_object)
        state.refresh_landmark_status(item, canonical)
        visualization.update_landmark_marker(
            context, props, item, item.surface_point.world_xyz
        )
        # Targeted, not a sweep: only the definitions that reference THIS
        # landmark lose their result (sect. 13).
        affected = state.invalidate_measurements_for_landmark(
            context, item.stable_id, "landmark '%s' was re-picked" % item.label
        )
        if affected:
            print("[BSMT] re-picking '%s' invalidated %d measurement result(s)"
                  % (item.label, affected))

        self._restore(context)
        advanced = ""
        if props.guided_active:
            advanced = " | " + _guided_advance(context, props)
        self.report(
            {'INFO'},
            "BSMT: landmark '%s' set on '%s' - %s%s"
            % (item.label, obj.name, surface_note, advanced),
        )
        return {'FINISHED'}

    def _attach_surface_point(self, context, props, obj, coord, point):
        """Capture the canonical surface location for this click into `point`.

        Uses the canonical BVH, so the triangle index returned directly
        indexes canonical_triangles. Never snaps to a vertex: the stored
        barycentrics reproduce the exact ray/surface intersection.

        `point` is any BSMT_SurfacePoint - an A/B slot or a landmark's nested
        one. The pipeline is identical either way; only the destination
        differs, so there is one picking implementation, not two (sect. 5).
        """
        state.clear_surface_point(point)

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

        state.fill_surface_point(
            point,
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
        if point is state.surface_point(props, 'A') or point is state.surface_point(props, 'B'):
            # A/B consequence, unchanged from Phase 2: a re-picked A/B point
            # invalidates the stored surface distance. A landmark pick must
            # not, so this is decided by the destination, not by the caller.
            state.clear_surface_result(props)
            props.surface_status = ""
        return "triangle %d, component %d, %s" % (
            triangle_index,
            canonical.component_of(triangle_index),
            kind,
        )

    def _set_status(self, context):
        if self.target == 'LANDMARK':
            label = "landmark"
            collection = state.get_landmarks(context)
            props = state.get_props(context)
            index = (self.landmark_index if self.landmark_index >= 0
                     else (props.landmark_index if props else -1))
            if collection is not None and 0 <= index < len(collection):
                label = "'%s'" % collection[index].label
            text = (
                "BSMT: Left click on the mesh to set %s   |   "
                "ESC or Right click: cancel" % label
            )
        else:
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


class BSMT_OT_calculate_surface_distance(bpy.types.Operator):
    """Exact geodesic (surface) distance between Point A and Point B.

    Runs the MMP exact backend on a scratch mesh with both landmarks inserted
    as real vertices. The scan and the canonical mesh are never modified. This
    can block Blender for several seconds on a dense scan
    """

    bl_idname = "bsmt.calculate_surface_distance"
    bl_label = "Calculate Surface Distance"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or props.surface_running:
            return False
        return bool(props.surface_a.valid and props.surface_b.valid)

    def execute(self, context):
        props = state.get_props(context)
        if props is None:
            self.report({'ERROR'}, "BSMT: add-on properties are not registered")
            return {'CANCELLED'}

        # Guard against a second solve starting while one is already running.
        # The solve is a blocking C++ call, so a double-click during it would
        # otherwise queue a second full propagation.
        if props.surface_running:
            self.report({'WARNING'}, "BSMT: a surface solve is already running")
            return {'CANCELLED'}

        unavailable = geodesic.ensure_loaded()
        if unavailable:
            state.set_surface_failure(props, "Surface distance unavailable: " + unavailable)
            self.report({'ERROR'}, "BSMT: " + unavailable)
            return {'CANCELLED'}

        broken = geodesic.measure_error()
        if broken:
            state.set_surface_failure(props, broken)
            self.report({'ERROR'}, "BSMT: " + broken)
            return {'CANCELLED'}

        solve = geodesic.solve
        registry = geodesic.registry

        point_a = props.surface_a
        point_b = props.surface_b
        if not (point_a.valid and point_b.valid):
            message = solve.failure_message('POINTS_MISSING')
            state.set_surface_failure(props, message)
            self.report({'WARNING'}, "BSMT: " + message)
            return {'CANCELLED'}

        if not registry.available():
            message = solve.failure_message(
                'BACKEND_MISSING', registry.unavailable_reason()
            )
            state.set_surface_failure(props, message)
            print("[BSMT] " + message)
            original = geodesic.backend_import_traceback()
            if original:
                print(original)
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        obj = bpy.data.objects.get(point_a.source_object)
        if obj is None or obj.type != 'MESH':
            message = solve.failure_message(
                'STALE_POINTS',
                "source object '%s' is missing" % point_a.source_object,
            )
            state.set_surface_failure(props, message)
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        props.surface_running = True
        self._set_status(context, obj)
        try:
            return self._solve(context, props, obj, solve, registry)
        finally:
            props.surface_running = False
            self._restore(context)

    # -- internals ---------------------------------------------------------

    def _solve(self, context, props, obj, solve, registry):
        started = time.perf_counter()
        try:
            canonical = geodesic.meshcache.get(context, obj, props.unit)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            message = solve.failure_message(
                'STALE_POINTS', "canonical mesh unavailable (%s)" % exc
            )
            state.set_surface_failure(props, message)
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        specs = []
        for slot in ('A', 'B'):
            point = state.surface_point(props, slot)
            specs.append(solve.PointSpec(
                point.triangle_index,
                np.array(point.barycentric, dtype=np.float64),
                component_id=point.component_id,
                source_object=point.source_object,
                geometry_hash=point.geometry_hash,
                status=point.status,
                valid=point.valid,
            ))

        try:
            # Solver space is physical millimetres (sect. 6.2), so the value
            # returned here IS millimetres. No unit multiplication is applied
            # to it anywhere downstream; doing so twice is a named failure mode.
            result = solve.surface_distance(
                canonical.vertices_solver,
                canonical.triangles,
                specs[0],
                specs[1],
                geometry_hash=canonical.geometry_hash,
            )
        except solve.MeasurementError as exc:
            state.set_surface_failure(props, exc.message)
            print("[BSMT] %s" % exc.message)
            self.report({'ERROR'}, "BSMT: " + exc.message)
            return {'CANCELLED'}
        except Exception as exc:                      # noqa: BLE001
            # No backend exception may reach the user as a Blender traceback.
            traceback.print_exc()
            message = solve.failure_message(
                'BACKEND_ERROR', "%s: %s" % (type(exc).__name__, exc)
            )
            state.set_surface_failure(props, message)
            self.report({'ERROR'}, "BSMT: " + message + " - see the system console")
            return {'CANCELLED'}

        elapsed = time.perf_counter() - started
        self._store(props, canonical, obj, result, registry, elapsed)

        self.report(
            {'INFO'},
            "BSMT: surface distance = %s (straight %s, ratio %.4f) | %s"
            % (
                measurement.format_mm(result.distance_mm),
                measurement.format_mm(result.straight_mm),
                result.ratio,
                result.summary(),
            ),
        )
        return {'FINISHED'}

    @staticmethod
    def _store(props, canonical, obj, result, registry, elapsed):
        props.surface_distance_mm = float(result.distance_mm)
        props.surface_straight_mm = float(result.straight_mm)
        props.surface_ratio = float(result.ratio)
        props.surface_mode = result.mode
        props.surface_summary = result.summary()
        props.surface_status = ""
        props.surface_valid = True

        props.surface_object = canonical.source_object
        props.surface_geometry_hash = canonical.geometry_hash
        props.surface_metric_key = canonical.metric_key
        props.surface_metric_tensor = state.metric_tensor(
            obj.matrix_world, canonical.unit_multiplier
        )
        props.surface_component_id = int(result.component_id)

        report = result.report
        props.surface_backend_name = (
            report.backend_name if report is not None else registry.backend_name()
        )
        props.surface_backend_version = (
            report.backend_version if report is not None else registry.backend_version()
        )
        props.surface_algorithm_version = registry.ALGORITHM_VERSION
        props.surface_bound_factor = (
            float(report.bound_factor)
            if report is not None and report.bound_factor is not None
            else 0.0
        )
        props.surface_unbounded_fallback = bool(
            report.unbounded_fallback if report is not None else False
        )
        props.surface_attempts = int(report.attempts) if report is not None else 0
        props.surface_seconds = float(elapsed)
        props.surface_insertion_seconds = float(result.insertion_seconds)
        props.surface_solver_seconds = float(result.solver_seconds)

        record = registry.provenance(
            report,
            canonical.geometry_hash,
            canonical.metric_key,
            result.component_id,
            props.unit,
            canonical.source_object,
        ) if report is not None else None

        lines = [
            "Backend:        %s %s" % (props.surface_backend_name,
                                       props.surface_backend_version or "-"),
            "Algorithm:      %s" % props.surface_algorithm_version,
            "Mode:           %s" % result.mode,
            "Source object:  %s" % canonical.source_object,
            "Geometry hash:  %s" % canonical.geometry_hash,
            "Metric key:     %s" % canonical.metric_key,
            "Component:      %d" % result.component_id,
            "Coordinate unit:%s (1 unit = %g mm)" % (props.unit,
                                                     canonical.unit_multiplier),
            "Endpoint kinds: A=%s B=%s" % result.endpoint_kinds,
            "Scratch mesh:   %d vertices (+%d), %d triangles"
            % (result.scratch_vertex_count, result.added_vertex_count,
               result.scratch_triangle_count),
            "Straight:       %.6f mm" % result.straight_mm,
            "Surface:        %.6f mm" % result.distance_mm,
            "Ratio:          %.6f" % result.ratio,
            "Insertion:      %.3f s" % result.insertion_seconds,
            "Solver:         %.3f s" % result.solver_seconds,
            "Total:          %.3f s" % elapsed,
        ]
        if record is not None:
            lines.append("Bound factor:   %s"
                         % ("unbounded fallback"
                            if record["unbounded_fallback"]
                            else "%.2fx" % record["bound_factor"]))
            lines.append("Bound used:     %.3f mm" % record["bound_mm"]
                         if record["bound_mm"] not in (None, float("inf"))
                         else "Bound used:     unbounded")
            lines.append("Attempts:       %d" % record["attempts"])
            for factor, bound, seconds, reached in report.attempt_log:
                lines.append(
                    "  attempt %-10s bound %-12s %6.3f s  %s"
                    % ("unbounded" if factor is None else "%.2fx" % factor,
                       "inf" if bound == float("inf") else "%.2f mm" % bound,
                       seconds,
                       "reached" if reached else "not reached"))
            lines.append("Preprocessing:  weld=%s, %s"
                         % (record["preprocessing"]["weld"],
                            record["preprocessing"]["endpoint_insertion"]))
        else:
            lines.append("Bound factor:   n/a (analytic short-circuit)")
        props.surface_provenance = "\n".join(lines)
        print("\n[BSMT] Surface measurement\n" + props.surface_provenance + "\n")

    def _set_status(self, context, obj):
        text = ("BSMT: solving exact surface distance on '%s' - Blender may "
                "not redraw until it finishes" % obj.name)
        try:
            context.workspace.status_text_set(text)
            if context.area is not None:
                context.area.header_text_set(text)
                context.area.tag_redraw()
        except Exception:                             # pragma: no cover
            pass
        print("[BSMT] " + text)

    def _restore(self, context):
        try:
            context.workspace.status_text_set(None)
            if context.area is not None:
                context.area.header_text_set(None)
                context.area.tag_redraw()
        except Exception:                             # pragma: no cover
            pass


class BSMT_OT_clear_surface_distance(bpy.types.Operator):
    """Clear the stored surface distance. Leaves the points and the straight
    distance untouched"""

    bl_idname = "bsmt.clear_surface_distance"
    bl_label = "Clear Surface Distance"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        if props is not None:
            state.clear_surface_result(props)
            props.surface_status = ""
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

            # One implementation of the stale rule, shared with the Landmark
            # Manager (sect. 16), so A/B and named landmarks can never
            # disagree about what "stale" means.
            reason = landmarks.stale_reason(
                point.geometry_hash,
                point.triangle_index,
                canonical.geometry_hash,
                canonical.triangle_count,
            )
            if reason:
                # Never reproject onto a changed surface.
                point.status = "STALE: " + reason
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


# ---------------------------------------------------------------------------
# Landmark Manager (Milestone 3.0)
# ---------------------------------------------------------------------------


def _guided_targets(context, props):
    """Indices guided picking will visit, in list order."""
    collection = state.get_landmarks(context)
    if not collection:
        return []
    if props.guided_skip_valid:
        return [
            index for index, item in enumerate(collection)
            if item.status != landmarks.STATUS_VALID
        ]
    return list(range(len(collection)))


def _guided_advance(context, props):
    """Move guided picking to the next target. Returns a short note."""
    targets = _guided_targets(context, props)
    if not targets:
        props.guided_active = False
        return "guided picking complete"
    collection = state.get_landmarks(context)
    current = props.landmark_index
    following = [index for index in targets if index > current]
    if not following:
        props.guided_active = False
        return "guided picking complete"
    props.landmark_index = following[0]
    props.guided_index = targets.index(following[0])
    return "next: %s" % collection[following[0]].label


class BSMT_OT_add_landmark(bpy.types.Operator):
    """Add a named research landmark. It starts NOT PICKED"""

    bl_idname = "bsmt.add_landmark"
    bl_label = "Add Landmark"
    bl_options = {'REGISTER', 'UNDO'}

    landmark_name: StringProperty(
        name="Name",
        description="Anatomical landmark name. Any name you like; BSMT never "
                    "assumes a naming convention",
        default="",
    )
    notes: StringProperty(name="Notes", default="")
    allow_duplicate: BoolProperty(
        name="Allow Duplicate Name",
        description="Add the landmark even though the name is already used. "
                    "It is given a numeric suffix so the two stay "
                    "distinguishable",
        default=False,
    )

    def invoke(self, context, event):
        collection = state.get_landmarks(context)
        count = len(collection) + 1 if collection is not None else 1
        if not self.landmark_name:
            self.landmark_name = "P%02d" % count
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        props = state.get_props(context)
        if props is None:
            self.report({'ERROR'}, "BSMT: add-on properties are not registered")
            return {'CANCELLED'}
        try:
            item = state.add_landmark(
                context, props, self.landmark_name, notes=self.notes,
                allow_duplicate=self.allow_duplicate,
            )
        except landmarks.LandmarkError as exc:
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}
        self.report({'INFO'}, "BSMT: added landmark '%s' (%s)"
                    % (item.name, item.protocol_id))
        return {'FINISHED'}


class BSMT_OT_remove_landmark(bpy.types.Operator):
    """Delete the selected landmark and its marker"""

    bl_idname = "bsmt.remove_landmark"
    bl_label = "Delete Landmark"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return state.active_landmark(context) is not None

    def execute(self, context):
        props = state.get_props(context)
        item = state.active_landmark(context, props)
        if item is None:
            return {'CANCELLED'}
        name = item.label
        stable_id = state.remove_landmark(context, props, props.landmark_index)
        if stable_id is not None:
            visualization.remove_landmark_marker(stable_id)
        self.report({'INFO'}, "BSMT: deleted landmark '%s'" % name)
        return {'FINISHED'}


class BSMT_OT_clear_landmark_position(bpy.types.Operator):
    """Forget the selected landmark's surface location, keeping its name"""

    bl_idname = "bsmt.clear_landmark_position"
    bl_label = "Clear Position"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        item = state.active_landmark(context)
        return item is not None and item.surface_point.valid

    def execute(self, context):
        props = state.get_props(context)
        item = state.active_landmark(context, props)
        if item is None:
            return {'CANCELLED'}
        state.clear_landmark_position(item, context)
        visualization.remove_landmark_marker(item.stable_id)
        self.report({'INFO'}, "BSMT: cleared position of '%s'" % item.label)
        return {'FINISHED'}


class BSMT_OT_clear_landmarks(bpy.types.Operator):
    """Delete every named landmark. A/B, the topology preview and the scan
    are not affected"""

    bl_idname = "bsmt.clear_landmarks"
    bl_label = "Clear Landmark Data"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        collection = state.get_landmarks(context)
        return bool(collection)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        props = state.get_props(context)
        collection = state.get_landmarks(context)
        count = len(collection) if collection else 0
        stale_ids = [int(item.stable_id) for item in collection]
        state.clear_landmarks(context, props)
        removed = visualization.clear_landmark_markers()
        for stable_id in stale_ids:
            state.invalidate_measurements_for_landmark(
                context, stable_id, "all landmarks were cleared"
            )
        for item in state.get_measurements(context) or ():
            source, target = state.resolve_measurement_landmarks(context, item)
            if source is None or target is None:
                item.status = measurements.STATUS_INVALID_REFERENCE
                item.status_detail = "the referenced landmark no longer exists"
        self.report({'INFO'},
                    "BSMT: cleared %d landmark(s), removed %d marker(s). "
                    "A/B and the scan are untouched." % (count, removed))
        return {'FINISHED'}


class BSMT_OT_validate_landmarks(bpy.types.Operator):
    """Re-check every landmark against the current canonical mesh.

    Refreshes transform-dependent coordinates and marks stale landmarks. A
    stale landmark is never silently re-projected onto changed geometry
    """

    bl_idname = "bsmt.validate_landmarks"
    bl_label = "Validate All Landmarks"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(state.get_landmarks(context))

    def execute(self, context):
        props = state.get_props(context)
        collection = state.get_landmarks(context)
        if props is None or collection is None:
            return {'CANCELLED'}

        geodesic.ensure_loaded()
        meshcache = geodesic.meshcache if geodesic.MESHCACHE_AVAILABLE else None

        # One canonical mesh per source object, not per landmark (sect. 9).
        wanted = {
            item.surface_point.source_object
            for item in collection
            if item.surface_point.valid and item.surface_point.source_object
        }
        canonicals = {}
        for name in sorted(wanted):
            obj = bpy.data.objects.get(name)
            if obj is None or obj.type != 'MESH':
                canonicals[name] = None
                continue
            if meshcache is None:
                canonicals[name] = None
                continue
            try:
                canonicals[name] = meshcache.get(context, obj, props.unit,
                                                 rebuild=True)
            except Exception as exc:                  # noqa: BLE001
                traceback.print_exc()
                print("[BSMT] canonical mesh failed for '%s': %s" % (name, exc))
                canonicals[name] = None

        statuses = []
        for item in collection:
            point = item.surface_point
            canonical = canonicals.get(point.source_object)
            obj = bpy.data.objects.get(point.source_object)
            status = state.refresh_landmark_status(
                item, canonical, object_exists=obj is not None
            )
            statuses.append(status)

            if status == landmarks.STATUS_VALID and canonical is not None:
                # Refresh transform-dependent coordinates only. The canonical
                # location itself is never touched.
                bary = np.array(point.barycentric, dtype=np.float64)
                world = canonical.world_from(point.triangle_index, bary)
                point.local_xyz = tuple(
                    float(v) for v in canonical.local_from(point.triangle_index, bary)
                )
                point.world_xyz = tuple(float(v) for v in world)
                point.physical_mm_xyz = tuple(
                    float(v) * canonical.unit_multiplier for v in world
                )
                point.component_id = canonical.component_of(point.triangle_index)
                visualization.update_landmark_marker(context, props, item, world)
            elif point.valid:
                # Keep the marker where it is, recoloured to show it can no
                # longer be trusted. Never re-projected.
                visualization.update_landmark_marker(
                    context, props, item, point.world_xyz
                )

        visualization.remove_orphan_landmark_markers(
            [item.stable_id for item in collection]
        )
        visualization.apply_landmark_display(context, props)

        counts, summary = landmarks.summarise(statuses)
        props.landmark_summary = summary
        problems = (counts.get(landmarks.STATUS_STALE, 0)
                    + counts.get(landmarks.STATUS_INVALID, 0))
        self.report({'WARNING'} if problems else {'INFO'},
                    "BSMT: %s" % summary)
        return {'FINISHED'}


class BSMT_OT_pick_landmark(bpy.types.Operator):
    """Pick the selected landmark on the mesh surface.

    Thin launcher: the actual picking is the same modal operator, the same
    viewport ray and the same canonical BVH pipeline the A/B workflow uses
    """

    bl_idname = "bsmt.pick_landmark"
    bl_label = "Pick Selected"
    bl_options = {'REGISTER'}

    index: IntProperty(default=-1, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and state.active_landmark(context) is not None)

    def execute(self, context):
        props = state.get_props(context)
        if self.index >= 0:
            props.landmark_index = self.index
        return bpy.ops.bsmt.pick_point(
            'INVOKE_DEFAULT', target='LANDMARK',
            landmark_index=props.landmark_index,
        )


class BSMT_OT_guided_picking(bpy.types.Operator):
    """Start, step or cancel guided picking through the landmark list.

    Guided picking is UI state, not a long-lived modal operator: only the
    individual pick is modal, exactly as A/B already works. Normal Blender
    keyboard input is therefore never globally swallowed (sect. 13)
    """

    bl_idname = "bsmt.guided_picking"
    bl_label = "Guided Picking"
    bl_options = {'REGISTER'}

    action: EnumProperty(
        name="Action",
        items=(
            ('START', "Start", "Begin guided picking"),
            ('NEXT', "Next", "Move to the next landmark without picking"),
            ('PREVIOUS', "Previous", "Move to the previous landmark"),
            ('CANCEL', "Cancel", "Leave guided picking"),
        ),
        default='START',
        options={'SKIP_SAVE'},
    )

    def execute(self, context):
        props = state.get_props(context)
        collection = state.get_landmarks(context)
        if props is None or collection is None or not len(collection):
            self.report({'WARNING'}, "BSMT: there are no landmarks to pick")
            return {'CANCELLED'}

        if self.action == 'CANCEL':
            props.guided_active = False
            props.guided_index = 0
            self.report({'INFO'}, "BSMT: guided picking cancelled")
            return {'FINISHED'}

        targets = _guided_targets(context, props)
        if not targets:
            props.guided_active = False
            self.report({'INFO'},
                        "BSMT: every landmark is already picked. Turn off "
                        "'Skip Already Picked' to re-pick.")
            return {'FINISHED'}

        if self.action == 'START':
            props.guided_active = True
            props.guided_index = 0
            props.landmark_index = targets[0]
        elif self.action == 'NEXT':
            props.guided_index = min(props.guided_index + 1, len(targets) - 1)
            props.landmark_index = targets[props.guided_index]
        elif self.action == 'PREVIOUS':
            props.guided_index = max(props.guided_index - 1, 0)
            props.landmark_index = targets[props.guided_index]

        item = collection[props.landmark_index]
        self.report({'INFO'}, "BSMT: pick %d/%d: %s"
                    % (props.guided_index + 1, len(targets), item.label))
        return {'FINISHED'}


class BSMT_OT_save_protocol(bpy.types.Operator):
    """Save the landmark names and order as a reusable protocol.

    Names and order only. Scan-specific triangle indices and barycentric
    coordinates are never written into a protocol file
    """

    bl_idname = "bsmt.save_protocol"
    bl_label = "Save Landmark Protocol"
    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH')
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    check_existing: BoolProperty(default=True, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return bool(state.get_landmarks(context))

    def invoke(self, context, event):
        props = state.get_props(context)
        if not self.filepath:
            name = (props.protocol_name or "bsmt_protocol").replace(" ", "_")
            self.filepath = name + ".json"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = state.get_props(context)
        collection = state.get_landmarks(context)
        if not collection:
            self.report({'ERROR'}, "BSMT: there are no landmarks to save")
            return {'CANCELLED'}
        entries = [
            (item.protocol_id, item.name, item.notes) for item in collection
        ]
        try:
            protocol.save(
                self.filepath,
                props.protocol_name or "BSMT Landmark Protocol",
                entries,
            )
        except (protocol.ProtocolError, OSError) as exc:
            self.report({'ERROR'}, "BSMT: could not save protocol: %s" % exc)
            return {'CANCELLED'}
        self.report({'INFO'}, "BSMT: saved %d landmark name(s) to %s"
                    % (len(entries), self.filepath))
        return {'FINISHED'}


class BSMT_OT_load_protocol(bpy.types.Operator):
    """Load a landmark protocol: names and order, no positions.

    Every loaded landmark starts NOT PICKED. A file that carries scan-specific
    coordinates is refused rather than partially imported
    """

    bl_idname = "bsmt.load_protocol"
    bl_label = "Load Landmark Protocol"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    replace: BoolProperty(
        name="Replace Existing Landmarks",
        description="Delete the current landmarks and their markers before "
                    "loading. Turn off to append",
        default=True,
    )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = state.get_props(context)
        if props is None:
            return {'CANCELLED'}
        try:
            name, entries = protocol.load(self.filepath)
        except protocol.ProtocolError as exc:
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        if self.replace:
            state.clear_landmarks(context, props)
            visualization.clear_landmark_markers()

        added = 0
        skipped = []
        for protocol_id, landmark_name, notes in entries:
            try:
                state.add_landmark(context, props, landmark_name,
                                   protocol_id=protocol_id, notes=notes)
                added += 1
            except landmarks.LandmarkError as exc:
                skipped.append("%s (%s)" % (landmark_name, exc))

        props.protocol_name = name
        props.landmark_index = 0
        props.landmark_summary = ""
        _counts, summary = landmarks.summarise(
            [item.status for item in state.get_landmarks(context)]
        )
        props.landmark_summary = summary

        if skipped:
            for line in skipped:
                print("[BSMT] protocol landmark skipped: %s" % line)
            self.report({'WARNING'},
                        "BSMT: loaded '%s' - %d landmark(s), %d skipped "
                        "(see the system console)" % (name, added, len(skipped)))
        else:
            self.report({'INFO'},
                        "BSMT: loaded protocol '%s' - %d landmark(s), all NOT "
                        "PICKED" % (name, added))
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Measurement Manager (Milestone 3.1)
# ---------------------------------------------------------------------------
#
# There is deliberately NO all-pairs path anywhere below. "Calculate All
# Defined" means the enabled definitions the researcher wrote, and nothing
# else: for 50 landmarks that is the handful they care about, not 1,225
# combinations (sect. 20).


def _measure_one(context, props, item, report_lines=None):
    """Calculate one measurement definition. Returns (ok, message).

    Never returns a substituted number. Every refusal states which landmark
    or which condition caused it.
    """
    started = time.perf_counter()
    source, target = state.resolve_measurement_landmarks(context, item)

    status, detail = measurements.readiness(
        source, target, item.source_stable_id, item.target_stable_id
    )
    if status != measurements.STATUS_READY:
        state.clear_measurement_result(item)
        item.status = status
        item.status_detail = detail
        return False, detail or status

    source_point = source.surface_point
    target_point = target.surface_point

    if source_point.source_object != target_point.source_object:
        state.clear_measurement_result(item)
        item.status = measurements.STATUS_FAILED
        item.status_detail = (
            "landmarks belong to different scan objects ('%s' and '%s')"
            % (source_point.source_object, target_point.source_object)
        )
        return False, item.status_detail

    obj = bpy.data.objects.get(source_point.source_object)
    if obj is None or obj.type != 'MESH':
        state.clear_measurement_result(item)
        item.status = measurements.STATUS_FAILED
        item.status_detail = ("source object '%s' is missing"
                              % source_point.source_object)
        return False, item.status_detail

    wants_straight = measurements.needs_straight(item.measurement_type)
    wants_surface = measurements.needs_surface(item.measurement_type)

    # --- straight ---------------------------------------------------------
    straight_mm = 0.0
    if wants_straight:
        # The Phase 1 production function, unchanged. The distance
        # mathematics is not reimplemented here (sect. 7).
        straight_mm = measurement.straight_distance_mm(
            source_point.world_xyz, target_point.world_xyz, props.unit
        )

    # --- surface ----------------------------------------------------------
    surface_mm = 0.0
    surface_report = None
    surface_mode = ""
    canonical = None
    if wants_surface:
        unavailable = geodesic.ensure_loaded()
        if unavailable:
            state.clear_measurement_result(item)
            item.status = measurements.STATUS_FAILED
            item.status_detail = unavailable
            return False, unavailable

        broken = geodesic.measure_error()
        if broken:
            state.clear_measurement_result(item)
            item.status = measurements.STATUS_FAILED
            item.status_detail = broken
            return False, broken

        solve = geodesic.solve
        if not geodesic.registry.available():
            message = solve.failure_message(
                'BACKEND_MISSING', geodesic.registry.unavailable_reason()
            )
            state.clear_measurement_result(item)
            item.status = measurements.STATUS_FAILED
            item.status_detail = message
            return False, message

        try:
            canonical = geodesic.meshcache.get(context, obj, props.unit)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            message = "canonical mesh unavailable (%s)" % exc
            state.clear_measurement_result(item)
            item.status = measurements.STATUS_FAILED
            item.status_detail = message
            return False, message

        specs = []
        for point in (source_point, target_point):
            specs.append(solve.PointSpec(
                point.triangle_index,
                np.array(point.barycentric, dtype=np.float64),
                component_id=point.component_id,
                source_object=point.source_object,
                geometry_hash=point.geometry_hash,
                status=point.status,
                valid=point.valid,
            ))

        try:
            # The Milestone 2.3 production pipeline, unchanged: validation,
            # scratch-mesh endpoint insertion, bounded exact MMP query and
            # the result invariants.
            result = solve.surface_distance(
                canonical.vertices_solver,
                canonical.triangles,
                specs[0],
                specs[1],
                geometry_hash=canonical.geometry_hash,
            )
        except solve.MeasurementError as exc:
            state.clear_measurement_result(item)
            item.status = measurements.STATUS_FAILED
            item.status_detail = exc.message
            return False, exc.message
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            message = solve.failure_message(
                'BACKEND_ERROR', "%s: %s" % (type(exc).__name__, exc)
            )
            state.clear_measurement_result(item)
            item.status = measurements.STATUS_FAILED
            item.status_detail = message
            return False, message

        surface_mm = float(result.distance_mm)
        surface_report = result.report
        surface_mode = result.mode

        if not wants_straight:
            # Recorded for the ratio and the invariant even when the
            # researcher only asked for the surface distance.
            straight_mm = float(result.straight_mm)

    if canonical is None:
        try:
            canonical = geodesic.meshcache.get(context, obj, props.unit)
        except Exception:                             # noqa: BLE001
            canonical = None

    # --- store ------------------------------------------------------------
    item.straight_valid = bool(wants_straight)
    item.straight_mm = float(straight_mm)
    item.surface_valid = bool(wants_surface)
    item.surface_mm = float(surface_mm)
    item.ratio = (measurements.ratio(straight_mm, surface_mm)
                  if wants_surface else 0.0)
    item.surface_mode = surface_mode
    item.backend_name = (surface_report.backend_name
                         if surface_report is not None else "")
    item.backend_version = (surface_report.backend_version
                            if surface_report is not None else "")
    item.bound_factor = (
        float(surface_report.bound_factor)
        if surface_report is not None and surface_report.bound_factor is not None
        else 0.0
    )
    item.unbounded_fallback = bool(
        surface_report.unbounded_fallback if surface_report is not None else False
    )
    item.attempts = int(surface_report.attempts) if surface_report is not None else 0
    item.elapsed_s = time.perf_counter() - started

    if canonical is not None:
        item.result_object = canonical.source_object
        item.result_geometry_hash = canonical.geometry_hash
        item.result_metric_tensor = state.metric_tensor(
            obj.matrix_world, canonical.unit_multiplier
        )
    item.status = measurements.STATUS_VALID
    item.status_detail = ""

    line = "%s %s: %s" % (
        item.protocol_id, item.label,
        measurements.format_result(item.straight_mm, item.straight_valid,
                                   item.surface_mm, item.surface_valid),
    )
    if wants_surface:
        line += "  ratio %.4f" % item.ratio
        line += "  | bound %s, attempts %d" % (
            "unbounded" if item.unbounded_fallback else "%.2fx" % item.bound_factor,
            item.attempts,
        )
    line += "  | %.3f s" % item.elapsed_s
    if report_lines is not None:
        report_lines.append(line)
    print("[BSMT] " + line)
    return True, line


class BSMT_OT_add_measurement(bpy.types.Operator):
    """Define a measurement between two named landmarks.

    The landmarks need not be picked yet: a definition is a plan, and its
    status simply reports that it is not ready
    """

    bl_idname = "bsmt.add_measurement"
    bl_label = "Add Measurement"
    bl_options = {'REGISTER', 'UNDO'}

    use_selected: BoolProperty(
        name="Use Selected Landmark As Source",
        description="Start the definition from the landmark selected in the "
                    "Landmark Manager",
        default=False,
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        return state.get_measurements(context) is not None

    def execute(self, context):
        props = state.get_props(context)
        collection = state.get_landmarks(context)
        if props is None:
            self.report({'ERROR'}, "BSMT: add-on properties are not registered")
            return {'CANCELLED'}
        if not collection:
            self.report({'ERROR'},
                        "BSMT: define some landmarks before defining a "
                        "measurement between them")
            return {'CANCELLED'}

        source = target = None
        if self.use_selected:
            source = state.active_landmark(context, props)
        if source is None:
            source = collection[0]
        if len(collection) > 1:
            target = collection[1] if collection[1] is not source else collection[0]
        else:
            target = source

        try:
            item = state.add_measurement(
                context, props, source=source, target=target,
                measurement_type=measurements.TYPE_BOTH,
            )
        except measurements.MeasurementError as exc:
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        state.refresh_measurement_status(context, item)
        self.report({'INFO'}, "BSMT: added measurement %s '%s'"
                    % (item.protocol_id, item.label))
        return {'FINISHED'}


class BSMT_OT_remove_measurement(bpy.types.Operator):
    """Delete the selected measurement definition"""

    bl_idname = "bsmt.remove_measurement"
    bl_label = "Delete Measurement"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return state.active_measurement(context) is not None

    def execute(self, context):
        props = state.get_props(context)
        item = state.active_measurement(context, props)
        if item is None:
            return {'CANCELLED'}
        label = item.label
        state.remove_measurement(context, props, props.measurement_index)
        self.report({'INFO'}, "BSMT: deleted measurement '%s'" % label)
        return {'FINISHED'}


class BSMT_OT_clear_measurements(bpy.types.Operator):
    """Delete every measurement definition. Landmarks, A/B and the scan are
    not affected"""

    bl_idname = "bsmt.clear_measurements"
    bl_label = "Clear Measurements"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(state.get_measurements(context))

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        props = state.get_props(context)
        count = state.clear_measurements(context, props)
        self.report({'INFO'}, "BSMT: cleared %d measurement definition(s)" % count)
        return {'FINISHED'}


class BSMT_OT_remove_invalid_measurements(bpy.types.Operator):
    """Delete only the measurements whose landmark references cannot be
    resolved.

    Explicit and opt-in: a broken reference is never cleaned up automatically
    """

    bl_idname = "bsmt.remove_invalid_measurements"
    bl_label = "Remove Invalid Measurements"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        collection = state.get_measurements(context)
        if not collection:
            return False
        return any(item.status == measurements.STATUS_INVALID_REFERENCE
                   for item in collection)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        props = state.get_props(context)
        collection = state.get_measurements(context)
        removed = 0
        for index in range(len(collection) - 1, -1, -1):
            source, target = state.resolve_measurement_landmarks(
                context, collection[index]
            )
            if source is None or target is None:
                state.remove_measurement(context, props, index)
                removed += 1
        self.report({'INFO'},
                    "BSMT: removed %d measurement(s) with unresolved "
                    "references" % removed)
        return {'FINISHED'}


class BSMT_OT_calculate_measurement(bpy.types.Operator):
    """Calculate the selected measurement.

    A surface measurement runs the exact MMP backend and may block Blender
    for several seconds on a dense scan
    """

    bl_idname = "bsmt.calculate_measurement"
    bl_label = "Calculate Selected"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or props.measurement_running:
            return False
        return state.active_measurement(context) is not None

    def execute(self, context):
        props = state.get_props(context)
        item = state.active_measurement(context, props)
        if item is None:
            return {'CANCELLED'}
        if props.measurement_running:
            self.report({'WARNING'}, "BSMT: a calculation is already running")
            return {'CANCELLED'}

        props.measurement_running = True
        props.measurement_progress = "Calculating %s %s" % (
            item.protocol_id, item.label
        )
        try:
            ok, message = _measure_one(context, props, item)
        finally:
            props.measurement_running = False
            props.measurement_progress = ""

        self.report({'INFO'} if ok else {'ERROR'}, "BSMT: " + message)
        return {'FINISHED'} if ok else {'CANCELLED'}


class BSMT_OT_calculate_all_measurements(bpy.types.Operator):
    """Calculate every ENABLED measurement definition, in order.

    Only the definitions the researcher wrote, and only the enabled ones. No
    landmark pair is measured unless a definition says so
    """

    bl_idname = "bsmt.calculate_all_measurements"
    bl_label = "Calculate All Defined"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or props.measurement_running:
            return False
        return bool(state.get_measurements(context))

    def execute(self, context):
        props = state.get_props(context)
        collection = state.get_measurements(context)
        if props is None or not collection:
            return {'CANCELLED'}
        if props.measurement_running:
            self.report({'WARNING'}, "BSMT: a batch is already running")
            return {'CANCELLED'}

        plan = measurements.batch_plan(collection)
        print("\n[BSMT] Calculate All Defined: %s" % plan["summary"])
        if plan["disabled"]:
            print("[BSMT] skipping %d disabled definition(s)" % plan["disabled"])
        if plan["enabled"] == 0:
            self.report({'WARNING'},
                        "BSMT: no enabled measurement definitions to calculate")
            return {'CANCELLED'}

        enabled = [item for item in collection if item.enabled]
        props.measurement_running = True
        started = time.perf_counter()
        lines = []
        statuses = []
        try:
            for position, item in enumerate(enabled, start=1):
                # Sequential, single threaded (sect. 8). Progress is reported
                # between measurements, which is all Blender can repaint while
                # a blocking C++ solve is running.
                progress = "Calculating %d / %d: %s %s" % (
                    position, plan["enabled"], item.protocol_id, item.label
                )
                props.measurement_progress = progress
                print("[BSMT] " + progress)
                self._nudge(context)
                try:
                    _measure_one(context, props, item, lines)
                except Exception as exc:              # noqa: BLE001
                    # One failure must never abort the batch (sect. 10).
                    traceback.print_exc()
                    state.clear_measurement_result(item)
                    item.status = measurements.STATUS_FAILED
                    item.status_detail = "%s: %s" % (type(exc).__name__, exc)
                statuses.append(item.status)
        finally:
            props.measurement_running = False
            props.measurement_progress = ""

        elapsed = time.perf_counter() - started
        _counts, summary = measurements.summarise(statuses)
        props.measurement_summary = summary
        print("[BSMT] batch finished in %.2f s: %s" % (elapsed, summary))

        failed = sum(1 for status in statuses
                     if status in (measurements.STATUS_FAILED,
                                   measurements.STATUS_INVALID_REFERENCE))
        self.report(
            {'WARNING'} if failed else {'INFO'},
            "BSMT: %s in %.2f s (%d disabled, skipped)"
            % (summary, elapsed, plan["disabled"]),
        )
        return {'FINISHED'}

    @staticmethod
    def _nudge(context):
        """Best-effort redraw between measurements."""
        try:
            if context.area is not None:
                context.area.tag_redraw()
        except Exception:                             # pragma: no cover
            pass


class BSMT_OT_clear_measurement_results(bpy.types.Operator):
    """Forget every calculated result. The definitions are kept"""

    bl_idname = "bsmt.clear_measurement_results"
    bl_label = "Clear Results"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        collection = state.get_measurements(context)
        return bool(collection) and any(item.has_result for item in collection)

    def execute(self, context):
        count = state.invalidate_all_measurement_results(
            context, "results cleared by the operator"
        )
        props = state.get_props(context)
        if props is not None:
            props.measurement_summary = ""
        self.report({'INFO'}, "BSMT: cleared %d result(s)" % count)
        return {'FINISHED'}


class BSMT_OT_refresh_measurements(bpy.types.Operator):
    """Re-evaluate every measurement's status against the current landmarks,
    geometry and metric"""

    bl_idname = "bsmt.refresh_measurements"
    bl_label = "Refresh Status"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(state.get_measurements(context))

    def execute(self, context):
        props = state.get_props(context)
        collection = state.get_measurements(context)
        geodesic.ensure_loaded()
        meshcache = geodesic.meshcache if geodesic.MESHCACHE_AVAILABLE else None

        statuses = []
        for item in collection:
            canonical = None
            matrix = None
            name = item.result_object
            if meshcache is not None and name:
                canonical = meshcache.peek(name)
                obj = bpy.data.objects.get(name)
                if obj is not None:
                    matrix = obj.matrix_world
            state.refresh_measurement_status(context, item, canonical, matrix)
            statuses.append(item.status)

        _counts, summary = measurements.summarise(statuses)
        props.measurement_summary = summary
        self.report({'INFO'}, "BSMT: %s" % summary)
        return {'FINISHED'}


class BSMT_OT_save_measurement_template(bpy.types.Operator):
    """Save the measurement definitions as a reusable template.

    Definitions only: no distances, no timings, no coordinates
    """

    bl_idname = "bsmt.save_measurement_template"
    bl_label = "Save Measurement Template"
    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH')
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    check_existing: BoolProperty(default=True, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return bool(state.get_measurements(context))

    def invoke(self, context, event):
        props = state.get_props(context)
        if not self.filepath:
            name = (props.measurement_protocol_name
                    or "bsmt_measurements").replace(" ", "_")
            self.filepath = name + ".json"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = state.get_props(context)
        collection = state.get_measurements(context)
        if not collection:
            self.report({'ERROR'}, "BSMT: there are no measurements to save")
            return {'CANCELLED'}

        entries = []
        for item in collection:
            source, target = state.resolve_measurement_landmarks(context, item)
            # The landmark PROTOCOL id travels in the file, because that is
            # what makes a template meaningful alongside a landmark protocol
            # on another scan. The live protocol id wins when the reference
            # still resolves; the cached one is the fallback so an unresolved
            # definition can still be saved and diagnosed.
            from_id = source.protocol_id if source is not None else item.source_protocol_id
            to_id = target.protocol_id if target is not None else item.target_protocol_id
            from_name = source.name if source is not None else item.source_name
            to_name = target.name if target is not None else item.target_name
            entries.append((
                item.protocol_id, item.name,
                from_id, from_name, to_id, to_name,
                item.measurement_type, bool(item.enabled), item.notes,
            ))

        try:
            protocol.save_measurements(
                self.filepath,
                props.measurement_protocol_name or "BSMT Measurement Template",
                entries,
            )
        except (protocol.ProtocolError, OSError) as exc:
            self.report({'ERROR'}, "BSMT: could not save template: %s" % exc)
            return {'CANCELLED'}

        self.report({'INFO'}, "BSMT: saved %d measurement definition(s) to %s"
                    % (len(entries), self.filepath))
        return {'FINISHED'}


class BSMT_OT_load_measurement_template(bpy.types.Operator):
    """Load measurement definitions from a template.

    Landmark references resolve by landmark protocol id. A reference that
    cannot be resolved is marked INVALID_REFERENCE - never matched to a
    different landmark
    """

    bl_idname = "bsmt.load_measurement_template"
    bl_label = "Load Measurement Template"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    replace: BoolProperty(
        name="Replace Existing Measurements",
        description="Delete the current definitions before loading",
        default=True,
    )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = state.get_props(context)
        if props is None:
            return {'CANCELLED'}
        try:
            name, entries = protocol.load_measurements(self.filepath)
        except protocol.ProtocolError as exc:
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        if self.replace:
            state.clear_measurements(context, props)

        landmark_collection = state.get_landmarks(context)
        by_protocol_id = {}
        if landmark_collection:
            for landmark in landmark_collection:
                if landmark.protocol_id:
                    by_protocol_id.setdefault(landmark.protocol_id, landmark)

        unresolved = 0
        for entry in entries:
            item = state.add_measurement(
                context, props,
                name=entry["name"],
                measurement_type=entry["type"],
                notes=entry["notes"],
                protocol_id=entry["id"],
            )
            item.enabled = entry["enabled"]

            for slot, id_key, name_key in (
                ("source", "from_landmark_id", "from_landmark_name"),
                ("target", "to_landmark_id", "to_landmark_name"),
            ):
                # The id is authoritative. The name is recorded for
                # diagnostics and is NEVER used to find a substitute
                # landmark (sect. 16).
                landmark = by_protocol_id.get(entry[id_key])
                if landmark is not None:
                    state.bind_measurement_landmark(item, slot, landmark)
                else:
                    setattr(item, slot + "_stable_id", 0)
                    setattr(item, slot + "_protocol_id", entry[id_key])
                    setattr(item, slot + "_name", entry[name_key])

            state.refresh_measurement_status(context, item)
            if item.status == measurements.STATUS_INVALID_REFERENCE:
                unresolved += 1

        props.measurement_protocol_name = name
        props.measurement_index = 0
        _counts, summary = measurements.summarise(
            [item.status for item in state.get_measurements(context)]
        )
        props.measurement_summary = summary

        if unresolved:
            self.report(
                {'WARNING'},
                "BSMT: loaded '%s' - %d definition(s), %d with unresolved "
                "landmark references. Load the matching landmark protocol "
                "first." % (name, len(entries), unresolved),
            )
        else:
            self.report({'INFO'}, "BSMT: loaded measurement template '%s' - "
                                  "%d definition(s)" % (name, len(entries)))
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
    BSMT_OT_calculate_surface_distance,
    BSMT_OT_clear_surface_distance,
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
    BSMT_OT_add_landmark,
    BSMT_OT_remove_landmark,
    BSMT_OT_clear_landmark_position,
    BSMT_OT_clear_landmarks,
    BSMT_OT_validate_landmarks,
    BSMT_OT_pick_landmark,
    BSMT_OT_guided_picking,
    BSMT_OT_save_protocol,
    BSMT_OT_load_protocol,
    BSMT_OT_add_measurement,
    BSMT_OT_remove_measurement,
    BSMT_OT_clear_measurements,
    BSMT_OT_remove_invalid_measurements,
    BSMT_OT_calculate_measurement,
    BSMT_OT_calculate_all_measurements,
    BSMT_OT_clear_measurement_results,
    BSMT_OT_refresh_measurements,
    BSMT_OT_save_measurement_template,
    BSMT_OT_load_measurement_template,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
