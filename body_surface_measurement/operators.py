"""Operators: modal point picking, distance calculation, clearing."""

import time
import traceback

import numpy as np

import bpy
from mathutils import Matrix, Vector
from bpy.props import (BoolProperty, EnumProperty, FloatProperty,
                       IntProperty, StringProperty)

from . import (alignment, artifact, attach, export, geodesic, interior,
               interiorcache, landmarks, localrepair, measurement,
               measurements, meshrepair, overlay, panelpreview, pathcache,
               picking, preprocess, protocol, readiness, regions, repair,
               scancopy, state, surfacearea, timing, visualization, viz)

def _addon_version():
    # VERSION, not bl_info: Blender strips bl_info from an extension module.
    from . import VERSION
    return ".".join(str(part) for part in VERSION)


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
    bl_description = ("Click a point on the mesh surface to store it as Point"
                      " A or Point B")
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
            ('ALIGN', "Alignment Reference",
             "Store into an alignment reference slot"),
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
    align_slot: EnumProperty(
        name="Alignment Slot",
        items=(('LEFT', "Left", "Subject's LEFT reference"),
               ('RIGHT', "Right", "Subject's RIGHT reference"),
               ('SUPERIOR', "Superior", "Upper reference"),
               ('INFERIOR', "Inferior", "Lower reference")),
        default='LEFT',
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
        # Restrict the cast to the active object. A measurement copy sits at
        # the same transform as its source, so a scene-wide cast can return
        # the original instead and record the landmark against the wrong mesh.
        target = self._pick_target(context)

        # Say WHY before saying "nothing there". A target that is disabled in
        # the viewport has no evaluated mesh, so the cast finds nothing - and
        # BSMT's own Source / Measurement buttons set that flag. Because a
        # measurement mesh sits at its source's transform, the researcher is
        # then looking at a body, clicking on it, and being told their cursor
        # is over no surface.
        blocker = "" if target is None else picking.pick_blocker(context, target)
        if blocker:
            self.report(
                {'WARNING'},
                "BSMT: '%s' %s (ESC or right click to cancel)"
                % (target.name, blocker),
            )
            return {'RUNNING_MODAL'}

        hit = picking.ray_cast_surface(context, self._region, rv3d, coord,
                                       target=target)
        if hit is None:
            self.report(
                {'WARNING'},
                "BSMT: no mesh surface under the cursor%s - click on the mesh "
                "(ESC or right click to cancel)"
                % ("" if target is None else " of '%s'" % target.name),
            )
            return {'RUNNING_MODAL'}

        location, _normal, obj = hit
        props = state.get_props(context)
        if props is None:
            self._restore(context)
            self.report({'ERROR'}, "BSMT: add-on properties are not registered")
            return {'CANCELLED'}

        self._warn_if_source_has_copy(self, obj)

        if self.target == 'ALIGN':
            return self._pick_alignment(context, props, obj, coord, location)
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

    def _pick_alignment(self, context, props, obj, coord, location):
        """Store this click into one alignment reference slot.

        Alignment references are separate from the research landmarks, but
        they are the same BSMT_SurfacePoint type, so there is still one
        surface-location representation and they follow a transform for free.
        """
        point = state.align_point(props, self.align_slot)
        note = self._attach_surface_point(context, props, obj, coord, point)
        if not note.startswith("triangle"):
            state.clear_surface_point(point)
            self.report({'WARNING'}, "BSMT: %s" % note)
            return {'RUNNING_MODAL'}
        # A new reference invalidates any preview drawn from the old frame.
        props.align_preview = False
        visualization.clear_alignment_helpers()
        self._restore(context)
        self.report({'INFO'}, "BSMT: %s reference set on '%s' - %s"
                    % (self.align_slot, obj.name, note))
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
        overlay.tag_redraw(context)
        # Targeted, not a sweep: only the definitions that reference THIS
        # landmark lose their result (sect. 13).
        affected = state.invalidate_measurements_for_landmark(
            context, item.stable_id, "landmark '%s' was re-picked" % item.label
        )
        if affected:
            print("[BSMT] re-picking '%s' invalidated %d measurement result(s)"
                  % (item.label, affected))
        # Regions depend on the landmark DIRECTLY, so they are restated on
        # the same event rather than through whatever measurements happen to
        # share it. A boundary whose corner moved is STALE; it is never
        # re-solved without being asked.
        restated = state.invalidate_regions_for_landmark(
            context, item.stable_id, "landmark '%s' was re-picked" % item.label
        )
        if restated:
            print("[BSMT] re-picking '%s' restated %d surface region(s)"
                  % (item.label, restated))

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

    @staticmethod
    def _pick_target(context):
        """The object a click should land on: the active mesh, if there is one.

        Selection is the researcher's statement of intent, and it is the only
        thing that can separate two coincident objects. When there is no
        usable active mesh the cast falls back to the whole scene.
        """
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            return None
        if visualization.is_helper(obj):
            return None
        return obj

    @staticmethod
    def _warn_if_source_has_copy(operator, obj):
        """Say so when a landmark is being placed on an ORIGINAL scan.

        Not a refusal - measuring the original is a legitimate choice - but it
        is almost always a mistake when a prepared copy exists, and it is the
        exact situation that produced a measurement against the wrong mesh.
        """
        provenance = getattr(obj, "bsmt_scan", None)
        if provenance is not None and provenance.is_measurement_copy:
            return ""
        copy = scancopy.find_measurement_copy(obj)
        if copy is None:
            return ""
        message = ("picked on the SOURCE MESH '%s'; its measurement mesh "
                   "'%s' exists. Select the measurement mesh if you meant to "
                   "measure on it." % (obj.name, copy.name))
        print("[BSMT] " + message)
        operator.report({'WARNING'}, "BSMT: " + message)
        return message

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
        # `obj` here is the object the surface ray actually hit, so the
        # canonical cast and the SurfacePoint agree by construction.
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
        if self.target == 'ALIGN':
            text = ("BSMT: Left click to set the %s alignment reference   |   "
                    "ESC or Right click: cancel" % self.align_slot)
        elif self.target == 'LANDMARK':
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
    bl_label = "Calculate Straight Distance"
    bl_description = ("Calculate the straight-line distance between Point A"
                      " and Point B")
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
    bl_description = ("Calculate the exact geodesic distance across the"
                      " surface between Point A and Point B. This may block"
                      " Blender for several seconds on a dense mesh")
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

        # Ownership is settled BEFORE anything is logged as the solver
        # target, so the log can never name a mesh the measurement was then
        # refused on.
        if props.surface_a.source_object != props.surface_b.source_object:
            message = solve.failure_message(
                'DIFFERENT_OBJECTS',
                "A on '%s', B on '%s'" % (props.surface_a.source_object,
                                          props.surface_b.source_object))
            state.set_surface_failure(props, message)
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        # Safety gate BEFORE any solver construction (sect. 9), on the mesh
        # the A/B SurfacePoints were actually picked on.
        log_solver_target("A/B surface distance", obj, canonical)
        gate = solver_preflight(props, canonical)
        if not gate["allowed"]:
            message = _report_preflight(self, gate, " (A/B)", obj.name)
            state.set_surface_failure(props, message)
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}
        for line in gate["warnings"]:
            print("[BSMT] warning (A/B): %s" % line)

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
    bl_description = ("Forget the stored surface distance. The points and the"
                      " straight distance are kept")
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
    bl_label = "Validate Points"
    bl_description = ("Re-check Point A and Point B against the current mesh,"
                      " and move their markers to match")
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
    bl_description = ("Report whether the handlers that make markers follow"
                      " the mesh are registered (developer tool)")
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
    bl_label = "Refresh Markers"
    bl_description = ("Move the markers and the straight line back onto their"
                      " stored surface positions")
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
    bl_description = ("Forget Point A and Point B and remove their markers."
                      " The mesh is not touched")
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
    bl_label = "Analyze Topology"
    bl_description = ("Report the topology of the active mesh. Read-only: the"
                      " mesh is never modified")
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
    bl_label = "Show Components"
    bl_description = ("Color each connected component with temporary helper"
                      " objects. The mesh is not touched")
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
    bl_description = ("Independently re-check the component labelling"
                      " (developer tool)")
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
    bl_label = "Clear Components"
    bl_description = ("Remove the component helper objects. The mesh is not"
                      " touched")
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
    bl_description = "Show only this component, or show all of them again"
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
    bl_description = ("Report the Python environment Blender is running and"
                      " whether the exact geodesic backend can be imported"
                      " (developer tool)")
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
    bl_label = "Run Backend Self-Test"
    bl_description = ("Run the synthetic proof of the exact geodesic backend"
                      " against known analytic distances (developer tool)")
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
    bl_description = "Clear the environment and self-test reports"
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
    bl_description = ("Add a named landmark. It starts unpicked - pick it on"
                      " the surface to give it a position")
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
    bl_description = "Delete the selected landmark and its marker"
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
            # Nothing to delete since 3.9 - the marker is drawn, not built -
            # but a marker object from an older file would otherwise outlive
            # the landmark it belongs to.
            visualization.remove_landmark_marker(stable_id)
        overlay.tag_redraw(context)
        self.report({'INFO'}, "BSMT: deleted landmark '%s'" % name)
        return {'FINISHED'}


class BSMT_OT_clear_landmark_position(bpy.types.Operator):
    """Forget the selected landmark's surface location, keeping its name"""

    bl_idname = "bsmt.clear_landmark_position"
    bl_label = "Clear Position"
    bl_description = ("Forget the selected landmark's surface position,"
                      " keeping its name")
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
        overlay.tag_redraw(context)
        self.report({'INFO'}, "BSMT: cleared position of '%s'" % item.label)
        return {'FINISHED'}


class BSMT_OT_clear_landmarks(bpy.types.Operator):
    """Delete every named landmark. A/B, the topology preview and the scan
    are not affected"""

    bl_idname = "bsmt.clear_landmarks"
    bl_label = "Delete All Landmarks"
    bl_description = ("Delete every landmark. Point A/B, the measurements and"
                      " the mesh are not affected")
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
        state.refresh_all_region_statuses(context)
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
    bl_label = "Validate Landmarks"
    bl_description = ("Re-check every landmark against the current mesh and"
                      " update its status")
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

        # The overlay reads status and world position straight from the
        # landmarks on the next redraw, so a landmark that has just become
        # stale is recoloured with nothing to push. Never re-projected.
        visualization.remove_orphan_landmark_markers(
            [item.stable_id for item in collection]
        )
        overlay.tag_redraw(context)

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
    bl_label = "Pick Landmark"
    bl_description = ("Click a point on the mesh surface to position the"
                      " selected landmark")
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
    bl_description = "Step through the landmark list, picking each one in turn"
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
    bl_description = ("Save the landmark names and their order to a JSON"
                      " file. Positions are not saved")
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
    bl_description = ("Load landmark names and order from a JSON file. No"
                      " positions are loaded")
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
            overlay.tag_redraw(context)

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

        log_solver_target("measurement %s" % item.protocol_id, obj, canonical)
        gate = solver_preflight(props, canonical)
        if not gate["allowed"]:
            message = _report_preflight(None, gate,
                                        " (%s)" % item.protocol_id, obj.name)
            state.clear_measurement_result(item)
            item.status = measurements.STATUS_FAILED
            item.status_detail = message
            return False, message
        for line in gate["warnings"]:
            print("[BSMT] warning (%s): %s" % (item.protocol_id, line))

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
    bl_description = ("Start a new measurement. Choose its From and To"
                      " landmarks to complete it")
    bl_options = {'REGISTER', 'UNDO'}

    use_selected: BoolProperty(
        name="Start From Selected Landmark",
        description="Fill in From with the landmark selected in the "
                    "Landmark Manager",
        default=False,
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        return state.get_measurements(context) is not None

    def execute(self, context):
        props = state.get_props(context)
        landmarks_collection = state.get_landmarks(context)
        measurements_collection = state.get_measurements(context)
        if props is None or measurements_collection is None:
            self.report({'ERROR'}, "BSMT: add-on properties are not registered")
            return {'CANCELLED'}
        if not landmarks_collection:
            self.report({'ERROR'},
                        "BSMT: add some landmarks first - a measurement "
                        "connects two of them")
            return {'CANCELLED'}

        # Sect. 6: pressing Add twice must never leave two unfinished rows
        # behind. If the last row is still a draft, that draft IS the new
        # measurement - select it and say so, rather than stacking another
        # empty row the researcher then has to delete.
        existing = state.trailing_draft_index(measurements_collection)
        if existing >= 0:
            props.measurement_index = existing
            props.show_measurement_detail = True
            item = measurements_collection[existing]
            state.refresh_measurement_status(context, item)
            self.report({'INFO'},
                        "BSMT: %s is still unfinished - choose its From and "
                        "To landmarks" % item.protocol_id)
            return {'FINISHED'}

        source = None
        if self.use_selected:
            source = state.active_landmark(context, props)

        try:
            item = state.add_measurement(
                context, props, source=source,
                measurement_type=measurements.TYPE_BOTH,
            )
        except measurements.MeasurementError as exc:
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        props.show_measurement_detail = True
        state.refresh_measurement_status(context, item)
        self.report({'INFO'}, "BSMT: %s added - choose its From and To "
                              "landmarks" % item.protocol_id)
        return {'FINISHED'}


class BSMT_OT_cancel_measurement_draft(bpy.types.Operator):
    """Discard the unfinished measurement.

    Only ever removes a DRAFT - a row with no complete From/To pair - so it
    can never delete a real measurement or a stored result
    """

    bl_idname = "bsmt.cancel_measurement_draft"
    bl_label = "Cancel New Measurement"
    bl_description = "Discard this unfinished measurement"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        item = state.active_measurement(context)
        return item is not None and state.measurement_is_draft(item)

    def execute(self, context):
        props = state.get_props(context)
        item = state.active_measurement(context, props)
        if item is None or not state.measurement_is_draft(item):
            self.report({'WARNING'},
                        "BSMT: the selected measurement is not a draft")
            return {'CANCELLED'}
        protocol_id = item.protocol_id
        state.remove_measurement(context, props, props.measurement_index)
        self.report({'INFO'}, "BSMT: discarded draft %s" % protocol_id)
        return {'FINISHED'}


class BSMT_OT_remove_measurement(bpy.types.Operator):
    """Delete the selected measurement definition"""

    bl_idname = "bsmt.remove_measurement"
    bl_label = "Delete Measurement"
    bl_description = "Delete the selected measurement and its result"
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
    bl_label = "Delete All Measurements"
    bl_description = ("Delete every measurement. Landmarks, Point A/B and the"
                      " mesh are not affected")
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
    bl_label = "Delete Unresolved"
    bl_description = ("Delete only the measurements whose landmarks no longer"
                      " exist")
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
    bl_description = ("Calculate the selected measurement. A surface distance"
                      " may block Blender for several seconds")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or props.measurement_running:
            return False
        item = state.active_measurement(context)
        return item is not None and not state.measurement_is_draft(item)

    def execute(self, context):
        props = state.get_props(context)
        item = state.active_measurement(context, props)
        if item is None:
            return {'CANCELLED'}
        if state.measurement_is_draft(item):
            self.report({'WARNING'},
                        "BSMT: choose a From and a To landmark first - they "
                        "must be two different landmarks")
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
    bl_label = "Calculate All"
    bl_description = ("Calculate every enabled measurement, in order. Drafts"
                      " and disabled rows are skipped")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or props.measurement_running:
            return False
        collection = state.get_measurements(context)
        if not collection:
            return False
        return any(item.enabled for item in measurements.defined(collection))

    def execute(self, context):
        props = state.get_props(context)
        collection = state.get_measurements(context)
        if props is None or not collection:
            return {'CANCELLED'}
        if props.measurement_running:
            self.report({'WARNING'}, "BSMT: a batch is already running")
            return {'CANCELLED'}

        plan = measurements.batch_plan(collection)
        print("\n[BSMT] Calculate All: %s" % plan["summary"])
        if plan["disabled"]:
            print("[BSMT] skipping %d disabled measurement(s)" % plan["disabled"])
        if plan["drafts"]:
            # A draft is not a measurement (sect. 6), so it is skipped in
            # silence-free fashion: said out loud, never calculated.
            print("[BSMT] skipping %d unfinished draft(s)" % plan["drafts"])
        if plan["enabled"] == 0:
            self.report({'WARNING'},
                        "BSMT: nothing to calculate - no enabled measurement "
                        "has both landmarks chosen")
            return {'CANCELLED'}

        enabled = [item for item in measurements.defined(collection)
                   if item.enabled]
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
        report_lines = measurements.batch_report(
            statuses, plan["enabled"], plan["disabled"]
        )
        # Spelled out rather than compressed, so "did everything I asked for
        # actually run" is answerable at a glance.
        props.measurement_summary = "\n".join(report_lines)
        print("[BSMT] batch finished in %.2f s" % elapsed)
        for line in report_lines:
            print("[BSMT]   %s" % line)

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
    bl_description = ("Forget every calculated result. The measurements"
                      " themselves are kept")
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
    bl_label = "Refresh"
    bl_description = ("Re-check every measurement against the current"
                      " landmarks, geometry and scale")
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
    bl_description = ("Save the measurement definitions to a JSON file."
                      " Results are not saved")
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
    bl_description = ("Load measurement definitions from a JSON file. No"
                      " results are loaded")
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
                    state.bind_measurement_landmark(item, slot, landmark,
                                                    context)
                else:
                    setattr(item, slot + "_stable_id", 0)
                    setattr(item, slot + "_protocol_id", entry[id_key])
                    setattr(item, slot + "_name", entry[name_key])

            # A template stores the name the researcher saved, and the file
            # format is not extended to carry the Auto Name flag - that would
            # change protocol semantics. Instead it is inferred: a saved name
            # that is exactly what auto-naming would produce was an auto name,
            # so it keeps following its landmarks. Anything else is a custom
            # name and is preserved verbatim.
            item.auto_name = (item.name == state.auto_name_for(context, item))

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


# ---------------------------------------------------------------------------
# Measurement visualisation (Milestone 3.2)
# ---------------------------------------------------------------------------
#
# A surface PATH is never computed as a side effect. Not by Calculate
# Selected, not by Calculate All, not by creating a measurement, not
# by picking a landmark, and not by switching a display mode on. It happens
# only when the operator below is pressed, because it needs the unbounded
# geodesicDistance() query - tens of seconds at scan scale (sect. 5.1b).


class BSMT_OT_compute_surface_path(bpy.types.Operator):
    """Compute the exact geodesic path for the selected measurement.

    Expensive and explicit: this runs the unbounded exact solve and will
    block Blender for tens of seconds on a dense scan. It never changes the
    already-calculated surface distance
    """

    bl_idname = "bsmt.compute_surface_path"
    bl_label = "Compute Surface Path"
    bl_description = ("Compute the exact geodesic path for the selected"
                      " measurement. This runs the unbounded solve and may"
                      " block Blender for tens of seconds")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or props.viz_running or props.measurement_running:
            return False
        item = state.active_measurement(context, props)
        return item is not None and item.surface_valid

    def execute(self, context):
        props = state.get_props(context)
        item = state.active_measurement(context, props)
        if props is None or item is None:
            return {'CANCELLED'}
        if props.viz_running:
            self.report({'WARNING'}, "BSMT: a path computation is already running")
            return {'CANCELLED'}

        if not item.surface_valid:
            message = ("calculate the surface distance for '%s' before asking "
                       "for its path" % item.label)
            props.viz_status = message
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        unavailable = geodesic.ensure_loaded() or geodesic.measure_error()
        if unavailable:
            props.viz_status = unavailable
            self.report({'ERROR'}, "BSMT: " + unavailable)
            return {'CANCELLED'}

        source, target = state.resolve_measurement_landmarks(context, item)
        if source is None or target is None:
            message = "the referenced landmark no longer exists"
            props.viz_status = message
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        object_name = source.surface_point.source_object
        obj = bpy.data.objects.get(object_name)
        if obj is None or obj.type != 'MESH':
            message = "source object '%s' is missing" % object_name
            props.viz_status = message
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        props.viz_running = True
        props.viz_status = "Computing exact surface path..."
        print("[BSMT] Computing exact surface path for %s '%s' - Blender will "
              "not redraw until the solver returns"
              % (item.protocol_id, item.label))
        self._nudge(context)
        started = time.perf_counter()
        try:
            return self._solve(context, props, item, obj, source, target,
                               started)
        finally:
            props.viz_running = False

    def _solve(self, context, props, item, obj, source, target, started):
        solve = geodesic.solve
        try:
            canonical = geodesic.meshcache.get(context, obj, props.unit)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            message = "canonical mesh unavailable (%s)" % exc
            props.viz_status = message
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        log_solver_target("surface path", obj, canonical)
        gate = solver_preflight(props, canonical)
        if not gate["allowed"]:
            message = _report_preflight(self, gate, " (path)", obj.name)
            props.viz_status = message
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}
        for line in gate["warnings"]:
            print("[BSMT] warning (path): %s" % line)

        specs = []
        for landmark in (source, target):
            point = landmark.surface_point
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
            # The stored surface distance is passed in as the reference the
            # path must agree with. A disagreement refuses the path; it never
            # rewrites the measurement.
            result = solve.surface_path(
                canonical.vertices_solver,
                canonical.triangles,
                specs[0], specs[1],
                geometry_hash=canonical.geometry_hash,
                expected_distance_mm=item.surface_mm,
            )
        except solve.MeasurementError as exc:
            state.clear_measurement_path(item)
            props.viz_status = exc.message
            print("[BSMT] %s" % exc.message)
            self.report({'ERROR'}, "BSMT: " + exc.message)
            return {'CANCELLED'}
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            state.clear_measurement_path(item)
            message = "surface path failed (%s: %s)" % (type(exc).__name__, exc)
            props.viz_status = message
            self.report({'ERROR'}, "BSMT: " + message + " - see the console")
            return {'CANCELLED'}

        elapsed = time.perf_counter() - started

        # The cache entry's identity, written in full. Everything needed to
        # decide later whether this polyline is still the answer, WITHOUT
        # reading geometry or running anything: both landmark ids, both
        # endpoint surface locations, the geometry hash and the metric.
        item.path_point_count = int(result.point_count)
        item.path_length_mm = float(result.polyline_length_mm)
        item.path_distance_mm = float(result.distance_mm)
        item.path_agreement_mm = float(result.agreement_mm)
        item.path_elapsed_s = float(elapsed)
        item.path_mode = result.mode
        item.path_object = canonical.source_object
        item.path_geometry_hash = canonical.geometry_hash
        item.path_metric_tensor = state.metric_tensor(
            obj.matrix_world, canonical.unit_multiplier
        )
        item.path_metric_key = canonical.metric_key
        item.path_unit = props.unit
        item.path_source_stable_id = int(item.source_stable_id)
        item.path_target_stable_id = int(item.target_stable_id)
        item.path_source_triangle = int(source.surface_point.triangle_index)
        item.path_source_bary = tuple(
            float(value) for value in source.surface_point.barycentric
        )
        item.path_target_triangle = int(target.surface_point.triangle_index)
        item.path_target_bary = tuple(
            float(value) for value in target.surface_point.barycentric
        )
        item.path_stale = False
        item.path_stale_reason = ""
        item.path_valid = True

        if not viz.build_path(context, props, item, result.polyline_solver,
                              canonical):
            state.clear_measurement_path(item)
            message = "the path was computed but could not be drawn"
            props.viz_status = message
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        if not item.show_visualization:
            item.show_visualization = True
        # Asking for a path is asking to see it.
        item.path_shown = True
        viz.refresh(context, props)

        props.viz_status = (
            "Path computed | Elapsed: %.2f s | Points: %d"
            % (elapsed, result.point_count)
        )
        summary = (
            "path %.4f mm, polyline %.4f mm, stored surface %.4f mm "
            "(agreement %.3e mm), %d points, %.2f s"
            % (result.distance_mm, result.polyline_length_mm, item.surface_mm,
               result.agreement_mm, result.point_count, elapsed)
        )
        print("[BSMT] %s %s: %s" % (item.protocol_id, item.label, summary))
        self.report({'INFO'}, "BSMT: " + summary)
        return {'FINISHED'}

    @staticmethod
    def _nudge(context):
        try:
            if context.area is not None:
                context.area.tag_redraw()
        except Exception:                             # pragma: no cover
            pass


class BSMT_OT_refresh_visualization(bpy.types.Operator):
    """Rebuild the measurement visualisation from what is already computed.

    Never solves anything: a measurement with no cached path simply gets no
    path drawn
    """

    bl_idname = "bsmt.refresh_visualization"
    bl_label = "Refresh Display"
    bl_description = ("Redraw the measurements from what is already computed."
                      " Nothing is recalculated")
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        if props is None:
            return {'CANCELLED'}
        outcome = viz.refresh(context, props)
        self.report({'INFO'}, "BSMT: visualization refreshed (%s)" % outcome)
        return {'FINISHED'}


class BSMT_OT_clear_visualization(bpy.types.Operator):
    """Remove the selected measurement's visualisation helpers.

    Definitions, results, landmarks, A/B and the scan are untouched
    """

    bl_idname = "bsmt.clear_visualization"
    bl_label = "Clear Selected"
    bl_description = ("Remove the selected measurement's lines from the"
                      " viewport. The cached path is kept")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return state.active_measurement(context) is not None

    def execute(self, context):
        props = state.get_props(context)
        item = state.active_measurement(context, props)
        if item is None:
            return {'CANCELLED'}
        removed = viz.clear_for(item)
        item.show_visualization = False
        # The cached path is deliberately KEPT. Since 0.20.0 the polyline
        # lives in its own datablock rather than in the helper curve, so
        # removing the drawing costs nothing to undo - which is what this
        # operator's own description has always promised. Discarding a solve
        # is what "Clear Cached Path" is for, and it has to be asked for.
        props.viz_status = ""
        self.report({'INFO'}, "BSMT: removed %d helper(s) for '%s'"
                    % (removed, item.label))
        return {'FINISHED'}


class BSMT_OT_toggle_surface_path(bpy.types.Operator):
    """Show or hide the selected measurement's cached surface path.

    Display only. It reads the cached polyline and never calls the solver:
    a path that has already been computed costs nothing to put back on screen
    """

    bl_idname = "bsmt.toggle_surface_path"
    bl_label = "Show/Hide Path"
    bl_description = ("Show or hide the cached surface path. Nothing is"
                      " computed: hiding and showing a cached path is free")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or props.viz_running:
            return False
        item = state.active_measurement(context, props)
        return item is not None and item.path_valid

    def execute(self, context):
        props = state.get_props(context)
        item = state.active_measurement(context, props)
        if props is None or item is None:
            return {'CANCELLED'}

        current, reason = viz.path_state(context, props, item)
        if current != state.PATH_CACHED:
            message = ("the cached path is %s%s"
                       % (state.PATH_STATE_LABELS.get(current, current),
                          " - %s" % reason if reason else ""))
            props.viz_status = message
            self.report({'WARNING'}, "BSMT: " + message
                        + ". Press Compute Surface Path to solve it again")
            return {'CANCELLED'}

        showing = item.path_shown and visualization.measurement_helper_visible(
            item.stable_id, 'PATH')
        if showing:
            # Recorded on the measurement, not just on the helper, so the
            # next refresh does not undo the button. The cache is untouched.
            item.path_shown = False
            visualization.set_measurement_helper_visible(item.stable_id,
                                                         'PATH', False)
            props.viz_status = "Path hidden (still cached)"
            self.report({'INFO'}, "BSMT: path hidden; the cache is kept")
            return {'FINISHED'}

        if props.viz_mode == 'STRAIGHT':
            # Asking to see the path while the mode says otherwise is an
            # instruction, not a conflict.
            props.viz_mode = 'BOTH'
        item.path_shown = True
        item.show_visualization = True
        if not viz.draw_cached_path(context, props, item):
            message = "the cached path could not be drawn"
            props.viz_status = message
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}
        visualization.set_measurement_helper_visible(item.stable_id, 'PATH',
                                                     True)
        props.viz_status = ("Path shown from cache (%d points, no solve)"
                            % item.path_point_count)
        self.report({'INFO'}, "BSMT: path shown from cache; nothing computed")
        return {'FINISHED'}


