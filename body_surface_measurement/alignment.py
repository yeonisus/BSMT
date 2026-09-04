"""Rigid anatomical alignment (Milestone 3.6). Pure numpy - no bpy.

Alignment is a **display and interpretation** operation, not a measurement
one. It moves the object, never the mesh, so it cannot change a distance:
rigid motions are isometries of the embedding (PROJECT_SPEC.md sect. 6.3).
The architecture already guarantees that - ``geometry_hash`` excludes
``matrix_world`` entirely, and ``metric_key`` is built from L^T L, which for
the polar decomposition L = R*S equals S^2 and is therefore exactly invariant
to rotation. A rigid alignment consequently leaves every SurfacePoint VALID
and every stored distance untouched, by construction rather than by care.

Axis convention
---------------
Chosen once, here, and used everywhere::

    +X  the SUBJECT'S LEFT
    +Y  POSTERIOR   (so anterior is -Y)
    +Z  SUPERIOR

The sign of X is the free choice, and it is made so that Blender's **Front
view (Numpad 1)** - which looks from -Y toward +Y - shows the subject's
anatomical FRONT. Taking +X as the subject's right instead would put the back
of the body in Blender's front view, which makes the visual check the
researcher is asked to perform in the preview actively misleading.

The frame is right-handed: with x = subject's left and z = superior,
y = z x x points posteriorly, which is what the convention above says.

The remaining ambiguity, and what "Flip Front/Back" really is
------------------------------------------------------------
Four correctly labelled points determine the frame completely - there is no
residual anterior/posterior freedom. What there IS, in practice, is the very
easy mistake of labelling the subject's left and right the wrong way round,
because on screen the subject's left is on the viewer's right.

Swapping those two labels sends x -> -x, and therefore y = z x x -> -y, while
z is unchanged. That is exactly a 180 degree rotation about Z. So the flip
control is not a guess about anatomy: it is the precise correction for a
swapped left/right labelling, and it is offered rather than inferred because
BSMT cannot tell which way round the researcher meant.
"""

import numpy as np

#: A frame whose two source axes are further from perpendicular than this is
#: reported as poor quality. Real landmark pairs are never exactly orthogonal,
#: so the residual is recorded rather than assumed away.
RESIDUAL_WARN_DEGREES = 20.0

#: UI GUIDANCE ONLY (Milestone 3.7, sect. 12). These bands tell a researcher
#: whether their four picks look self-consistent. They are NOT a validated
#: anthropometric criterion, and no measurement is accepted or refused on the
#: strength of them - the residual is reported either way, and the frame is
#: exactly orthonormal at any residual. `quality()` says so in its own words.
RESIDUAL_GOOD_DEGREES = 5.0
RESIDUAL_CHECK_DEGREES = 15.0

#: Tolerance for calling a 3x3 linear part a rotation.
RIGID_TOLERANCE = 1e-5

#: How far an achieved world axis may sit from the world axis it is supposed
#: to BE before the applied alignment is called a failure. The frame is
#: constructed orthonormal, so a correct apply lands within ~1e-6 degrees of
#: exact; 0.05 degrees is therefore enormously generous next to float error
#: and far tighter than anything a researcher could see in a viewport.
AXIS_TOLERANCE_DEGREES = 0.05

#: Orthonormality tolerance for the achieved basis, as max |B^T B - I|, and
#: for |det(B) - 1|. The basis is built by Gram-Schmidt in float64 from the
#: reconstructed points, so it lands near 1e-16 at any residual.
ORTHOGONALITY_TOLERANCE = 1e-6

#: How far the raw RIGHT->LEFT . +X may sit from cos(residual). Both come from
#: the same four points, so this is a check on the ROTATION, not on the picks.
LATERAL_CONSISTENCY_TOLERANCE = 1e-6

#: Position criteria are the only ones measured in world units rather than
#: degrees, so they are the only ones that need to know the scale of the
#: scene. See position_tolerance() for why a fixed absolute number is wrong.
POSITION_RELATIVE_TOLERANCE = 1e-6
POSITION_ABSOLUTE_FLOOR = 1e-9

