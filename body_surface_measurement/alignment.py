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
    residual_degrees = abs(90.0 - float(np.degrees(np.arccos(cosine))))

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
        "residual_degrees": residual_degrees,
        "residual_ok": residual_degrees <= RESIDUAL_WARN_DEGREES,
        "lateral_mm": lateral_length,
        "vertical_mm": vertical_length,
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


def alignment_report(frame, scale, method=METHOD_LANDMARK):
    """Lines describing an alignment, for the panel and the log."""
    lines = ["Anatomical alignment (%s)" % method, "  " + AXIS_DESCRIPTION]
    if frame is not None:
        lines.append("  left-right span   %.1f mm" % frame["lateral_mm"])
        lines.append("  superior-inferior %.1f mm" % frame["vertical_mm"])
        lines.append("  residual non-orthogonality %.2f deg%s"
                     % (frame["residual_degrees"],
                        "" if frame["residual_ok"]
                        else "  <- large; check the reference points"))
    if scale is not None and scale["message"]:
        lines.append("  " + scale["message"])
    return lines