class BSMT_OT_clear_cached_path(bpy.types.Operator):
    """Discard the selected measurement's cached surface path.

    The only route that throws a solve away. Showing it again afterwards
    means running the unbounded solver, which on a dense scan is minutes
    """

    bl_idname = "bsmt.clear_cached_path"
    bl_label = "Clear Cached Path"
    bl_description = ("Discard the cached surface path for this measurement."
                      " Recomputing it later runs the full unbounded solve"
                      " again")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or props.viz_running:
            return False
        item = state.active_measurement(context, props)
        return item is not None and item.path_valid

    def execute(self, context):
        props = state.get_props(context)
        item = state.active_measurement(context, props)
        if props is None or item is None:
            return {'CANCELLED'}
        points = item.path_point_count
        state.clear_measurement_path(item)
        props.viz_status = "Cached path discarded"
        self.report({'INFO'},
                    "BSMT: discarded the cached path for '%s' (%d points)"
                    % (item.label, points))
        return {'FINISHED'}


class BSMT_OT_path_timing_report(bpy.types.Operator):
    """Print what the path pipeline has spent its time on.

    Says whether slowness is the solver, a cache miss, Blender curve
    construction or handler churn - the four are fixed differently
    """

    bl_idname = "bsmt.path_timing_report"
    bl_label = "Print Timing Report"
    bl_description = ("Print the recorded stage timings to the system"
                      " console: solver, cache, helper and handler")
    bl_options = {'REGISTER'}

    def execute(self, context):
        totals = timing.totals()
        if not totals:
            self.report({'INFO'}, "BSMT: nothing timed yet")
            return {'CANCELLED'}
        print("[BSMT TIMING] --- stage totals (last %d samples) ---"
              % len(timing.samples()))
        for label in sorted(totals, key=lambda key: -totals[key][1]):
            count, total = totals[label]
            print("[BSMT TIMING] %-18s %5d call(s) %10.2f ms total "
                  "%8.2f ms each" % (label, count, total, total / count))
        self.report({'INFO'}, "BSMT: timing report printed to the console")
        return {'FINISHED'}


class BSMT_OT_clear_all_visualizations(bpy.types.Operator):
    """Remove every measurement visualisation helper.

    Definitions, results, landmarks, A/B and the scan are untouched
    """

    bl_idname = "bsmt.clear_all_visualizations"
    bl_label = "Clear All"
    bl_description = ("Remove every measurement line from the viewport."
                      " Cached paths are kept")
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        collection = state.get_measurements(context)
        removed = viz.clear_all()
        if collection:
            for item in collection:
                item.show_visualization = False
        # Cached paths are kept, as this operator's description says. The
        # drawing is what is being cleared, not the tens of seconds of solve
        # behind it.
        if props is not None:
            props.viz_status = ""
        self.report({'INFO'}, "BSMT: removed %d measurement helper(s)" % removed)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Scan preprocessing and the solver safety gate (Milestone 3.3)
# ---------------------------------------------------------------------------


def log_solver_target(label, obj, canonical):
    """Print exactly which mesh is about to be solved on.

    Deliberately unconditional developer logging. A measurement once ran
    against the ORIGINAL scan instead of its measurement copy - the two are
    coincident, so a scene-wide pick had recorded the wrong owner - and
    nothing in the output said which mesh the numbers belonged to. These four
    lines make that class of mistake visible immediately.
    """
    print("[BSMT] solver target (%s)" % label)
    print("[BSMT]   object    : %s" % (obj.name if obj is not None else "?"))
    print("[BSMT]   mesh      : %s"
          % (obj.data.name if obj is not None and obj.data else "?"))
    print("[BSMT]   triangles : %s"
          % "{:,}".format(canonical.triangle_count))
    print("[BSMT]   geom hash : %s" % canonical.geometry_hash)
    provenance = getattr(obj, "bsmt_scan", None) if obj is not None else None
    if provenance is not None and provenance.is_measurement_copy:
        # Informational ONLY. Provenance never redirects a measurement back
        # to the source scan; the authoritative object is the one the
        # SurfacePoints were picked on.
        print("[BSMT]   note      : measurement mesh of '%s' (informational)"
              % provenance.source_name)


def solver_preflight(props, canonical):
    """Gate every route to the native solver. Returns the preflight dict.

    A dense Design X OBJ with non-manifold edges has crashed Blender with
    SIGSEGV, and a crash takes the whole session with it. The canonical mesh
    already carries its topology report from build time, so this costs
    nothing and runs before pygeodesic is ever constructed.
    """
    report = getattr(canonical, "topology", None) or {}
    return preprocess.preflight(
        report,
        dense_threshold=props.dense_threshold_triangles,
        guard_dense=props.guard_dense_solve,
    )


def _report_preflight(operator, result, label="", object_name=""):
    """Print the refusals and warnings, and return the message to display.

    The object name is included so a refusal can never be misread as being
    about a different mesh than the one it was measured on.
    """
    if object_name:
        label = "%s on '%s'" % (label, object_name)
    for line in result["refusals"]:
        print("[BSMT] REFUSED%s: %s" % (label, line))
    for line in result["warnings"]:
        print("[BSMT] warning%s: %s" % (label, line))
    if result["refusals"]:
        return " ".join(result["refusals"])
    return " ".join(result["warnings"])


def _landmarks_on_object(context, object_name):
    """How many landmarks are currently anchored to this object.

    Counted, never moved. Preprocessing produces a NEW surface, and a stored
    triangle index means nothing on it (sect. 10).
    """
    collection = state.get_landmarks(context)
    if not collection:
        return 0
    return sum(
        1 for item in collection
        if item.surface_point.valid
        and item.surface_point.source_object == object_name
    )