#: How far object scale may differ between axes before it counts as
#: non-uniform, and from 1.0 before it is worth a warning.
SCALE_UNIFORM_TOLERANCE = 1e-4
SCALE_UNITY_TOLERANCE = 1e-3

METHOD_LANDMARK = 'LANDMARK'
METHOD_MANUAL = 'MANUAL'

AXIS_DESCRIPTION = (
    "+X = subject's left, +Y = posterior (anterior is -Y), +Z = superior"
)


class AlignmentError(Exception):
    """An alignment cannot be constructed or applied as asked."""


# ---------------------------------------------------------------------------
# scale and rigidity
# ---------------------------------------------------------------------------

def linear_scale(matrix):
    """Per-axis scale of a 4x4 (or 3x3) transform, from its column norms."""
    matrix = np.asarray(matrix, dtype=np.float64)
    linear = matrix[:3, :3]
    return np.linalg.norm(linear, axis=0)


def scale_report(matrix):
    """Describe an object's scale: values, uniformity, and unity."""
    scale = linear_scale(matrix)
    smallest = float(scale.min())
    largest = float(scale.max())
    uniform = bool(largest - smallest <= SCALE_UNIFORM_TOLERANCE * max(largest, 1.0))
    unity = bool(uniform and abs(largest - 1.0) <= SCALE_UNITY_TOLERANCE)
    return {
        "scale": [float(v) for v in scale],
        "uniform": uniform,
        "unity": unity,
        "min": smallest,
        "max": largest,
        "message": (
            "" if unity else
            ("scale is %.5f (uniform, not 1.0)" % largest if uniform else
             "scale is NON-UNIFORM (%.5f, %.5f, %.5f)"
             % (scale[0], scale[1], scale[2]))
        ),
    }


def is_rigid(matrix, tolerance=RIGID_TOLERANCE):
    """True when the 3x3 linear part is a rotation: R^T R = I and det = +1."""
    matrix = np.asarray(matrix, dtype=np.float64)
    linear = matrix[:3, :3]
    if not np.all(np.isfinite(linear)):
        return False
    product = linear.T @ linear
    if not np.allclose(product, np.eye(3), atol=tolerance):
        return False
    return abs(float(np.linalg.det(linear)) - 1.0) <= tolerance


def check_alignable(matrix):
    """Whether an object may be aligned. Returns (ok, reason).

    A non-uniformly scaled body scan is refused. Left-multiplying by a
    rotation would in fact leave the metric untouched - L^T L is unchanged by
    it - so this is not a correctness necessity; it is refused because a
    non-uniformly scaled human scan is already wrong for anthropometry, and
    aligning it would make a broken scan look ready.
    """
    report = scale_report(matrix)
    if not report["uniform"]:
        return False, (
            "the object has non-uniform scale (%.5f, %.5f, %.5f). Alignment "
            "is refused: a non-uniformly scaled scan is not measurable. Apply "
            "or correct the scale first (Object > Apply > Scale), then "
            "re-run the topology diagnostics."
            % tuple(report["scale"])
        )
    return True, report["message"]


# ---------------------------------------------------------------------------
# the anatomical frame
# ---------------------------------------------------------------------------

