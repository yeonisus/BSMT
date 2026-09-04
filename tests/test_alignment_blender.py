"""Acceptance tests for alignment correctness (Milestone 3.20).

    /path/to/blender -b --factory-startup --python tests/test_alignment_blender.py

The offline suite (`tests/test_alignment.py`) proves the frame algebra. This
one proves the thing that actually failed: that the pose Blender ends up in
satisfies the contract the panel advertises, and that BSMT says so only when
it does.

Every successful case asserts the **transformed world reference vectors**,
reconstructed from each SurfacePoint's stored local position against the live
`matrix_world`. Euler angles are never used as evidence: they are a
parameterisation, not a measurement, and reading them is how a wrong pose
passed for a right one.
"""

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_alignment_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_alignment_blender.py")
    raise SystemExit(0)

import numpy as np  # noqa: E402

FAILURES = []
CHECKS = [0]

X = np.array([1.0, 0.0, 0.0])
Y = np.array([0.0, 1.0, 0.0])
Z = np.array([0.0, 0.0, 1.0])

#: The frame is built orthonormal, so a correct apply lands within float
#: error. Anything looser would let a real defect through.
AXIS_TOL = 1e-6


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------
#
# The body is deliberately anisotropic - 0.35 wide, 0.22 deep, 1.0 tall - so
# "which way is this facing" is a question the numbers can answer. A sphere
# would look identical from every side and could not fail the Top-view check.

#: Anatomy in the object's OWN local space, before whatever pose it is given:
#: local +X is the subject's left, local -Y anterior, local +Z superior.
ANATOMY_LOCAL = {
    'LEFT': (0.35, 0.0, 0.30),
    'RIGHT': (-0.35, 0.0, 0.30),
    'SUPERIOR': (0.0, 0.0, 1.00),
    'INFERIOR': (0.0, 0.0, -1.00),
}


def make_body(context, rotation=(0.0, 0.0, 0.0), location=(0.0, 0.0, 0.0)):
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    obj = context.object
    obj.name = "Body"
    obj.scale = (0.35, 0.22, 1.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.rotation_euler = rotation
    obj.location = location
    context.view_layer.objects.active = obj
    context.view_layer.update()
    # A brand-new scan, so no alignment session is in progress. Without this
    # the restore point recorded for the PREVIOUS fixture - which was also
    # called "Body" - would still be the one Reset restores.
    from body_surface_measurement import state
    state.clear_alignment_state(props_of(context), keep_points=False)
    return obj


def props_of(context):
    return context.scene.bsmt


def pick_reference(context, props, obj, slot, local_target, inside=False):
    """Set one reference to the real surface point nearest `local_target`.

    Goes through `state.fill_surface_point` with a genuine canonical triangle
    and barycentric, exactly as the modal picker does, so the stored reference
    is the same kind of object the operator will meet in the field.

    `inside` places the point WITHIN the nearest triangle instead of at its
    centroid. Centroid snapping quantises a reference to the mesh resolution,
    which on a body-scale fixture is tens of millimetres - far too coarse to
    build the sub-degree residual a real careful pick produces.
    """
    from body_surface_measurement import geodesic, state
    canonical = geodesic.meshcache.get(context, obj, props.unit, rebuild=True)
    target = np.asarray(local_target, dtype=np.float64)
    centroids = canonical.vertices_local[canonical.triangles].mean(axis=1)
    index = int(np.argmin(np.linalg.norm(centroids - target, axis=1)))
    bary = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0])
    if inside:
        corners = canonical.triangle_corners_local(index)
        solved, *_ = np.linalg.lstsq(
            np.vstack([corners.T, np.ones(3)]), np.append(target, 1.0),
            rcond=None)
        solved = np.clip(solved, 1e-6, None)
        bary = solved / solved.sum()
    local = canonical.local_from(index, bary)
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    world = matrix[:3, :3] @ local + matrix[:3, 3]
    state.fill_surface_point(
        state.align_point(props, slot), canonical.source_object,
        canonical.geometry_hash, index, bary, canonical.component_of(index),
        "FACE", local, world, world * canonical.unit_multiplier, 0.0)
    return world


def pick_all(context, props, obj, anatomy=None):
    anatomy = anatomy or ANATOMY_LOCAL
    return {slot: pick_reference(context, props, obj, slot, target)
            for slot, target in anatomy.items()}