class BSMT_OT_create_measurement_copy(bpy.types.Operator):
    """Create a lighter TEXTURED measurement copy of the active scan.

    The source scan, its mesh, UVs, materials and image textures are never
    modified. Nothing is welded and no hole is filled
    """

    bl_idname = "bsmt.create_measurement_copy"
    bl_label = "Create Measurement Mesh"
    bl_description = ("Create a lighter textured copy of the active scan to"
                      " measure on. The source scan is never modified")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # One policy, in `scancopy.creation_block`, so the greyed button and
        # the sentence the panel prints under it cannot disagree. The
        # conditions and their order are unchanged; a measurement mesh is
        # still refused by execute(), with an explanation, rather than by a
        # silently unavailable button.
        block = scancopy.creation_block(context.active_object,
                                        state.get_props(context))
        return block is None or not block["blocks_poll"]

    def execute(self, context):
        import datetime

        props = state.get_props(context)
        source = context.active_object
        if props is None or source is None or source.type != 'MESH':
            self.report({'ERROR'}, "BSMT: select a mesh scan first")
            return {'CANCELLED'}
        if visualization.is_helper(source):
            self.report({'ERROR'},
                        "BSMT: '%s' is a BSMT helper, not a scan" % source.name)
            return {'CANCELLED'}
        if getattr(source, "bsmt_scan", None) is not None \
                and source.bsmt_scan.is_measurement_copy:
            self.report({'ERROR'},
                        "BSMT: '%s' is already a measurement mesh. Select the "
                        "original scan." % source.name)
            return {'CANCELLED'}

        unavailable = geodesic.ensure_loaded()
        if unavailable:
            self.report({'ERROR'}, "BSMT: " + unavailable)
            return {'CANCELLED'}

        props.preprocess_running = True
        try:
            return self._run(context, props, source, datetime)
        finally:
            props.preprocess_running = False

    def _run(self, context, props, source, datetime):
        lines = []
        source_facts = scancopy.audit_object(source)
        source_triangles = scancopy.triangle_count(source.data)
        source_mesh_name = source.data.name
        source_vertex_count = len(source.data.vertices)

        # --- precheck on the ORIGINAL (sect. 5) ---------------------------
        started = time.perf_counter()
        try:
            before_report = self._diagnose(context, props, source)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report({'ERROR'},
                        "BSMT: topology precheck failed (%s)" % exc)
            return {'CANCELLED'}
        precheck_seconds = time.perf_counter() - started
        # Topology warnings never block preprocessing - preprocessing is what
        # the researcher runs BECAUSE the input has warnings.

        try:
            step = preprocess.plan(props.preprocess_target_triangles,
                                   source_triangles)
        except preprocess.PreprocessError as exc:
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        lines.append("Plan: %s" % step["summary"])
        print("[BSMT] measurement mesh of '%s': %s"
              % (source.name, step["summary"]))

        # sect. 10: landmarks are NEVER transferred to the copy. A decimated
        # mesh is a different polyhedral surface, so a stored triangle index
        # and barycentric pair does not name the same point on it - and
        # re-projecting one would silently move a researcher's landmark. The
        # honest outcome is to say so and let them re-pick.
        stranded = _landmarks_on_object(context, source.name)
        if stranded:
            warning = (
                "'%s' already carries %d landmark(s). They are NOT copied to "
                "the measurement mesh and are not re-projected onto it - "
                "re-pick them on the copy. Preprocessing is best done before "
                "landmarking." % (source.name, stranded)
            )
            lines.append("")
            lines.append("!! " + warning)
            print("[BSMT] warning: %s" % warning)

        # --- duplicate, then decimate the COPY ----------------------------
        existing = scancopy.find_measurement_copy(source)
        if existing is not None:
            lines.append("Replaced the previous copy '%s'." % existing.name)
            bpy.data.objects.remove(existing, do_unlink=True)

        copy = scancopy.duplicate(source)
        decimate_seconds = 0.0
        if step["method"] == preprocess.METHOD_DECIMATE:
            try:
                decimate_seconds = scancopy.apply_decimation(
                    context, copy, step["ratio"]
                )
            except Exception as exc:                  # noqa: BLE001
                traceback.print_exc()
                bpy.data.objects.remove(copy, do_unlink=True)
                self.report({'ERROR'}, "BSMT: decimation failed (%s)" % exc)
                return {'CANCELLED'}

        actual_triangles = scancopy.triangle_count(copy.data)

        # --- texture / UV verification (sect. 4) --------------------------
        copy_facts = scancopy.audit_object(copy)
        texture_ok, problems, notes = preprocess.compare_texture(
            source_facts, copy_facts
        )

        # --- postcheck on the COPY (sect. 6) ------------------------------
        started = time.perf_counter()
        try:
            after_report = self._diagnose(context, props, copy)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            after_report = {}
            problems.append("topology postcheck failed (%s)" % exc)
        postcheck_seconds = time.perf_counter() - started

        record = {
            "source_name": source.name,
            "source_mesh_name": source_mesh_name,
            "original_triangles": source_triangles,
            "target_triangles": int(props.preprocess_target_triangles),
            "actual_triangles": actual_triangles,
            "method": step["method"],
            "ratio": step["ratio"],
            "bsmt_version": _addon_version(),
            "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        scancopy.write_provenance(copy, source, record)

        # --- assemble the report ------------------------------------------
        accuracy, within = preprocess.accuracy_note(
            actual_triangles, props.preprocess_target_triangles
        )
        lines.append("Result: %s" % accuracy)
        if not within and step["method"] == preprocess.METHOD_DECIMATE:
            lines.append("  (collapse decimation is approximate)")
        lines.append("")
        lines.extend(preprocess.format_comparison(before_report, after_report))
        lines.append("")
        lines.append("Texture / UV")
        for note in notes:
            lines.append("  ok  %s" % note)
        for problem in problems:
            lines.append("  !!  %s" % problem)
        lines.append("")
        lines.extend(preprocess.provenance_lines(record))
        lines.append("")
        lines.append("Timing")
        lines.append("  precheck   %.2f s" % precheck_seconds)
        lines.append("  decimate   %.2f s" % decimate_seconds)
        lines.append("  postcheck  %.2f s" % postcheck_seconds)

        gate = preprocess.preflight(
            after_report, props.dense_threshold_triangles,
            props.guard_dense_solve,
        )

        # sect. 6: one plain verdict about the COPY, from the same
        # diagnostics everything else reads. `after_report` is empty only
        # when the postcheck itself failed, which is exactly the
        # "canonical mesh could not be built" case.
        status, reasons = preprocess.classify_ready(
            after_report,
            canonical_built=bool(after_report),
            appearance_ok=texture_ok,
            appearance_problems=problems,
            dense_threshold=props.dense_threshold_triangles,
        )
        lines.append("")
        lines.extend(preprocess.ready_lines(status, reasons, limit=6))

        lines.append("")
        lines.append("Exact geodesic readiness")
        if gate["allowed"]:
            lines.append("  the copy passes the solver safety gate")
        for line in gate["refusals"]:
            lines.append("  REFUSED: %s" % line)
        for line in gate["warnings"]:
            lines.append("  warning: %s" % line)

        props.preprocess_report = "\n".join(lines)
        props.preprocess_valid = True
        props.preprocess_copy_name = copy.name
        props.preprocess_status = status
        props.preprocess_status_detail = reasons[0] if reasons else ""
        props.preprocess_seconds = float(decimate_seconds)
        props.preprocess_diagnostic_seconds = float(
            precheck_seconds + postcheck_seconds
        )
        print("\n[BSMT] Scan preprocessing\n" + props.preprocess_report + "\n")

        if not texture_ok:
            # sect. 4: a copy that lost its texture is NOT measurement-ready.
            # It is kept so the researcher can see what happened, but it is
            # reported as a failure rather than offered as usable.
            self.report(
                {'ERROR'},
                "BSMT: preprocessing FAILED - %s. '%s' is not "
                "measurement-ready." % ("; ".join(problems), copy.name),
            )
            return {'CANCELLED'}

        source_unchanged = (
            scancopy.triangle_count(source.data) == source_triangles
            and len(source.data.vertices) == source_vertex_count
            and source.data.name == source_mesh_name
        )
        if not source_unchanged:
            # Should be impossible - everything ran on the duplicate - but the
            # promise "the source is never modified" is worth verifying rather
            # than asserting.
            self.report({'ERROR'},
                        "BSMT: the source scan changed during preprocessing. "
                        "This is a bug; do not trust the copy.")
            return {'CANCELLED'}

        # The generated mesh becomes the active object. Every stage after
        # this one - repair, alignment, landmarks, measurement - acts on the
        # ACTIVE object, and leaving the source active after preprocessing
        # left the researcher looking at a Mesh Repair panel correctly saying
        # the selected mesh is not a measurement mesh, with nothing on screen
        # naming the object to select instead. Selection state only: neither
        # object's geometry, visibility or provenance is touched, and the
        # source is one click (or the Source button above) away.
        try:
            for selected in list(context.selected_objects):
                selected.select_set(False)
            copy.select_set(True)
            context.view_layer.objects.active = copy
        except (AttributeError, RuntimeError):
            # Selection is a convenience. A scene that will not take it -
            # a hidden object, a context without a view layer - must not
            # turn a completed preprocessing run into a failure.
            pass

        self.report(
            {'WARNING'} if gate["refusals"] else {'INFO'},
            "BSMT: created '%s' - %s. %s"
            % (copy.name, accuracy,
               "Still not solver-safe: " + " ".join(gate["refusals"])
               if gate["refusals"] else "Passes the solver safety gate."),
        )
        return {'FINISHED'}

    @staticmethod
    def _diagnose(context, props, obj):
        canonical = geodesic.meshcache.get(context, obj, props.unit,
                                           rebuild=True)
        return dict(canonical.topology or {})


class BSMT_OT_toggle_measurement_copy(bpy.types.Operator):
    """Show the original scan or its measurement copy, one at a time.

    Visibility only. Neither object is deleted, and nothing is irreversible
    """

    bl_idname = "bsmt.toggle_measurement_copy"
    bl_label = "Toggle Source / Measurement Mesh"
    bl_description = ("Show the source scan or its measurement mesh, one at a"
                      " time")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            return False
        return (scancopy.resolve_source(obj) is not None
                or scancopy.find_measurement_copy(obj) is not None)

    def execute(self, context):
        obj = context.active_object
        source = scancopy.resolve_source(obj)
        if source is not None:
            copy = obj
        else:
            source = obj
            copy = scancopy.find_measurement_copy(obj)
        if source is None or copy is None:
            self.report({'ERROR'}, "BSMT: no original/copy pair found")
            return {'CANCELLED'}

        showing_copy = not copy.hide_viewport
        copy.hide_viewport = showing_copy
        source.hide_viewport = not showing_copy
        now = source.name if showing_copy else copy.name
        self.report({'INFO'}, "BSMT: showing '%s'" % now)
        return {'FINISHED'}


def _source_and_copy(context):
    """(source, copy) for the active object's preprocessing pair, or (None, None)."""
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return None, None
    source = scancopy.resolve_source(obj)
    if source is not None:
        return source, obj
    return obj, scancopy.find_measurement_copy(obj)


class BSMT_OT_show_scan(bpy.types.Operator):
    """Show the source scan or its measurement copy, one at a time.

    Visibility only. Neither object is deleted, neither is irreversibly
    hidden, and the researcher can flip back and forth to compare silhouette,
    landmark regions and texture registration
    """

    bl_idname = "bsmt.show_scan"
    bl_label = "Show"
    bl_description = ("Show the source scan, its measurement mesh, or both."
                      " Visibility only - nothing is deleted")
    bl_options = {'REGISTER'}

    which: EnumProperty(
        name="Which",
        items=(
            ('SOURCE', "Show Source", "Show the original scan"),
            ('COPY', "Show Measurement Mesh", "Show the measurement mesh"),
            ('BOTH', "Show Both", "Show both, to compare them directly"),
        ),
        default='SOURCE',
    )

    @classmethod
    def poll(cls, context):
        source, copy = _source_and_copy(context)
        return source is not None and copy is not None

    def execute(self, context):
        source, copy = _source_and_copy(context)
        if source is None or copy is None:
            self.report({'ERROR'}, "BSMT: no source/measurement-copy pair found")
            return {'CANCELLED'}
        # hide_viewport only. Nothing is unlinked and nothing is deleted, so
        # every one of these is one click from being undone (sect. 8).
        source.hide_viewport = self.which == 'COPY'
        copy.hide_viewport = self.which == 'SOURCE'
        shown = {
            'SOURCE': source.name,
            'COPY': copy.name,
            'BOTH': "%s and %s" % (source.name, copy.name),
        }[self.which]
        self.report({'INFO'}, "BSMT: showing %s" % shown)
        return {'FINISHED'}


class BSMT_OT_clear_preprocess_report(bpy.types.Operator):
    """Clear the preprocessing report. Objects are not touched"""

    bl_idname = "bsmt.clear_preprocess_report"
    bl_label = "Clear Report"
    bl_description = "Clear the preprocessing report. No object is touched"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        if props is not None:
            props.preprocess_report = ""
            props.preprocess_valid = False
            props.preprocess_copy_name = ""
            props.preprocess_status = ""
            props.preprocess_status_detail = ""
            props.preprocess_seconds = 0.0
            props.preprocess_diagnostic_seconds = 0.0
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Controlled mesh repair (Milestone 3.4)
# ---------------------------------------------------------------------------
#
# Every repair is an explicit action on a region the researcher selected.
# There is no "make it manifold" button and no global cleanup: on a human scan
# a global weld or a fill-everything pass fuses anatomically distinct surfaces
# that happen to touch, and the resulting geodesic is confidently wrong and
# systematically short.


def _repair_target(context):
    """The object repairs may run on, or (None, reason).

    Only a generated measurement copy qualifies. Refusing the original is the
    whole safety story of this milestone: the source scan is never modified.
    """
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return None, "select a mesh object"
    if visualization.is_helper(obj):
        return None, "'%s' is a BSMT helper, not a scan" % obj.name
    provenance = getattr(obj, "bsmt_scan", None)
    if provenance is None or not provenance.is_measurement_copy:
        return None, ("'%s' is not a measurement mesh. Repairs run only on a "
                      "mesh created by Scan Preprocessing, so the source scan "
                      "is never modified." % obj.name)
    return obj, ""


def _canonical_arrays(context, props, obj, rebuild=True):
    canonical = geodesic.meshcache.get(context, obj, props.unit,
                                       rebuild=rebuild)
    return canonical


def _component_facts(canonical, label, defect_vertices=(), cache=None):
    """Facts about one connected component, memoised per analysis pass.

    Memoised because most defects share a component and the measurement -
    which classifies every edge of the component - is the expensive part on a
    350,000-triangle body.
    """
    if cache is not None and label in cache:
        facts = dict(cache[label])
        facts["holds_focused_defect"] = bool(
            set(int(v) for v in defect_vertices)
            & set(int(v) for v in facts["vertex_indices"])
        )
        return facts
    facts = artifact.component_facts(
        canonical.vertices_solver, canonical.triangles,
        canonical.vertex_components, canonical.triangle_components,
        label, defect_vertex_indices=defect_vertices,
    )
    if cache is not None:
        cache[label] = facts
    return facts


def _fill_nonmanifold_defects(props, canonical):
    """Refill the focused-defect list from a freshly analysed canonical mesh.

    The list is rebuilt wholesale on every analysis rather than patched,
    because a geometry edit renumbers vertices, edges and triangles: a stored
    region id or component number from before an edit describes a mesh that
    no longer exists (sect. 7.7). `repair_geometry_hash`, set beside it, is
    what later lets an operator PROVE the numbers still apply.
    """
    props.repair_nonmanifold_defects.clear()
    props.repair_nonmanifold_index = 0
    props.repair_artifact_preview = ""
    try:
        regions = artifact.defect_regions(
            canonical.vertices_solver, canonical.triangles,
            canonical.vertex_components,
        )
    except Exception:                                 # noqa: BLE001
        traceback.print_exc()
        return 0

    cache = {}
    for region in regions:
        entry = props.repair_nonmanifold_defects.add()
        entry.region_id = region["region_id"]
        entry.edge_count = region["edge_count"]
        entry.vertex_count = region["vertex_count"]
        entry.bbox_diagonal_mm = region["bbox_diagonal_mm"]
        entry.label = artifact.describe_defect(region)
        local = viz.solver_to_local(canonical, [region["center_mm"]])[0]
        entry.center_local = tuple(float(value) for value in local)

        label = region["component"]
        if label < 0:
            entry.component_index = 0
            entry.deletion_block = (
                "this defect's endpoints do not agree on one connected "
                "component - re-analyze the mesh"
            )
            continue
        facts = _component_facts(canonical, label, region["vertex_indices"],
                                 cache)
        entry.component_index = facts["component_index"]
        entry.component_triangle_count = facts["triangle_count"]
        entry.component_vertex_count = facts["vertex_count"]
        entry.component_percent = facts["percent"]
        entry.component_is_largest = facts["is_largest"]
        entry.component_is_small = facts["is_small"]
        entry.component_bbox = tuple(facts["bbox_mm"])
        _allowed, _code, reason = artifact.deletion_block(facts)
        entry.deletion_block = reason
    return len(props.repair_nonmanifold_defects)


def _analyse_repair(context, props, obj):
    """Rebuild the diagnostics and refill the repair lists. Returns lines."""
    canonical = _canonical_arrays(context, props, obj)
    report = dict(canonical.topology or {})

    loops = repair.boundary_loops(canonical.vertices_solver,
                                  canonical.triangles)
    rows = repair.component_rows(canonical.component_triangle_counts,
                                 canonical.component_vertex_counts)

    state.clear_repair_lists(props)
    for loop in loops:
        entry = props.boundary_loops.add()
        entry.loop_id = loop["loop_id"]
        entry.edge_count = loop["edge_count"]
        entry.vertex_count = loop["vertex_count"]
        entry.perimeter_mm = loop["perimeter_mm"]
        entry.bbox_x, entry.bbox_y, entry.bbox_z = loop["bbox_mm"]
        entry.closed = loop["closed"]
        entry.label = repair.describe_loop(loop)
    for row in rows:
        entry = props.repair_components.add()
        entry.index = row["index"]
        entry.triangle_count = row["triangle_count"]
        entry.vertex_count = row["vertex_count"]
        entry.percent = row["percent"]
        entry.is_small = row["is_small"]
        entry.is_largest = row["is_largest"]
        entry.label = repair.describe_component(row)

    # Milestone 3.19: the one blocking defect this milestone can repair.
    # Collected by the same Analyze pass rather than by a second operator,
    # so the lists can never describe different states of the mesh.
    defects = repair.degenerate_defects(
        canonical.vertices_solver, canonical.triangles,
        duplicate_groups=geodesic.topology.exact_duplicate_groups(
            canonical.vertices_solver),
    )
    for defect in defects:
        entry = props.repair_degenerates.add()
        entry.triangle_index = defect["index"]
        entry.kind = defect["kind"]
        entry.label = repair.describe_degenerate(defect)
        entry.repairable = defect["kind"] == repair.DEGENERATE_COINCIDENT
        entry.centroid = defect["centroid"]
        entry.area = defect["area"]
        entry.merge_count = sum(len(drops) for _root, drops in defect["merges"])
    props.repair_degenerate_index = 0
    props.repair_degenerate_preview = ""

    # Milestone 3.28: the non-manifold defects, each already carrying the
    # connected component it sits in and whether that component may be
    # offered for deletion. Computed HERE, in the same pass as everything
    # else, so the panel cannot show a defect from one analysis beside a
    # component list from another.
    _fill_nonmanifold_defects(props, canonical)
    props.repair_geometry_hash = str(
        getattr(canonical, "geometry_hash", "") or "")

    verdict = repair.readiness(report)
    props.repair_readiness = "\n".join(repair.readiness_lines(verdict))
    props.repair_object = obj.name
    props.repair_valid = True

    lines = [
        "Diagnostics for '%s'" % obj.name,
        "  Vertices             %s" % "{:,}".format(report.get("vertex_count", 0)),
        "  Triangles            %s" % "{:,}".format(report.get("triangle_count", 0)),
        "  Connected components %d" % report.get("component_count", 0),
        "  Boundary edges       %d" % report.get("boundary_edge_count", 0),
        "  Non-manifold edges   %d" % report.get("nonmanifold_edge_count", 0),
        "  Degenerate triangles %d" % report.get("degenerate_triangle_count", 0),
        "  Coincident vertices  %d" % report.get("duplicate_vertex_count", 0),
        "",
    ]
    lines.extend(repair.readiness_lines(verdict))
    props.repair_report = "\n".join(lines)
    return canonical, report, verdict


def _after_repair(self, context, props, obj, action, before_report,
                  before_texture, detail=""):
    """Re-diagnose, verify texture, mark points stale, log. Returns (ok, msg)."""
    texture_ok, problems, _after_facts = meshrepair.verify_texture(
        obj, before_texture
    )
    canonical, after_report, verdict = _analyse_repair(context, props, obj)
    record = repair.repair_record(action, before_report, after_report, detail)

    lines = repair.repair_lines(record)
    if texture_ok:
        lines.append("  texture         preserved")
    else:
        lines.append("  TEXTURE LOST    %s" % "; ".join(problems))
    props.repair_log = ((props.repair_log + "\n" if props.repair_log else "")
                        + "\n".join(lines))
    print("[BSMT] repair: " + " | ".join(lines))

    # The mesh changed, so its geometry hash changed. Every SurfacePoint that
    # referred to the old surface is now STALE and is never re-projected.
    stale = _mark_points_stale(context, props, obj, canonical)
    if stale:
        print("[BSMT] repair invalidated %d landmark(s)/point(s) as STALE"
              % stale)

    if not texture_ok:
        return False, ("repair completed but the texture was lost: %s"
                       % "; ".join(problems))
    return True, "%s | %s" % (record["action"], verdict["headline"])


def _mark_points_stale(context, props, obj, canonical):
    """Mark every stored point on this object stale after a geometry change."""
    affected = 0
    for slot in ('A', 'B'):
        point = state.surface_point(props, slot)
        if point.valid and point.source_object == obj.name:
            reason = landmarks.stale_reason(
                point.geometry_hash, point.triangle_index,
                canonical.geometry_hash, canonical.triangle_count,
            )
            if reason:
                point.status = "STALE: " + reason
                affected += 1

    collection = state.get_landmarks(context)
    for item in collection or ():
        point = item.surface_point
        if point.valid and point.source_object == obj.name:
            state.refresh_landmark_status(item, canonical)
            if item.status != landmarks.STATUS_VALID:
                affected += 1
                state.invalidate_measurements_for_landmark(
                    context, item.stable_id,
                    "the mesh was repaired since this landmark was picked",
                )
                state.invalidate_regions_for_landmark(
                    context, item.stable_id,
                    "the mesh was repaired since this landmark was picked",
                )
    return affected


class BSMT_OT_analyse_repair(bpy.types.Operator):
    """Diagnose the selected measurement copy for repair.

    Read-only: nothing is modified
    """

    bl_idname = "bsmt.analyse_repair"
    bl_label = "Analyze Mesh"
    bl_description = ("Diagnose the selected measurement mesh: non-manifold"
                      " edges, boundary loops and components. Read-only")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _repair_target(context)[0] is not None

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        if geodesic.ensure_loaded():
            self.report({'ERROR'}, "BSMT: " + geodesic.ensure_loaded())
            return {'CANCELLED'}
        try:
            _canonical, report, verdict = _analyse_repair(context, props, obj)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report({'ERROR'}, "BSMT: analysis failed (%s)" % exc)
            return {'CANCELLED'}
        print("\n[BSMT] " + props.repair_report + "\n")
        self.report({'WARNING'} if not verdict["ready"] else {'INFO'},
                    "BSMT: %s - %d non-manifold, %d boundary, %d component(s)"
                    % (verdict["headline"],
                       report.get("nonmanifold_edge_count", 0),
                       report.get("boundary_edge_count", 0),
                       report.get("component_count", 0)))
        return {'FINISHED'}


class BSMT_OT_show_non_manifold(bpy.types.Operator):
    """Highlight the non-manifold edges in the viewport.

    Overlay only: the mesh is not modified
    """

    bl_idname = "bsmt.show_non_manifold"
    bl_label = "Show Non-Manifold Edges"
    bl_description = ("Highlight the non-manifold edges in the viewport."
                      " Overlay only - the mesh is not modified")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _repair_target(context)[0] is not None

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        geodesic.ensure_loaded()
        canonical = _canonical_arrays(context, props, obj, rebuild=False)
        edges = repair.classify_edges(canonical.triangles,
                                      canonical.vertex_count)["non_manifold"]
        if edges.shape[0] == 0:
            visualization.show_repair_edges(
                context, props, visualization.REPAIR_NON_MANIFOLD,
                [], [], obj.matrix_world,
                visualization.REPAIR_NON_MANIFOLD_COLOR,
                _defect_highlight_radius(canonical))
            self.report({'INFO'}, "BSMT: no non-manifold edges to show")
            return {'FINISHED'}

        used = np.unique(edges)
        remap = {int(v): i for i, v in enumerate(used)}
        points = canonical.vertices_local[used]
        local_edges = [(remap[int(a)], remap[int(b)]) for a, b in edges]
        visualization.show_repair_edges(
            context, props, visualization.REPAIR_NON_MANIFOLD,
            points, local_edges, obj.matrix_world,
            visualization.REPAIR_NON_MANIFOLD_COLOR,
            _defect_highlight_radius(canonical))
        self.report({'INFO'},
                    "BSMT: highlighted %d non-manifold edge(s)"
                    % edges.shape[0])
        return {'FINISHED'}


class BSMT_OT_focus_non_manifold(bpy.types.Operator):
    """Frame the viewport on the non-manifold edges.

    Moves the VIEW only. The scan is never moved, rotated or scaled
    """

    bl_idname = "bsmt.focus_non_manifold"
    bl_label = "Focus Non-Manifold Edges"
    bl_description = ("Point the 3D view at the non-manifold edges. The scan"
                      " itself is never moved")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _repair_target(context)[0] is not None

    def execute(self, context):
        # Deliberately a separate press, not something Show Edges does on its
        # own: BSMT never moves a researcher's view as a side effect. But a
        # defect one mesh edge long is about 0.3% of a subject's height, and
        # a highlight that is honest about its size is still something you
        # have to be looking at the right part of the body to see. This is
        # the same answer the degenerate-triangle stage already gives.
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        geodesic.ensure_loaded()
        canonical = _canonical_arrays(context, props, obj, rebuild=False)
        edges = repair.classify_edges(canonical.triangles,
                                      canonical.vertex_count)["non_manifold"]
        if edges.shape[0] == 0:
            self.report({'INFO'}, "BSMT: no non-manifold edges on '%s'"
                        % obj.name)
            return {'CANCELLED'}

        used = np.unique(edges)
        points = [obj.matrix_world @ Vector(
            (float(p[0]), float(p[1]), float(p[2])))
            for p in canonical.vertices_local[used]]
        center = Vector((0.0, 0.0, 0.0))
        for point in points:
            center += point
        center /= len(points)
        extent = max((point - center).length for point in points)

        # Every non-manifold edge in one view. With the usual handful of
        # edges in one place that is a close-up; with defects scattered over
        # the body it frames all of them, which is still an answer.
        scale = float(obj.matrix_world.to_scale().length / 3.0 ** 0.5)
        distance = max(extent * 4.0,
                       _defect_highlight_radius(canonical) * scale * 20.0,
                       1.0)
        moved = 0
        for area in getattr(context.screen, "areas", ()) or ():
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type != 'VIEW_3D' or space.region_3d is None:
                    continue
                space.region_3d.view_location = center
                space.region_3d.view_distance = distance
                moved += 1
            area.tag_redraw()
        if not moved:
            self.report({'WARNING'},
                        "BSMT: no 3D viewport to focus; the edges are around "
                        "(%.1f, %.1f, %.1f)" % (center.x, center.y, center.z))
            return {'CANCELLED'}
        self.report({'INFO'}, "BSMT: framed %d non-manifold edge(s)"
                    % edges.shape[0])
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# The focused defect, and the component it sits in (Milestone 3.28)
# ---------------------------------------------------------------------------
#
# A real scan carries small detached fragments, and one of them is often what
# holds the non-manifold edge that blocks exact measurement. Deleting that
# fragment is the right repair; deciding that it is a fragment and not anatomy
# is NOT something BSMT does. Everything below is the machinery for letting a
# researcher inspect one defect, see the whole component it belongs to, and
# then say so - and for refusing when that component is the body.


def _lists_match_mesh(props, obj, canonical):
    """Do the stored repair lists describe THIS mesh? (ok, reason).

    A geometry edit renumbers vertices, edges and triangles, so a stored
    region id or component number from before an edit names something else
    afterwards. The geometry hash is what turns "probably still valid" into a
    checkable fact, and nothing on the destructive path runs without it
    (sect. 7.7).

    The OBJECT is checked as well as the hash. Two measurement meshes copied
    from the same scan have the same geometry hash by construction, so the
    hash alone would let an analysis of one be applied to the other.
    """
    stored = str(getattr(props, "repair_geometry_hash", "") or "")
    live = str(getattr(canonical, "geometry_hash", "") or "")
    if not stored or not props.repair_valid:
        return False, ("this mesh has not been analyzed - press Analyze Mesh "
                       "first")
    if props.repair_object != obj.name:
        return False, ("the analysis on screen is of '%s', not '%s' - press "
                       "Analyze Mesh on this one first"
                       % (props.repair_object or "another mesh", obj.name))
    if stored != live:
        return False, ("the mesh changed since it was analyzed - press "
                       "Analyze Mesh again before deleting anything")
    return True, ""


def _focused_defect_region(props, canonical):
    """The FOCUSED defect, re-derived from `canonical`. (region, reason).

    Re-derived on every call, never read back from the stored entry: the
    stored numbers are a DISPLAY of one analysis, and the only thing carried
    across from it is the defect's identity - its region id, cross-checked
    against the position that analysis recorded. If the defect at that id has
    moved, the answer is "re-analyze", not an edit aimed at whatever is
    there now.

    One definition, used by everything that acts on the focused defect:
    component deletion (``_focused_component``) and local face repair both
    ask this, so they can never disagree about which defect is focused or
    whether it is still the one that was inspected.
    """
    entry = state.active_nonmanifold_defect(props)
    if entry is None:
        return None, ("no focused defect - press Analyze Mesh, then "
                      "select the defect you have inspected")
    regions = artifact.defect_regions(canonical.vertices_solver,
                                      canonical.triangles,
                                      canonical.vertex_components)
    region = next((candidate for candidate in regions
                   if candidate["region_id"] == int(entry.region_id)), None)
    if region is None:
        return None, ("defect %d is no longer present on this mesh - "
                      "re-analyze" % int(entry.region_id))

    local = viz.solver_to_local(canonical, [region["center_mm"]])[0]
    stored = np.asarray(tuple(entry.center_local), dtype=np.float64)
    tolerance = _local_length(
        canonical, max(float(entry.bbox_diagonal_mm), 1.0))
    if float(np.linalg.norm(local - stored)) > tolerance:
        return None, ("defect %d is not where it was analyzed - re-analyze "
                      "before changing anything" % int(entry.region_id))
    return region, ""


def _focused_component(props, canonical):
    """The component holding the focused defect. (facts, region, reason)."""
    region, why = _focused_defect_region(props, canonical)
    if region is None:
        return None, None, why

    if region["component"] < 0:
        return None, region, (
            "defect %d does not sit in a single connected component, so "
            "there is no component to delete - re-analyze"
            % int(region["region_id"])
        )
    facts = artifact.component_facts(
        canonical.vertices_solver, canonical.triangles,
        canonical.vertex_components, canonical.triangle_components,
        region["component"], defect_vertex_indices=region["vertex_indices"],
    )
    return facts, region, ""


def _component_local_geometry(canonical, facts):
    """The component's own triangles, compacted into local coordinates.

    Compacted on purpose: handing the helper builder the whole vertex array
    would put 350,000 unused vertices in a preview of a 400-triangle fragment.
    """
    used = np.asarray(facts["vertex_indices"], dtype=np.int64)
    triangles = np.asarray(facts["triangle_indices"], dtype=np.int64)
    if used.size == 0 or triangles.size == 0:
        return np.zeros((0, 3), dtype=np.float64), np.zeros((0, 3),
                                                            dtype=np.int64)
    remap = np.full(int(canonical.vertex_count), -1, dtype=np.int64)
    remap[used] = np.arange(used.size, dtype=np.int64)
    faces = remap[canonical.triangles[triangles]]
    return canonical.vertices_local[used], faces


def _measurements_on_object(context, object_name):
    return sum(1 for item in (state.get_measurements(context) or ())
               if item.result_object == object_name
               or item.path_object == object_name)


class BSMT_OT_step_nonmanifold_defect(bpy.types.Operator):
    """Step to the previous or next non-manifold defect.

    Selection only: the mesh is not modified and the view is not moved
    """

    bl_idname = "bsmt.step_nonmanifold_defect"
    bl_label = "Step Defect"
    bl_description = ("Select the previous or next non-manifold defect for"
                      " inspection")
    bl_options = {'REGISTER'}

    direction: EnumProperty(
        name="Direction",
        items=(('PREV', "Previous", "Select the previous defect"),
               ('NEXT', "Next", "Select the next defect")),
        default='NEXT',
    )

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return props is not None and len(props.repair_nonmanifold_defects) > 1

    def execute(self, context):
        props = state.get_props(context)
        total = len(props.repair_nonmanifold_defects)
        if not total:
            return {'CANCELLED'}
        step = 1 if self.direction == 'NEXT' else -1
        props.repair_nonmanifold_index = (
            (props.repair_nonmanifold_index + step) % total
        )
        # The previous defect's component preview and local-topology
        # inspection both describe a DIFFERENT defect now, so neither
        # survives the step - nor do the highlights they drew.
        props.repair_artifact_preview = ""
        state.clear_local_repair(props)
        for name in (visualization.REPAIR_COMPONENT,
                     visualization.REPAIR_LOCAL_CANDIDATE):
            visualization.remove_object(bpy.data.objects.get(name))
        entry = state.active_nonmanifold_defect(props)
        self.report({'INFO'}, "BSMT: defect %d / %d - %s"
                    % (props.repair_nonmanifold_index + 1, total,
                       entry.label if entry else "?"))
        return {'FINISHED'}


class BSMT_OT_focus_nonmanifold_defect(bpy.types.Operator):
    """Frame the viewport on the focused non-manifold defect.

    Moves the VIEW only. The scan is never moved, rotated or scaled
    """

    bl_idname = "bsmt.focus_nonmanifold_defect"
    bl_label = "Focus Selected Defect"
    bl_description = ("Point the 3D view at the focused non-manifold defect."
                      " The scan itself is never moved")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return (props is not None
                and state.active_nonmanifold_defect(props) is not None
                and _repair_target(context)[0] is not None)

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        entry = state.active_nonmanifold_defect(props)
        if obj is None or entry is None:
            self.report({'ERROR'}, "BSMT: " + (reason or "no defect focused"))
            return {'CANCELLED'}
        try:
            canonical = _canonical_arrays(context, props, obj, rebuild=False)
        except Exception as exc:                      # noqa: BLE001
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        local = tuple(float(value) for value in entry.center_local)
        world = obj.matrix_world @ Vector(local)
        # view_distance is a WORLD length. The defect's extent is recorded in
        # physical millimetres, so it comes back through the unit multiplier;
        # the highlight radius is LOCAL, so it comes back through the scale.
        scale = float(obj.matrix_world.to_scale().length / 3.0 ** 0.5)
        multiplier = float(getattr(canonical, "unit_multiplier", 1.0) or 1.0)
        distance = max(
            float(entry.bbox_diagonal_mm) / multiplier * 4.0,
            _defect_highlight_radius(canonical) * scale * 20.0,
            1.0,
        )
        moved = 0
        for area in getattr(context.screen, "areas", ()) or ():
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type != 'VIEW_3D' or space.region_3d is None:
                    continue
                space.region_3d.view_location = world
                space.region_3d.view_distance = distance
                moved += 1
            area.tag_redraw()
        if not moved:
            self.report({'WARNING'},
                        "BSMT: no 3D viewport to focus; defect %d is around "
                        "(%.1f, %.1f, %.1f)"
                        % (int(entry.region_id), world.x, world.y, world.z))
            return {'CANCELLED'}
        self.report({'INFO'}, "BSMT: framed defect %d / %d"
                    % (props.repair_nonmanifold_index + 1,
                       len(props.repair_nonmanifold_defects)))
        return {'FINISHED'}


class BSMT_OT_preview_artifact_component(bpy.types.Operator):
    """Show the WHOLE connected component the focused defect belongs to.

    Read-only. Nothing is deleted, nothing is selected, and the mesh is not
    modified - this is what the deletion would remove, drawn before deciding
    """

    bl_idname = "bsmt.preview_artifact_component"
    bl_label = "Preview Artifact"
    bl_description = ("Highlight the entire connected component holding the"
                      " focused defect, and state what deleting it would"
                      " remove. Nothing is modified")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return (props is not None
                and state.active_nonmanifold_defect(props) is not None
                and _repair_target(context)[0] is not None)

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        try:
            canonical = _canonical_arrays(context, props, obj, rebuild=False)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        current, why = _lists_match_mesh(props, obj, canonical)
        if not current:
            props.repair_artifact_preview = why
            self.report({'WARNING'}, "BSMT: " + why)
            return {'CANCELLED'}

        facts, _region, why = _focused_component(props, canonical)
        if facts is None:
            props.repair_artifact_preview = why
            self.report({'WARNING'}, "BSMT: " + why)
            return {'CANCELLED'}

        points, faces = _component_local_geometry(canonical, facts)
        _helper, drawing = visualization.show_repair_surface(
            context, visualization.REPAIR_COMPONENT, points, faces,
            obj.matrix_world, visualization.REPAIR_COMPONENT_COLOR,
        )

        allowed, _code, block = artifact.deletion_block(facts)
        lines = artifact.confirmation_lines(
            facts, obj.name,
            _landmarks_on_object(context, obj.name),
            _measurements_on_object(context, obj.name),
        )
        if not allowed:
            lines = ["DELETION BLOCKED", block, ""] + lines[5:]
        if drawing:
            lines.append("")
            lines.append("Not highlighted: %s." % drawing)
        props.repair_artifact_preview = "\n".join(lines)
        print("[BSMT] artifact preview on '%s': %s"
              % (obj.name, artifact.describe_component(facts)))
        self.report({'INFO'} if allowed else {'WARNING'},
                    "BSMT: %s%s" % (artifact.describe_component(facts),
                                    "" if allowed else " - deletion blocked"))
        return {'FINISHED'}


class BSMT_OT_show_boundary_loop(bpy.types.Operator):
    """Highlight the selected boundary loop. Overlay only"""

    bl_idname = "bsmt.show_boundary_loop"
    bl_label = "Show Boundary"
    bl_description = ("Highlight the selected boundary loop. Overlay only -"
                      " the mesh is not modified")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return (props is not None and len(props.boundary_loops) > 0
                and _repair_target(context)[0] is not None)

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        entry = state.active_boundary_loop(props)
        if entry is None:
            self.report({'WARNING'}, "BSMT: select a boundary loop first")
            return {'CANCELLED'}

        canonical = _canonical_arrays(context, props, obj, rebuild=False)
        loops = repair.boundary_loops(canonical.vertices_solver,
                                      canonical.triangles)
        loop = next((entry_loop for entry_loop in loops
                     if entry_loop["loop_id"] == entry.loop_id), None)
        if loop is None:
            self.report({'ERROR'},
                        "BSMT: that loop no longer exists - re-analyse")
            return {'CANCELLED'}

        edges = loop["edges"]
        used = np.unique(edges)
        remap = {int(v): i for i, v in enumerate(used)}
        visualization.show_repair_edges(
            context, props, visualization.REPAIR_BOUNDARY,
            canonical.vertices_local[used],
            [(remap[int(a)], remap[int(b)]) for a, b in edges],
            obj.matrix_world, visualization.REPAIR_BOUNDARY_COLOR,
            _defect_highlight_radius(canonical))
        self.report({'INFO'}, "BSMT: highlighted loop %d (%d edges, %.1f mm)"
                    % (loop["loop_id"], loop["edge_count"],
                       loop["perimeter_mm"]))
        return {'FINISHED'}


def _degenerate_defect_objects(context, props, obj):
    """Rebuild the pure defect records for the CURRENT canonical mesh.

    The stored list is display state; a repair has to act on defects derived
    from the mesh as it is now, so this re-derives them rather than trusting
    property rows that may predate an edit.
    """
    canonical = geodesic.meshcache.get(context, obj, props.unit)
    return canonical, repair.degenerate_defects(
        canonical.vertices_solver, canonical.triangles,
        duplicate_groups=geodesic.topology.exact_duplicate_groups(
            canonical.vertices_solver),
    )


def _chosen_defects(props, defects):
    """The defects the current scope selects. Empty means nothing to do."""
    if props.repair_degenerate_scope == 'SELECTED':
        entry = state.active_degenerate_defect(props)
        if entry is None:
            return []
        return [defect for defect in defects
                if defect["index"] == entry.triangle_index]
    return list(defects)


def _local_length(canonical, length_mm):
    """A physical millimetre length, expressed in the object's LOCAL units.

    Highlight sizes are decided in millimetres, against the scan's own
    bounding box, because that is the only scale a researcher can reason
    about. The helper meshes, though, are built in object-local coordinates
    and then given the object's world matrix - so a size in millimetres has
    to come back through both the unit multiplier and the object's scale. A
    highlight that is the right size only when the scan happens to be stored
    in millimetres with scale 1 is not the right size; it is a coincidence.
    """
    multiplier = float(getattr(canonical, "unit_multiplier", 1.0) or 1.0)
    matrix = np.asarray(canonical.matrix_world, dtype=np.float64)
    scale = float(np.mean([np.linalg.norm(matrix[:3, column])
                           for column in range(3)]))
    if scale <= 0.0:
        scale = 1.0
    return float(length_mm) / multiplier / scale


def _defect_marker_size(canonical):
    """Marker size for a defect cross: small against the body, still visible."""
    report = canonical.topology or {}
    diagonal = float(report.get("bbox_diagonal", 0.0) or 0.0)
    return _local_length(canonical, max(diagonal * 0.005, 1.0))


def _defect_highlight_radius(canonical):
    """Rod radius for a defect highlight, in the object's local units.

    0.4% of the scan's bounding-box diagonal, floored at 0.5 mm: a 7 mm
    radius on a 1.7 m body, so the mark is about three times the diameter of
    a landmark marker and reads at the zoom a researcher looks at a whole
    scan from, while staying slender enough not to bury the surface under it.
    Only the thickness is exaggerated - a highlight's endpoints are always
    the defect's own.
    """
    report = canonical.topology or {}
    diagonal = float(report.get("bbox_diagonal", 0.0) or 0.0)
    return _local_length(canonical, max(diagonal * 0.004, 0.5))


class BSMT_OT_show_degenerate_triangles(bpy.types.Operator):
    """Mark every degenerate triangle in the viewport.

    Overlay only: the mesh is never modified, nothing is selected, and the
    markers are removed by Clear Highlights
    """

    bl_idname = "bsmt.show_degenerate_triangles"
    bl_label = "Show Degenerate Triangles"
    bl_description = ("Mark the degenerate triangles in the viewport. A"
                      " zero-area triangle has nothing to see, so each one is"
                      " marked with a cross. Overlay only")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _repair_target(context)[0] is not None

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        try:
            canonical, defects = _degenerate_defect_objects(context, props, obj)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report({'ERROR'}, "BSMT: analysis failed (%s)" % exc)
            return {'CANCELLED'}
        if not defects:
            visualization.clear_repair_highlights()
            self.report({'INFO'}, "BSMT: no degenerate triangles on '%s'"
                        % obj.name)
            return {'FINISHED'}

        size = _defect_marker_size(canonical)
        locals_all = [
            viz.solver_to_local(canonical, [defect["centroid"]])[0]
            for defect in defects
        ]
        visualization.show_repair_markers(
            context, visualization.REPAIR_DEGENERATE, locals_all, size,
            obj.matrix_world, visualization.REPAIR_DEGENERATE_COLOR,
            _defect_highlight_radius(canonical),
        )
        self._mark_active(context, props, canonical, defects, obj, size)
        self.report({'INFO'}, "BSMT: marked %d degenerate triangle(s)"
                    % len(defects))
        return {'FINISHED'}

    @staticmethod
    def _mark_active(context, props, canonical, defects, obj, size):
        """A second, larger, white marker on the selected defect."""
        entry = state.active_degenerate_defect(props)
        if entry is None:
            return
        chosen = [defect for defect in defects
                  if defect["index"] == entry.triangle_index]
        if not chosen:
            return
        point = viz.solver_to_local(canonical, [chosen[0]["centroid"]])[0]
        visualization.show_repair_markers(
            context, visualization.REPAIR_DEGENERATE_ACTIVE, [point],
            size * 2.0, obj.matrix_world,
            visualization.REPAIR_DEGENERATE_ACTIVE_COLOR,
            _defect_highlight_radius(canonical) * 1.5,
        )


class BSMT_OT_step_degenerate_defect(bpy.types.Operator):
    """Step to the previous or next degenerate triangle.

    Selection only: the mesh is not modified and the view is not moved
    """

    bl_idname = "bsmt.step_degenerate_defect"
    bl_label = "Step Defect"
    bl_description = "Select the previous or next degenerate triangle"
    bl_options = {'REGISTER'}

    direction: EnumProperty(
        name="Direction",
        items=(('PREV', "Previous", "Select the previous defect"),
               ('NEXT', "Next", "Select the next defect")),
        default='NEXT',
    )

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return props is not None and len(props.repair_degenerates) > 1

    def execute(self, context):
        props = state.get_props(context)
        total = len(props.repair_degenerates)
        if not total:
            return {'CANCELLED'}
        step = 1 if self.direction == 'NEXT' else -1
        props.repair_degenerate_index = (
            (props.repair_degenerate_index + step) % total
        )
        entry = state.active_degenerate_defect(props)
        props.repair_degenerate_preview = ""
        self.report({'INFO'}, "BSMT: defect %d / %d - %s"
                    % (props.repair_degenerate_index + 1, total,
                       entry.label if entry else "?"))
        return {'FINISHED'}


class BSMT_OT_focus_degenerate_defect(bpy.types.Operator):
    """Frame the viewport on the selected degenerate triangle.

    Moves the VIEW only. The scan is never moved, rotated or scaled
    """

    bl_idname = "bsmt.focus_degenerate_defect"
    bl_label = "Focus Selected Defect"
    bl_description = ("Point the 3D view at the selected degenerate triangle."
                      " The scan itself is never moved")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return (props is not None
                and state.active_degenerate_defect(props) is not None)

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        entry = state.active_degenerate_defect(props)
        if obj is None or entry is None:
            self.report({'ERROR'}, "BSMT: " + (reason or "no defect selected"))
            return {'CANCELLED'}
        try:
            canonical = geodesic.meshcache.get(context, obj, props.unit)
        except Exception as exc:                      # noqa: BLE001
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        local = viz.solver_to_local(canonical, [tuple(entry.centroid)])[0]
        world = obj.matrix_world @ Vector(
            (float(local[0]), float(local[1]), float(local[2]))
        )
        moved = 0
        for area in getattr(context.screen, "areas", ()) or ():
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type != 'VIEW_3D' or space.region_3d is None:
                    continue
                # Only the view's own pivot and zoom. Nothing on the object.
                space.region_3d.view_location = world
                # view_distance is a WORLD length, so the marker size -
                # which is local - comes back through the object's scale.
                space.region_3d.view_distance = max(
                    _defect_marker_size(canonical)
                    * float(obj.matrix_world.to_scale().length / 3.0 ** 0.5)
                    * 20.0, 1.0
                )
                moved += 1
            area.tag_redraw()
        if not moved:
            self.report({'WARNING'},
                        "BSMT: no 3D viewport to focus; the defect is at "
                        "(%.1f, %.1f, %.1f) mm" % tuple(entry.centroid))
            return {'CANCELLED'}
        self.report({'INFO'}, "BSMT: focused defect %d / %d"
                    % (props.repair_degenerate_index + 1,
                       len(props.repair_degenerates)))
        return {'FINISHED'}


class BSMT_OT_preview_degenerate_repair(bpy.types.Operator):
    """Say exactly what a local repair would do. Changes nothing.

    If no safe local repair exists it says so rather than guessing
    """

    bl_idname = "bsmt.preview_degenerate_repair"
    bl_label = "Preview Repair"
    bl_description = ("Describe exactly what a local repair would merge and"
                      " remove. Nothing is modified")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _repair_target(context)[0] is not None

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        try:
            _canonical, defects = _degenerate_defect_objects(context, props, obj)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report({'ERROR'}, "BSMT: analysis failed (%s)" % exc)
            return {'CANCELLED'}

        chosen = _chosen_defects(props, defects)
        plan = repair.plan_degenerate_repair(
            _canonical.vertices_solver, _canonical.triangles, chosen
        )
        message = plan["summary"] if plan["safe"] else plan["reason"]
        props.repair_degenerate_preview = message
        print("[BSMT] repair preview on '%s': %s" % (obj.name, message))
        self.report({'INFO'} if plan["safe"] else {'WARNING'},
                    "BSMT: " + message)
        return {'FINISHED'}


class BSMT_OT_repair_degenerate_local(bpy.types.Operator):
    """Merge the exactly coincident vertices behind degenerate triangles.

    Strictly local: only vertices inside the chosen degenerate triangles are
    merged, and only when they sit at bit-identically the same position.
    There is no distance tolerance, and no global weld
    """

    bl_idname = "bsmt.repair_degenerate_local"
    bl_label = "Apply Repair"
    bl_description = ("Merge only the exactly coincident vertices inside the"
                      " chosen degenerate triangles, and drop the faces that"
                      " lose their area. No global weld, no tolerance")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or props.repair_running:
            return False
        return _repair_target(context)[0] is not None

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}

        props.repair_running = True
        try:
            return self._run(context, props, obj)
        finally:
            props.repair_running = False

    def _run(self, context, props, obj):
        import datetime

        try:
            canonical, defects = _degenerate_defect_objects(context, props, obj)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report({'ERROR'}, "BSMT: analysis failed (%s)" % exc)
            return {'CANCELLED'}

        before = dict(canonical.topology or {})
        chosen = _chosen_defects(props, defects)
        plan = repair.plan_degenerate_repair(
            canonical.vertices_solver, canonical.triangles, chosen
        )
        if not plan["safe"]:
            props.repair_degenerate_preview = plan["reason"]
            self.report({'WARNING'}, "BSMT: " + plan["reason"])
            return {'CANCELLED'}

        landmark_count = _landmarks_on_object(context, obj.name)
        backup = meshrepair.make_backup(obj)
        texture_before = scancopy.audit_object(obj)

        try:
            applied = meshrepair.apply_degenerate_plan(obj, plan)
        except meshrepair.RepairAborted as exc:
            meshrepair.restore_backup(obj, backup)
            meshrepair.discard_backup(backup)
            self.report({'ERROR'}, "BSMT: repair aborted - %s" % exc)
            return {'CANCELLED'}
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            meshrepair.restore_backup(obj, backup)
            meshrepair.discard_backup(backup)
            self.report({'ERROR'}, "BSMT: repair failed (%s)" % exc)
            return {'CANCELLED'}

        # --- sect. 17: the mesh must not have got worse -------------------
        try:
            after_canonical = geodesic.meshcache.get(context, obj, props.unit,
                                                     rebuild=True)
            after = dict(after_canonical.topology or {})
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            meshrepair.restore_backup(obj, backup)
            meshrepair.discard_backup(backup)
            self.report({'ERROR'},
                        "BSMT: the repaired mesh could not be re-analyzed "
                        "(%s); the mesh has been restored" % exc)
            return {'CANCELLED'}

        ok, problems = repair.verify_degenerate_repair(before, after)
        texture_ok, texture_problems, _notes = preprocess.compare_texture(
            texture_before, scancopy.audit_object(obj)
        )
        if not texture_ok:
            ok = False
            problems.extend(texture_problems)

        if not ok:
            meshrepair.restore_backup(obj, backup)
            meshrepair.discard_backup(backup)
            geodesic.meshcache.get(context, obj, props.unit, rebuild=True)
            message = ("the repair made the topology worse (%s); the mesh has "
                       "been restored" % "; ".join(problems))
            props.repair_degenerate_preview = message
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        meshrepair.discard_backup(backup)

        # --- sect. 12: nothing may keep a pre-repair verdict --------------
        stale = state.invalidate_for_geometry_change(context, obj.name)
        _analyse_repair(context, props, obj)

        _record_repair_provenance(obj, plan, applied, before, after,
                                  datetime.datetime.now())

        verdict, reasons = preprocess.classify_ready(after)
        summary = (
            "merged %d coincident vertex/vertices, removed %d face(s); "
            "degenerate %d -> %d; verdict %s"
            % (applied["merged_vertices"], applied["removed_faces"],
               int(before.get("degenerate_triangle_count", 0) or 0),
               int(after.get("degenerate_triangle_count", 0) or 0),
               preprocess.READY_LABELS.get(verdict, verdict))
        )
        props.repair_degenerate_preview = summary
        note = ""
        if landmark_count:
            note = (" %d landmark(s) on this mesh are now STALE and must be "
                    "re-picked." % landmark_count)
        print("[BSMT] repair on '%s': %s%s" % (obj.name, summary, note))
        if reasons:
            print("[BSMT]   remaining: %s" % "; ".join(reasons[:3]))
        self.report({'INFO'}, "BSMT: " + summary + note)
        return {'FINISHED'}


