"""Offline tests for Milestone 3.6: rigid anatomical alignment.

    python3 tests/test_alignment.py

Covers the frame construction, the axis convention, rigidity and the scale
rules - all of which are pure geometry. The Blender side (picking references,
moving the object, landmark validity) is exercised by the in-Blender
acceptance script. Nothing here imports bpy.
"""

import importlib.util
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "body_surface_measurement")

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


spec = importlib.util.spec_from_file_location(
    "bsmt_alignment", os.path.join(PACKAGE, "alignment.py"))
alignment = importlib.util.module_from_spec(spec)
sys.modules["bsmt_alignment"] = alignment
spec.loader.exec_module(alignment)


def raises(label, call):
    try:
        call()
    except alignment.AlignmentError:
        check(label, True)
    except Exception as exc:  # noqa: BLE001
        check(label, False, "raised %s instead: %s" % (type(exc).__name__, exc))
    else:
        check(label, False, "did not raise")


#: An upright subject facing -Y: left hand at +X, head at +Z.
UPRIGHT = {
    "left": np.array([200.0, 0.0, 900.0]),
    "right": np.array([-200.0, 0.0, 900.0]),
    "superior": np.array([0.0, 0.0, 1700.0]),
    "inferior": np.array([0.0, 0.0, 0.0]),
}


def random_rotation(seed):
    rng = np.random.default_rng(seed)
    matrix, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    if np.linalg.det(matrix) < 0:
        matrix[:, 0] *= -1
    return matrix


# ---------------------------------------------------------------------------
# the axis convention
# ---------------------------------------------------------------------------

def test_axis_convention():
    print("\n[frame] the documented convention")
    frame = alignment.anatomical_frame(**UPRIGHT)
    check("+X is the subject's LEFT",
          np.allclose(frame["x_axis"], [1, 0, 0]), str(frame["x_axis"]))
    check("+Z is SUPERIOR", np.allclose(frame["z_axis"], [0, 0, 1]))
    check("+Y is POSTERIOR", np.allclose(frame["y_axis"], [0, 1, 0]),
          str(frame["y_axis"]))
    check("anterior is therefore -Y, which is what Blender's Front view sees",
          np.allclose(-frame["y_axis"], [0, -1, 0]))
    check("the frame is right-handed",
          np.allclose(np.cross(frame["x_axis"], frame["y_axis"]),
                      frame["z_axis"]))
    check("the frame is orthonormal",
          np.allclose(frame["matrix"].T @ frame["matrix"], np.eye(3),
                      atol=1e-12))
    check("the convention is documented on the frame",
          "subject's left" in frame["convention"], frame["convention"])
    check("and matches the module constant",
          frame["convention"] == alignment.AXIS_DESCRIPTION)

    check("an already-upright body needs no rotation",
          np.allclose(alignment.rotation_to_world(frame), np.eye(3)))


def test_any_orientation_is_brought_upright():
    print("\n[frame] any scanner orientation maps onto the world axes")
    worst = 0.0
    for seed in range(12):
        rotation = random_rotation(seed)
        offset = np.array([321.0, -87.0, 55.0]) * (seed + 1)
        points = {name: rotation @ point + offset
                  for name, point in UPRIGHT.items()}
        frame = alignment.anatomical_frame(**points)
        align = alignment.rotation_to_world(frame)
        check("seed %d: the rotation is rigid" % seed, alignment.is_rigid(align))
        for axis, target in (("x_axis", [1, 0, 0]), ("y_axis", [0, 1, 0]),
                             ("z_axis", [0, 0, 1])):
            worst = max(worst, float(np.abs(align @ frame[axis]
                                            - np.asarray(target)).max()))
    check("every axis lands on its world axis (worst %.2e)" % worst,
          worst < 1e-9)
    check("translation does not affect the frame",
          np.allclose(
              alignment.anatomical_frame(**UPRIGHT)["matrix"],
              alignment.anatomical_frame(
                  **{k: v + 1000.0 for k, v in UPRIGHT.items()})["matrix"]))