def anatomical_frame(left, right, superior, inferior):
    """Build an orthonormal anatomical frame from four reference points.

    Returns a dict with the three axes, the residual non-orthogonality of the
    two source directions, and the 3x3 matrix whose COLUMNS are the axes -
    that is, the map from anatomical coordinates to the current world.

    Superior-inferior is taken as primary and the left-right direction is
    orthogonalised against it. On a standing scan the vertical is by far the
    better conditioned of the two: the shoulders or hips that define
    left-right are rarely level, while the head-to-foot direction is long and
    unambiguous. The residual is reported, never silently absorbed.
    """
    left = np.asarray(left, dtype=np.float64)
    right = np.asarray(right, dtype=np.float64)
    superior = np.asarray(superior, dtype=np.float64)
    inferior = np.asarray(inferior, dtype=np.float64)
    for name, point in (("left", left), ("right", right),
                        ("superior", superior), ("inferior", inferior)):
        if point.shape != (3,) or not np.all(np.isfinite(point)):
            raise AlignmentError("the %s reference is not a 3D point" % name)

    # +X is the subject's LEFT, so the direction runs from RIGHT to LEFT.
    lateral = left - right
    vertical = superior - inferior

    lateral_length = float(np.linalg.norm(lateral))
    vertical_length = float(np.linalg.norm(vertical))
    if vertical_length <= 0.0:
        raise AlignmentError(
            "the superior and inferior references are at the same position"
        )
    if lateral_length <= 0.0:
        raise AlignmentError(
            "the left and right references are at the same position"
        )

    z_axis = vertical / vertical_length
    lateral_unit = lateral / lateral_length

    # Residual: how far from perpendicular the two picked directions are.
    cosine = float(np.clip(np.dot(lateral_unit, z_axis), -1.0, 1.0))
    raw_angle_degrees = float(np.degrees(np.arccos(cosine)))
    residual_degrees = abs(90.0 - raw_angle_degrees)

    projected = lateral - np.dot(lateral, z_axis) * z_axis
    projected_length = float(np.linalg.norm(projected))
    if projected_length <= 1e-9 * lateral_length:
        raise AlignmentError(
            "the left-right direction is parallel to the superior-inferior "
            "direction, so no anatomical frame can be built from these four "
            "points"
        )
    x_axis = projected / projected_length
    y_axis = np.cross(z_axis, x_axis)          # right-handed: posterior

    matrix = np.column_stack([x_axis, y_axis, z_axis])
    return {
        "x_axis": x_axis,
        "y_axis": y_axis,
        "z_axis": z_axis,
        "matrix": matrix,
        "raw_angle_degrees": raw_angle_degrees,
        "residual_degrees": residual_degrees,
        "residual_ok": residual_degrees <= RESIDUAL_WARN_DEGREES,
        # World units, NOT millimetres: these are distances between world
        # reference points and the caller owns the unit multiplier.
        "lateral_span": lateral_length,
        "vertical_span": vertical_length,
        "convention": AXIS_DESCRIPTION,
    }


def quality(residual_degrees):
    """A plain-language reading of the residual. UI GUIDANCE ONLY.

    Returns (verdict, advice, severity). `severity` is 0 good, 1 worth a look,
    2 worth repicking - meant for choosing an icon, nothing else.

    The bands are a usability aid, not a research threshold: they have not
    been validated against any anthropometric standard, and BSMT never refuses
    or adjusts a measurement because of them. Whatever the residual, the
    reported frame is exactly orthonormal and the residual itself is always
    shown, so the researcher decides.
    """
    value = float(residual_degrees)
    if value <= RESIDUAL_GOOD_DEGREES:
        return ("Good",
                "the reference points are close to perpendicular", 0)
    if value <= RESIDUAL_CHECK_DEGREES:
        return ("Check references",
                "left/right may not be at the same height", 1)
    return ("Repick recommended",
            "the two reference axes are far from perpendicular", 2)


WORLD_AXES = {
    "x": np.array([1.0, 0.0, 0.0]),
    "y": np.array([0.0, 1.0, 0.0]),
    "z": np.array([0.0, 0.0, 1.0]),
}


def axis_error_degrees(vector, target):
    """Angle in degrees between `vector` and the unit axis `target`."""
    vector = np.asarray(vector, dtype=np.float64)
    length = float(np.linalg.norm(vector))
    if length <= 0.0:
        return 180.0
    cosine = float(np.clip(np.dot(vector / length, np.asarray(target)), -1.0, 1.0))
    return float(np.degrees(np.arccos(cosine)))


def position_tolerance(reach):
    """How far a transformed point may miss an exact world target.

    Scale-relative, because the error being bounded is scale-relative. Blender
    stores an object transform as single-precision loc/rot/scale, so a point
    that goes out through `matrix_world` comes back carrying about 1e-7 of the
    magnitude of the coordinates involved - the mesh-local position AND the
    translation column, not the size of the body.

    The 0.25.0 check used a fixed 1e-4 world units, and that is not a
    physically meaningful quantity: on a scan authored in metres it is a
    tenth of a millimetre, and on a millimetre scan whose mesh data was never
    recentred - object at (-28570, -2692, -176), local coordinates carrying
    the matching offset, which is exactly the real case - it is a tenth of a
    micrometre, ten times finer than the storage can hold. A correct
    translation was rejected because the tolerance had no dimensions.

    1e-6 of the coordinate reach is about eight times the single-precision
    epsilon, so it clears the storage noise with room to spare while still
    being a millionth of the scene: a translation that is actually wrong
    misses by millimetres or metres, which is orders of magnitude outside it.
    """
    return max(POSITION_ABSOLUTE_FLOOR,
               POSITION_RELATIVE_TOLERANCE * float(abs(reach)))