def _record_repair_provenance(obj, plan, applied, before, after, when):
    """Append repair facts to the mesh's existing provenance (sect. 15).

    Appended, never overwritten: the preprocessing record says where this
    mesh came from and must survive, because a repaired mesh is still a
    decimated copy of a particular scan.
    """
    provenance = getattr(obj, "bsmt_scan", None)
    if provenance is None:
        return None
    provenance.repair_applied = True
    provenance.repair_type = repair.DEGENERATE_COINCIDENT
    provenance.repair_degenerate_before = int(
        before.get("degenerate_triangle_count", 0) or 0)
    provenance.repair_degenerate_after = int(
        after.get("degenerate_triangle_count", 0) or 0)
    provenance.repair_merged_vertices = int(applied["merged_vertices"])
    provenance.repair_removed_faces = int(applied["removed_faces"])
    provenance.repair_version = _addon_version()
    provenance.repair_created = when.strftime("%Y-%m-%d %H:%M:%S")
    return provenance


class BSMT_OT_clear_repair_highlight(bpy.types.Operator):
    """Remove the repair highlights. Nothing else is affected"""

    bl_idname = "bsmt.clear_repair_highlight"
    bl_label = "Clear Highlight"
    bl_description = "Remove the repair highlights. Nothing else is affected"
    bl_options = {'REGISTER'}

    def execute(self, context):
        removed = visualization.clear_repair_highlights()
        self.report({'INFO'}, "BSMT: removed %d highlight(s)" % removed)
        return {'FINISHED'}


class _RepairBase(bpy.types.Operator):
    """Shared safety wrapper: back up, edit, re-diagnose, verify texture."""

    bl_options = {'REGISTER', 'UNDO'}

    def _guarded(self, context, action, work, detail="",
                 require_nonmanifold_decrease=False, verify=None):
        """Run `work` inside the backup / re-diagnose / accept-or-revert loop.

        `verify` is an optional second acceptance test, called with
        (before_report, after_report, outcome) and returning a list of
        reasons to REVERT. It can only ever add reasons: the existing
        `repair.accept_repair` rule is asked first and in full, so a caller
        cannot use this to make acceptance more permissive.
        """
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        if props.repair_running:
            self.report({'WARNING'}, "BSMT: a repair is already running")
            return {'CANCELLED'}
        if geodesic.ensure_loaded():
            self.report({'ERROR'}, "BSMT: " + geodesic.ensure_loaded())
            return {'CANCELLED'}

        source = scancopy.resolve_source(obj)
        source_triangles = (scancopy.triangle_count(source.data)
                            if source is not None else None)

        canonical = _canonical_arrays(context, props, obj)
        before_report = dict(canonical.topology or {})
        before_texture = scancopy.audit_object(obj)

        # Backed up before anything is touched, so a repair can be undone even
        # if the undo stack has been disturbed.
        backup = meshrepair.make_backup(obj)
        props.repair_backup_mesh = backup
        props.repair_running = True
        try:
            outcome = work(obj, canonical, before_report)
        except meshrepair.RepairAborted as exc:
            meshrepair.restore_backup(obj, backup)
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            meshrepair.restore_backup(obj, backup)
            self.report({'ERROR'},
                        "BSMT: repair failed and was rolled back (%s: %s)"
                        % (type(exc).__name__, exc))
            return {'CANCELLED'}
        finally:
            props.repair_running = False

        texture_ok, _problems, _facts = meshrepair.verify_texture(
            obj, before_texture)
        # Guarded: a mesh that cannot be re-analysed is a mesh whose state
        # nothing can vouch for, and leaving that edit in place - which an
        # unhandled exception here did - is worse than any repair it might
        # have been.
        try:
            after_probe = dict(
                _canonical_arrays(context, props, obj).topology or {})
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            meshrepair.restore_backup(obj, backup)
            self.report({'ERROR'},
                        "BSMT: the edited mesh could not be re-analyzed "
                        "(%s: %s); it has been restored"
                        % (type(exc).__name__, exc))
            return {'CANCELLED'}
        accepted, why = repair.accept_repair(
            before_report, after_probe, texture_ok,
            require_nonmanifold_decrease=require_nonmanifold_decrease)
        if verify is not None:
            extra = list(verify(before_report, after_probe, outcome) or ())
            if extra:
                accepted = False
                why = list(why) + extra
        if not accepted:
            meshrepair.restore_backup(obj, backup)
            _analyse_repair(context, props, obj)
            message = "; ".join(why)
            print("[BSMT] repair REVERTED: %s" % message)
            props.repair_log = ((props.repair_log + "\n" if props.repair_log
                                 else "") + "%s REVERTED - %s"
                                % (action, message))
            self.report({'ERROR'},
                        "BSMT: repair reverted - %s" % message)
            return {'CANCELLED'}

        ok, message = _after_repair(self, context, props, obj, action,
                                    before_report, before_texture,
                                    detail or str(outcome))
        if not ok:
            meshrepair.restore_backup(obj, backup)
            _analyse_repair(context, props, obj)
            self.report({'ERROR'}, "BSMT: %s - rolled back" % message)
            return {'CANCELLED'}

        if source is not None and source_triangles is not None:
            if scancopy.triangle_count(source.data) != source_triangles:
                self.report({'ERROR'},
                            "BSMT: the SOURCE scan changed during a repair. "
                            "This is a bug; do not trust the copy.")
                return {'CANCELLED'}

        self.report({'INFO'}, "BSMT: " + message)
        return {'FINISHED'}


class BSMT_OT_fill_boundary_loop(_RepairBase):
    """Fill the selected boundary loop and triangulate the new faces.

    Only the selected loop. No other boundary in the mesh is touched
    """

    bl_idname = "bsmt.fill_boundary_loop"
    bl_label = "Fill Selected Hole"
    bl_description = ("Fill the selected boundary loop with triangles."
                      " Accepted only if it does not make the topology worse")

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return (props is not None and len(props.boundary_loops) > 0
                and _repair_target(context)[0] is not None)

    def execute(self, context):
        props = state.get_props(context)
        entry = state.active_boundary_loop(props)
        if entry is None:
            self.report({'WARNING'}, "BSMT: select a boundary loop first")
            return {'CANCELLED'}
        loop_id = entry.loop_id

        def work(obj, canonical, _before):
            loops = repair.boundary_loops(canonical.vertices_solver,
                                          canonical.triangles)
            loop = next((entry_loop for entry_loop in loops
                         if entry_loop["loop_id"] == loop_id), None)
            if loop is None:
                raise meshrepair.RepairAborted(
                    "boundary loop %d no longer exists - re-analyse" % loop_id
                )
            filled = meshrepair.fill_boundary_loop(obj, loop["edges"])
            return "loop %d, %d face(s) created" % (loop_id, filled)

        return self._guarded(context, "Fill boundary loop", work)


class BSMT_OT_remove_small_component(_RepairBase):
    """Delete the selected connected component.

    Explicit and per-component. A component is never removed automatically,
    and never merely because it is smaller than another
    """

    bl_idname = "bsmt.remove_small_component"
    bl_label = "Delete Component"
    bl_description = ("Delete the selected connected component. Only ever"
                      " removes what is listed")

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or len(props.repair_components) < 2:
            return False
        return _repair_target(context)[0] is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        props = state.get_props(context)
        entry = state.active_repair_component(props)
        if entry is None:
            self.report({'WARNING'}, "BSMT: select a component first")
            return {'CANCELLED'}
        if entry.is_largest:
            self.report({'ERROR'},
                        "BSMT: refusing to remove the largest component - "
                        "that is the body")
            return {'CANCELLED'}
        component_index = entry.index

        def work(obj, canonical, _before):
            labels = np.asarray(canonical.vertex_components)
            wanted = np.where(labels == (component_index - 1))[0]
            if wanted.size == 0:
                raise meshrepair.RepairAborted(
                    "component %d no longer exists - re-analyse"
                    % component_index
                )
            removed = meshrepair.remove_component(obj, wanted)
            return "component %d, %d vertices removed" % (component_index,
                                                          removed)

        return self._guarded(context, "Remove component", work)


class BSMT_OT_delete_defect_component(_RepairBase):
    """Delete the whole connected component holding the focused defect.

    The researcher's decision, never BSMT's: nothing here judges whether
    geometry is anatomically relevant. Refused outright when that component
    is the primary body
    """

    bl_idname = "bsmt.delete_defect_component"
    bl_label = "Delete Artifact"
    bl_description = ("Permanently delete the connected component containing"
                      " the focused defect, from the MEASUREMENT MESH only."
                      " Refused when that component is the primary body")

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or props.repair_running:
            return False
        if state.active_nonmanifold_defect(props) is None:
            return False
        return _repair_target(context)[0] is not None

    # -- confirmation --------------------------------------------------

    def invoke(self, context, event):
        """Say exactly what goes, before it goes.

        Not `invoke_confirm`: a one-line "OK?" cannot state the component's
        size, that the source scan is safe, or that measurements stop being
        trustworthy - and those are precisely the three things a researcher
        can only regret not having been told beforehand.
        """
        props = state.get_props(context)
        obj, _reason = _repair_target(context)
        if props is not None and obj is not None:
            try:
                canonical = _canonical_arrays(context, props, obj,
                                              rebuild=False)
                current, why = _lists_match_mesh(props, obj, canonical)
                facts = None
                if current:
                    facts, _region, why = _focused_component(props, canonical)
                if facts is not None:
                    props.repair_artifact_preview = "\n".join(
                        artifact.confirmation_lines(
                            facts, obj.name,
                            _landmarks_on_object(context, obj.name),
                            _measurements_on_object(context, obj.name),
                        )
                    )
                else:
                    props.repair_artifact_preview = why
            except Exception:                         # noqa: BLE001
                traceback.print_exc()
        window_manager = context.window_manager
        try:
            return window_manager.invoke_props_dialog(
                self, width=420, confirm_text="Delete Artifact")
        except TypeError:
            # Blender before 4.1 has no confirm_text.
            return window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        props = state.get_props(context)
        layout = self.layout
        column = layout.column(align=True)
        column.scale_y = 0.8
        text = (props.repair_artifact_preview if props is not None else "")
        if not text:
            column.label(text="Delete the component holding the focused "
                              "defect?", icon='ERROR')
            return
        for line in text.split("\n"):
            if not line.strip():
                column.separator()
            elif line.startswith("DELETION BLOCKED") or line.startswith("NOTE"):
                row = column.row()
                row.alert = True
                row.label(text=line)
            else:
                column.label(text=line)

    # -- the operation -------------------------------------------------

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        if geodesic.ensure_loaded():
            self.report({'ERROR'}, "BSMT: " + geodesic.ensure_loaded())
            return {'CANCELLED'}

        # --- pre-flight, before a single byte of geometry is touched -----
        try:
            canonical = _canonical_arrays(context, props, obj, rebuild=False)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}
        current, why = _lists_match_mesh(props, obj, canonical)
        if not current:
            self.report({'ERROR'}, "BSMT: " + why)
            return {'CANCELLED'}
        facts, _region, why = _focused_component(props, canonical)
        if facts is None:
            self.report({'ERROR'}, "BSMT: " + why)
            return {'CANCELLED'}
        allowed, _code, block = artifact.deletion_block(facts)
        if not allowed:
            props.repair_artifact_preview = "DELETION BLOCKED\n" + block
            print("[BSMT] artifact deletion refused on '%s': %s"
                  % (obj.name, block))
            self.report({'ERROR'}, "BSMT: " + block)
            return {'CANCELLED'}

        captured = {}

        def work(work_obj, fresh, _before):
            # Re-derived against the canonical mesh the wrapper just REBUILT,
            # so the vertex indices handed to bmesh come from the same mesh
            # state the acceptance test will be measured against. The
            # pre-flight above is what refuses; this is what acts.
            ok, why_fresh = _lists_match_mesh(props, work_obj, fresh)
            if not ok:
                raise meshrepair.RepairAborted(why_fresh)
            fresh_facts, _fresh_region, why_fresh = _focused_component(
                props, fresh)
            if fresh_facts is None:
                raise meshrepair.RepairAborted(why_fresh)
            fresh_allowed, _fresh_code, fresh_block = artifact.deletion_block(
                fresh_facts)
            if not fresh_allowed:
                raise meshrepair.RepairAborted(fresh_block)
            captured["facts"] = fresh_facts
            removed = meshrepair.remove_component(
                work_obj, fresh_facts["vertex_indices"])
            return ("component %d - %s triangle(s), %d vertex/vertices "
                    "removed, %.1f mm across"
                    % (fresh_facts["component_index"],
                       "{:,}".format(fresh_facts["triangle_count"]), removed,
                       fresh_facts["bbox_diagonal_mm"]))

        def verify(before_report, after_report, _outcome):
            removed_facts = captured.get("facts")
            if removed_facts is None:
                return ["the deletion did not record what it removed"]
            return artifact.extra_deletion_reasons(
                before_report, after_report,
                {"triangle_count": removed_facts["triangle_count"],
                 "primary_triangle_count":
                     removed_facts["largest_triangle_count"]},
            )

        result = self._guarded(context, "Delete defect component", work,
                               require_nonmanifold_decrease=True,
                               verify=verify)
        if result != {'FINISHED'}:
            return result

        # --- sect. 12: nothing may keep a pre-deletion verdict -----------
        #
        # The highlights go FIRST. They were built from vertex indices of a
        # mesh that no longer exists, and a rod drawn where a deleted
        # fragment used to be is a dangling reference a researcher can see.
        visualization.clear_repair_highlights()
        summary = state.invalidate_for_geometry_change(context, obj.name,
                                                       props)
        removed_facts = captured.get("facts") or {}
        props.repair_artifact_preview = (
            "Deleted component %d: %s triangles, %s vertices. %d landmark(s) "
            "restated, %d measurement(s) invalidated."
            % (removed_facts.get("component_index", 0),
               "{:,}".format(removed_facts.get("triangle_count", 0)),
               "{:,}".format(removed_facts.get("vertex_count", 0)),
               summary["landmarks_restated"],
               summary["measurements_invalidated"])
        )
        print("[BSMT] " + props.repair_artifact_preview)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Local face repair inside a component that may not be deleted (M 3.30)
# ---------------------------------------------------------------------------
#
# The three repair options BSMT now offers are deliberately different tools
# for different topology, and none of them is a general "make it manifold":
#
#   * Weld Non-Manifold Region  - coincident / near-coincident VERTICES that
#     should have been one vertex. Merges, removes nothing.
#   * Delete Artifact           - a whole detached COMPONENT that is not the
#     body. Removes everything in it, and is refused outright on the primary
#     body component.
#   * Remove Local Faces (here) - a handful of FACES hanging off a defect
#     INSIDE a component, including the primary body. Removes only the faces
#     an unambiguous branch separation identified, or refuses.


def _stable_mm(canonical):
    """Physical millimetre positions that do NOT move when vertices are deleted.

    ``vertices_solver`` is centred on the mesh's own bounding box, so deleting
    a flap that happens to sit at an extreme of that box shifts every
    coordinate in the array by a fraction of a millimetre. A before/after
    signature built on it would then report the entire mesh as changed. The
    un-centred figure is the same physical position either way.
    """
    return (np.asarray(canonical.vertices_solver, dtype=np.float64)
            + np.asarray(canonical.center_mm, dtype=np.float64))


def _mean_edge_mm(canonical):
    report = canonical.topology or {}
    return float(report.get("edge_length_mean", 0.0) or 0.0)


def _inspect_local_defect(props, canonical):
    """Classify the focused defect's local topology. (report, region, reason).

    Read-only and re-derived: nothing stored on `props` is trusted for
    anything but WHICH defect is focused.
    """
    region, why = _focused_defect_region(props, canonical)
    if region is None:
        return None, None, why
    report = localrepair.classify_local_defect(
        canonical.vertices_solver, canonical.triangles, region["edges"],
        mean_edge_mm=_mean_edge_mm(canonical),
    )
    return report, region, ""


def _store_local_inspection(props, canonical, report, region):
    """Cache one inspection for the panel. Display only - it never acts."""
    props.repair_local_report = "\n".join(
        localrepair.report_lines(report, region["region_id"]))
    props.repair_local_classification = report["classification"]
    props.repair_local_removable = bool(report["removable"])
    props.repair_local_hash = str(getattr(canonical, "geometry_hash", "") or "")
    props.repair_local_region_id = int(region["region_id"])
    candidate = report["candidate"] or {}
    props.repair_local_face_count = int(candidate.get("face_count", 0))
    props.repair_local_vertex_count = int(candidate.get("vertex_count", 0))
    props.repair_local_area_mm2 = float(candidate.get("area_mm2", 0.0))


def _local_inspection_is_current(props, canonical):
    """Does the stored inspection still describe THIS mesh and THIS defect?"""
    if not props.repair_local_hash:
        return False, "no local inspection - press Inspect Local Topology"
    live = str(getattr(canonical, "geometry_hash", "") or "")
    if props.repair_local_hash != live:
        return False, ("the mesh changed since the local topology was "
                       "inspected - press Analyze Mesh and inspect again")
    entry = state.active_nonmanifold_defect(props)
    if entry is None or int(entry.region_id) != int(props.repair_local_region_id):
        return False, ("the focused defect changed since it was inspected - "
                       "press Inspect Local Topology again")
    return True, ""


def _candidate_local_geometry(canonical, candidate):
    """The candidate's own triangles, compacted into local coordinates."""
    used = np.asarray(candidate["vertex_indices"], dtype=np.int64)
    triangles = np.asarray(candidate["face_indices"], dtype=np.int64)
    if used.size == 0 or triangles.size == 0:
        return (np.zeros((0, 3), dtype=np.float64),
                np.zeros((0, 3), dtype=np.int64))
    remap = np.full(int(canonical.vertex_count), -1, dtype=np.int64)
    remap[used] = np.arange(used.size, dtype=np.int64)
    return canonical.vertices_local[used], remap[canonical.triangles[triangles]]


class BSMT_OT_inspect_local_defect(bpy.types.Operator):
    """Classify the local topology around the focused non-manifold defect.

    Read-only: nothing is modified, nothing is selected, and no solver runs.
    It either names an unambiguous removable candidate or says why it will
    not
    """

    bl_idname = "bsmt.inspect_local_defect"
    bl_label = "Inspect Local Topology"
    bl_description = ("Classify the surface around the focused non-manifold"
                      " defect and say whether a small removable candidate"
                      " can be identified. Nothing is modified")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return (props is not None
                and state.active_nonmanifold_defect(props) is not None
                and _repair_target(context)[0] is not None)

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        try:
            canonical = _canonical_arrays(context, props, obj, rebuild=False)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        current, why = _lists_match_mesh(props, obj, canonical)
        if not current:
            state.clear_local_repair(props)
            props.repair_local_report = why
            self.report({'WARNING'}, "BSMT: " + why)
            return {'CANCELLED'}

        try:
            report, region, why = _inspect_local_defect(props, canonical)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report({'ERROR'}, "BSMT: local inspection failed (%s)" % exc)
            return {'CANCELLED'}
        if report is None:
            state.clear_local_repair(props)
            props.repair_local_report = why
            self.report({'WARNING'}, "BSMT: " + why)
            return {'CANCELLED'}

        _store_local_inspection(props, canonical, report, region)
        # The previous candidate highlight described a different inspection.
        visualization.remove_object(
            bpy.data.objects.get(visualization.REPAIR_LOCAL_CANDIDATE))
        summary = localrepair.describe(report)
        print("[BSMT] local topology at defect %d on '%s': %s"
              % (region["region_id"], obj.name, summary))
        self.report({'INFO'} if report["removable"] else {'WARNING'},
                    "BSMT: " + summary)
        return {'FINISHED'}


class BSMT_OT_preview_local_candidate(bpy.types.Operator):
    """Highlight ONLY the faces a local repair would remove.

    Read-only. Not the component, not the defect's neighbourhood - the
    candidate faces themselves, drawn where they are, at their real size
    """

    bl_idname = "bsmt.preview_local_candidate"
    bl_label = "Preview Candidate Faces"
    bl_description = ("Highlight exactly the faces a local repair would"
                      " remove. Nothing is modified and no solver runs")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return (props is not None and props.repair_local_removable
                and _repair_target(context)[0] is not None)

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        try:
            canonical = _canonical_arrays(context, props, obj, rebuild=False)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        for check in (_lists_match_mesh(props, obj, canonical),
                      _local_inspection_is_current(props, canonical)):
            ok, why = check
            if not ok:
                props.repair_local_report = why
                props.repair_local_removable = False
                self.report({'WARNING'}, "BSMT: " + why)
                return {'CANCELLED'}

        report, region, why = _inspect_local_defect(props, canonical)
        if report is None or not report["removable"]:
            message = why or report["reason"]
            if report is not None:
                _store_local_inspection(props, canonical, report, region)
            self.report({'WARNING'}, "BSMT: " + message)
            return {'CANCELLED'}

        points, faces = _candidate_local_geometry(canonical,
                                                  report["candidate"])
        _helper, drawing = visualization.show_repair_surface(
            context, visualization.REPAIR_LOCAL_CANDIDATE, points, faces,
            obj.matrix_world, visualization.REPAIR_LOCAL_CANDIDATE_COLOR,
        )
        _store_local_inspection(props, canonical, report, region)
        if drawing:
            props.repair_local_report = (props.repair_local_report
                                         + "\n\nNot highlighted: %s." % drawing)
            self.report({'WARNING'}, "BSMT: " + drawing)
            return {'CANCELLED'}
        summary = localrepair.describe(report)
        print("[BSMT] local candidate preview on '%s': %s" % (obj.name, summary))
        self.report({'INFO'}, "BSMT: highlighted %d candidate face(s) - %s"
                    % (report["candidate"]["face_count"], summary))
        return {'FINISHED'}