def make_real_scan(context, location, rotation, height=1700.0,
                   local_offset=(0.0, 0.0, 0.0)):
    """A body-scale scan whose MESH DATA was never recentred.

    `local_offset` pushes the vertices away from the object origin, which is
    what a scanner export that kept its own global frame looks like. It is the
    part that matters: the reconstruction computes `R @ local + t` with both
    terms near 30,000, so the absolute error on a world point is set by that
    magnitude and not by the size of the body.
    """
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1.0)
    obj = context.object
    obj.name = "scan (1)_BSMT"
    obj.scale = (0.20 * height, 0.13 * height, 0.5 * height)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if any(local_offset):
        for vertex in obj.data.vertices:
            vertex.co = (vertex.co[0] + local_offset[0],
                         vertex.co[1] + local_offset[1],
                         vertex.co[2] + local_offset[2])
        obj.data.update()
    obj.rotation_euler = rotation
    obj.location = location
    context.view_layer.objects.active = obj
    context.view_layer.update()
    from body_surface_measurement import state
    state.clear_alignment_state(props_of(context), keep_points=False)
    return obj


def pick_body_references(context, props, obj, tilt_mm, height=1700.0,
                         local_offset=(0.0, 0.0, 0.0)):
    """Four good picks, with LEFT deliberately `tilt_mm` above RIGHT.

    The tilt is what produces the residual: over a left-right span of about
    0.4 * height, a rise of `tilt_mm` puts the two picked axes
    atan(tilt / span) off perpendicular.
    """
    ox, oy, oz = local_offset
    targets = {
        'LEFT': (0.20 * height + ox, oy, 0.15 * height + tilt_mm + oz),
        'RIGHT': (-0.20 * height + ox, oy, 0.15 * height + oz),
        'SUPERIOR': (ox, oy, 0.5 * height + oz),
        'INFERIOR': (ox, oy, -0.5 * height + oz),
    }
    return {slot: pick_reference(context, props, obj, slot, target, inside=True)
            for slot, target in targets.items()}


def apply_alignment():
    """Run Apply Alignment, mapping a refusal to {'CANCELLED'}.

    Blender turns an operator that reports ERROR into a RuntimeError when it
    is driven from a script, so a refusal has to be caught rather than
    returned. The operator reports ERROR exactly when it returns CANCELLED, so
    the mapping is exact.
    """
    try:
        return bpy.ops.bsmt.apply_alignment(), ""
    except RuntimeError as exc:
        return {'CANCELLED'}, str(exc)


def world_references(props):
    """The four references in world space, from the live transform."""
    from body_surface_measurement import attach
    points, reason = attach.live_reference_points(props)
    assert points is not None, reason
    return points


def axes_of(points):
    lateral = points['LEFT'] - points['RIGHT']
    vertical = points['SUPERIOR'] - points['INFERIOR']
    return (lateral / np.linalg.norm(lateral),
            vertical / np.linalg.norm(vertical))


def assert_contract(label, props, expected_lr_dot_x=1.0, tol=AXIS_TOL):
    """The whole contract, measured. Returns the live validation dict."""
    from body_surface_measurement import attach
    points = world_references(props)
    lateral, vertical = axes_of(points)

    validation, reason = attach.validate_applied_alignment(props)
    check("%s: the pose can be validated at all" % label,
          validation is not None, reason)
    if validation is None:
        return None

    check("%s: INFERIOR->SUPERIOR . +Z = +1 (%.9f)"
          % (label, float(vertical @ Z)),
          abs(float(vertical @ Z) - 1.0) <= tol)
    check("%s: RIGHT->LEFT . +X = %+.6f, the cosine of the residual"
          % (label, float(lateral @ X)),
          abs(float(lateral @ X) - expected_lr_dot_x) <= 1e-6,
          "%.9f vs %.9f" % (float(lateral @ X), expected_lr_dot_x))
    check("%s: the applied basis is orthonormal (%.2e)"
          % (label, validation["orthogonality_error"]),
          validation["orthogonality_error"] <= 1e-9)
    check("%s: worst axis error %.2e deg"
          % (label, validation["worst_axis_error_degrees"]),
          validation["worst_axis_error_degrees"] <= 1e-4)
    # The visual-orientation sanity check, derived from world axes rather than
    # from a screenshot: the frontal plane is spanned by left-right and
    # superior-inferior, so its normal is anterior-posterior. If +Z really is
    # superior that normal is horizontal, and a Top view - which looks along
    # +Z - therefore cannot be showing the front of the body.
    check("%s: the frontal plane is NOT the Top view (normal . +Z = %+.2e)"
          % (label, validation["frontal_normal_dot_up"]),
          abs(validation["frontal_normal_dot_up"]) <= 1e-6)
    check("%s: BSMT reports it as verified" % label, validation["ok"],
          "; ".join(validation["failures"]))
    return validation


