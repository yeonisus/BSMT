"""Offline tests for the workflow-ordered sidebar (Milestone 3.16).

    python3 tests/test_panel_order.py

Panel ORDER is a property of the class attributes, so it is checked here
without Blender: registration order must not matter, every panel must declare
an explicit `bl_order`, nesting must stay one level deep, and the sidebar's
order must be the workflow's order. That the panels actually DRAW in every
scene state needs real Blender and lives in tests/test_workflow_ui.py.
"""

import importlib
import os
import sys
import types

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


# The package needs a stubbed bpy to import. test_import.py builds the same
# one; it is reused rather than duplicated.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_import_tests = importlib.import_module("test_import")
_import_tests.install_stubs()

bsmt = importlib.import_module("body_surface_measurement")
panels = bsmt.panels
readiness = bsmt.readiness

#: The workflow, in the order the brief specifies it.
EXPECTED_STAGES = (
    "Scan Setup",
    "Scan Preprocessing",
    "Mesh Repair",
    "Alignment",
    "Landmark Manager",
    "Measurement Manager",
    "Measurement Visualization",
    "Results and Export",
)


def panel_classes():
    return [cls for cls in panels.classes
            if getattr(cls, "bl_idname", "").startswith("BSMT_PT_")]


def top_level():
    return [cls for cls in panels.workflow_panels()
            if not getattr(cls, "bl_parent_id", "")]


def test_workflow_order():
    print("\n[order] the sidebar is the workflow, top to bottom")
    got = [cls.bl_label for cls in top_level()]
    check("the eight stages are in workflow order",
          tuple(got) == EXPECTED_STAGES, got)
    check("and there is no ninth top-level panel",
          len(got) == len(EXPECTED_STAGES), got)


def test_order_is_explicit():
    print("\n[order] ordering is declared, never inherited from registration")
    for cls in panel_classes():
        check("%s declares bl_order" % cls.bl_idname,
              isinstance(getattr(cls, "bl_order", None), int),
              getattr(cls, "bl_order", None))
    orders = [cls.bl_order for cls in top_level()]
    check("top-level orders strictly increase", orders == sorted(set(orders)),
          orders)
    check("stage numbers leave room to insert one later",
          all(b - a >= 10 for a, b in zip(orders, orders[1:])), orders)


def test_registration_order_does_not_decide():
    print("\n[order] shuffling the registration tuple changes nothing")
    original = panels.classes
    try:
        panels.classes = tuple(reversed(original))
        got = [cls.bl_label for cls in top_level()]
        check("the order survives a reversed registration tuple",
              tuple(got) == EXPECTED_STAGES, got)
    finally:
        panels.classes = original
    check("and the tuple is restored", panels.classes is original)


def test_stage_map_matches_readiness():
    print("\n[order] the panel order and the stage vocabulary agree")
    check("every readiness stage has a panel order",
          all(stage in panels.STAGE_ORDER for stage in readiness.STAGE_ORDER),
          sorted(panels.STAGE_ORDER))
    check("and no panel order invents a stage",
          all(stage in readiness.STAGE_ORDER for stage in panels.STAGE_ORDER))
    check("the two orderings are the same sequence",
          [stage for stage in readiness.STAGE_ORDER]
          == sorted(panels.STAGE_ORDER, key=panels.STAGE_ORDER.get),
          sorted(panels.STAGE_ORDER, key=panels.STAGE_ORDER.get))
    for stage, title in readiness.STAGE_TITLES.items():
        check("stage %s names a real panel" % stage,
              title in [cls.bl_label for cls in panel_classes()], title)


def test_nesting_is_shallow():
    print("\n[order] nesting is one level deep, never more")
    by_id = {cls.bl_idname: cls for cls in panel_classes()}
    children = 0
    for cls in panel_classes():
        parent_id = getattr(cls, "bl_parent_id", "")
        if not parent_id:
            continue
        children += 1
        parent = by_id.get(parent_id)
        check("%s's parent exists" % cls.bl_idname, parent is not None,
              parent_id)
        if parent is not None:
            check("  and %s is top level" % parent.bl_idname,
                  not getattr(parent, "bl_parent_id", ""))
    check("there are child panels at all", children > 0, children)
    check("a parent must be registered before its children",
          all(list(panels.classes).index(by_id[cls.bl_parent_id])
              < list(panels.classes).index(cls)
              for cls in panel_classes()
              if getattr(cls, "bl_parent_id", "")))


def test_the_legacy_panel_is_demoted():
    print("\n[order] the Phase 1 A-to-B tool is no longer the front door")
    quick = next(cls for cls in panel_classes()
                 if cls.bl_idname == "BSMT_PT_body_measurement")
    check("Quick Measure is a child, not a stage",
          getattr(quick, "bl_parent_id", "") == "BSMT_PT_scan_setup",
          getattr(quick, "bl_parent_id", ""))
    check("and it is closed by default",
          'DEFAULT_CLOSED' in getattr(quick, "bl_options", set()))
    check("Scan Setup is the first thing a researcher sees",
          top_level()[0].bl_idname == "BSMT_PT_scan_setup")


def test_one_status_line():
    print("\n[ui] status is stated once, not repeated per panel")
    source = open(os.path.join(PACKAGE, "panels.py")).read()
    def call_count(name):
        """Calls only - the `def` line matches the same text."""
        return sum(1 for line in source.splitlines()
                   if name + "(context, layout, props)" in line
                   and not line.lstrip().startswith("def "))

    check("the readiness line is drawn in exactly one place",
          call_count("_draw_readiness") == 1, call_count("_draw_readiness"))
    check("the full measurement-target block likewise",
          call_count("_draw_measurement_target") == 1,
          call_count("_draw_measurement_target"))
    check("and Measurement Manager uses the one-line form instead",
          "_draw_target_line(context, layout, props)" in source)


def test_hints_are_guidance_not_gates():
    print("\n[ui] guidance never disables anything (sect. 11)")
    source = open(os.path.join(PACKAGE, "panels.py")).read()
    check("no panel switches its whole layout off",
          "layout.enabled = False" not in source)
    check("the hint helper draws a label and returns",
          "row.label(text=text, icon='INFO')" in source)
    readiness_source = open(os.path.join(PACKAGE, "readiness.py")).read()
    check("and the guidance module says it is not enforcement",
          "guidance, never enforcement" in readiness_source)
    check("naming who does enforce", "preflight" in readiness_source)


def test_terminology():
    print("\n[ui] one vocabulary: measurement mesh")
    for name in ("panels.py", "readiness.py"):
        source = open(os.path.join(PACKAGE, name)).read()
        check("%s never says 'measurement copy'" % name,
              "measurement copy" not in source.lower(), name)


def main():
    print("BSMT Milestone 3.16 - workflow-ordered sidebar")
    for test in (
        test_workflow_order,
        test_order_is_explicit,
        test_registration_order_does_not_decide,
        test_stage_map_matches_readiness,
        test_nesting_is_shallow,
        test_the_legacy_panel_is_demoted,
        test_one_status_line,
        test_hints_are_guidance_not_gates,
        test_terminology,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for label in FAILURES:
        print("  FAILED: %s" % label)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