class BSMT_OT_remove_local_faces(_RepairBase):
    """Remove the identified local candidate faces from the focused defect.

    Only the faces an unambiguous branch separation identified, inside the
    measurement mesh, inside one transaction that rolls back unless every
    postcondition holds. Never offered when the classification is ambiguous
    """

    bl_idname = "bsmt.remove_local_faces"
    bl_label = "Remove Local Artifact Faces"
    bl_description = ("Remove exactly the candidate faces identified at the"
                      " focused non-manifold defect, from the MEASUREMENT"
                      " MESH only. Rolled back unless the topology improves")

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or props.repair_running:
            return False
        if not props.repair_local_removable:
            return False
        return _repair_target(context)[0] is not None

    # -- confirmation --------------------------------------------------

    def invoke(self, context, event):
        """State exactly what goes, before it goes (sect. 6)."""
        props = state.get_props(context)
        obj, _reason = _repair_target(context)
        if props is not None and obj is not None:
            try:
                canonical = _canonical_arrays(context, props, obj,
                                              rebuild=False)
                ok, why = _lists_match_mesh(props, obj, canonical)
                report = region = None
                if ok:
                    report, region, why = _inspect_local_defect(props,
                                                                canonical)
                if report is not None and report["removable"]:
                    props.repair_local_report = "\n".join(
                        localrepair.confirmation_lines(
                            report, region["region_id"], obj.name,
                            _landmarks_on_object(context, obj.name),
                            _measurements_on_object(context, obj.name),
                        )
                    )
                else:
                    props.repair_local_report = (
                        why or (report["reason"] if report else "")
                        or localrepair.MANUAL_EDIT_MESSAGE)
            except Exception:                         # noqa: BLE001
                traceback.print_exc()
        window_manager = context.window_manager
        try:
            return window_manager.invoke_props_dialog(
                self, width=420, confirm_text="Remove Local Faces")
        except TypeError:
            # Blender before 4.1 has no confirm_text.
            return window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        props = state.get_props(context)
        layout = self.layout
        column = layout.column(align=True)
        column.scale_y = 0.8
        text = (props.repair_local_report if props is not None else "")
        if not text:
            column.label(text="Remove the local candidate faces?",
                         icon='ERROR')
            return
        for line in text.split("\n"):
            if not line.strip():
                column.separator()
            elif line.startswith("NOTE") or line.startswith("Automatic"):
                row = column.row()
                row.alert = True
                row.label(text=line)
            else:
                column.label(text=line)

    # -- the operation -------------------------------------------------

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        if geodesic.ensure_loaded():
            self.report({'ERROR'}, "BSMT: " + geodesic.ensure_loaded())
            return {'CANCELLED'}

        # --- pre-flight, before a single byte of geometry is touched -----
        try:
            canonical = _canonical_arrays(context, props, obj, rebuild=False)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}
        for check in (_lists_match_mesh(props, obj, canonical),
                      _local_inspection_is_current(props, canonical)):
            ok, why = check
            if not ok:
                props.repair_local_report = why
                props.repair_local_removable = False
                self.report({'ERROR'}, "BSMT: " + why)
                return {'CANCELLED'}
        report, _region, why = _inspect_local_defect(props, canonical)
        if report is None or not report["removable"]:
            message = why or (report["reason"] if report else
                              localrepair.MANUAL_EDIT_MESSAGE)
            props.repair_local_report = message
            props.repair_local_removable = False
            print("[BSMT] local repair refused on '%s': %s" % (obj.name,
                                                               message))
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        captured = {}

        def work(work_obj, fresh, _before):
            # Re-derived against the canonical mesh the wrapper just REBUILT,
            # so the vertex sets handed to bmesh come from the same mesh state
            # the acceptance test will be measured against. The pre-flight
            # above is what refuses; this is what acts (sect. 7, sect. 11).
            ok, why_fresh = _lists_match_mesh(props, work_obj, fresh)
            if not ok:
                raise meshrepair.RepairAborted(why_fresh)
            ok, why_fresh = _local_inspection_is_current(props, fresh)
            if not ok:
                raise meshrepair.RepairAborted(why_fresh)
            fresh_report, fresh_region, why_inner = _inspect_local_defect(
                props, fresh)
            if fresh_report is None:
                raise meshrepair.RepairAborted(why_inner)
            if not fresh_report["removable"]:
                raise meshrepair.RepairAborted(fresh_report["reason"])

            candidate = fresh_report["candidate"]
            captured["report"] = fresh_report
            captured["candidate"] = candidate
            captured["region_id"] = fresh_region["region_id"]
            captured["vertices_local"] = np.array(fresh.vertices_local,
                                                  copy=True)
            captured["triangles"] = np.array(fresh.triangles, copy=True)
            stable = _stable_mm(fresh)
            # Two different questions, two different signatures:
            #   * did THIS defect's own non-manifold edges go? and
            #   * did any non-manifold edge appear anywhere that was not
            #     there before?
            # Both keyed by midpoint POSITION, because removing faces
            # renumbers every vertex index in the mesh.
            captured["defect_signature"] = repair.region_signature(
                stable, fresh_region)
            captured["before_signature"] = repair.nonmanifold_signature(
                stable, fresh.triangles)

            counts = {}
            for key in candidate["face_keys"]:
                counts[key] = counts.get(key, 0) + 1
            # Addressed by VERTEX SET, never by index: a canonical triangle is
            # a loop triangle, and on a mesh that still carries quads it is
            # not polygon i (sect. 7.7). A candidate face that is half a quad
            # simply has no polygon with that vertex set, so the count below
            # refuses instead of removing something else.
            removed = meshrepair.remove_faces_by_vertex_sets(work_obj, counts)
            if removed != candidate["face_count"]:
                raise meshrepair.RepairAborted(
                    "%d face(s) were removed, not the %d the approved "
                    "candidate held - the candidate does not exist as whole "
                    "faces in this mesh, so triangulate it first"
                    % (removed, candidate["face_count"])
                )
            return ("defect %d - %s, %d face(s), %d vertex/vertices, "
                    "%.4f mm2, %.2f mm across"
                    % (fresh_region["region_id"],
                       localrepair.CLASSIFICATION_LABELS.get(
                           fresh_report["classification"],
                           fresh_report["classification"]),
                       removed, candidate["dropped_vertex_count"],
                       candidate["area_mm2"], candidate["bbox_diagonal_mm"]))

        def verify(before_report, after_report, _outcome):
            candidate = captured.get("candidate")
            if candidate is None:
                return ["the removal did not record what it removed"]
            reasons = localrepair.extra_removal_reasons(
                before_report, after_report,
                {"face_count": candidate["face_count"],
                 "dropped_vertex_count": candidate["dropped_vertex_count"]},
            )
            try:
                after = _canonical_arrays(context, props, obj, rebuild=False)
            except Exception as exc:                  # noqa: BLE001
                traceback.print_exc()
                return reasons + ["the edited mesh could not be re-read (%s)"
                                  % exc]
            # sect. 9: the locality claim, checked rather than asserted.
            reasons.extend(localrepair.locality_reasons(
                captured["vertices_local"], captured["triangles"],
                candidate["face_indices"],
                after.vertices_local, after.triangles,
            ))
            after_signature = repair.nonmanifold_signature(
                _stable_mm(after), after.triangles)
            reasons.extend(localrepair.defect_resolved_reasons(
                captured["defect_signature"], after_signature))
            reasons.extend(localrepair.introduced_reasons(
                captured["before_signature"], after_signature))
            return reasons

        result = self._guarded(context, "Remove local artifact faces", work,
                               require_nonmanifold_decrease=True,
                               verify=verify)
        if result != {'FINISHED'}:
            return result

        # --- sect. 10/11: nothing may keep a pre-repair verdict -----------
        #
        # The highlights go FIRST. They were built from vertex indices of a
        # mesh that no longer exists, and a violet patch drawn where deleted
        # faces used to be is a dangling reference a researcher can see.
        visualization.clear_repair_highlights()
        summary = state.invalidate_for_geometry_change(context, obj.name,
                                                       props)
        candidate = captured.get("candidate") or {}
        report = captured.get("report") or {}
        message = (
            "Removed %d local face(s) (%s) at defect %d: %d vertex/vertices "
            "freed, %.4f mm2. %d landmark(s) restated, %d measurement(s) "
            "invalidated."
            % (candidate.get("face_count", 0),
               localrepair.CLASSIFICATION_LABELS.get(
                   report.get("classification", ""),
                   report.get("classification", "")),
               captured.get("region_id", 0),
               candidate.get("dropped_vertex_count", 0),
               candidate.get("area_mm2", 0.0),
               summary["landmarks_restated"],
               summary["measurements_invalidated"])
        )
        # The inspection described a mesh that no longer exists. It is
        # cleared, not updated: the defect ids have been renumbered by the
        # re-analysis the wrapper already ran (sect. 11).
        state.clear_local_repair(props)
        props.repair_local_report = message
        print("[BSMT] " + message)
        return {'FINISHED'}


class BSMT_OT_remove_duplicate_faces(_RepairBase):
    """Delete faces that exactly repeat another face.

    The safest non-manifold repair: a duplicated face adds no surface, so
    removing it cannot move any anatomy
    """

    bl_idname = "bsmt.remove_duplicate_faces"
    bl_label = "Remove Duplicate Faces"
    bl_description = "Delete faces that exactly repeat another face"

    @classmethod
    def poll(cls, context):
        return _repair_target(context)[0] is not None

    def execute(self, context):
        def work(obj, canonical, _before):
            duplicates = repair.duplicate_faces(canonical.triangles)
            if duplicates.size == 0:
                raise meshrepair.RepairAborted(
                    "this mesh has no duplicate faces"
                )
            removed = meshrepair.remove_duplicate_faces(obj, duplicates)
            return "%d duplicate face(s) removed" % removed

        return self._guarded(context, "Remove duplicate faces", work,
                             require_nonmanifold_decrease=True)


class BSMT_OT_weld_non_manifold(_RepairBase):
    """Merge coincident vertices AT THE NON-MANIFOLD EDGES ONLY.

    Not a global merge-by-distance: the vertex set is the endpoints of the
    reported non-manifold edges and nothing else, so it cannot weld an arm to
    a torso elsewhere in the scan
    """

    bl_idname = "bsmt.weld_non_manifold"
    bl_label = "Weld Non-Manifold Region"
    bl_description = ("Merge coincident vertices at the non-manifold edges"
                      " only. The rest of the mesh is untouched")

    @classmethod
    def poll(cls, context):
        return _repair_target(context)[0] is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        props = state.get_props(context)
        distance_mm = props.repair_weld_distance_mm

        def work(obj, canonical, _before):
            edges = repair.classify_edges(
                canonical.triangles, canonical.vertex_count)["non_manifold"]
            if edges.shape[0] == 0:
                raise meshrepair.RepairAborted(
                    "this mesh has no non-manifold edges"
                )
            # The weld runs on the object's own mesh, whose units are
            # coordinate units, so the physical millimetre setting is
            # converted rather than passed through raw.
            local = measurement.mm_to_units(distance_mm, props.unit)
            merged = meshrepair.weld_non_manifold_region(obj, edges, local)
            return ("%d vertex/vertices merged at %d non-manifold edge(s), "
                    "tolerance %.4f mm" % (merged, edges.shape[0], distance_mm))

        return self._guarded(context, "Weld non-manifold region", work,
                             require_nonmanifold_decrease=True)


class BSMT_OT_auto_repair_local(_RepairBase):
    """Automatically repair small, localised non-manifold artefacts.

    Iterative and one region at a time: the mesh is re-analysed after every
    accepted edit, because a local change alters connectivity and every later
    plan would otherwise be computed against topology that no longer exists.
    A step that does not strictly improve, or that introduces a non-manifold
    edge anywhere else, is reverted
    """

    bl_idname = "bsmt.auto_repair_local"
    bl_label = "Repair Local Defects"
    bl_description = ("Repair small, local non-manifold artefacts one at a"
                      " time, reverting any step that does not improve the"
                      " topology")

    #: Hard stop on iterations. Each accepted step must strictly reduce the
    #: non-manifold count, so the loop is bounded anyway; this is a guard
    #: against a pathological mesh, not the normal exit.
    MAX_ITERATIONS = 64

    @classmethod
    def poll(cls, context):
        return _repair_target(context)[0] is not None

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        if props.repair_running:
            self.report({'WARNING'}, "BSMT: a repair is already running")
            return {'CANCELLED'}
        if geodesic.ensure_loaded():
            self.report({'ERROR'}, "BSMT: " + geodesic.ensure_loaded())
            return {'CANCELLED'}

        source = scancopy.resolve_source(obj)
        source_triangles = (scancopy.triangle_count(source.data)
                            if source is not None else None)
        before_texture = scancopy.audit_object(obj)
        canonical = _canonical_arrays(context, props, obj)
        first_report = dict(canonical.topology or {})

        if int(first_report.get("nonmanifold_edge_count", 0) or 0) == 0:
            _analyse_repair(context, props, obj)
            self.report({'INFO'},
                        "BSMT: no repair required - 0 non-manifold edges")
            return {'FINISHED'}

        props.repair_running = True
        lines = ["Auto repair of '%s'" % obj.name]
        try:
            accepted, rejected = self._iterate(context, props, obj, lines)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            props.repair_running = False
            self.report({'ERROR'},
                        "BSMT: auto repair failed (%s: %s) - see the console"
                        % (type(exc).__name__, exc))
            return {'CANCELLED'}
        finally:
            props.repair_running = False

        canonical = _canonical_arrays(context, props, obj)
        final_report = dict(canonical.topology or {})
        texture_ok, problems, _facts = meshrepair.verify_texture(
            obj, before_texture)

        summary = repair.change_summary(first_report, final_report)
        lines.append("")
        lines.append("Before -> after")
        for label, key in (("non-manifold edges", "nonmanifold_edge_count"),
                           ("boundary edges", "boundary_edge_count"),
                           ("components", "component_count"),
                           ("triangles", "triangle_count")):
            lines.append("  %-20s %s -> %s"
                         % (label,
                            "{:,}".format(first_report.get(key, 0) or 0),
                            "{:,}".format(final_report.get(key, 0) or 0)))
        lines.extend(repair.change_lines(summary))
        lines.append("  texture         %s"
                     % ("preserved" if texture_ok
                        else "LOST: " + "; ".join(problems)))
        verdict = repair.readiness(final_report)
        lines.append("")
        lines.extend(repair.readiness_lines(verdict))

        props.repair_log = ((props.repair_log + "\n" if props.repair_log
                             else "") + "\n".join(lines))
        print("\n[BSMT] " + "\n".join(lines) + "\n")
        _analyse_repair(context, props, obj)

        if accepted:
            _mark_points_stale(context, props, obj, canonical)

        if source is not None and source_triangles is not None:
            if scancopy.triangle_count(source.data) != source_triangles:
                self.report({'ERROR'},
                            "BSMT: the SOURCE scan changed during auto repair. "
                            "This is a bug; do not trust the copy.")
                return {'CANCELLED'}

        remaining = int(final_report.get("nonmanifold_edge_count", 0) or 0)
        if accepted == 0:
            self.report({'WARNING'},
                        "BSMT: no region could be repaired conservatively "
                        "(%d rejected) - %d non-manifold edge(s) remain, "
                        "manual cleanup is required" % (rejected, remaining))
            return {'CANCELLED'}
        self.report(
            {'INFO'} if remaining == 0 else {'WARNING'},
            "BSMT: %d region(s) repaired, %d attempt(s) reverted - "
            "non-manifold %d -> %d"
            % (accepted, rejected,
               first_report.get("nonmanifold_edge_count", 0), remaining),
        )
        return {'FINISHED'}

    def _iterate(self, context, props, obj, lines):
        """Repair one region at a time, re-analysing after each accepted edit."""
        accepted = 0
        rejected = 0
        for iteration in range(self.MAX_ITERATIONS):
            canonical = _canonical_arrays(context, props, obj)
            report = dict(canonical.topology or {})
            remaining = int(report.get("nonmanifold_edge_count", 0) or 0)
            if remaining == 0:
                lines.append("  iteration %d: 0 non-manifold edges - done"
                             % (iteration + 1))
                break

            mean_edge = float(report.get("edge_length_mean", 0.0) or 0.0)
            regions = repair.non_manifold_regions(canonical.vertices_solver,
                                                  canonical.triangles)
            lines.append("  iteration %d: %d non-manifold edge(s) in %d "
                         "region(s), limit %.1f mm (mean edge %.2f mm)"
                         % (iteration + 1, remaining, len(regions),
                            repair.region_diagonal_limit(mean_edge), mean_edge))

            progressed = False
            for region in regions:
                plan = repair.plan_region_repair(
                    canonical.vertices_solver, canonical.triangles, region,
                    mean_edge_mm=mean_edge)
                label = "    %s -> %s" % (
                    repair.describe_region(region),
                    repair.DEFECT_LABELS.get(plan["classification"],
                                             plan["classification"]))
                if not plan["remove_faces"]:
                    lines.append(label + " | REFUSED: %s" % plan["detail"])
                    continue

                outcome = self._try_region(context, props, obj, canonical,
                                           report, region, plan)
                lines.append(label + " | " + outcome["message"])
                if outcome["accepted"]:
                    accepted += 1
                    progressed = True
                    break                      # re-analyse before the next one
                rejected += 1

            if not progressed:
                lines.append("  no further region could be repaired safely")
                break
        return accepted, rejected

    def _try_region(self, context, props, obj, canonical, before_report,
                    region, plan):
        """One transactional attempt on one region."""
        before_signature = repair.nonmanifold_signature(
            canonical.vertices_solver, canonical.triangles)
        target_signature = repair.region_signature(canonical.vertices_solver,
                                                   region)
        before_degenerate = repair.degenerate_signature(
            canonical.vertices_solver, canonical.triangles)
        before_texture = scancopy.audit_object(obj)
        backup = meshrepair.make_backup(obj)
        props.repair_backup_mesh = backup

        wanted = {}
        for face_index in plan["remove_faces"]:
            key = tuple(sorted(int(v) for v in
                               canonical.triangles[face_index]))
            wanted[key] = wanted.get(key, 0) + 1

        try:
            removed = meshrepair.remove_faces_by_vertex_sets(obj, wanted)
        except meshrepair.RepairAborted as exc:
            meshrepair.restore_backup(obj, backup)
            return {"accepted": False, "message": "REVERTED: %s" % exc}

        # Fill only holes this removal actually opened, only genuine
        # boundaries, only closed, only small, and only near this region.
        filled = 0
        created = 0
        fresh = _canonical_arrays(context, props, obj)
        loops = repair.boundary_loops(fresh.vertices_solver, fresh.triangles)
        candidates = []
        centre = np.asarray(region["center_mm"], dtype=np.float64)
        # Tight: a hole opened by THIS removal sits inside the region, so a
        # generous radius only risks adopting a neighbouring defect's hole.
        reach = max(1.5 * region["bbox_diagonal_mm"], 2.0)
        for loop in loops:
            if loop["edge_count"] > repair.MAX_PATCH_EDGES:
                continue
            if loop["perimeter_mm"] > repair.MAX_PATCH_PERIMETER_MM:
                continue
            if loop["bbox_diagonal_mm"] > repair.MAX_PATCH_DIAGONAL_MM:
                continue
            if float(np.linalg.norm(
                    np.asarray(loop["center_mm"]) - centre)) > reach:
                continue
            candidates.append(loop)
        good, _rejected = repair.fillable_boundary_loops(
            fresh.vertices_solver, fresh.triangles, candidates)
        if good:
            created, filled, skipped = meshrepair.fill_small_loops(obj, good)
            for note in skipped:
                print("[BSMT] auto repair skipped %s" % note)
            fresh = _canonical_arrays(context, props, obj)

        after_report = dict(fresh.topology or {})
        after_signature = repair.nonmanifold_signature(
            fresh.vertices_solver, fresh.triangles)
        after_degenerate = repair.degenerate_signature(
            fresh.vertices_solver, fresh.triangles)
        texture_ok, _problems, _facts = meshrepair.verify_texture(
            obj, before_texture)

        ok, reasons = repair.step_acceptable(
            before_signature, after_signature, before_report, after_report,
            texture_ok, before_degenerate, after_degenerate)

        if ok:
            local_ok, local_problems = repair.local_invariants(
                target_signature, after_signature,
                fresh.vertices_solver, fresh.triangles,
                region["center_mm"], reach)
            if not local_ok:
                ok = False
                reasons = local_problems

        if not ok:
            meshrepair.restore_backup(obj, backup)
            _canonical_arrays(context, props, obj)
            return {"accepted": False,
                    "message": "REVERTED: %s" % "; ".join(reasons)}

        return {
            "accepted": True,
            "message": ("repaired: %d face(s) removed, %d hole(s) filled "
                        "(%d new face(s)), non-manifold %d -> %d"
                        % (removed, filled, created,
                           len(before_signature), len(after_signature))),
        }


class BSMT_OT_auto_repair_boundaries(_RepairBase):
    """Fill only the tiny boundary structures.

    Crop planes, neck cuts and large scan openings are never touched: a loop
    must be under the tiny-boundary limits to qualify
    """

    bl_idname = "bsmt.auto_repair_boundaries"
    bl_label = "Fill Small Holes"
    bl_description = ("Fill only the small boundary loops. Large openings,"
                      " such as a cropped bottom, are left alone")

    @classmethod
    def poll(cls, context):
        return _repair_target(context)[0] is not None

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        if geodesic.ensure_loaded():
            self.report({'ERROR'}, "BSMT: " + geodesic.ensure_loaded())
            return {'CANCELLED'}

        canonical = _canonical_arrays(context, props, obj)
        loops = repair.boundary_loops(canonical.vertices_solver,
                                      canonical.triangles)
        if not loops:
            _analyse_repair(context, props, obj)
            self.report({'INFO'}, "BSMT: no repair required - 0 boundary edges")
            return {'FINISHED'}

        tiny = []
        lines = ["Tiny boundary repair of '%s'" % obj.name,
                 "  %d boundary loop(s)" % len(loops)]
        for loop in loops:
            ok, why = repair.is_tiny_boundary(loop)
            lines.append("  %s -> %s" % (repair.describe_loop(loop),
                                         "fill" if ok else "LEFT ALONE (%s)" % why))
            if ok and loop["closed"] and loop["edge_count"] >= 3:
                tiny.append(loop)
            elif ok:
                lines.append("      not a closed loop of 3+ edges - skipped")

        if not tiny:
            props.repair_log = ((props.repair_log + "\n" if props.repair_log
                                 else "") + "\n".join(lines))
            _analyse_repair(context, props, obj)
            self.report({'INFO'},
                        "BSMT: no boundary was small enough to fill "
                        "automatically")
            return {'FINISHED'}

        keys = [loop["loop_id"] for loop in tiny]

        def work(target, current, _before):
            fresh_loops = repair.boundary_loops(current.vertices_solver,
                                                current.triangles)
            chosen = [loop for loop in fresh_loops if loop["loop_id"] in keys]
            created, filled, skipped = meshrepair.fill_small_loops(target,
                                                                   chosen)
            for note in skipped:
                print("[BSMT] tiny boundary skipped %s" % note)
            if filled == 0:
                raise meshrepair.RepairAborted(
                    "none of the tiny boundaries could be filled"
                )
            return "%d tiny boundary/boundaries filled (%d new face(s))" % (
                filled, created)

        result = self._guarded(context, "Auto repair tiny boundaries", work)
        props.repair_log = ((props.repair_log + "\n" if props.repair_log
                             else "") + "\n".join(lines))
        print("\n[BSMT] " + "\n".join(lines) + "\n")
        return result


class BSMT_OT_restore_repair_backup(bpy.types.Operator):
    """Restore the mesh as it was before the last repair"""

    bl_idname = "bsmt.restore_repair_backup"
    bl_label = "Undo Repair"
    bl_description = ("Restore the mesh exactly as it was before the last"
                      " repair")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        if props is None or not props.repair_backup_mesh:
            return False
        return _repair_target(context)[0] is not None

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _repair_target(context)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        if not meshrepair.restore_backup(obj, props.repair_backup_mesh):
            self.report({'ERROR'}, "BSMT: the backup mesh is gone")
            return {'CANCELLED'}
        _analyse_repair(context, props, obj)
        props.repair_log = ((props.repair_log + "\n" if props.repair_log else "")
                            + "Restored the pre-repair backup")
        self.report({'INFO'}, "BSMT: restored '%s' from backup" % obj.name)
        return {'FINISHED'}


class BSMT_OT_clear_repair_report(bpy.types.Operator):
    """Clear the repair analysis and log. No mesh is touched"""

    bl_idname = "bsmt.clear_repair_report"
    bl_label = "Clear Repair Report"
    bl_description = "Clear the repair analysis and log. No mesh is touched"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        if props is not None:
            state.clear_repair_state(props)
            props.repair_log = ""
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Rigid anatomical alignment (Milestone 3.6)
# ---------------------------------------------------------------------------
#
# Object transforms only. No mesh vertex is touched, so geometry_hash cannot
# change and metric_key - built from the rotation-invariant L^T L - cannot
# either. SurfacePoints therefore stay VALID and stored distances stay
# untouched by construction, not by care.


def _align_target(context, props):
    """The object to align, and why not. Returns (obj, reason).

    The references decide, not the selection: if the four points were picked
    on a mesh, that is the mesh being aligned. Falls back to the active object
    only when no reference has been picked yet.
    """
    names = state.align_objects(props)
    if len(names) > 1:
        return None, ("the alignment references are on different objects (%s)"
                      % ", ".join(sorted(names)))
    if names:
        name = sorted(names)[0]
        obj = bpy.data.objects.get(name)
        if obj is None:
            return None, "the reference object '%s' is missing" % name
        return obj, ""
    obj = context.active_object
    if obj is None or obj.type != 'MESH' or visualization.is_helper(obj):
        return None, "select a mesh object"
    return obj, ""


def _apply_world_matrix(obj, matrix_rows):
    obj.matrix_world = Matrix([[float(v) for v in row] for row in matrix_rows])


#: Status properties a refused Apply must put back exactly as it found them.
_STATUS_PROPERTIES = (
    "align_applied", "align_moved", "align_method", "align_created",
    "align_object", "align_report", "align_validation",
    "align_origin_requested", "align_validation_report", "align_lr_dot_x",
    "align_si_dot_z", "align_axis_error_degrees", "align_orthogonality_error",
    "align_residual_degrees", "align_previous_matrix", "align_applied_matrix",
)


def _capture_alignment_status(props):
    return {name: (tuple(getattr(props, name))
                   if name.endswith("_matrix") else getattr(props, name))
            for name in _STATUS_PROPERTIES}


def _restore_alignment_status(props, captured):
    for name, value in captured.items():
        setattr(props, name, value)


def _axis_helper_length(frame):
    """Length of the drawn axis arms, in WORLD units - what the helper uses."""
    return max(float(frame["vertical_span"]) * 0.35, 1e-6)


def _remember_pre_alignment(props, obj):
    """Record the transform to return to, once per alignment session."""
    if not props.align_moved or props.align_object != obj.name:
        props.align_previous_matrix = state.matrix_to_flat(obj.matrix_world)
        props.align_object = obj.name


def _refresh_after_alignment(context, props, obj, method, lines,
                             applied=True):
    """Re-derive helper positions and confirm nothing metric changed.

    `applied` is the measured verdict, not a formality: an alignment that
    moved the object but failed its own postcondition must not leave the panel
    saying "Aligned".
    """
    import datetime

    props.align_applied = bool(applied)
    # Separate from align_applied on purpose: this one records "BSMT has moved
    # this object and holds a restore point for it", which stays true even
    # when the result failed validation. Tying the restore point to the
    # success verdict would let a second Apply overwrite the researcher's only
    # way back to the original pose.
    props.align_moved = True
    props.align_object = obj.name
    props.align_method = method
    props.align_created = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    props.align_applied_matrix = state.matrix_to_flat(obj.matrix_world)
    props.align_report = "\n".join(lines)

    context.view_layer.update()
    attach.refresh(props, reason="alignment")
    print("\n[BSMT] " + "\n".join(lines) + "\n")