def _criterion(key, label, measured, limit, ok, detail=""):
    return {"key": key, "label": label, "measured": float(measured),
            "limit": float(limit), "ok": bool(ok), "detail": detail}


def validate_world_frame(left, right, superior, inferior,
                         tolerance_degrees=AXIS_TOLERANCE_DEGREES,
                         origin_reference=None, coordinate_reach=None):
    """Measure whether four WORLD reference points satisfy the contract.

    This is the postcondition, not the recipe. It is given the four reference
    points as they actually are in world space **now** - reconstructed from
    the live object transform, never read from a cached world position - and
    it answers one question: does this pose really mean what the panel says
    it means?

    Three things are separated on purpose, because conflating them is what let
    a wrong pose report success:

    * **The frame.** The right->left and inferior->superior picks are turned
      into an orthonormal basis exactly as `anatomical_frame` does it. Those
      three axes are the contract, and after a correct Apply they must BE the
      world axes. This is the hard postcondition, and it is purely angular -
      therefore free of scale, and the same tolerance is right for a scan in
      metres and one in millimetres.
    * **The raw picks.** `lr_dot_x` is the dot product of the *unorthogonalised*
      RIGHT->LEFT direction with +X. It is cos(residual) and nothing else, so
      it is reported and checked for consistency, never required to be 1 -
      demanding that would refuse every real pair of landmarks. `si_dot_z` has
      no such excuse: superior-inferior is the primary axis, it is not
      orthogonalised against anything, so it must land on +Z exactly.
    * **The visual consequence.** A frontal body plane is spanned by
      left-right and superior-inferior; its normal is the anterior-posterior
      axis. If +Z really is superior then that normal is horizontal, so the
      frontal plane cannot be what a Top view shows. `frontal_normal_dot_up`
      measures exactly that, and it is the number that goes non-zero when a
      body still faces the Top view after an alignment claimed to have
      succeeded.

    `origin_reference` names the slot that Move To World Origin was asked to
    put at (0,0,0) - the only criterion here measured in world units rather
    than degrees, and therefore the only one that needs `coordinate_reach`:
    the largest coordinate magnitude that passed through the object transform.
    See `position_tolerance`.

    Returns a dict. `criteria` is the list of individual PASS/FAIL judgements,
    and `ok` is simply "every criterion passed" - there is no verdict reached
    anywhere else, so the panel and the operator cannot disagree about what
    failed.
    Raises AlignmentError only if the points cannot form a frame at all.
    """
    frame = anatomical_frame(left, right, superior, inferior)

    points = {
        'LEFT': np.asarray(left, dtype=np.float64),
        'RIGHT': np.asarray(right, dtype=np.float64),
        'SUPERIOR': np.asarray(superior, dtype=np.float64),
        'INFERIOR': np.asarray(inferior, dtype=np.float64),
    }

    lateral = points['LEFT'] - points['RIGHT']
    vertical = points['SUPERIOR'] - points['INFERIOR']
    lateral_unit = lateral / np.linalg.norm(lateral)
    vertical_unit = vertical / np.linalg.norm(vertical)

    x_error = axis_error_degrees(frame["x_axis"], WORLD_AXES["x"])
    y_error = axis_error_degrees(frame["y_axis"], WORLD_AXES["y"])
    z_error = axis_error_degrees(frame["z_axis"], WORLD_AXES["z"])

    basis = np.asarray(frame["matrix"], dtype=np.float64)
    orthogonality_error = float(np.abs(basis.T @ basis - np.eye(3)).max())
    determinant = float(np.linalg.det(basis))

    lr_dot_x = float(np.dot(lateral_unit, WORLD_AXES["x"]))
    si_dot_z = float(np.dot(vertical_unit, WORLD_AXES["z"]))

    # What lr_dot_x is ALLOWED to be: exactly cos(residual). The only thing
    # the alignment removed from the lateral pick was its component along
    # superior-inferior, so l . x_axis = sin(angle(l, z)) = cos(residual) as
    # an identity of the construction - and when x_axis has landed on +X, as
    # the axis criteria above require, l . +X is that same number. A
    # disagreement therefore means the rotation did not do what the frame
    # says it did. It is NOT a demand that the raw picks be perpendicular.
    residual = float(frame["residual_degrees"])
    expected_lr_dot_x = float(np.cos(np.radians(residual)))
    lateral_consistency = abs(lr_dot_x - expected_lr_dot_x)

    frontal_normal_dot_up = float(np.dot(frame["y_axis"], WORLD_AXES["z"]))
    # sin, not cos: the frontal normal must be PERPENDICULAR to world up.
    frontal_limit = float(np.sin(np.radians(tolerance_degrees)))

    criteria = [
        _criterion(
            "si_axis", "INFERIOR->SUPERIOR lands on world +Z",
            z_error, tolerance_degrees, z_error <= tolerance_degrees,
            "SI . +Z = %+.9f, %.6f deg off world +Z" % (si_dot_z, z_error)),
        _criterion(
            "lr_axis", "the subject's LEFT axis lands on world +X",
            x_error, tolerance_degrees, x_error <= tolerance_degrees,
            "%.6f deg off +X" % x_error),
        _criterion(
            "posterior_axis", "the POSTERIOR axis lands on world +Y",
            y_error, tolerance_degrees, y_error <= tolerance_degrees,
            "%.6f deg off +Y" % y_error),
        _criterion(
            "orthonormal", "the applied basis is orthonormal",
            orthogonality_error, ORTHOGONALITY_TOLERANCE,
            orthogonality_error <= ORTHOGONALITY_TOLERANCE,
            "max |B^T B - I| = %.3e" % orthogonality_error),
        _criterion(
            "right_handed", "the basis is right-handed (det = +1)",
            abs(determinant - 1.0), ORTHOGONALITY_TOLERANCE,
            abs(determinant - 1.0) <= ORTHOGONALITY_TOLERANCE,
            "det = %+.12f" % determinant),
        _criterion(
            "lateral_consistency",
            "RIGHT->LEFT . +X equals cos(residual), as the construction says",
            lateral_consistency, LATERAL_CONSISTENCY_TOLERANCE,
            lateral_consistency <= LATERAL_CONSISTENCY_TOLERANCE,
            "measured %+.9f, cos(%.4f deg) = %+.9f"
            % (lr_dot_x, residual, expected_lr_dot_x)),
        _criterion(
            "frontal_plane", "the frontal plane is not the Top view",
            abs(frontal_normal_dot_up), frontal_limit,
            abs(frontal_normal_dot_up) <= frontal_limit,
            "anterior-posterior . +Z = %+.3e" % frontal_normal_dot_up),
    ]

    origin_distance = None
    origin_limit = None
    if origin_reference:
        target = points[origin_reference]
        origin_distance = float(np.linalg.norm(target))
        if coordinate_reach is None:
            # Nothing better was supplied, so bound the reach by the points
            # themselves. It is a lower bound - the mesh-local coordinates may
            # be larger - so a caller that knows them should pass them.
            coordinate_reach = float(max(
                np.abs(np.stack(list(points.values()))).max(),
                float(np.linalg.norm(vertical))))
        origin_limit = position_tolerance(coordinate_reach)
        criteria.append(_criterion(
            "origin", "the %s reference is at the world origin"
                      % origin_reference,
            origin_distance, origin_limit, origin_distance <= origin_limit,
            "%.3e world units away; the limit is %.3e, which is %g of the "
            "%.4g coordinate reach"
            % (origin_distance, origin_limit, POSITION_RELATIVE_TOLERANCE,
               coordinate_reach)))

    failures = ["%s (%s)" % (item["label"], item["detail"])
                for item in criteria if not item["ok"]]

    return {
        "ok": not failures,
        "failures": failures,
        "criteria": criteria,
        "left_world": points['LEFT'],
        "right_world": points['RIGHT'],
        "superior_world": points['SUPERIOR'],
        "inferior_world": points['INFERIOR'],
        "lr_world": lateral_unit,
        "si_world": vertical_unit,
        "lr_dot_x": lr_dot_x,
        "si_dot_z": si_dot_z,
        "expected_lr_dot_x": expected_lr_dot_x,
        "lateral_consistency": lateral_consistency,
        "x_error_degrees": x_error,
        "y_error_degrees": y_error,
        "z_error_degrees": z_error,
        "worst_axis_error_degrees": max(x_error, y_error, z_error),
        "orthogonality_error": orthogonality_error,
        "determinant": determinant,
        "residual_degrees": residual,
        "raw_angle_degrees": float(frame["raw_angle_degrees"]),
        "frontal_normal_dot_up": frontal_normal_dot_up,
        "tolerance_degrees": float(tolerance_degrees),
        "origin_reference": origin_reference or "",
        "origin_distance": origin_distance,
        "origin_limit": origin_limit,
        "coordinate_reach": (float(coordinate_reach)
                             if coordinate_reach is not None else None),
        "frame": frame,
    }