def rigid_snapshot(context, props, obj):
    from body_surface_measurement import geodesic, state
    canonical = geodesic.meshcache.get(context, obj, props.unit, rebuild=True)
    vertices = np.array([v.co[:] for v in obj.data.vertices], dtype=np.float64)
    refs = {slot: (state.align_point(props, slot).triangle_index,
                   tuple(state.align_point(props, slot).barycentric),
                   tuple(state.align_point(props, slot).local_xyz))
            for slot in state.ALIGN_SLOTS}
    return {
        "geometry_hash": canonical.geometry_hash,
        "metric_key": canonical.metric_key,
        "vertices": vertices,
        "refs": refs,
        "scale": tuple(obj.scale),
    }


def check_rigid(label, context, props, obj, before):
    from body_surface_measurement import alignment, geodesic
    after = rigid_snapshot(context, props, obj)
    check("%s: geometry hash unchanged" % label,
          before["geometry_hash"] == after["geometry_hash"])
    check("%s: metric key unchanged (the rigid-invariant metric state)" % label,
          before["metric_key"] == after["metric_key"])
    check("%s: no mesh vertex was edited" % label,
          np.array_equal(before["vertices"], after["vertices"]))
    check("%s: SurfacePoint triangle and barycentric preserved" % label,
          before["refs"] == after["refs"])
    check("%s: object scale unchanged" % label,
          np.allclose(before["scale"], after["scale"], atol=1e-12),
          "%s -> %s" % (before["scale"], after["scale"]))
    check("%s: the applied transform is a rotation" % label,
          alignment.is_rigid(np.array(obj.matrix_world, dtype=np.float64)))
    # Pairwise Euclidean distances between the references: a rigid motion is
    # an isometry, so all six must survive to float error.
    def pairwise(points):
        keys = sorted(points)
        return np.array([np.linalg.norm(points[a] - points[b])
                         for i, a in enumerate(keys) for b in keys[i + 1:]])
    return pairwise


def run_case(context, props, label, rotation, location, move_to_origin,
             anatomy=None, expected_lr_dot_x=1.0):
    obj = make_body(context, rotation, location)
    picked = pick_all(context, props, obj, anatomy)
    props.align_move_to_origin = move_to_origin

    before_refs = world_references(props)
    before_snapshot = rigid_snapshot(context, props, obj)
    before_pairs = np.array([
        np.linalg.norm(before_refs[a] - before_refs[b])
        for i, a in enumerate(sorted(before_refs))
        for b in sorted(before_refs)[i + 1:]])

    result, _refusal = apply_alignment()
    context.view_layer.update()
    check("%s: the operator finished" % label, result == {'FINISHED'},
          str(result))

    validation = assert_contract(label, props, expected_lr_dot_x)
    check_rigid(label, context, props, obj, before_snapshot)

    after_refs = world_references(props)
    after_pairs = np.array([
        np.linalg.norm(after_refs[a] - after_refs[b])
        for i, a in enumerate(sorted(after_refs))
        for b in sorted(after_refs)[i + 1:]])
    check("%s: pairwise reference distances preserved (max drift %.2e)"
          % (label, float(np.abs(after_pairs - before_pairs).max())),
          np.allclose(after_pairs, before_pairs, atol=1e-6, rtol=0))

    text, verified, live = _status(props)
    check("%s: the panel status is 'verified'" % label, verified, text)
    check("%s: the status text names the method" % label,
          "Landmark" in text, text)
    return obj, validation, picked


def _status(props):
    from body_surface_measurement import attach
    return attach.alignment_status(props)


# ---------------------------------------------------------------------------



class FakeLayout(object):
    """Enough of a UILayout to run a panel's draw() headless."""

    def __init__(self, log):
        self.log = log
        self.alert = False
        self.enabled = True
        self.active = True
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.alignment = 'EXPAND'

    def _child(self, *args, **kwargs):
        return FakeLayout(self.log)

    box = row = column = column_flow = split = grid_flow = _child

    def label(self, **kwargs):
        self.log.append(kwargs.get("text", ""))

    def prop(self, *args, **kwargs):
        pass

    def operator(self, idname, **kwargs):
        return FakeLayout(self.log)

    def separator(self, *args, **kwargs):
        pass