def test_residual_is_reported_not_absorbed():
    print("\n[frame] non-orthogonality is measured, never assumed away")
    frame = alignment.anatomical_frame(**UPRIGHT)
    check("a clean pick has ~0 residual", frame["residual_degrees"] < 1e-9,
          "%.2e" % frame["residual_degrees"])
    check("and is flagged ok", frame["residual_ok"])

    tilted = dict(UPRIGHT)
    tilted["left"] = np.array([200.0, 0.0, 1000.0])     # 100 mm higher
    frame = alignment.anatomical_frame(**tilted)
    expected = math.degrees(math.atan2(100.0, 400.0))
    check("a tilted left/right pair reports its residual",
          abs(frame["residual_degrees"] - expected) < 1e-6,
          "%.3f vs %.3f" % (frame["residual_degrees"], expected))
    check("the frame is still exactly orthonormal",
          np.allclose(frame["matrix"].T @ frame["matrix"], np.eye(3),
                      atol=1e-12))
    check("superior-inferior is taken as primary and stays exact",
          np.allclose(frame["z_axis"], [0, 0, 1]))
    check("the left-right axis is orthogonalised against it",
          abs(float(np.dot(frame["x_axis"], frame["z_axis"]))) < 1e-12)

    wild = dict(UPRIGHT)
    wild["left"] = np.array([50.0, 0.0, 1600.0])
    frame = alignment.anatomical_frame(**wild)
    check("a badly conditioned pick is flagged", not frame["residual_ok"],
          "%.1f deg" % frame["residual_degrees"])
    check("the warning threshold is a named constant",
          alignment.RESIDUAL_WARN_DEGREES > 0)

    check("the span of each reference pair is reported",
          abs(alignment.anatomical_frame(**UPRIGHT)["lateral_mm"] - 400.0) < 1e-9
          and abs(alignment.anatomical_frame(**UPRIGHT)["vertical_mm"]
                  - 1700.0) < 1e-9)


def test_degenerate_references_are_refused():
    print("\n[frame] degenerate reference sets are refused, not guessed")
    raises("superior == inferior",
           lambda: alignment.anatomical_frame(
               UPRIGHT["left"], UPRIGHT["right"], UPRIGHT["superior"],
               UPRIGHT["superior"]))
    raises("left == right",
           lambda: alignment.anatomical_frame(
               UPRIGHT["left"], UPRIGHT["left"], UPRIGHT["superior"],
               UPRIGHT["inferior"]))
    raises("left-right parallel to superior-inferior",
           lambda: alignment.anatomical_frame(
               np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 500.0]),
               UPRIGHT["superior"], UPRIGHT["inferior"]))
    raises("a non-finite reference",
           lambda: alignment.anatomical_frame(
               np.array([np.nan, 0.0, 0.0]), UPRIGHT["right"],
               UPRIGHT["superior"], UPRIGHT["inferior"]))
    raises("a 2D reference",
           lambda: alignment.anatomical_frame(
               np.array([0.0, 0.0]), UPRIGHT["right"], UPRIGHT["superior"],
               UPRIGHT["inferior"]))


# ---------------------------------------------------------------------------
# the flip
# ---------------------------------------------------------------------------

def test_flip_is_the_swapped_left_right_correction():
    print("\n[flip] the flip is exactly the swapped-label correction")
    frame = alignment.anatomical_frame(**UPRIGHT)
    swapped = alignment.anatomical_frame(
        UPRIGHT["right"], UPRIGHT["left"], UPRIGHT["superior"],
        UPRIGHT["inferior"])
    check("swapping left/right negates X",
          np.allclose(swapped["x_axis"], -frame["x_axis"]))
    check("and therefore negates Y",
          np.allclose(swapped["y_axis"], -frame["y_axis"]))
    check("while Z is untouched",
          np.allclose(swapped["z_axis"], frame["z_axis"]))

    flip = alignment.flip_matrix()
    check("which is exactly 180 degrees about Z",
          np.allclose(flip[:3, :3] @ frame["matrix"], swapped["matrix"],
                      atol=1e-12))
    check("the flip is rigid", alignment.is_rigid(flip))
    check("applying it twice is the identity",
          np.allclose(flip @ flip, np.eye(4), atol=1e-12))


# ---------------------------------------------------------------------------
# rigidity, scale and composition
# ---------------------------------------------------------------------------

def test_rigidity():
    print("\n[rigid] what counts as a rigid transform")
    check("the identity is rigid", alignment.is_rigid(np.eye(4)))
    check("every axis rotation is rigid",
          all(alignment.is_rigid(alignment.axis_rotation(axis, degrees))
              for axis in "XYZ"
              for degrees in (-180, -90, -37, 0, 37, 90, 180)))
    check("uniform scale is NOT rigid",
          not alignment.is_rigid(np.diag([2.0, 2.0, 2.0, 1.0])))
    check("a reflection is NOT rigid",
          not alignment.is_rigid(np.diag([-1.0, 1.0, 1.0, 1.0])))
    check("a shear is NOT rigid",
          not alignment.is_rigid(np.array([[1.0, 0.4, 0, 0], [0, 1, 0, 0],
                                           [0, 0, 1, 0], [0, 0, 0, 1.0]])))
    check("a non-finite matrix is not rigid",
          not alignment.is_rigid(np.full((4, 4), np.nan)))
    raises("an unknown axis is refused",
           lambda: alignment.axis_rotation('W', 90.0))

    for axis in "XYZ":
        quarter = alignment.axis_rotation(axis, 90.0)
        check("%s: four quarter turns is the identity" % axis,
              np.allclose(np.linalg.matrix_power(quarter, 4), np.eye(4),
                          atol=1e-12))
        check("%s: +90 then -90 is the identity" % axis,
              np.allclose(quarter @ alignment.axis_rotation(axis, -90.0),
                          np.eye(4), atol=1e-12))