def validation_lines(validation):
    """The measured postcondition, for the panel and the log.

    Numbers first, and every criterion listed whether it passed or not. A
    researcher who has been told "Aligned" once by a tool that was not, is
    owed the measurement rather than the adjective - and a researcher whose
    alignment was refused is owed the specific criterion rather than a
    sentence that trails off.
    """
    def vector(name, value):
        return "  %-9s %14.5f %14.5f %14.5f" % (
            name, value[0], value[1], value[2])

    lines = ["Applied-frame check (measured in world space)"]
    lines.append(vector("LEFT", validation["left_world"]))
    lines.append(vector("RIGHT", validation["right_world"]))
    lines.append(vector("SUPERIOR", validation["superior_world"]))
    lines.append(vector("INFERIOR", validation["inferior_world"]))
    lines.append(vector("LR unit", validation["lr_world"]))
    lines.append(vector("SI unit", validation["si_world"]))
    lines.append("  reference axes %.4f deg apart, residual %.4f deg"
                 % (validation["raw_angle_degrees"],
                    validation["residual_degrees"]))
    lines.append("  LR . +X = %+.9f   expected %+.9f (cos of the residual)"
                 % (validation["lr_dot_x"], validation["expected_lr_dot_x"]))
    lines.append("  SI . +Z = %+.9f   (primary axis; must be +1)"
                 % validation["si_dot_z"])
    lines.append("  axis error  X %.6f  Y %.6f  Z %.6f deg"
                 % (validation["x_error_degrees"],
                    validation["y_error_degrees"],
                    validation["z_error_degrees"]))
    lines.append("  basis orthogonality %.3e   determinant %+.9f"
                 % (validation["orthogonality_error"],
                    validation["determinant"]))
    if validation["origin_reference"]:
        lines.append("  %s is %.3e from the world origin (limit %.3e on a "
                     "coordinate reach of %.4g)"
                     % (validation["origin_reference"],
                        validation["origin_distance"],
                        validation["origin_limit"],
                        validation["coordinate_reach"]))
    for item in validation["criteria"]:
        lines.append("  [%s] %s" % ("PASS" if item["ok"] else "FAIL",
                                    item["label"]))
        lines.append("         %s" % item["detail"])
    lines.append("  %s" % ("PASS - the world axes mean what the convention says"
                           if validation["ok"]
                           else "FAILED VALIDATION on %d criterion/criteria"
                                % len(validation["failures"])))
    return lines