class BSMT_OT_pick_alignment_reference(bpy.types.Operator):
    """Pick one anatomical alignment reference on the surface"""

    bl_idname = "bsmt.pick_alignment_reference"
    bl_label = "Pick Reference Point"
    bl_description = ("Click a point on the mesh surface to set this"
                      " anatomical reference")
    bl_options = {'REGISTER'}

    slot: EnumProperty(
        name="Slot",
        items=(('LEFT', "Left", "Subject's LEFT"),
               ('RIGHT', "Right", "Subject's RIGHT"),
               ('SUPERIOR', "Superior", "Upper reference"),
               ('INFERIOR', "Inferior", "Lower reference")),
        default='LEFT',
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and context.active_object is not None
                and context.active_object.type == 'MESH')

    def execute(self, context):
        return bpy.ops.bsmt.pick_point('INVOKE_DEFAULT', target='ALIGN',
                                       align_slot=self.slot)


class BSMT_OT_clear_alignment_references(bpy.types.Operator):
    """Forget the four alignment references. No object is moved"""

    bl_idname = "bsmt.clear_alignment_references"
    bl_label = "Clear Reference Points"
    bl_description = "Forget the four reference points. No object is moved"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = state.get_props(context)
        if props is not None:
            state.clear_align_points(props)
            props.align_preview = False
        visualization.clear_alignment_helpers()
        self.report({'INFO'}, "BSMT: alignment references cleared")
        return {'FINISHED'}


class BSMT_OT_manual_align(bpy.types.Operator):
    """Rotate the object about a world axis, or move it to the origin.

    Object transform only: the mesh is never touched
    """

    bl_idname = "bsmt.manual_align"
    bl_label = "Manual Align"
    bl_description = ("Rotate the object about a world axis, or move it to"
                      " the world origin. The mesh is not modified")
    bl_options = {'REGISTER', 'UNDO'}

    axis: EnumProperty(
        items=(('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")),
        default='Z', options={'SKIP_SAVE'},
    )
    degrees: FloatProperty(default=90.0, options={'SKIP_SAVE'})
    action: EnumProperty(
        items=(('ROTATE', "Rotate", ""),
               ('ORIGIN', "Move To Origin", "")),
        default='ROTATE', options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return props is not None and _align_target(context, props)[0] is not None

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _align_target(context, props)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}

        ok, why = alignment.check_alignable(obj.matrix_world)
        if not ok:
            self.report({'ERROR'}, "BSMT: " + why)
            return {'CANCELLED'}

        _remember_pre_alignment(props, obj)
        current = np.array(obj.matrix_world, dtype=np.float64)

        if self.action == 'ORIGIN':
            updated = current.copy()
            updated[:3, 3] = 0.0
            label = "moved to the world origin"
        else:
            rotation = alignment.axis_rotation(self.axis, self.degrees)
            # Turn about the object's own origin so the body rotates in place.
            pivot = current[:3, 3].copy()
            updated = alignment.compose(current, rotation, pivot=pivot)
            label = "rotated %+.1f deg about world %s" % (self.degrees, self.axis)

        _apply_world_matrix(obj, updated)
        context.view_layer.update()
        lines = ["Manual alignment of '%s'" % obj.name, "  " + label,
                 "  " + alignment.AXIS_DESCRIPTION]

        # A manual nudge is still subject to the same contract, so when there
        # ARE references it is measured against them rather than trusted. With
        # no references there is nothing to measure, and the status says that
        # instead of implying a check that never happened.
        validation, why = attach.validate_applied_alignment(
            props, require_origin=False)
        attach.store_validation(props, validation, why)
        props.align_origin_requested = False
        if validation is not None:
            lines.append("")
            lines.extend(alignment.validation_lines(validation))
        _refresh_after_alignment(context, props, obj,
                                 alignment.METHOD_MANUAL, lines,
                                 applied=True)
        self.report({'INFO'}, "BSMT: %s %s%s"
                    % (obj.name, label,
                       "" if validation is None or validation["ok"]
                       else " - the references do NOT satisfy the axis "
                            "contract in this pose"))
        return {'FINISHED'}


class BSMT_OT_preview_alignment(bpy.types.Operator):
    """Draw the anatomical axes the current references would produce.

    Nothing is moved: this is a look before you leap
    """

    bl_idname = "bsmt.preview_alignment"
    bl_label = "Preview"
    bl_description = ("Draw the anatomical axes the current reference points"
                      " would produce. Nothing is moved")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return props is not None and state.align_points_ready(props)[0]

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _align_target(context, props)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}
        try:
            frame, points = _build_frame(props)
        except alignment.AlignmentError as exc:
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        origin = points['INFERIOR']
        # The span is already in world units - the axes are drawn in world
        # space - so it must NOT be divided by the mm multiplier. Doing so
        # made the preview axes 1000x too short on a scan authored in metres.
        length = _axis_helper_length(frame)
        # THE SAME BASIS Apply will use. Until now the preview drew the WORLD
        # axes, which are identical whatever the references are: it could not
        # disagree with a wrong alignment because it was not looking at the
        # references at all. Drawn along `frame["matrix"]`, the arms point
        # where the subject's left, posterior and superior currently are, so
        # "does the blue arm run up the body?" is finally a real check.
        visualization.show_alignment_axes(context, origin, length,
                                          basis=frame["matrix"])
        props.align_preview = True
        props.align_residual_degrees = frame["residual_degrees"]

        lines = alignment.alignment_report(
            frame, alignment.scale_report(obj.matrix_world),
            multiplier=measurement.unit_multiplier(props.unit))
        lines.insert(1, "  preview only - nothing has been moved")
        lines.append("  axes drawn along the frame Apply would use, at the "
                     "INFERIOR reference")
        lines.append("  " + alignment.orthogonalisation_note(frame))
        props.align_report = "\n".join(lines)
        print("\n[BSMT] " + "\n".join(lines) + "\n")
        self.report(
            {'WARNING'} if not frame["residual_ok"] else {'INFO'},
            "BSMT: preview drawn - residual %.2f deg. Red=+X subject's left, "
            "green=+Y posterior, blue=+Z superior."
            % frame["residual_degrees"],
        )
        return {'FINISHED'}


class BSMT_OT_clear_alignment_preview(bpy.types.Operator):
    """Remove the alignment axis helper"""

    bl_idname = "bsmt.clear_alignment_preview"
    bl_label = "Clear Preview"
    bl_description = "Remove the preview axes"
    bl_options = {'REGISTER'}

    def execute(self, context):
        removed = visualization.clear_alignment_helpers()
        props = state.get_props(context)
        if props is not None:
            props.align_preview = False
        self.report({'INFO'}, "BSMT: removed %d alignment helper(s)" % removed)
        return {'FINISHED'}


def _build_frame(props):
    """The anatomical frame from the four references, in world space.

    The world positions are reconstructed from the live `matrix_world`, not
    read from the SurfacePoint's cached `world_xyz`. The cache is normally
    correct - `attach.refresh` keeps it so - but "normally" is not a thing to
    build a rotation on: any path that moves the object without reaching the
    handler would have the frame describe a pose the object left, and the
    resulting alignment would be wrong by exactly that missed motion while
    reporting a clean residual.
    """
    points, reason = attach.live_reference_points(props)
    if points is None:
        raise alignment.AlignmentError(reason)
    frame = alignment.anatomical_frame(
        points['LEFT'], points['RIGHT'], points['SUPERIOR'], points['INFERIOR'])
    return frame, points


class BSMT_OT_apply_alignment(bpy.types.Operator):
    """Rotate the object so its anatomical axes match the world axes.

    Rigid only - rotation and translation, never scale. The mesh is not
    touched, so landmarks and stored distances stay valid
    """

    bl_idname = "bsmt.apply_alignment"
    bl_label = "Apply Alignment"
    bl_description = ("Rotate the object so its anatomical axes match the"
                      " world axes. Object transform only - the mesh is never"
                      " modified")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return props is not None and state.align_points_ready(props)[0]

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _align_target(context, props)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}

        ok, why = alignment.check_alignable(obj.matrix_world)
        if not ok:
            self.report({'ERROR'}, "BSMT: " + why)
            return {'CANCELLED'}

        try:
            frame, points = _build_frame(props)
            rotation = alignment.rotation_to_world(frame)
        except alignment.AlignmentError as exc:
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        if not alignment.is_rigid(rotation):
            self.report({'ERROR'},
                        "BSMT: the computed alignment is not rigid - refused")
            return {'CANCELLED'}

        before_matrix = np.array(obj.matrix_world, dtype=np.float64)
        before_scale = alignment.linear_scale(before_matrix)
        # The object's OWN stored transform, which is what a rollback has to
        # put back. Restoring `matrix_world` instead would go out through
        # Blender's single-precision loc/rot/scale decomposition and land
        # about 1e-7 away from where it started - a rollback that does not
        # quite roll back. matrix_basis IS the stored data, so writing it back
        # is bit-exact, and it is also the only correct thing to restore on a
        # parented or constrained object, whose world matrix is derived.
        before_basis = Matrix(obj.matrix_basis)
        status_before = _capture_alignment_status(props)
        _remember_pre_alignment(props, obj)

        pivot = points['INFERIOR']
        updated = alignment.compose(
            before_matrix, rotation, pivot=pivot,
            translate_to=(np.zeros(3) if props.align_move_to_origin else None))

        after_scale = alignment.linear_scale(updated)
        if not np.allclose(before_scale, after_scale, atol=1e-9):
            self.report({'ERROR'},
                        "BSMT: alignment would change scale (%s -> %s) - "
                        "refused" % (before_scale, after_scale))
            return {'CANCELLED'}

        # ---- the transaction ---------------------------------------------
        # From here the object is moved on trial. It keeps the new pose only
        # if the measurement below says the pose means what the panel would
        # claim it means; otherwise `before_matrix` goes back exactly, so a
        # refused alignment leaves nothing behind to clean up. Leaving a scan
        # transformed under a FAILED banner - which 0.25.0 did - hands the
        # researcher a pose that is neither the original nor a valid one.
        _apply_world_matrix(obj, updated)
        # Blender must have settled before anything is measured: matrix_world
        # is a *request* on an object with a parent, a delta transform or a
        # constraint, and the pose that matters is the one the depsgraph
        # actually produced.
        context.view_layer.update()

        lines = alignment.alignment_report(
            frame, alignment.scale_report(obj.matrix_world),
            multiplier=measurement.unit_multiplier(props.unit))
        lines.insert(1, "  object: %s" % obj.name)
        lines.append("  rotation applied about the INFERIOR reference")
        if props.align_move_to_origin:
            lines.append("  inferior reference moved to the world origin")
        lines.append("  rigid: rotation + translation only, no scale")
        lines.append("  mesh geometry, geometry hash and metric key unchanged")
        lines.append("  " + alignment.orthogonalisation_note(frame))
        props.align_residual_degrees = frame["residual_degrees"]
        props.align_origin_requested = bool(props.align_move_to_origin)

        # ---- the postcondition -------------------------------------------
        # Everything above is what BSMT INTENDED. This is what it achieved,
        # measured from the four references reconstructed against the pose the
        # object is actually in. "Aligned" is now a result, not an assertion.
        # Every criterion, including Move To World Origin, is judged inside
        # validate_world_frame, so there is exactly one list of failures and
        # the message can never trail off with nothing after the dash.
        validation, why = attach.validate_applied_alignment(
            props, require_origin=props.align_move_to_origin)
        verdict = attach.store_validation(props, validation, why)
        lines.append("")
        lines.extend(alignment.validation_lines(validation)
                     if validation is not None
                     else ["Applied-frame check could not be made: " + why])

        if verdict != 'PASS':
            # ---- rollback ------------------------------------------------
            # A refused alignment must leave NOTHING behind: not the trial
            # pose, and not a status describing it. The object goes back to
            # `before_matrix` and every status property is restored to the
            # value it had when execute() started, so the transaction either
            # happened or it did not.
            #
            # The one thing that is kept is the evidence: the criterion table
            # measured on the trial pose is what says why the alignment was
            # refused, and the pose it describes no longer exists, so it
            # cannot be re-derived from anything and has to be recorded.
            refusal = (alignment.validation_lines(validation)
                       if validation is not None
                       else ["The applied-frame check could not be made:",
                             "  " + why])
            obj.matrix_basis = before_basis
            context.view_layer.update()
            attach.refresh(props, reason="alignment rolled back")
            _restore_alignment_status(props, status_before)
            props.align_refusal_report = "\n".join(
                ["Apply Alignment was REFUSED and rolled back",
                 "  '%s' is back at its pre-Apply transform" % obj.name]
                + refusal)
            props.align_report = "\n".join(
                lines[:1] + ["  REFUSED - see Alignment Validation below"])
            print("\n[BSMT] " + "\n".join(lines)
                  + "\n[BSMT] rolled back; object restored\n")
            detail = ("; ".join(validation["failures"])
                      if validation is not None else why)
            self.report(
                {'ERROR'},
                "BSMT: alignment REFUSED and rolled back - %s. The full "
                "criterion table is in the Alignment Validation panel."
                % (detail or "the postcondition could not be measured"))
            return {'CANCELLED'}
        props.align_refusal_report = ""

        _refresh_after_alignment(context, props, obj,
                                 alignment.METHOD_LANDMARK, lines,
                                 applied=True)

        if props.align_preview:
            # Aligned means the anatomical frame IS the world frame, so the
            # helper is drawn on the identity basis - and if that ever looks
            # wrong, the validation above has already said so in numbers.
            visualization.show_alignment_axes(
                context, np.zeros(3) if props.align_move_to_origin else pivot,
                _axis_helper_length(frame), basis=np.eye(3))

        self.report(
            {'WARNING'} if not frame["residual_ok"] else {'INFO'},
            "BSMT: '%s' aligned and VERIFIED - SI.+Z = %+.6f, LR.+X = %+.6f "
            "(cos of the %.2f deg residual), worst axis error %.4f deg. %s"
            % (obj.name, validation["si_dot_z"], validation["lr_dot_x"],
               frame["residual_degrees"],
               validation["worst_axis_error_degrees"],
               alignment.AXIS_DESCRIPTION),
        )
        return {'FINISHED'}


class BSMT_OT_flip_front_back(bpy.types.Operator):
    """Turn the object 180 degrees about Z.

    This is the correction for having labelled the subject's left and right
    the wrong way round, which also reverses front and back
    """

    bl_idname = "bsmt.flip_front_back"
    bl_label = "Flip Front / Back"
    bl_description = ("Turn the object 180 degrees about Z. Use this if the"
                      " subject's left and right ended up swapped")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return props is not None and _align_target(context, props)[0] is not None

    def execute(self, context):
        props = state.get_props(context)
        obj, reason = _align_target(context, props)
        if obj is None:
            self.report({'ERROR'}, "BSMT: " + reason)
            return {'CANCELLED'}

        _remember_pre_alignment(props, obj)
        current = np.array(obj.matrix_world, dtype=np.float64)
        pivot = current[:3, 3].copy()
        live, _reason = attach.live_reference_points(props)
        if live is not None:
            pivot = live['INFERIOR']
        updated = alignment.compose(current, alignment.flip_matrix(),
                                    pivot=pivot)
        _apply_world_matrix(obj, updated)
        context.view_layer.update()

        lines = ["Flip front/back on '%s'" % obj.name,
                 "  180 deg about Z - the correction for swapped left/right",
                 "  " + alignment.AXIS_DESCRIPTION]
        swapped = False
        if live is not None:
            # The turn and the relabelling are one correction, not two. See
            # state.swap_align_points().
            state.swap_align_points(props, 'LEFT', 'RIGHT')
            swapped = True
            lines.append("  the LEFT and RIGHT references were exchanged with "
                         "the body, so the labels still describe the subject")

        applied = props.align_applied
        if swapped:
            validation, why = attach.validate_applied_alignment(
                props, require_origin=props.align_origin_requested)
            verdict = attach.store_validation(props, validation, why)
            lines.append("")
            lines.extend(alignment.validation_lines(validation)
                         if validation is not None
                         else ["Applied-frame check could not be made: " + why])
            applied = verdict == 'PASS'
        _refresh_after_alignment(context, props, obj, props.align_method
                                 or alignment.METHOD_MANUAL, lines,
                                 applied=applied)
        self.report({'INFO'}, "BSMT: '%s' flipped front/back%s"
                    % (obj.name,
                       "" if not swapped or applied
                       else " - the result does NOT satisfy the axis contract"))
        return {'FINISHED'}


class BSMT_OT_reset_alignment(bpy.types.Operator):
    """Restore the object transform recorded before alignment began.

    The mesh is never touched, here or anywhere else in alignment
    """

    bl_idname = "bsmt.reset_alignment"
    bl_label = "Reset Alignment"
    bl_description = "Restore the object transform recorded before alignment"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = state.get_props(context)
        return (props is not None and bool(props.align_object)
                and props.align_moved)

    def execute(self, context):
        props = state.get_props(context)
        obj = bpy.data.objects.get(props.align_object)
        if obj is None:
            self.report({'ERROR'}, "BSMT: '%s' is missing" % props.align_object)
            return {'CANCELLED'}

        _apply_world_matrix(obj, state.flat_to_rows(props.align_previous_matrix))
        context.view_layer.update()
        attach.refresh(props, reason="alignment reset")
        visualization.clear_alignment_helpers()

        props.align_applied = False
        props.align_moved = False
        props.align_preview = False
        props.align_origin_requested = False
        props.align_validation = ""
        props.align_validation_report = ""
        props.align_refusal_report = ""
        props.align_lr_dot_x = 0.0
        props.align_si_dot_z = 0.0
        props.align_axis_error_degrees = 0.0
        props.align_orthogonality_error = 0.0
        props.align_report = ("Alignment reset - '%s' restored to its "
                              "pre-alignment transform" % obj.name)
        print("[BSMT] " + props.align_report)
        self.report({'INFO'}, "BSMT: " + props.align_report)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Export and protocol reuse (Milestone 3.11)
# ---------------------------------------------------------------------------


def _export_context(context):
    """(props, session, mesh record, version) for an export, or None."""
    props = state.get_props(context)
    if props is None:
        return None
    obj = state.export_object(context, props)
    return (props, state.session_metadata(props),
            state.mesh_provenance(obj), _addon_version())


class _ExportBase(bpy.types.Operator):
    """Shared file-dialog plumbing for the two CSV exports."""

    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH')
    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={'HIDDEN'})
    check_existing: BoolProperty(default=True, options={'HIDDEN'})

    #: 'measurements' or 'landmarks'; used for the default file name.
    kind = "export"

    def invoke(self, context, event):
        props = state.get_props(context)
        if props is not None and not self.filepath:
            obj = state.export_object(context, props)
            self.filepath = export.default_filename(
                self.kind,
                subject_id=props.session_subject_id,
                condition=props.session_condition,
                scan_id=props.session_scan_id,
                fallback=obj.name if obj is not None else "",
            )
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def _finish(self, context, props, path, columns, rows, lines):
        try:
            written = export.write_csv(path, columns, rows)
        except export.ExportError as exc:
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}
        report = [path] + lines
        props.export_report = "\n".join(report)
        print("\n[BSMT] export")
        for line in report:
            print("[BSMT]   %s" % line)
        self.report({'INFO'}, "BSMT: %s" % lines[0])
        return {'FINISHED'}


class BSMT_OT_export_measurements(_ExportBase):
    """Write every DEFINED measurement to a CSV file.

    Drafts are not measurements and are never written. A value that is not
    current is written as a BLANK field, never as a zero
    """

    bl_idname = "bsmt.export_measurements"
    bl_label = "Measurements CSV"
    bl_description = ("Write the defined measurements to a CSV file. Drafts"
                      " are skipped and uncalculated values are left blank")
    kind = "measurements"

    @classmethod
    def poll(cls, context):
        collection = state.get_measurements(context)
        return bool(collection) and bool(measurements.defined(collection))

    def execute(self, context):
        prepared = _export_context(context)
        if prepared is None:
            self.report({'ERROR'}, "BSMT: add-on properties are not registered")
            return {'CANCELLED'}
        props, session, mesh, version = prepared
        collection = state.get_measurements(context)
        defined = measurements.defined(collection or ())
        if not defined:
            self.report({'ERROR'},
                        "BSMT: there are no defined measurements to export - "
                        "a draft is not a measurement")
            return {'CANCELLED'}

        exported = export.timestamp()
        rows = []
        statuses = []
        for item in defined:
            record = state.measurement_export_record(context, item)
            rows.append(export.measurement_row(session, record, mesh, version,
                                               exported))
            statuses.append(item.status)
        _counts, lines = export.summarise_measurements(statuses)
        drafts = len(collection) - len(defined)
        if drafts:
            lines.append("%d draft not exported" % drafts if drafts == 1
                         else "%d drafts not exported" % drafts)
        return self._finish(context, props, self.filepath,
                            export.MEASUREMENT_COLUMNS, rows, lines)


class BSMT_OT_export_landmarks(_ExportBase):
    """Write every landmark to a CSV file, positioned or not.

    An unpositioned landmark keeps its definition row with the geometric
    fields blank, so a landmark that was missed is visible in the data
    """

    bl_idname = "bsmt.export_landmarks"
    bl_label = "Landmarks CSV"
    bl_description = ("Write every landmark to a CSV file. Unpositioned ones"
                      " keep their row with the coordinates left blank")
    kind = "landmarks"

    @classmethod
    def poll(cls, context):
        return bool(state.get_landmarks(context))

    def execute(self, context):
        prepared = _export_context(context)
        if prepared is None:
            self.report({'ERROR'}, "BSMT: add-on properties are not registered")
            return {'CANCELLED'}
        props, session, mesh, version = prepared
        collection = state.get_landmarks(context)
        if not collection:
            self.report({'ERROR'}, "BSMT: there are no landmarks to export")
            return {'CANCELLED'}

        exported = export.timestamp()
        rows = []
        statuses = []
        for item in collection:
            record = state.landmark_export_record(item)
            rows.append(export.landmark_row(session, record, mesh, version,
                                            exported, unit=props.unit))
            statuses.append(item.status)
        _counts, lines = export.summarise_landmarks(statuses)
        return self._finish(context, props, self.filepath,
                            export.LANDMARK_COLUMNS, rows, lines)


class BSMT_OT_save_study_protocol(bpy.types.Operator):
    """Save the landmark and measurement DEFINITIONS as a reusable protocol.

    Definitions only: no picked positions, no results, no scan name and no
    subject data. protocol.py refuses to write anything else
    """

    bl_idname = "bsmt.save_study_protocol"
    bl_label = "Save Protocol"
    bl_description = ("Save the landmark and measurement definitions to a"
                      " JSON file. No positions, results or subject data")
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
        if props is not None and not self.filepath:
            name = export.sanitize(props.protocol_name) or "bsmt_protocol"
            self.filepath = name + ".json"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = state.get_props(context)
        if props is None:
            self.report({'ERROR'}, "BSMT: add-on properties are not registered")
            return {'CANCELLED'}
        landmark_entries, measurement_entries = state.protocol_entries(
            context, props)
        if not landmark_entries:
            self.report({'ERROR'}, "BSMT: there are no landmarks to save")
            return {'CANCELLED'}
        name = props.protocol_name or "Untitled Protocol"
        try:
            protocol.save_protocol(self.filepath, name, landmark_entries,
                                   measurement_entries)
        except protocol.ProtocolError as exc:
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}
        message = ("saved protocol '%s': %d landmark(s), %d measurement(s)"
                   % (name, len(landmark_entries), len(measurement_entries)))
        print("[BSMT] %s -> %s" % (message, self.filepath))
        self.report({'INFO'}, "BSMT: " + message)
        return {'FINISHED'}


class BSMT_OT_load_study_protocol(bpy.types.Operator):
    """Load landmark and measurement definitions from a protocol file.

    REPLACES what is in the scene. Every landmark arrives UNPOSITIONED and
    every measurement arrives with no result and no cached path, because the
    protocol says what to measure and this scan has not been measured yet
    """

    bl_idname = "bsmt.load_study_protocol"
    bl_label = "Load Protocol"
    bl_description = ("Replace the landmark and measurement definitions with"
                      " those from a JSON protocol. Nothing is positioned")
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        props = state.get_props(context)
        if props is None:
            self.report({'ERROR'}, "BSMT: add-on properties are not registered")
            return {'CANCELLED'}
        try:
            name, landmark_entries, measurement_entries = (
                protocol.load_protocol(self.filepath))
        except protocol.ProtocolError as exc:
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}

        outcome = state.apply_protocol(context, props, name, landmark_entries,
                                       measurement_entries)
        print("\n[BSMT] loaded protocol '%s'" % name)
        for line in outcome["lines"]:
            print("[BSMT]   %s" % line)
        overlay.tag_redraw(context)

        if outcome["unresolved"]:
            # Sect. 7: a reference that cannot be resolved is reported, never
            # redirected to some other landmark.
            self.report({'WARNING'},
                        "BSMT: loaded '%s' with %d unresolved reference(s) - "
                        "see the system console"
                        % (name, len(outcome["unresolved"])))
        else:
            self.report({'INFO'}, "BSMT: " + outcome["lines"][0])
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Surface Regions (Milestone 3.29)
# ---------------------------------------------------------------------------
#
# NOT ONE OPERATOR IN THIS SECTION CALLS THE SOLVER, and that is a design
# constraint rather than an accident. A region is assembled from paths that
# have ALREADY been computed; validating, showing, reordering, reversing,
# renaming or deleting one reads properties and `pathcache` and stops there.
# On a real scan a surface solve is tens of seconds to minutes, and a
# researcher who has to think twice before renaming a region will not use
# regions.


def _region_context(context):
    """(props, region) or (props, None). No geometry is read."""
    props = state.get_props(context)
    if props is None:
        return None, None
    return props, state.active_region(context, props)


def _region_canonical(context, props, item):
    """The canonical mesh a region's paths were solved on, or None.

    `rebuild=False` on purpose: this is asked from validation, and a region
    must never be the thing that forces a mesh rebuild. When the mesh is not
    already cached the answer is None, and validation then simply does not
    apply the geometry-hash and metric checks - which is honest, because it
    genuinely has not checked them.
    """
    object_name = item.boundary_object if item is not None else ""
    if not object_name:
        object_name = state.region_object_name(context, item) if item else ""
    obj = bpy.data.objects.get(object_name) if object_name else None
    if obj is None or obj.type != 'MESH':
        return None, None
    try:
        canonical = geodesic.meshcache.peek_current(obj)
    except Exception:                                 # noqa: BLE001
        canonical = None
    return canonical, obj


class BSMT_OT_add_region(bpy.types.Operator):
    """Create a new, empty Surface Region.

    A region is a closed boundary made of surface paths you have already
    computed. Creating one computes nothing
    """

    bl_idname = "bsmt.add_region"
    bl_label = "New Surface Region"
    bl_description = ("Create an empty Surface Region. Nothing is computed -"
                      " a region is assembled from paths that already exist")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return state.get_regions(context) is not None

    def execute(self, context):
        props = state.get_props(context)
        try:
            item = state.add_region(context, props)
        except regions.RegionError as exc:
            self.report({'ERROR'}, "BSMT: %s" % exc)
            return {'CANCELLED'}
        self.report({'INFO'}, "BSMT: added %s" % item.label)
        return {'FINISHED'}