def test_scale_rules():
    print("\n[scale] alignment never introduces scale")
    report = alignment.scale_report(np.eye(4))
    check("unit scale is uniform and unity",
          report["uniform"] and report["unity"])
    check("and needs no message", report["message"] == "")

    report = alignment.scale_report(np.diag([2.0, 2.0, 2.0, 1.0]))
    check("uniform non-unity is uniform but not unity",
          report["uniform"] and not report["unity"])
    check("and says so", "not 1.0" in report["message"], report["message"])

    report = alignment.scale_report(np.diag([2.0, 3.0, 0.5, 1.0]))
    check("non-uniform is detected", not report["uniform"])
    check("and reports all three values",
          "2.00000" in report["message"] and "0.50000" in report["message"])

    ok, why = alignment.check_alignable(np.eye(4))
    check("a unit-scale object is alignable", ok, why)
    ok, why = alignment.check_alignable(np.diag([2.0, 2.0, 2.0, 1.0]))
    check("uniform scale is allowed, with a note", ok, why)
    ok, why = alignment.check_alignable(np.diag([2.0, 3.0, 0.5, 1.0]))
    check("NON-uniform scale is refused", not ok)
    check("and the refusal says how to fix it", "Apply" in why, why)

    # A rotation applied on the left cannot change the metric - that is the
    # reason alignment cannot alter a distance.
    for seed in range(4):
        linear = random_rotation(seed)
        before = np.eye(4)
        before[:3, :3] = np.diag([2.0, 2.0, 2.0])
        before[:3, 3] = [5.0, 6.0, 7.0]
        after = alignment.compose(before, linear)
        check("seed %d: L^T L is unchanged by the rotation" % seed,
              np.allclose(before[:3, :3].T @ before[:3, :3],
                          after[:3, :3].T @ after[:3, :3], atol=1e-12))
        check("  and the scale survives",
              np.allclose(alignment.linear_scale(after),
                          alignment.linear_scale(before)))


def test_compose():
    print("\n[compose] rotate about a pivot, optionally move it")
    matrix = np.eye(4)
    matrix[:3, 3] = [10.0, 20.0, 30.0]
    pivot = np.array([100.0, 0.0, 500.0])
    rotation = alignment.axis_rotation('Z', 90.0)

    turned = alignment.compose(matrix, rotation, pivot=pivot)
    local = np.linalg.inv(matrix[:3, :3]) @ (pivot - matrix[:3, 3])
    check("a point at the pivot stays at the pivot",
          np.allclose(turned[:3, :3] @ local + turned[:3, 3], pivot,
                      atol=1e-9))

    moved = alignment.compose(matrix, rotation, pivot=pivot,
                              translate_to=np.zeros(3))
    check("translate_to puts the pivot at the world origin",
          np.allclose(moved[:3, :3] @ local + moved[:3, 3], np.zeros(3),
                      atol=1e-9))
    check("the rotation itself is unchanged by the translation",
          np.allclose(moved[:3, :3], turned[:3, :3], atol=1e-12))

    check("a 3x3 rotation is accepted as well as a 4x4",
          np.allclose(alignment.compose(matrix, rotation[:3, :3]),
                      alignment.compose(matrix, rotation)))
    check("composing with the identity changes nothing",
          np.allclose(alignment.compose(matrix, np.eye(4)), matrix))


def test_report_lines():
    print("\n[report] what the researcher is told")
    frame = alignment.anatomical_frame(**UPRIGHT)
    lines = alignment.alignment_report(
        frame, alignment.scale_report(np.eye(4)))
    text = "\n".join(lines)
    check("the convention is stated", "subject's left" in text)
    check("both spans are given", "400" in text and "1700" in text, text)
    check("the residual is given", "residual" in text)

    tilted = dict(UPRIGHT)
    tilted["left"] = np.array([50.0, 0.0, 1600.0])
    text = "\n".join(alignment.alignment_report(
        alignment.anatomical_frame(**tilted), None))
    check("a large residual is called out", "check the reference points" in text,
          text)


def main():
    print("BSMT Milestone 3.6 - alignment tests")
    print("  python : %s" % sys.version.split()[0])
    print("  numpy  : %s" % np.__version__)
    for test in (
        test_axis_convention,
        test_any_orientation_is_brought_upright,
        test_residual_is_reported_not_absorbed,
        test_degenerate_references_are_refused,
        test_flip_is_the_swapped_left_right_correction,
        test_rigidity,
        test_scale_rules,
        test_compose,
        test_report_lines,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