def orthogonalisation_note(frame):
    """How an imperfect pair of picked directions was made orthonormal."""
    return (
        "raw angle between RIGHT->LEFT and INFERIOR->SUPERIOR: %.2f deg "
        "(%.2f deg off perpendicular). Orthogonalisation: "
        "inferior->superior is taken as the primary axis and kept exactly; "
        "right->left is Gram-Schmidt projected onto the plane perpendicular "
        "to it; posterior = superior x left completes a right-handed set. "
        "The applied basis is orthonormal to %.1e whatever the picks."
        % (frame["raw_angle_degrees"], frame["residual_degrees"],
           float(np.abs(np.asarray(frame["matrix"]).T
                        @ np.asarray(frame["matrix"]) - np.eye(3)).max()))
    )


def rotation_to_world(frame):
    """The world rotation that carries the anatomical frame onto the axes.

    `frame["matrix"]` maps anatomical coordinates into the current world, so
    its transpose - which for a rotation is its inverse - is the rotation to
    apply to the object.
    """
    matrix = np.asarray(frame["matrix"], dtype=np.float64)
    rotation = matrix.T
    if not is_rigid(rotation):
        raise AlignmentError(
            "the constructed frame is not a rotation; the reference points "
            "are probably degenerate"
        )
    return rotation