class BSMT_OT_remove_region(bpy.types.Operator):
    """Delete the selected Surface Region.

    Deletes the BOUNDARY DEFINITION only. Every landmark, surface path and
    measurement it referenced is kept exactly as it was
    """

    bl_idname = "bsmt.remove_region"
    bl_label = "Delete Region"
    bl_description = ("Delete this region's boundary definition. The"
                      " landmarks, surface paths and measurements it refers"
                      " to are NOT deleted")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _region_context(context)[1] is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        props, item = _region_context(context)
        if item is None:
            self.report({'WARNING'}, "BSMT: select a region first")
            return {'CANCELLED'}
        label = item.label
        state.remove_region(context, props, int(props.region_index))
        self.report({'INFO'},
                    "BSMT: deleted %s. Its paths and landmarks are unchanged."
                    % label)
        return {'FINISHED'}


class BSMT_OT_add_region_landmark(bpy.types.Operator):
    """Append the selected landmark to this region's boundary.

    Nothing is computed. The boundary is solved only when you press Compute
    Boundary
    """

    bl_idname = "bsmt.add_region_landmark"
    bl_label = "Add Landmark"
    bl_description = ("Append the landmark selected in Landmark Manager to"
                      " this region's boundary. Nothing is computed")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props, item = _region_context(context)
        return item is not None and bool(state.get_landmarks(context))

    def execute(self, context):
        props, item = _region_context(context)
        if item is None:
            self.report({'WARNING'}, "BSMT: select a region first")
            return {'CANCELLED'}
        landmark = state.active_landmark(context, props)
        if landmark is None:
            self.report({'ERROR'},
                        "BSMT: select a landmark in Landmark Manager first")
            return {'CANCELLED'}
        state.append_region_landmark(item, landmark)
        state.refresh_region_status(context, item)
        self.report({'INFO'}, "BSMT: added %s to %s's boundary"
                    % (landmark.label, item.label))
        return {'FINISHED'}


class BSMT_OT_remove_region_landmark(bpy.types.Operator):
    """Remove this landmark from the region's boundary.

    The landmark itself is NOT deleted - only its place in this boundary
    """

    bl_idname = "bsmt.remove_region_landmark"
    bl_label = "Remove Landmark"
    bl_description = ("Remove this landmark from the boundary. The landmark"
                      " itself is kept")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        _props, item = _region_context(context)
        return item is not None and len(item.landmarks) > 0

    def execute(self, context):
        _props, item = _region_context(context)
        if item is None or not len(item.landmarks):
            return {'CANCELLED'}
        index = int(item.landmark_index)
        if not state.remove_region_landmark(item, index):
            self.report({'WARNING'}, "BSMT: select a boundary landmark first")
            return {'CANCELLED'}
        state.refresh_region_status(context, item)
        self.report({'INFO'}, "BSMT: removed boundary landmark %d" % (index + 1))
        return {'FINISHED'}


class BSMT_OT_move_region_landmark(bpy.types.Operator):
    """Reorder this boundary landmark.

    The order IS the boundary: A-B-C-D and A-C-B-D are different loops, so
    the computed boundary stops matching and must be computed again
    """

    bl_idname = "bsmt.move_region_landmark"
    bl_label = "Move Landmark"
    bl_description = ("Move this landmark up or down the boundary order. The"
                      " order defines the boundary")
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(
        name="Direction",
        items=(('UP', "Up", "Earlier in the boundary"),
               ('DOWN', "Down", "Later in the boundary")),
        default='UP',
    )

    @classmethod
    def poll(cls, context):
        _props, item = _region_context(context)
        return item is not None and len(item.landmarks) > 1

    def execute(self, context):
        _props, item = _region_context(context)
        if item is None:
            return {'CANCELLED'}
        offset = -1 if self.direction == 'UP' else 1
        moved = state.move_region_landmark(item, int(item.landmark_index),
                                           offset)
        if moved is None:
            return {'CANCELLED'}
        state.refresh_region_status(context, item)
        return {'FINISHED'}


class BSMT_OT_clear_region_landmarks(bpy.types.Operator):
    """Remove every landmark from this region's boundary.

    The landmarks themselves are kept; so is nothing else - the computed
    boundary is discarded, because it described a definition that is gone
    """

    bl_idname = "bsmt.clear_region_landmarks"
    bl_label = "Clear Boundary Landmarks"
    bl_description = ("Empty this region's boundary definition. The landmarks"
                      " themselves are kept")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        _props, item = _region_context(context)
        return item is not None and len(item.landmarks) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        _props, item = _region_context(context)
        if item is None:
            return {'CANCELLED'}
        count = len(item.landmarks)
        item.landmarks.clear()
        item.landmark_index = 0
        state.clear_region_boundary(item)
        try:
            visualization.remove_region_boundary(item.stable_id)
        except Exception:                             # pragma: no cover
            pass
        item.show_boundary = False
        state.refresh_region_status(context, item)
        self.report({'INFO'}, "BSMT: removed %d boundary landmark(s). The "
                              "landmarks themselves are unchanged." % count)
        return {'FINISHED'}


class BSMT_OT_compute_region_boundary(bpy.types.Operator):
    """Solve this region's closed boundary from its ordered landmarks.

    THE ONLY REGION OPERATION THAT RUNS THE SOLVER. One exact geodesic per
    consecutive landmark pair, including the closing pair, which on a real
    scan is tens of seconds to minutes EACH and will block Blender
    """

    bl_idname = "bsmt.compute_region_boundary"
    bl_label = "Compute Boundary"
    bl_description = ("Solve one exact surface path per consecutive landmark"
                      " pair, closing back to the first. This runs the"
                      " geodesic solver and may block Blender for minutes")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props, item = _region_context(context)
        if props is None or item is None or props.viz_running:
            return False
        return len(item.landmarks) >= regions.MIN_LANDMARKS

    def execute(self, context):
        props, item = _region_context(context)
        if props is None or item is None:
            return {'CANCELLED'}
        if props.viz_running:
            self.report({'WARNING'},
                        "BSMT: a path computation is already running")
            return {'CANCELLED'}

        # --- refuse on the DEFINITION before anything expensive ----------
        #
        # The pure rules already know every way a definition can fail to
        # describe a boundary - too few landmarks, a repeat, a landmark that
        # has gone, one that was never picked, two on different components.
        # Asking them first means the solver is never constructed for a
        # boundary that could not have been drawn anyway.
        verdict = state.refresh_region_status(context, item)
        if verdict["code"] in regions.DEFINITION_FAULTS:
            self.report({'ERROR'}, "BSMT: %s" % verdict["detail"])
            return {'CANCELLED'}

        unavailable = geodesic.ensure_loaded() or geodesic.measure_error()
        if unavailable:
            props.viz_status = unavailable
            self.report({'ERROR'}, "BSMT: " + unavailable)
            return {'CANCELLED'}

        landmark_collection = state.get_landmarks(context)
        ordered = [state.landmark_by_stable_id(landmark_collection,
                                               int(entry.landmark_stable_id))
                   for entry in item.landmarks]
        object_name = ordered[0].surface_point.source_object
        obj = bpy.data.objects.get(object_name)
        if obj is None or obj.type != 'MESH':
            message = "the scan '%s' is missing" % object_name
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}
        spread = {landmark.surface_point.source_object for landmark in ordered}
        if len(spread) > 1:
            self.report({'ERROR'},
                        "BSMT: the boundary landmarks are on %d different "
                        "meshes (%s). A region lies on one scan."
                        % (len(spread), ", ".join(sorted(spread))))
            return {'CANCELLED'}

        props.viz_running = True
        pairs = regions.segment_pairs(item.landmark_ids)
        props.viz_status = ("Computing %d boundary segments..." % len(pairs))
        print("[BSMT] Computing %d boundary segment(s) for region '%s' - "
              "Blender will not redraw until the solver returns"
              % (len(pairs), item.label))
        self._nudge(context)
        started = time.perf_counter()
        try:
            return self._solve(context, props, item, obj, ordered, started)
        finally:
            props.viz_running = False

    def _solve(self, context, props, item, obj, ordered, started):
        solve = geodesic.solve
        try:
            canonical = geodesic.meshcache.get(context, obj, props.unit)
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            message = "canonical mesh unavailable (%s)" % exc
            props.viz_status = message
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}

        log_solver_target("region boundary", obj, canonical)
        # The same gate every other route to the native solver passes. A
        # dense scan with non-manifold edges has crashed Blender with
        # SIGSEGV; a boundary is n solves rather than one, so a region is the
        # LAST place to skip it.
        gate = solver_preflight(props, canonical)
        if not gate["allowed"]:
            message = _report_preflight(self, gate, " (region boundary)",
                                        obj.name)
            props.viz_status = message
            state.refresh_region_status(context, item, canonical)
            self.report({'ERROR'}, "BSMT: " + message)
            return {'CANCELLED'}
        for line in gate["warnings"]:
            print("[BSMT] warning (region boundary): %s" % line)

        def spec(landmark):
            point = landmark.surface_point
            return solve.PointSpec(
                point.triangle_index,
                np.array(point.barycentric, dtype=np.float64),
                component_id=point.component_id,
                source_object=point.source_object,
                geometry_hash=point.geometry_hash,
                status=point.status,
                valid=point.valid,
            )

        # ------------------------------------------------------------------
        # TRANSACTIONAL. Every segment is solved into memory first and NOTHING
        # is written until all of them have succeeded. A boundary that is part
        # fresh and part stale is not a boundary - it is a drawing that looks
        # authoritative while describing two different definitions - so the
        # only two outcomes here are "the whole loop, computed now" and "the
        # previous state, untouched, plus a named failure".
        # ------------------------------------------------------------------
        solved = []
        count = len(ordered)
        for position in range(count):
            source = ordered[position]
            target = ordered[(position + 1) % count]
            label = "%s → %s" % (source.label, target.label)
            props.viz_status = ("Computing boundary segment %d of %d (%s)..."
                                % (position + 1, count, label))
            self._nudge(context)
            try:
                result = solve.surface_path(
                    canonical.vertices_solver, canonical.triangles,
                    spec(source), spec(target),
                    geometry_hash=canonical.geometry_hash,
                )
            except solve.MeasurementError as exc:
                return self._failed(context, props, item, canonical,
                                    position, label, exc.message)
            except Exception as exc:                  # noqa: BLE001
                traceback.print_exc()
                return self._failed(
                    context, props, item, canonical, position, label,
                    "%s: %s" % (type(exc).__name__, exc))
            solved.append((source, target, result))
            print("[BSMT]   segment %d/%d %s: %.4f mm, %d points"
                  % (position + 1, count, label, result.distance_mm,
                     result.point_count))

        # --- geometry must not have moved under us ------------------------
        live = geodesic.meshcache.peek_current(obj)
        if live is not None and live.geometry_hash != canonical.geometry_hash:
            return self._failed(
                context, props, item, canonical, -1, "",
                "the mesh geometry changed while the boundary was being "
                "computed")

        return self._commit(context, props, item, obj, canonical, solved,
                            started)

    def _failed(self, context, props, item, canonical, position, label,
                message):
        """Nothing is written. Say which segment, and leave the rest alone.

        POLICY, stated once: a failed compute NEVER replaces the previous
        cache and never keeps a partial one. Whatever boundary the region had
        before is still there and still described by its own definition key,
        so if the definition has since changed it reads STALE and if it has
        not it reads exactly as it did. The region cannot come out of here
        VALID on a boundary that was not fully solved.
        """
        where = ("boundary segment %d (%s)" % (position + 1, label)
                 if position >= 0 else "the boundary")
        detail = "%s could not be computed: %s" % (where, message)
        props.viz_status = detail
        print("[BSMT] REFUSED: %s" % detail)
        result = state.refresh_region_status(context, item, canonical)
        # The stored verdict comes from the rules, which is what keeps this
        # honest; the failure is reported separately rather than by writing a
        # status the rules did not derive.
        item.report = "\n".join(
            regions.summary_lines(result)
            + ["  compute refused: %s" % detail])
        self.report({'ERROR'}, "BSMT: " + detail)
        return {'CANCELLED'}

    def _commit(self, context, props, item, obj, canonical, solved, started):
        """Everything solved. Write the cache and the records in one go."""
        elapsed = time.perf_counter() - started
        item.segments.clear()
        try:
            pathcache.drop_region(item.stable_id)
        except Exception:                             # pragma: no cover
            pass

        total_mm = 0.0
        total_points = 0
        for position, (source, target, result) in enumerate(solved):
            points_local = np.array(
                viz.solver_to_local(canonical, result.polyline_solver),
                dtype=np.float64)
            normals_local = viz.surface_normals(canonical, points_local)
            stored = pathcache.store_region_segment(
                item.stable_id, position, points_local, normals_local)
            if not stored:
                # Storage failed after a successful solve. Refuse the whole
                # commit rather than keep a boundary with a hole in it.
                state.clear_region_boundary(item)
                return self._failed(context, props, item, canonical, position,
                                    "%s → %s" % (source.label, target.label),
                                    "the computed path could not be cached")
            segment = item.segments.add()
            segment.from_landmark = int(source.stable_id)
            segment.to_landmark = int(target.stable_id)
            segment.computed = True
            segment.point_count = int(stored)
            segment.length_mm = float(result.polyline_length_mm)
            segment.distance_mm = float(result.distance_mm)
            segment.mode = result.mode
            segment.object_name = canonical.source_object
            segment.geometry_hash = canonical.geometry_hash
            segment.metric_tensor = state.metric_tensor(
                obj.matrix_world, canonical.unit_multiplier)
            segment.metric_key = canonical.metric_key
            segment.unit = props.unit
            segment.from_triangle = int(source.surface_point.triangle_index)
            segment.from_bary = tuple(
                float(value) for value in source.surface_point.barycentric)
            segment.to_triangle = int(target.surface_point.triangle_index)
            segment.to_bary = tuple(
                float(value) for value in target.surface_point.barycentric)
            segment.component_id = int(source.surface_point.component_id)
            total_mm += float(result.polyline_length_mm)
            total_points += int(stored)

        # The definition this cache belongs to, stamped last. Until this line
        # runs there is no boundary as far as validation is concerned.
        item.cached_definition = regions.definition_key(item.landmark_ids)
        item.computed_elapsed_s = float(elapsed)
        # Orphans from a longer previous definition would otherwise sit in the
        # file waiting to be picked up by a future definition of the same size.
        try:
            pathcache.keep_only_regions(
                [(int(region.stable_id), len(region.segments))
                 for region in (state.get_regions(context) or ())])
        except Exception:                             # pragma: no cover
            pass

        result = state.refresh_region_status(context, item, canonical)
        item.show_boundary = True
        viz.draw_region_boundary(context, props, item,
                                 trusted=result["status"] in regions.TRUSTED)
        visualization.set_region_boundary_visible(item.stable_id, True)

        summary = ("boundary computed: %d segments, %.1f mm around, "
                   "%d points, %.2f s"
                   % (len(solved), total_mm, total_points, elapsed))
        props.viz_status = summary
        print("[BSMT] %s '%s': %s" % (item.protocol_id, item.label, summary))
        self.report({'INFO'}, "BSMT: " + summary)
        return {'FINISHED'}

    @staticmethod
    def _nudge(context):
        try:
            if context.area is not None:
                context.area.tag_redraw()
        except Exception:                             # pragma: no cover
            pass


class BSMT_OT_validate_region(bpy.types.Operator):
    """Check that this region's computed boundary is still the answer.

    Reads the cached boundary and its provenance. It never runs the geodesic
    solver, so it costs the same on a 20k-triangle mesh and a 1M one
    """

    bl_idname = "bsmt.validate_region"
    bl_label = "Validate Region"
    bl_description = ("Check that this region's computed boundary still"
                      " matches its landmarks. Reads the cache only - no"
                      " solver")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _region_context(context)[1] is not None

    def execute(self, context):
        props, item = _region_context(context)
        if item is None:
            self.report({'WARNING'}, "BSMT: select a region first")
            return {'CANCELLED'}
        canonical, _obj = _region_canonical(context, props, item)
        result = state.refresh_region_status(context, item, canonical,
                                             check_touching=True)
        lines = regions.summary_lines(result)
        if canonical is None:
            lines.append("  note: the mesh is not cached, so the geometry "
                         "and metric checks were NOT applied")
        item.report = "\n".join(lines)
        item.validated = True
        item.validated_fingerprint = state.region_fingerprint(item, result)
        print("\n[BSMT] region '%s'" % item.label)
        for line in lines:
            print("[BSMT]   %s" % line)
        level = ({'INFO'} if result["status"] == regions.STATUS_VALID
                 else {'WARNING'})
        self.report(level, "BSMT: %s - %s"
                    % (regions.STATUS_SHORT.get(result["status"],
                                                result["status"]),
                       result["detail"]))
        return {'FINISHED'}


class BSMT_OT_show_region_boundary(bpy.types.Operator):
    """Draw this region's closed boundary from its CACHED paths.

    No solver call: the polylines were computed once and kept
    """

    bl_idname = "bsmt.show_region_boundary"
    bl_label = "Show Boundary"
    bl_description = ("Draw the closed boundary from the cached surface"
                      " paths. Nothing is computed")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        _props, item = _region_context(context)
        return item is not None and len(item.segments) > 0

    def execute(self, context):
        props, item = _region_context(context)
        if item is None:
            return {'CANCELLED'}
        canonical, _obj = _region_canonical(context, props, item)
        result = state.refresh_region_status(context, item, canonical)
        trusted = result["status"] in regions.TRUSTED
        helper, missing = viz.draw_region_boundary(context, props, item,
                                                   trusted=trusted)
        if helper is None:
            self.report({'WARNING'},
                        "BSMT: nothing to draw - %s" % result["detail"])
            return {'CANCELLED'}
        item.show_boundary = True
        visualization.set_region_boundary_visible(item.stable_id, True)
        if missing:
            self.report({'WARNING'},
                        "BSMT: drew a PARTIAL boundary - segment(s) %s have "
                        "no computed path"
                        % ", ".join(str(value) for value in missing))
        elif not trusted:
            # Drawn, but in the warning colour and said out loud. A boundary
            # BSMT cannot vouch for must not pass as one it can.
            self.report({'WARNING'},
                        "BSMT: boundary shown in the warning colour - %s"
                        % result["detail"])
        else:
            self.report({'INFO'}, "BSMT: boundary shown (%.1f mm around)"
                        % result["length_mm"])
        return {'FINISHED'}


class BSMT_OT_hide_region_boundary(bpy.types.Operator):
    """Hide this region's boundary. The definition and the paths are kept"""

    bl_idname = "bsmt.hide_region_boundary"
    bl_label = "Hide Boundary"
    bl_description = ("Hide the boundary. Showing it again computes nothing")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _region_context(context)[1] is not None

    def execute(self, context):
        _props, item = _region_context(context)
        if item is None:
            return {'CANCELLED'}
        item.show_boundary = False
        visualization.set_region_boundary_visible(item.stable_id, False)
        return {'FINISHED'}


class BSMT_OT_refresh_region_boundaries(bpy.types.Operator):
    """Restate every region and redraw the boundaries that are shown.

    Reads cached paths only. No solver call, whatever the state of the scene
    """

    bl_idname = "bsmt.refresh_regions"
    bl_label = "Refresh Regions"
    bl_description = ("Re-check every region against its paths and redraw the"
                      " visible boundaries. Nothing is computed")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return state.get_regions(context) is not None

    def execute(self, context):
        props = state.get_props(context)
        collection = state.get_regions(context) or ()
        shown = 0
        for item in collection:
            canonical, _obj = _region_canonical(context, props, item)
            result = state.refresh_region_status(context, item, canonical)
            if item.show_boundary:
                viz.draw_region_boundary(
                    context, props, item,
                    trusted=result["status"] in regions.TRUSTED)
                visualization.set_region_boundary_visible(item.stable_id, True)
                shown += 1
            else:
                visualization.set_region_boundary_visible(item.stable_id,
                                                          False)
        visualization.remove_orphan_region_boundaries(
            [int(item.stable_id) for item in collection])
        self.report({'INFO'}, "BSMT: %d region(s) restated, %d boundary/ies "
                              "drawn" % (len(collection), shown))
        return {'FINISHED'}


class BSMT_OT_clear_region_boundaries(bpy.types.Operator):
    """Remove every region boundary from the viewport. Definitions are kept"""

    bl_idname = "bsmt.clear_region_boundaries"
    bl_label = "Clear Boundaries"
    bl_description = ("Remove every drawn region boundary. The definitions"
                      " and the cached paths are kept")
    bl_options = {'REGISTER'}

    def execute(self, context):
        removed = visualization.clear_region_boundaries()
        for item in (state.get_regions(context) or ()):
            item.show_boundary = False
        self.report({'INFO'}, "BSMT: removed %d boundary/ies" % removed)
        return {'FINISHED'}


class BSMT_OT_compute_region_interior(bpy.types.Operator):
    """Work out which side of this region's boundary is the region.

    Topology and geometry analysis on the mesh BSMT already has. It does NOT
    run the geodesic solver, so it costs seconds rather than minutes
    """

    bl_idname = "bsmt.compute_region_interior"
    bl_label = "Compute Interior"
    bl_description = ("Classify every triangle as inside, outside or cut by"
                      " the boundary, and clip the cut ones exactly. No"
                      " geodesic solver is used")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props, item = _region_context(context)
        if props is None or item is None or props.viz_running:
            return False
        return len(item.segments) > 0

    def execute(self, context):
        props, item = _region_context(context)
        if item is None:
            self.report({'WARNING'}, "BSMT: select a region first")
            return {'CANCELLED'}

        # The boundary must be CURRENT. An interior computed from a stale
        # boundary would be a precise answer to a question nobody asked.
        canonical, obj = _region_canonical(context, props, item)
        verdict = state.refresh_region_status(context, item, canonical)
        if verdict["status"] not in regions.TRUSTED:
            state.refresh_interior_status(item, verdict, canonical)
            self.report({'ERROR'},
                        "BSMT: the boundary is %s - compute it before asking "
                        "for its interior. %s"
                        % (regions.STATUS_SHORT.get(verdict["status"],
                                                    verdict["status"]),
                           verdict["detail"]))
            return {'CANCELLED'}
        if canonical is None or obj is None:
            self.report({'ERROR'},
                        "BSMT: the scan's analysed mesh is not available - "
                        "run Analyze Topology on it first")
            return {'CANCELLED'}

        loop, closed, missing = viz.region_boundary_points(item)
        if missing or loop.shape[0] < 3:
            self.report({'ERROR'},
                        "BSMT: the cached boundary is incomplete - press "
                        "Compute Boundary")
            return {'CANCELLED'}

        # Say what is about to happen BEFORE it happens. Compute Interior is
        # seconds on a real scan rather than the minutes a solve costs, but
        # Blender does not redraw while it runs, so silence reads as a hang.
        props.viz_status = ("Classifying %s triangles against the boundary..."
                            % "{:,}".format(len(canonical.triangles)))
        print("[BSMT] Computing interior for region '%s': %s triangles, %d "
              "boundary points - Blender will not redraw until it returns"
              % (item.label, "{:,}".format(len(canonical.triangles)),
                 loop.shape[0]))
        self._nudge(context)
        started = time.perf_counter()
        stages = {}
        try:
            analysis = interior.compute(
                canonical.vertices_local, canonical.triangles, loop,
                component_labels=canonical.triangle_components,
                timings=stages)
        except interior.InteriorError as exc:
            # The headline goes to the operator report; the diagnostic record
            # - which can run to a dozen lines - goes to the console, where
            # it can actually be read.
            headline = exc.message.split("\n", 1)[0]
            item.interior_status = interior.STATUS_INVALID
            item.interior_code = exc.code
            item.interior_detail = headline
            item.interior_definition = ""
            print("[BSMT] REFUSED (interior): %s" % headline)
            for line in exc.message.split("\n")[1:]:
                print("[BSMT]   %s" % line)
            self.report({'ERROR'}, "BSMT: " + headline)
            return {'CANCELLED'}
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            message = "interior analysis failed (%s: %s)" % (
                type(exc).__name__, exc)
            item.interior_status = interior.STATUS_INVALID
            item.interior_code = interior.CODE_NOT_COMPUTED
            item.interior_detail = message
            self.report({'ERROR'}, "BSMT: " + message + " - see the console")
            return {'CANCELLED'}

        elapsed = time.perf_counter() - started
        side = analysis["sides"][item.interior_side]
        item.interior_full_count = int(side["full_count"])
        item.interior_partial_count = int(side["partial_count"])
        item.interior_component_id = int(analysis["component_id"] or 0)
        item.interior_geometry_hash = canonical.geometry_hash
        item.interior_elapsed_s = float(elapsed)
        # Stamped LAST, for the same reason the boundary's key is: until this
        # line runs there is no interior as far as validation is concerned.
        # The classification itself, in float64, next to the drawing made
        # from it. The fill is float32 display geometry and carries no link
        # back to a clipped piece's parent triangle, so it can be drawn from
        # but not measured from - see `interiorcache`.
        interiorcache.store(
            item.stable_id, side["full_triangles"],
            [(entry["triangle"], entry["bary"]) for entry in side["partial"]],
            item.interior_side, canonical.geometry_hash)
        interiorcache.keep_only(
            [int(region.stable_id)
             for region in (state.get_regions(context) or ())])
        item.interior_definition = state.interior_fingerprint(item)
        item.interior_status = interior.STATUS_VALID
        item.interior_code = interior.CODE_NONE
        item.interior_detail = (
            "%s: %d whole triangles and %d clipped by the boundary."
            % (interior.SIDE_LABELS.get(item.interior_side,
                                        item.interior_side),
               side["full_count"], side["partial_count"]))

        self._draw_fill(context, props, item, canonical, obj, analysis)
        summary = ("interior computed: %s, %d full + %d partial faces, %.2f s"
                   % (interior.SIDE_LABELS.get(item.interior_side,
                                               item.interior_side),
                      side["full_count"], side["partial_count"], elapsed))
        print("[BSMT] %s '%s': %s" % (item.protocol_id, item.label, summary))
        tiling = analysis.get("tiling") or {}
        # How the boundary actually met the mesh, in one line. The two ways
        # it can are not interchangeable: a chord CUTS a triangle and has to
        # be clipped exactly, while a run ALONG a mesh edge cuts nothing and
        # severs an adjacency instead. Seeing both counts is what tells you
        # which kind of boundary you are looking at - a scan whose boundary
        # follows mesh edges will show few partials and many aligned edges.
        print("[BSMT]   interior boundary: %d partial triangles / %d "
              "edge-aligned mesh edges (%d adjacencies severed) / tiling max "
              "relative residual %.2e"
              % (analysis.get("partial_triangles", 0),
                 analysis.get("aligned_edges", 0),
                 analysis.get("severed_edges", 0),
                 (tiling or {}).get("worst_relative", 0.0)))
        if tiling.get("checked"):
            # The invariant that refuses a wrong partition, reported even
            # when it passes: "it tiled" is less useful than "it tiled, and
            # here is how close the worst one came".
            print("[BSMT]   tiling: %d boundary triangles | max abs residual "
                  "%.2e mm^2 | max relative %.2e | worst triangle %d"
                  % (tiling["checked"], tiling["worst_absolute"],
                     tiling["worst_relative"], tiling["worst_triangle"]))
        if analysis.get("timings"):
            print("[BSMT]   stages: %s" % analysis["timings"])
            print("[BSMT]   %s triangles, %d boundary points, %d cut "
                  "triangles, %d edges touching a cut"
                  % ("{:,}".format(len(canonical.triangles)), loop.shape[0],
                     len(analysis["crossed_triangles"]),
                     analysis.get("cut_edge_count", 0)))
        for line in (analysis["limits"],):
            print("[BSMT]   scope: %s" % line)
        self.report({'INFO'}, "BSMT: " + summary)
        return {'FINISHED'}

    @staticmethod
    def _nudge(context):
        try:
            if context.area is not None:
                context.area.tag_redraw()
        except Exception:                             # pragma: no cover
            pass

    @staticmethod
    def _draw_fill(context, props, item, canonical, obj, analysis):
        """Build the fill helper from the classification just computed."""
        side = analysis["sides"][item.interior_side]
        points, faces = state.interior_fill_geometry(
            canonical.vertices_local, canonical.triangles, side)
        visualization.update_region_fill(
            context, props, item.stable_id, points, faces, obj.matrix_world,
            tuple(item.fill_color), float(item.fill_opacity))
        item.show_fill = True
        visualization.set_region_fill_visible(item.stable_id, True)