def panel_text(panel_class, context):
    log = []
    stub = type("Stub", (object,), {
        name: staticmethod(getattr(panel_class, name))
        for name in dir(panel_class) if name.startswith("_draw")
    })()
    stub.layout = FakeLayout(log)
    panel_class.draw(stub, context)
    return "\n".join(log)


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (alignment, attach, panels,
                                          state, visualization)

    context = bpy.context
    props = context.scene.bsmt

    # ------------------------------------------------------------------ A --
    print("\nA. an already-upright scan")
    run_case(context, props, "A identity", (0, 0, 0), (0, 0, 0), False)

    # ------------------------------------------------------------------ B --
    print("\nB. 90 degrees about X - the classic Y-up import")
    #
    # This is the case the reported defect looked like: a body lying so that
    # superior runs along -Y, which is exactly what a Top view shows as a
    # frontal silhouette. Before alignment the contract must FAIL; after it,
    # it must hold.
    obj = make_body(context, (math.pi / 2, 0, 0), (0, 0, 0))
    pick_all(context, props, obj)
    points = world_references(props)
    _lateral, vertical = axes_of(points)
    check("B: before alignment SI . +Z = %+.4f, so the contract is broken"
          % float(vertical @ Z), abs(float(vertical @ Z)) < 1e-6)
    validation, _reason = attach.validate_applied_alignment(props)
    check("B: and the validator says so", not validation["ok"],
          "; ".join(validation["failures"]))
    check("B: it reports the frontal plane IS the Top view",
          abs(validation["frontal_normal_dot_up"]) > 0.99,
          "%.6f" % validation["frontal_normal_dot_up"])
    run_case(context, props, "B 90deg X", (math.pi / 2, 0, 0), (0, 0, 0), False)

    # ------------------------------------------------------------------ C --
    print("\nC. an arbitrary scanner orientation")
    for index, rotation in enumerate(((0.7, -1.1, 2.3), (-2.4, 0.9, 1.2),
                                      (1.9, 2.8, -0.6))):
        run_case(context, props, "C%d arbitrary XYZ" % index, rotation,
                 (0, 0, 0), False)

    # ------------------------------------------------------------------ D --
    print("\nD. a large translation as well as a rotation")
    run_case(context, props, "D far from the origin", (0.4, 0.9, -1.7),
             (12345.0, -6789.0, 4321.0), False)

    # ------------------------------------------------------------------ E --
    print("\nE. imperfect, non-orthogonal reference picks")
    #
    # The researcher's LEFT and RIGHT are not level. The frame must still come
    # out exactly orthonormal, superior-inferior must still be exact - it is
    # the primary axis and is never orthogonalised - and the raw right->left
    # direction must land at exactly cos(residual) from +X. Reporting LR . +X
    # as 1.0 here would be the lie; reporting the residual and the cosine is
    # the truth.
    tilted = dict(ANATOMY_LOCAL)
    tilted['LEFT'] = (0.35, 0.0, 0.65)
    tilted['RIGHT'] = (-0.35, 0.0, 0.25)
    obj = make_body(context, (0.3, -0.8, 1.4), (5.0, -2.0, 3.0))
    pick_all(context, props, obj, tilted)
    props.align_move_to_origin = False
    frame = alignment.anatomical_frame(
        *[world_references(props)[slot]
          for slot in ('LEFT', 'RIGHT', 'SUPERIOR', 'INFERIOR')])
    residual = frame["residual_degrees"]
    check("E: the picks are visibly non-orthogonal (%.2f deg residual)"
          % residual, residual > 5.0)
    _result, _refusal = apply_alignment()
    context.view_layer.update()
    validation = assert_contract("E non-orthogonal", props,
                                 expected_lr_dot_x=math.cos(math.radians(residual)))
    # 1e-4 degrees: the rotation travels through Blender's single-precision
    # loc/rot/scale storage on the way out, so an angle measured after Apply
    # differs from the same angle measured before it at about 2e-6 degrees.
    # That is the storage, not the arithmetic.
    check("E: the raw angle between the two picked axes is reported",
          min(abs(validation["raw_angle_degrees"] - (90.0 - residual)),
              abs(validation["raw_angle_degrees"] - (90.0 + residual))) < 1e-4,
          "raw %.6f, residual before %.6f, after %.6f"
          % (validation["raw_angle_degrees"], residual,
             validation["residual_degrees"]))
    check("E: the residual survives orthogonalisation unchanged",
          abs(validation["residual_degrees"] - residual) < 1e-4)
    check("E: the FINAL basis is orthonormal despite the bad picks (%.2e)"
          % validation["orthogonality_error"],
          validation["orthogonality_error"] <= 1e-9)
    check("E: superior-inferior is still exact - it is the primary axis",
          abs(validation["si_dot_z"] - 1.0) <= 1e-9,
          "%.12f" % validation["si_dot_z"])
    note = alignment.orthogonalisation_note(frame)
    check("E: the orthogonalisation METHOD is stated, not just the residual",
          "Gram-Schmidt" in note and "primary axis" in note, note)
    check("E: BSMT still reports the alignment as verified - an imperfect "
          "pick is not a failed alignment", validation["ok"])

    # ---------------------------------------------------------------- F/G --
    print("\nF. Move To World Origin ON - rotation and translation separately")
    obj, validation, _picked = run_case(
        context, props, "F origin ON", (0.4, 0.9, -1.7), (30.0, -14.0, 8.0),
        True)
    inferior = world_references(props)['INFERIOR']
    check("F: the INFERIOR reference landed on the world origin (%.2e)"
          % float(np.linalg.norm(inferior)),
          float(np.linalg.norm(inferior)) <= 1e-4,
          str(inferior))
    check("F: and the rotation is correct independently of that translation",
          validation["worst_axis_error_degrees"] <= 1e-4)

    print("\nG. Move To World Origin OFF")
    obj = make_body(context, (0.4, 0.9, -1.7), (30.0, -14.0, 8.0))
    picked = pick_all(context, props, obj)
    props.align_move_to_origin = False
    pivot_before = world_references(props)['INFERIOR'].copy()
    _result, _refusal = apply_alignment()
    context.view_layer.update()
    validation = assert_contract("G origin OFF", props)
    inferior = world_references(props)['INFERIOR']
    check("G: the INFERIOR reference did NOT move - it is the pivot (%.2e)"
          % float(np.linalg.norm(inferior - pivot_before)),
          np.allclose(inferior, pivot_before, atol=1e-6),
          "%s -> %s" % (pivot_before, inferior))
    check("G: the body is NOT at the world origin",
          float(np.linalg.norm(inferior)) > 1.0)
    check("G: the rotation is correct regardless (%.2e deg)"
          % validation["worst_axis_error_degrees"],
          validation["worst_axis_error_degrees"] <= 1e-4)

    # ------------------------------------------------------------------ H --
    print("\nH. the reported defect: 'Aligned' must not outlive the pose")
    obj = make_body(context, (0, 0, 0), (0, 0, 0))
    pick_all(context, props, obj)
    props.align_move_to_origin = False
    _result, _refusal = apply_alignment()
    context.view_layer.update()
    text, verified, _live = _status(props)
    check("H: right after Apply the status is verified", verified, text)

    obj.rotation_euler = (math.pi / 2, 0, 0)
    context.view_layer.update()
    attach.refresh(props, reason="test")
    points = world_references(props)
    _lateral, vertical = axes_of(points)
    check("H: the researcher's rotation broke the contract (SI . +Z = %+.4f)"
          % float(vertical @ Z), abs(float(vertical @ Z)) < 1e-6)
    text, verified, live = _status(props)
    check("H: BSMT NO LONGER claims the object is aligned", not verified, text)
    check("H: the status says the validation failed", "FAILED" in text, text)
    check("H: and it reports the measured axis error, not an adjective",
          live is not None and live["worst_axis_error_degrees"] > 1.0,
          text)
    check("H: the failure names the axis that is wrong",
          any("+Z" in failure for failure in live["failures"]),
          str(live["failures"]))

    # ------------------------------------------------------------------ I --
    print("\nI. Preview Axes uses the same basis Apply uses")
    obj = make_body(context, (0.7, -1.1, 2.3), (2.0, 3.0, 4.0))
    pick_all(context, props, obj)
    props.align_move_to_origin = False
    result = bpy.ops.bsmt.preview_alignment()
    check("I: preview finished", result == {'FINISHED'}, str(result))
    helper = bpy.data.objects.get(visualization.ALIGN_AXES)
    check("I: a preview helper exists", helper is not None)

    points = world_references(props)
    frame = alignment.anatomical_frame(
        points['LEFT'], points['RIGHT'],
        points['SUPERIOR'], points['INFERIOR'])
    helper_matrix = np.array(helper.matrix_world, dtype=np.float64)
    origin = helper_matrix[:3, :3] @ np.array(
        helper.data.vertices[0].co[:], dtype=np.float64) + helper_matrix[:3, 3]
    arms = []
    for index in range(1, 4):
        tip = helper_matrix[:3, :3] @ np.array(
            helper.data.vertices[index].co[:],
            dtype=np.float64) + helper_matrix[:3, 3]
        arms.append((tip - origin) / np.linalg.norm(tip - origin))

    for index, (name, axis) in enumerate((("x_axis", "subject's left"),
                                          ("y_axis", "posterior"),
                                          ("z_axis", "superior"))):
        check("I: the drawn %s arm IS the frame's %s (dot %.9f)"
              % (axis, name, float(arms[index] @ frame[name])),
              abs(float(arms[index] @ frame[name]) - 1.0) < 1e-6,
              str(arms[index]))
    check("I: the preview is drawn at the INFERIOR reference",
          np.allclose(origin, points['INFERIOR'], atol=1e-5),
          "%s vs %s" % (origin, points['INFERIOR']))
    # The regression this guards: the preview used to draw the WORLD axes
    # whatever the references were, so it agreed with every alignment.
    check("I: and it is NOT simply the world axes on a rotated scan",
          not np.allclose(np.array(arms), np.eye(3), atol=1e-3),
          str(np.array(arms)))

    _result, _refusal = apply_alignment()
    context.view_layer.update()
    helper = bpy.data.objects.get(visualization.ALIGN_AXES)
    if helper is not None:
        helper_matrix = np.array(helper.matrix_world, dtype=np.float64)
        after = []
        base = helper_matrix[:3, :3] @ np.array(
            helper.data.vertices[0].co[:],
            dtype=np.float64) + helper_matrix[:3, 3]
        for index in range(1, 4):
            tip = helper_matrix[:3, :3] @ np.array(
                helper.data.vertices[index].co[:],
                dtype=np.float64) + helper_matrix[:3, 3]
            after.append((tip - base) / np.linalg.norm(tip - base))
        check("I: after Apply the same arms lie on the world axes - preview "
              "and result agree", np.allclose(np.array(after), np.eye(3),
                                              atol=1e-6),
              str(np.array(after)))

    # ------------------------------------------------------------------ J --
    print("\nJ. Flip Front/Back relabels as well as turns")
    obj = make_body(context, (0, 0, 0), (0, 0, 0))
    pick_all(context, props, obj)
    props.align_move_to_origin = False
    _result, _refusal = apply_alignment()
    context.view_layer.update()
    left_before = world_references(props)['LEFT'].copy()
    bpy.ops.bsmt.flip_front_back()
    context.view_layer.update()
    points = world_references(props)
    lateral, vertical = axes_of(points)
    check("J: the body turned - the old LEFT position is now on the other side",
          float(np.sign(left_before[0])) != float(np.sign(points['RIGHT'][0]))
          or not np.allclose(left_before, points['LEFT'], atol=1e-6))
    check("J: superior is still +Z after the flip (%.9f)"
          % float(vertical @ Z), abs(float(vertical @ Z) - 1.0) < 1e-6)
    check("J: the subject's left is still +X, because the two labels were "
          "exchanged with the body (%.9f)" % float(lateral @ X),
          abs(float(lateral @ X) - 1.0) < 1e-6)
    text, verified, _live = _status(props)
    check("J: so the flip leaves a VERIFIED alignment, not a 180 deg error",
          verified, text)

    # ------------------------------------------------------------------ K --
    print("\nK. Reset restores the pre-alignment pose and drops the claim")
    obj = make_body(context, (0.4, 0.9, -1.7), (3.0, 1.0, 2.0))
    pick_all(context, props, obj)
    before = np.array(obj.matrix_world, dtype=np.float64)
    props.align_move_to_origin = True
    _result, _refusal = apply_alignment()
    context.view_layer.update()
    bpy.ops.bsmt.reset_alignment()
    context.view_layer.update()
    # 1e-5, not exactly: `align_previous_matrix` is a Blender
    # FloatVectorProperty and therefore single precision, so the restore point
    # is stored to ~7 significant digits. That is a recorded limit of the
    # round trip (PROJECT_SPEC sect. 11l.6), not slack in the check.
    check("K: the pre-alignment matrix is restored to storage precision",
          np.allclose(np.array(obj.matrix_world, dtype=np.float64), before,
                      atol=1e-5),
          str(np.array(obj.matrix_world) - before))
    text, verified, _live = _status(props)
    check("K: and BSMT stops claiming the object is aligned",
          not verified and text == "Not aligned", text)

    # ------------------------------------------------------------------ L --
    print("\nL. what the panel actually says, in each of the three states")
    obj = make_body(context, (0.7, -1.1, 2.3), (1.0, 2.0, 3.0))
    text = panel_text(panels.BSMT_PT_alignment, context)
    check("L: with no alignment the panel says 'Not aligned'",
          "Status: Not aligned" in text, text)

    pick_all(context, props, obj)
    props.align_move_to_origin = False
    _result, _refusal = apply_alignment()
    context.view_layer.update()
    text = panel_text(panels.BSMT_PT_alignment, context)
    check("L: after a good Apply the panel says verified",
          "verified" in text, text)
    check("L: and shows the measured dot products, not just a tick",
          "LR . +X" in text and "SI . +Z" in text, text)
    check("L: including the orthogonality error and the determinant",
          "max |B^T B - I|" in text and "det(basis)" in text, text)
    check("L: and a PASS/FAIL line per criterion",
          text.count("PASS  ") >= 7, text)

    obj.rotation_euler = (math.pi / 2, 0.0, 0.0)
    context.view_layer.update()
    attach.refresh(props, reason="test")
    text = panel_text(panels.BSMT_PT_alignment, context)
    check("L: once the object is moved the panel says FAILED validation",
          "FAILED validation" in text, text)
    check("L: it no longer says 'Aligned (Landmark)' anywhere",
          "Aligned (Landmark)" not in text, text)
    check("L: and it names the axis that is wrong",
          "+Z" in text, text)
    check("L: the reported SI . +Z is the measured 0, not the stored 1",
          any(line.startswith("SI . +Z") and abs(float(line.split()[-1])) < 1e-6
              for line in text.split("\n")),
          "\n".join(l for l in text.split("\n") if "SI . +Z" in l))
    check("L: and the failing criterion is marked FAIL",
          "FAIL  INFERIOR->SUPERIOR lands on world +Z" in text, text)

    # ------------------------------------------------------------------ M --
    print("\nM. the real repaired-PLY case: mm scan, never recentred, 0.4 deg")
    #
    # What the acceptance run actually met. The scan sat at
    # (-28570, -2692, -176) in millimetres with mesh data carrying the
    # matching offset, its references were good - about 0.4 deg off
    # perpendicular - and Apply was refused. Nothing about that geometry is
    # unusual for a scanner export, and it must align.
    obj = make_real_scan(context, location=(-28570.0, -2692.0, -176.0),
                         rotation=(math.radians(90.1), math.radians(-2.3),
                                   math.radians(-0.6)),
                         local_offset=(28570.0, 2692.0, 176.0))
    picked = pick_body_references(context, props, obj, tilt_mm=4.5,
                                  local_offset=(28570.0, 2692.0, 176.0))
    props.align_move_to_origin = True

    points = world_references(props)
    frame = alignment.anatomical_frame(
        points['LEFT'], points['RIGHT'],
        points['SUPERIOR'], points['INFERIOR'])
    residual = frame["residual_degrees"]
    check("M: the references are GOOD - %.3f deg off perpendicular, which "
          "alignment.quality() calls '%s'"
          % (residual, alignment.quality(residual)[0]),
          residual < 1.0 and alignment.quality(residual)[2] == 0,
          "%.4f deg" % residual)
    check("M: the object scale is exactly 1",
          np.allclose(tuple(obj.scale), (1.0, 1.0, 1.0), atol=0),
          str(tuple(obj.scale)))

    result, _refusal = apply_alignment()
    context.view_layer.update()
    check("M: Apply is ACCEPTED, not refused", result == {'FINISHED'},
          str(result) + " | " + props.align_refusal_report[:400])
    validation = assert_contract("M real scan", props,
                                 expected_lr_dot_x=math.cos(
                                     math.radians(residual)))
    check("M: every criterion passed", validation["ok"],
          "; ".join(validation["failures"]))
    inferior = world_references(props)['INFERIOR']
    check("M: INFERIOR reached the world origin (%.3e, limit %.3e)"
          % (float(np.linalg.norm(inferior)), validation["origin_limit"]),
          float(np.linalg.norm(inferior)) <= validation["origin_limit"])
    check("M: the origin limit is scale-aware, not a fixed 1e-4",
          validation["origin_limit"] > 1e-4,
          "limit %.3e on a reach of %.4g"
          % (validation["origin_limit"], validation["coordinate_reach"]))
    check("M: and the coordinate reach reflects the un-recentred mesh",
          validation["coordinate_reach"] > 20000.0,
          str(validation["coordinate_reach"]))
    # The regression this pins: 0.25.0 measured 1.008e-03 against a hard 1e-4.
    check("M: the achieved miss is far inside the limit (%.1fx margin)"
          % (validation["origin_limit"]
             / max(validation["origin_distance"], 1e-30)),
          validation["origin_distance"] * 5.0 < validation["origin_limit"],
          "%.3e vs %.3e" % (validation["origin_distance"],
                            validation["origin_limit"]))
    text, verified, _live = _status(props)
    check("M: the panel reports it verified", verified, text)

    print("\n  the criterion table this fixture produces:")
    for item in validation["criteria"]:
        print("    [%s] %-52s %s" % ("PASS" if item["ok"] else "FAIL",
                                     item["label"], item["detail"]))

    # ------------------------------------------------------------------ N --
    print("\nN. a scale sweep - the same geometry in different units")
    for label, height, distance in (("metres", 1.7, 28.57),
                                    ("centimetres", 170.0, 2857.0),
                                    ("millimetres", 1700.0, 28570.0)):
        obj = make_real_scan(context, location=(-distance, 0.0, 0.0),
                             rotation=(math.radians(90.1), 0.0, 0.0),
                             height=height,
                             local_offset=(distance, 0.0, 0.0))
        pick_body_references(context, props, obj,
                             tilt_mm=height * 0.0026, height=height,
                             local_offset=(distance, 0.0, 0.0))
        props.align_move_to_origin = True
        result, _refusal = apply_alignment()
        context.view_layer.update()
        validation, _why = attach.validate_applied_alignment(props)
        check("N: %s - accepted" % label, result == {'FINISHED'},
              str(result) + " | " + props.align_refusal_report[:300])
        check("N: %s - the origin criterion passes on its own scale" % label,
              validation["origin_distance"] <= validation["origin_limit"],
              "%.3e vs %.3e" % (validation["origin_distance"],
                                validation["origin_limit"]))

    # ------------------------------------------------------------------ O --
    print("\nO. a failed post-validation is TRANSACTIONAL")
    #
    # A Copy Rotation constraint makes `matrix_world = M` a request Blender
    # declines: the depsgraph overrides the rotation, so the pose Apply asked
    # for is not the pose that results, and the postcondition must catch it.
    # That is the honest way to force a failure - nothing about the check is
    # stubbed or monkeypatched.
    obj = make_body(context, (0.4, 0.9, -1.7), (3.0, 1.0, 2.0))
    pick_all(context, props, obj)
    props.align_move_to_origin = False
    bpy.ops.object.empty_add()
    blocker = context.object
    blocker.name = "Blocker"
    blocker.rotation_euler = (math.radians(37.0), math.radians(-11.0),
                              math.radians(64.0))
    context.view_layer.objects.active = obj
    constraint = obj.constraints.new('COPY_ROTATION')
    constraint.target = blocker
    context.view_layer.update()

    pre_matrix = np.array(obj.matrix_world, dtype=np.float64)
    pre_points = {slot: tuple(state.align_point(props, slot).local_xyz)
                  for slot in state.ALIGN_SLOTS}
    pre_triangles = {slot: state.align_point(props, slot).triangle_index
                     for slot in state.ALIGN_SLOTS}
    pre_applied = props.align_applied
    pre_status = props.align_method

    result, _refusal = apply_alignment()
    context.view_layer.update()

    check("O: the operator REFUSES rather than reporting a moved failure",
          result == {'CANCELLED'}, str(result))
    post_matrix = np.array(obj.matrix_world, dtype=np.float64)
    check("O: matrix_world is EXACTLY the pre-Apply matrix (max drift %.2e)"
          % float(np.abs(post_matrix - pre_matrix).max()),
          np.array_equal(post_matrix, pre_matrix),
          "%s\nvs\n%s" % (post_matrix, pre_matrix))
    check("O: the status is not ALIGNED",
          not props.align_applied and props.align_applied == pre_applied,
          "align_applied=%s" % props.align_applied)
    check("O: align_method is left as it was",
          props.align_method == pre_status, props.align_method)
    check("O: the reference points are preserved",
          {slot: tuple(state.align_point(props, slot).local_xyz)
           for slot in state.ALIGN_SLOTS} == pre_points)
    check("O: with their triangles intact",
          {slot: state.align_point(props, slot).triangle_index
           for slot in state.ALIGN_SLOTS} == pre_triangles)
    check("O: and every reference is still valid",
          all(state.align_point(props, slot).valid
              for slot in state.ALIGN_SLOTS))
    check("O: the refusal says WHICH criterion failed, persistently",
          "[FAIL]" in props.align_refusal_report,
          props.align_refusal_report[:400])
    check("O: and it is a real criterion label, not an empty message",
          any(name in props.align_refusal_report
              for name in ("world +Z", "world +X", "world +Y")),
          props.align_refusal_report[:400])
    text, verified, _live = _status(props)
    check("O: the panel says the last Apply was refused",
          not verified and "REFUSED" in text, text)
    panel = panel_text(panels.BSMT_PT_alignment, context)
    check("O: the panel renders the refusal table", "[FAIL]" in panel,
          panel[-600:])
    check("O: the panel still renders the live criterion table",
          "Alignment Validation (current pose)" in panel, panel[-600:])

    obj.constraints.remove(constraint)
    context.view_layer.update()

    # ------------------------------------------------------------------ P --
    print("\nP. after removing the obstruction the same picks align cleanly")
    result, _refusal = apply_alignment()
    context.view_layer.update()
    check("P: Apply now succeeds with the SAME references",
          result == {'FINISHED'}, str(result))
    assert_contract("P recovered", props)
    check("P: the refusal record is cleared once an Apply succeeds",
          props.align_refusal_report == "", props.align_refusal_report[:200])

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for failure in FAILURES:
        print("  FAILED: %s" % failure)
    if FAILURES:
        raise SystemExit(1)


main()