def flip_matrix():
    """180 degrees about Z: the correction for swapped left/right labels."""
    return np.array([
        [-1.0, 0.0, 0.0, 0.0],
        [0.0, -1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ], dtype=np.float64)


def axis_rotation(axis, degrees):
    """A 4x4 rotation of `degrees` about world 'X', 'Y' or 'Z'."""
    angle = float(np.radians(degrees))
    cos, sin = float(np.cos(angle)), float(np.sin(angle))
    matrix = np.eye(4, dtype=np.float64)
    if axis == 'X':
        matrix[1, 1], matrix[1, 2] = cos, -sin
        matrix[2, 1], matrix[2, 2] = sin, cos
    elif axis == 'Y':
        matrix[0, 0], matrix[0, 2] = cos, sin
        matrix[2, 0], matrix[2, 2] = -sin, cos
    elif axis == 'Z':
        matrix[0, 0], matrix[0, 1] = cos, -sin
        matrix[1, 0], matrix[1, 1] = sin, cos
    else:
        raise AlignmentError("axis must be 'X', 'Y' or 'Z', got %r" % (axis,))
    return matrix


def compose(matrix_world, rotation, pivot=None, translate_to=None):
    """Apply a world rotation about `pivot`, then optionally move the pivot.

    Returns the new 4x4. The object's own scale is carried through untouched:
    the rotation is applied on the LEFT, so the linear part becomes R*L and
    L^T L - the physical metric - is unchanged. That is why alignment cannot
    alter a distance.
    """
    matrix_world = np.asarray(matrix_world, dtype=np.float64)
    rotation = np.asarray(rotation, dtype=np.float64)
    if rotation.shape == (3, 3):
        full = np.eye(4, dtype=np.float64)
        full[:3, :3] = rotation
        rotation = full
    linear = rotation[:3, :3]

    result = rotation @ matrix_world
    if pivot is not None:
        # Turn the body in place rather than swinging it around the scene
        # origin: shift so the pivot maps to itself.
        pivot = np.asarray(pivot, dtype=np.float64)
        result[:3, 3] += pivot - linear @ pivot
    if translate_to is not None:
        target = np.asarray(translate_to, dtype=np.float64)
        # After the pivot-preserving rotation the pivot is still at `pivot`;
        # without a pivot the object's own origin is the anchor.
        anchor = pivot if pivot is not None else result[:3, 3]
        result[:3, 3] += target - anchor
    return result


def alignment_report(frame, scale, method=METHOD_LANDMARK, multiplier=None):
    """Lines describing an alignment, for the panel and the log.

    `multiplier` converts Blender world units to millimetres. The spans on the
    frame are measured between world reference points, so they are in world
    units; without the multiplier they are reported as such rather than
    labelled "mm" and hoped for. Calling 0.7 world units "0.7 mm" on a scan in
    metres is how a report stops being evidence.
    """
    lines = ["Anatomical alignment (%s)" % method, "  " + AXIS_DESCRIPTION]
    if frame is not None:
        if multiplier:
            span = lambda value: "%.1f mm" % (value * float(multiplier))
        else:
            span = lambda value: "%.4g world units" % value
        lines.append("  left-right span   %s" % span(frame["lateral_span"]))
        lines.append("  superior-inferior %s" % span(frame["vertical_span"]))
        lines.append("  reference axes %.2f deg apart, so residual "
                     "non-orthogonality %.2f deg%s"
                     % (frame["raw_angle_degrees"], frame["residual_degrees"],
                        "" if frame["residual_ok"]
                        else "  <- large; check the reference points"))
    if scale is not None and scale["message"]:
        lines.append("  " + scale["message"])
    return lines