class BSMT_OT_show_region_fill(bpy.types.Operator):
    """Draw this region's computed interior on the body surface.

    Reuses the classified faces. No solver, and no re-analysis
    """

    bl_idname = "bsmt.show_region_fill"
    bl_label = "Show Fill"
    bl_description = ("Draw the computed interior. Nothing is computed - the"
                      " classified faces are reused")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        _props, item = _region_context(context)
        return item is not None and bool(item.interior_definition)

    def execute(self, context):
        props, item = _region_context(context)
        if item is None:
            return {'CANCELLED'}
        canonical, obj = _region_canonical(context, props, item)
        verdict = state.refresh_region_status(context, item, canonical)
        result = state.refresh_interior_status(item, verdict, canonical)
        helper = visualization.region_fill(item.stable_id)
        if helper is None:
            self.report({'WARNING'},
                        "BSMT: nothing to draw - press Compute Interior")
            return {'CANCELLED'}
        trusted = result["status"] in interior.TRUSTED
        colour = (tuple(item.fill_color) if trusted
                  else visualization.REGION_FILL_UNTRUSTED_COLOR)
        visualization.update_region_fill(
            context, props, item.stable_id,
            [vertex.co.copy() for vertex in helper.data.vertices],
            [tuple(polygon.vertices) for polygon in helper.data.polygons],
            obj.matrix_world if obj is not None else helper.matrix_world,
            colour, float(item.fill_opacity))
        item.show_fill = True
        visualization.set_region_fill_visible(item.stable_id, True)
        if not trusted:
            # Drawn, but in the warning colour and said out loud. An interior
            # BSMT cannot vouch for must not pass for one it can.
            self.report({'WARNING'},
                        "BSMT: fill shown in the warning colour - %s"
                        % result["detail"])
        else:
            self.report({'INFO'}, "BSMT: fill shown (%d full + %d partial)"
                        % (item.interior_full_count,
                           item.interior_partial_count))
        return {'FINISHED'}


class BSMT_OT_hide_region_fill(bpy.types.Operator):
    """Hide this region's fill. The computed interior is kept"""

    bl_idname = "bsmt.hide_region_fill"
    bl_label = "Hide Fill"
    bl_description = ("Hide the fill. Showing it again computes nothing")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _region_context(context)[1] is not None

    def execute(self, context):
        _props, item = _region_context(context)
        if item is None:
            return {'CANCELLED'}
        item.show_fill = False
        visualization.set_region_fill_visible(item.stable_id, False)
        return {'FINISHED'}


class BSMT_OT_clear_region_fills(bpy.types.Operator):
    """Remove every drawn region fill. The boundaries are kept"""

    bl_idname = "bsmt.clear_region_fills"
    bl_label = "Clear Fills"
    bl_description = ("Remove every drawn region fill. The boundaries and"
                      " the computed interiors are kept")
    bl_options = {'REGISTER'}

    def execute(self, context):
        removed = visualization.clear_region_fills()
        for item in (state.get_regions(context) or ()):
            item.show_fill = False
        self.report({'INFO'}, "BSMT: removed %d fill(s)" % removed)
        return {'FINISHED'}


def _region_component_faces(canonical, component_id):
    """Every triangle of one connected component, as a face list."""
    labels = canonical.triangle_components
    wanted = int(component_id)
    return [tuple(int(value) for value in canonical.triangles[index])
            for index in range(len(canonical.triangles))
            if int(labels[index]) == wanted]


def _outward_direction(canonical, component_id):
    """+1 / -1 for which way to offset, or (None, why) when it is unknown.

    Determined from the component's SIGNED VOLUME, not assumed from the
    winding: outward is a property of a solid, and a component that does not
    enclose one has no outward at all. BSMT refuses there rather than picking
    a side - extruding a preview into the body would look entirely plausible
    and be exactly backwards.
    """
    faces = _region_component_faces(canonical, component_id)
    if not faces:
        return None, ("the interior's surface component could not be "
                      "identified")
    if not panelpreview.is_closed(faces):
        return None, ("the surface component this region lies on is OPEN - "
                      "it has boundary edges - so nothing defines which side "
                      "is outward. BSMT refuses rather than guessing, "
                      "because extruding into the body would look right and "
                      "be backwards")
    sign = panelpreview.outward_sign(canonical.vertices_local, faces)
    if sign is None:
        return None, ("the surface component encloses no measurable volume, "
                      "so outward cannot be determined")
    return sign, ""


class BSMT_OT_show_region_panel(bpy.types.Operator):
    """Draw a thickness preview of this region's computed interior.

    A VISUALIZATION: the classified interior offset outward along the body
    surface normals. It is not a physical simulation and not a manufacturing
    model
    """

    bl_idname = "bsmt.show_region_panel"
    bl_label = "Show Thickness Preview"
    bl_description = ("Offset the computed interior outward by the thickness"
                      " and draw it as a panel. Nothing is re-analysed and no"
                      " solver runs - this is a visualization, not a"
                      " simulation")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        _props, item = _region_context(context)
        return item is not None and bool(item.interior_definition)

    def execute(self, context):
        props, item = _region_context(context)
        if item is None:
            return {'CANCELLED'}

        # The INTERIOR must be current. A preview of a stale classification
        # is a solid-looking picture of a region that has moved.
        canonical, obj = _region_canonical(context, props, item)
        verdict = state.refresh_region_status(context, item, canonical)
        result = state.refresh_interior_status(item, verdict, canonical)
        if result["status"] not in interior.TRUSTED:
            state.clear_region_panel(item)
            self.report({'ERROR'},
                        "BSMT: the interior is %s - compute it before asking "
                        "for a thickness preview. %s"
                        % (interior.STATUS_SHORT.get(result["status"],
                                                     result["status"]),
                           result["detail"]))
            return {'CANCELLED'}

        try:
            thickness = panelpreview.check_thickness(item.panel_thickness_mm)
        except panelpreview.PreviewError as exc:
            item.panel_detail = exc.message
            self.report({'ERROR'}, "BSMT: " + exc.message)
            return {'CANCELLED'}

        # BUILT FROM THE FILL, which IS the classified interior. Not from a
        # fresh analysis - that is what makes "changing the thickness
        # recomputes nothing" true by construction rather than by promise.
        fill = visualization.region_fill(item.stable_id)
        if fill is None or not len(fill.data.polygons):
            self.report({'ERROR'},
                        "BSMT: there is no computed interior to give a "
                        "thickness to - press Compute Interior")
            return {'CANCELLED'}

        direction, why = _outward_direction(canonical,
                                            item.interior_component_id)
        if direction is None:
            state.clear_region_panel(item)
            item.panel_detail = why
            print("[BSMT] REFUSED (thickness preview): %s" % why)
            self.report({'ERROR'}, "BSMT: " + why)
            return {'CANCELLED'}

        points = [tuple(vertex.co) for vertex in fill.data.vertices]
        faces = [tuple(polygon.vertices) for polygon in fill.data.polygons]
        diagonal = float(np.linalg.norm(
            canonical.vertices_local.max(axis=0)
            - canonical.vertices_local.min(axis=0)))
        try:
            shell = panelpreview.build_shell(points, faces, thickness,
                                             direction,
                                             diagonal * 1e-6)
        except panelpreview.PreviewError as exc:
            state.clear_region_panel(item)
            item.panel_detail = exc.message
            print("[BSMT] REFUSED (thickness preview): %s" % exc.message)
            self.report({'ERROR'}, "BSMT: " + exc.message)
            return {'CANCELLED'}

        visualization.update_region_panel(
            context, props, item.stable_id, shell["points"], shell["faces"],
            obj.matrix_world if obj is not None else fill.matrix_world,
            tuple(item.panel_color), float(item.panel_opacity))
        item.show_panel = True
        visualization.set_region_panel_visible(item.stable_id, True)
        item.panel_built = state.panel_fingerprint(item)
        item.panel_folded_faces = int(shell["folded_faces"])
        item.panel_detail = (
            "%.2f mm outward: %d surface faces, %d side-wall faces, %d "
            "boundary loop(s)."
            % (thickness, shell["base_face_count"], shell["wall_face_count"],
               shell["loop_count"]))

        print("[BSMT] %s '%s': thickness preview %s"
              % (item.protocol_id, item.label, item.panel_detail))
        print("[BSMT]   scope: %s" % panelpreview.LIMITS)
        if shell["folded_faces"]:
            warning = ("%d face(s) of the offset surface have folded through "
                       "themselves - the thickness exceeds the local radius "
                       "of curvature there. The preview is drawn, but it is "
                       "not a usable shape in those places."
                       % shell["folded_faces"])
            print("[BSMT]   WARNING: %s" % warning)
            self.report({'WARNING'}, "BSMT: " + warning)
        else:
            self.report({'INFO'}, "BSMT: thickness preview - "
                        + item.panel_detail)
        return {'FINISHED'}


class BSMT_OT_hide_region_panel(bpy.types.Operator):
    """Hide this region's thickness preview. The interior is kept"""

    bl_idname = "bsmt.hide_region_panel"
    bl_label = "Hide Thickness Preview"
    bl_description = "Hide the thickness preview. Nothing is recomputed"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _region_context(context)[1] is not None

    def execute(self, context):
        _props, item = _region_context(context)
        if item is None:
            return {'CANCELLED'}
        item.show_panel = False
        visualization.set_region_panel_visible(item.stable_id, False)
        return {'FINISHED'}


class BSMT_OT_clear_region_panels(bpy.types.Operator):
    """Remove every drawn thickness preview. Interiors and fills are kept"""

    bl_idname = "bsmt.clear_region_panels"
    bl_label = "Clear Thickness Previews"
    bl_description = ("Remove every drawn thickness preview. The computed"
                      " interiors and the fills are kept")
    bl_options = {'REGISTER'}

    def execute(self, context):
        removed = visualization.clear_region_panels()
        for item in (state.get_regions(context) or ()):
            item.panel_built = ""
            item.show_panel = False
        self.report({'INFO'}, "BSMT: removed %d thickness preview(s)"
                    % removed)
        return {'FINISHED'}


class BSMT_OT_compute_region_area(bpy.types.Operator):
    """Measure the selected region's surface area on the body mesh.

    The mesh surface area of the selected region on the triangular body
    mesh - whole interior triangles plus the exactly-clipped polygons where
    the boundary cuts through one. No solver, and no re-analysis
    """

    bl_idname = "bsmt.compute_region_area"
    bl_label = "Compute Area"
    bl_description = ("Sum the whole interior triangles and the clipped"
                      " boundary polygons. Mesh area on the triangular body"
                      " mesh - not true anatomical surface area")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props, item = _region_context(context)
        return (props is not None and item is not None
                and not props.viz_running
                and bool(item.interior_definition))

    def execute(self, context):
        props, item = _region_context(context)
        if item is None:
            self.report({'WARNING'}, "BSMT: select a region first")
            return {'CANCELLED'}

        # The INTERIOR must be current. Measuring a stale classification
        # would produce a precise number for a region that has moved, which
        # is worse than no number at all.
        canonical, _obj = _region_canonical(context, props, item)
        verdict = state.refresh_region_status(context, item, canonical)
        result = state.refresh_interior_status(item, verdict, canonical)
        if result["status"] not in interior.TRUSTED:
            state.refresh_area_status(item, result)
            self.report({'ERROR'},
                        "BSMT: the interior is %s - compute it before asking "
                        "for its area. %s"
                        % (interior.STATUS_SHORT.get(result["status"],
                                                     result["status"]),
                           result["detail"]))
            return {'CANCELLED'}
        if canonical is None:
            self.report({'ERROR'},
                        "BSMT: the scan's analysed mesh is not available - "
                        "run Analyze Topology on it first")
            return {'CANCELLED'}

        stored = interiorcache.load(item.stable_id)
        if stored is None:
            item.area_status = surfacearea.STATUS_INVALID
            item.area_code = surfacearea.CODE_NO_INTERIOR
            item.area_detail = ("The interior classification is not in this "
                                "file. Press Compute Interior.")
            self.report({'ERROR'}, "BSMT: " + item.area_detail)
            return {'CANCELLED'}
        if stored["geometry_hash"] != canonical.geometry_hash:
            item.area_status = surfacearea.STATUS_STALE
            item.area_code = surfacearea.CODE_GEOMETRY_MISMATCH
            item.area_detail = ("The interior was classified on different "
                                "geometry. Press Compute Interior.")
            self.report({'ERROR'}, "BSMT: " + item.area_detail)
            return {'CANCELLED'}

        started = time.perf_counter()
        try:
            # PHYSICAL MILLIMETRES. The interior is classified in the scan's
            # object-local space, so its own areas are in local units; the
            # clipped pieces are stored barycentrically precisely so they can
            # be rebuilt against the millimetre corners of their own parent
            # triangle instead of being rescaled after the fact.
            measured = surfacearea.region_area_mm2(
                canonical.vertices_solver, canonical.triangles,
                stored["full_triangles"], stored["pieces"])
        except surfacearea.AreaError as exc:
            item.area_status = surfacearea.STATUS_INVALID
            item.area_code = exc.code
            item.area_detail = exc.message
            item.area_mm2 = 0.0
            item.area_interior_key = ""
            print("[BSMT] REFUSED (area): %s" % exc.message)
            self.report({'ERROR'}, "BSMT: " + exc.message)
            return {'CANCELLED'}
        except Exception as exc:                      # noqa: BLE001
            traceback.print_exc()
            message = "area failed (%s: %s)" % (type(exc).__name__, exc)
            item.area_status = surfacearea.STATUS_INVALID
            item.area_code = surfacearea.CODE_NOT_COMPUTED
            item.area_detail = message
            self.report({'ERROR'}, "BSMT: " + message + " - see the console")
            return {'CANCELLED'}

        elapsed = time.perf_counter() - started
        item.area_mm2 = float(measured["area_mm2"])
        item.area_full_mm2 = float(measured["full_mm2"])
        item.area_partial_mm2 = float(measured["partial_mm2"])
        item.area_component_mm2 = surfacearea.component_area_mm2(
            canonical.vertices_solver, canonical.triangles,
            canonical.triangle_components, item.interior_component_id)
        item.area_method = measured["method"]
        item.area_side = item.interior_side
        item.area_geometry_hash = canonical.geometry_hash
        item.area_elapsed_s = float(elapsed)
        item.area_detail = (
            "%s and %s of the %s: %d whole triangles and %d clipped pieces."
            % (surfacearea.format_mm2(item.area_mm2), surfacearea.format_cm2(item.area_mm2),
               interior.SIDE_LABELS.get(item.interior_side,
                                        item.interior_side),
               measured["full_count"], measured["partial_count"]))
        # Stamped LAST: until this line runs there is no area as far as
        # validation is concerned.
        item.area_interior_key = state.area_fingerprint(item)
        item.area_status = surfacearea.STATUS_VALID
        item.area_code = surfacearea.CODE_NONE

        share = (100.0 * item.area_mm2 / item.area_component_mm2
                 if item.area_component_mm2 > 0.0 else 0.0)
        print("[BSMT] %s '%s': %s" % (item.protocol_id, item.label,
                                      item.area_detail))
        print("[BSMT]   method: %s" % surfacearea.METHOD_LABEL)
        print("[BSMT]   %s of the surface component's %s (%.2f%%)"
              % (surfacearea.format_mm2(item.area_mm2),
                 surfacearea.format_mm2(item.area_component_mm2), share))
        print("[BSMT]   this is the %s. It is NOT true anatomical surface "
              "area." % surfacearea.DEFINITION)
        self.report({'INFO'}, "BSMT: %s (%s)"
                    % (surfacearea.format_mm2(item.area_mm2),
                       surfacearea.format_cm2(item.area_mm2)))
        return {'FINISHED'}


class BSMT_OT_clear_topology(bpy.types.Operator):
    """Clear the topology diagnostics report"""

    bl_idname = "bsmt.clear_topology"
    bl_label = "Clear Report"
    bl_description = "Clear the topology report. No mesh is touched"
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
    BSMT_OT_add_region,
    BSMT_OT_remove_region,
    BSMT_OT_add_region_landmark,
    BSMT_OT_remove_region_landmark,
    BSMT_OT_move_region_landmark,
    BSMT_OT_clear_region_landmarks,
    BSMT_OT_compute_region_boundary,
    BSMT_OT_validate_region,
    BSMT_OT_show_region_boundary,
    BSMT_OT_hide_region_boundary,
    BSMT_OT_refresh_region_boundaries,
    BSMT_OT_clear_region_boundaries,
    BSMT_OT_compute_region_interior,
    BSMT_OT_show_region_fill,
    BSMT_OT_hide_region_fill,
    BSMT_OT_clear_region_fills,
    BSMT_OT_show_region_panel,
    BSMT_OT_hide_region_panel,
    BSMT_OT_clear_region_panels,
    BSMT_OT_compute_region_area,
    BSMT_OT_export_measurements,
    BSMT_OT_export_landmarks,
    BSMT_OT_save_study_protocol,
    BSMT_OT_load_study_protocol,
    BSMT_OT_add_measurement,
    BSMT_OT_cancel_measurement_draft,
    BSMT_OT_remove_measurement,
    BSMT_OT_clear_measurements,
    BSMT_OT_remove_invalid_measurements,
    BSMT_OT_calculate_measurement,
    BSMT_OT_calculate_all_measurements,
    BSMT_OT_clear_measurement_results,
    BSMT_OT_refresh_measurements,
    BSMT_OT_save_measurement_template,
    BSMT_OT_load_measurement_template,
    BSMT_OT_compute_surface_path,
    BSMT_OT_toggle_surface_path,
    BSMT_OT_clear_cached_path,
    BSMT_OT_path_timing_report,
    BSMT_OT_refresh_visualization,
    BSMT_OT_clear_visualization,
    BSMT_OT_clear_all_visualizations,
    BSMT_OT_create_measurement_copy,
    BSMT_OT_toggle_measurement_copy,
    BSMT_OT_show_scan,
    BSMT_OT_clear_preprocess_report,
    BSMT_OT_pick_alignment_reference,
    BSMT_OT_clear_alignment_references,
    BSMT_OT_manual_align,
    BSMT_OT_preview_alignment,
    BSMT_OT_clear_alignment_preview,
    BSMT_OT_apply_alignment,
    BSMT_OT_flip_front_back,
    BSMT_OT_reset_alignment,
    BSMT_OT_analyse_repair,
    BSMT_OT_show_degenerate_triangles,
    BSMT_OT_step_degenerate_defect,
    BSMT_OT_focus_degenerate_defect,
    BSMT_OT_preview_degenerate_repair,
    BSMT_OT_repair_degenerate_local,
    BSMT_OT_show_non_manifold,
    BSMT_OT_focus_non_manifold,
    BSMT_OT_step_nonmanifold_defect,
    BSMT_OT_focus_nonmanifold_defect,
    BSMT_OT_preview_artifact_component,
    BSMT_OT_show_boundary_loop,
    BSMT_OT_clear_repair_highlight,
    BSMT_OT_fill_boundary_loop,
    BSMT_OT_remove_small_component,
    BSMT_OT_delete_defect_component,
    BSMT_OT_inspect_local_defect,
    BSMT_OT_preview_local_candidate,
    BSMT_OT_remove_local_faces,
    BSMT_OT_remove_duplicate_faces,
    BSMT_OT_weld_non_manifold,
    BSMT_OT_auto_repair_local,
    BSMT_OT_auto_repair_boundaries,
    BSMT_OT_restore_repair_backup,
    BSMT_OT_clear_repair_report,
)


# ---------------------------------------------------------------------------
# Which stage each operator means the researcher is working in
# ---------------------------------------------------------------------------
#
# DISPLAY STATE ONLY. The single thing this decides is whether the SELECTED
# landmark keeps its emphasis ring (see state.enter_stage). No measurement,
# landmark, SurfacePoint or cached path reads it, and no geodesic is
# recomputed because of it.
#
# It is a table applied once at registration rather than a line added to
# thirty operator bodies. That keeps a viewport-decoration concern out of the
# operators that do the actual work, and it makes the rule readable in one
# place instead of inferable from thirty. An operator that is not listed
# leaves the stage exactly as it found it, so omitting one is inert.

STAGE_BY_OPERATOR = {
    # Landmark Manager - picking, naming and checking landmarks. This is the
    # only stage in which the emphasis ring is telling the researcher
    # something: which row the next click belongs to.
    "bsmt.add_landmark": readiness.STAGE_LANDMARKS,
    "bsmt.remove_landmark": readiness.STAGE_LANDMARKS,
    "bsmt.clear_landmark_position": readiness.STAGE_LANDMARKS,
    "bsmt.clear_landmarks": readiness.STAGE_LANDMARKS,
    "bsmt.validate_landmarks": readiness.STAGE_LANDMARKS,
    "bsmt.pick_landmark": readiness.STAGE_LANDMARKS,
    "bsmt.guided_picking": readiness.STAGE_LANDMARKS,
    "bsmt.save_protocol": readiness.STAGE_LANDMARKS,
    "bsmt.load_protocol": readiness.STAGE_LANDMARKS,

    # Measurement Manager - defining and computing measurements.
    "bsmt.add_measurement": readiness.STAGE_MEASUREMENTS,
    "bsmt.cancel_measurement_draft": readiness.STAGE_MEASUREMENTS,
    "bsmt.remove_measurement": readiness.STAGE_MEASUREMENTS,
    "bsmt.clear_measurements": readiness.STAGE_MEASUREMENTS,
    "bsmt.remove_invalid_measurements": readiness.STAGE_MEASUREMENTS,
    "bsmt.calculate_measurement": readiness.STAGE_MEASUREMENTS,
    "bsmt.calculate_all_measurements": readiness.STAGE_MEASUREMENTS,
    "bsmt.clear_measurement_results": readiness.STAGE_MEASUREMENTS,
    "bsmt.refresh_measurements": readiness.STAGE_MEASUREMENTS,
    "bsmt.save_measurement_template": readiness.STAGE_MEASUREMENTS,
    "bsmt.load_measurement_template": readiness.STAGE_MEASUREMENTS,

    # Measurement Visualization.
    "bsmt.compute_surface_path": readiness.STAGE_VISUALIZATION,
    "bsmt.refresh_visualization": readiness.STAGE_VISUALIZATION,
    "bsmt.clear_visualization": readiness.STAGE_VISUALIZATION,
    "bsmt.toggle_surface_path": readiness.STAGE_VISUALIZATION,
    "bsmt.clear_cached_path": readiness.STAGE_VISUALIZATION,
    "bsmt.path_timing_report": readiness.STAGE_VISUALIZATION,
    "bsmt.clear_all_visualizations": readiness.STAGE_VISUALIZATION,

    # Surface Regions - assembling closed boundaries from computed paths.
    "bsmt.add_region": readiness.STAGE_REGIONS,
    "bsmt.remove_region": readiness.STAGE_REGIONS,
    "bsmt.add_region_landmark": readiness.STAGE_REGIONS,
    "bsmt.remove_region_landmark": readiness.STAGE_REGIONS,
    "bsmt.move_region_landmark": readiness.STAGE_REGIONS,
    "bsmt.clear_region_landmarks": readiness.STAGE_REGIONS,
    "bsmt.compute_region_boundary": readiness.STAGE_REGIONS,
    "bsmt.validate_region": readiness.STAGE_REGIONS,
    "bsmt.show_region_boundary": readiness.STAGE_REGIONS,
    "bsmt.hide_region_boundary": readiness.STAGE_REGIONS,
    "bsmt.refresh_regions": readiness.STAGE_REGIONS,
    "bsmt.clear_region_boundaries": readiness.STAGE_REGIONS,
    "bsmt.compute_region_interior": readiness.STAGE_REGIONS,
    "bsmt.show_region_fill": readiness.STAGE_REGIONS,
    "bsmt.hide_region_fill": readiness.STAGE_REGIONS,
    "bsmt.clear_region_fills": readiness.STAGE_REGIONS,
    "bsmt.show_region_panel": readiness.STAGE_REGIONS,
    "bsmt.hide_region_panel": readiness.STAGE_REGIONS,
    "bsmt.clear_region_panels": readiness.STAGE_REGIONS,
    "bsmt.compute_region_area": readiness.STAGE_REGIONS,

    # Results and export.
    "bsmt.export_measurements": readiness.STAGE_EXPORT,
    "bsmt.export_landmarks": readiness.STAGE_EXPORT,
    "bsmt.save_study_protocol": readiness.STAGE_EXPORT,
    "bsmt.load_study_protocol": readiness.STAGE_EXPORT,
}

#: Marks a method this module has already wrapped, so a re-register - which
#: Blender does on every "Reload Scripts" - cannot wrap it a second time.
_STAGE_WRAPPED = "_bsmt_stage_wrapped"


def _note_stage(context, stage):
    """Record the stage, and never let a viewport hint break the work."""
    try:
        state.enter_stage(context, stage)
    except Exception:                                 # pragma: no cover
        traceback.print_exc()


def _wrap_for_stage(method, stage, name):
    """`method` with a stage note recorded before it runs.

    Before, not after: `pick_point` is modal and returns RUNNING_MODAL, so
    "after it finishes" is not a moment this wrapper is present for. Recording
    on entry is also the honest reading - the researcher is in that stage from
    the moment they press the button.

    The two shapes are spelled out rather than taken as *args because Blender
    INSPECTS the argument count when registering an operator and rejects a
    class whose `invoke` does not take exactly (self, context, event).
    """
    if name == "invoke":
        def wrapped(self, context, event):
            _note_stage(context, stage)
            return method(self, context, event)
    else:
        def wrapped(self, context):
            _note_stage(context, stage)
            return method(self, context)

    wrapped.__name__ = getattr(method, "__name__", name)
    wrapped.__doc__ = method.__doc__
    setattr(wrapped, _STAGE_WRAPPED, True)
    return wrapped


def _apply_stage_notes(operator_classes):
    """Attach the stage note to every operator the table names.

    Returns how many methods were wrapped, which is what the test asserts
    against the table rather than against a hand-copied number.
    """
    wrapped = 0
    for cls in operator_classes:
        stage = STAGE_BY_OPERATOR.get(getattr(cls, "bl_idname", ""))
        if stage is None:
            continue
        for name in ("execute", "invoke"):
            method = cls.__dict__.get(name)
            if method is None or getattr(method, _STAGE_WRAPPED, False):
                continue
            setattr(cls, name, _wrap_for_stage(method, stage, name))
            wrapped += 1
    return wrapped


def register():
    _apply_stage_notes(classes)
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
