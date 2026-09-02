"""Offline tests for Milestone 3.7: measurement drafts and global readiness.

    python3 tests/test_readiness.py

Covers the two pure modules the UI polish rests on - the draft rules in
`measurements.py` and the readiness summary in `readiness.py`. The Blender
side (Add behaviour, panel drawing, wording) is exercised by the in-Blender
acceptance script. Nothing here imports bpy.
"""

import importlib.util
import os
import sys

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


def load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(PACKAGE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


measurements = load("bsmt_measurements", "measurements.py")
readiness = load("bsmt_readiness", "readiness.py")


class FakeMeasurement(object):
    """The handful of fields the pure functions actually read."""

    def __init__(self, source=0, target=0, enabled=True,
                 measurement_type='BOTH'):
        self.source_stable_id = source
        self.target_stable_id = target
        self.enabled = enabled
        self.measurement_type = measurement_type


class FakeLandmark(object):
    def __init__(self, name="P", status='VALID'):
        self.name = name
        self.status = status


# ---------------------------------------------------------------------------
# drafts (sect. 6, 7)
# ---------------------------------------------------------------------------

def test_what_counts_as_a_draft():
    print("\n[draft] a row is a measurement only when it is complete")
    check("nothing chosen is a draft", measurements.is_draft(0, 0))
    check("From only is a draft", measurements.is_draft(3, 0))
    check("To only is a draft", measurements.is_draft(0, 3))
    check("the same landmark twice is a draft", measurements.is_draft(3, 3))
    check("two different landmarks are NOT a draft",
          not measurements.is_draft(3, 4))
    check("None is treated as unset", measurements.is_draft(None, None))

    check("is_defined agrees", measurements.is_defined(FakeMeasurement(3, 4)))
    check("and rejects a draft",
          not measurements.is_defined(FakeMeasurement(3, 3)))

    rows = [FakeMeasurement(1, 2), FakeMeasurement(0, 0), FakeMeasurement(3, 4),
            FakeMeasurement(5, 5)]
    real = measurements.defined(rows)
    check("defined() keeps only the real ones", len(real) == 2, len(real))
    check("in their original order",
          real[0] is rows[0] and real[1] is rows[2])


def test_a_draft_is_never_named():
    print("\n[draft] no meaningless names")
    check("a complete pair is named",
          measurements.default_name("Shoulder_L", "Shoulder_R")
          == "Shoulder_L to Shoulder_R")
    check("a missing source gives NO name",
          measurements.default_name("", "Shoulder_R") == "",
          repr(measurements.default_name("", "Shoulder_R")))
    check("a missing target gives NO name",
          measurements.default_name("Shoulder_L", "") == "")
    check("neither gives NO name", measurements.default_name("", "") == "")
    for bad in ("None to None", "? to ?", "None", "?"):
        check("never produces %r" % bad,
              measurements.default_name("", "") != bad)


def test_draft_status_is_reported_specifically():
    print("\n[draft] the status says which end is missing")
    status, detail = measurements.readiness(None, None, 0, 0)
    check("nothing chosen -> DRAFT", status == measurements.STATUS_DRAFT)
    check("and asks for both", "From and a To" in detail, detail)

    status, detail = measurements.readiness(FakeLandmark(), None, 7, 0)
    check("To missing -> DRAFT", status == measurements.STATUS_DRAFT)
    check("and asks for To only", detail == "choose a To landmark", detail)

    status, detail = measurements.readiness(None, FakeLandmark(), 0, 7)
    check("From missing -> DRAFT", status == measurements.STATUS_DRAFT)
    check("and asks for From only", detail == "choose a From landmark", detail)

    same = FakeLandmark()
    status, detail = measurements.readiness(same, same, 7, 7)
    check("From == To -> DRAFT", status == measurements.STATUS_DRAFT)
    check("and says why", "same landmark" in detail, detail)

    # A draft is NOT an invalid reference: they mean different things.
    status, _detail = measurements.readiness(None, None, 7, 8)
    check("a genuinely missing landmark is INVALID_REFERENCE, not DRAFT",
          status == measurements.STATUS_INVALID_REFERENCE, status)

    status, _detail = measurements.readiness(
        FakeLandmark("a"), FakeLandmark("b"), 7, 8)
    check("a complete pair of valid landmarks is READY",
          status == measurements.STATUS_READY, status)

    status, detail = measurements.readiness(
        FakeLandmark("a"), FakeLandmark("b", 'NOT_PICKED'), 7, 8)
    check("an unpicked landmark is NOT_READY, not DRAFT",
          status == measurements.STATUS_NOT_READY, status)
    check("and it is named as the To end", "To 'b'" in detail, detail)


def test_draft_status_is_registered():
    print("\n[draft] DRAFT is a first-class status")
    identifiers = [item[0] for item in measurements.STATUS_ITEMS]
    check("DRAFT is in STATUS_ITEMS",
          measurements.STATUS_DRAFT in identifiers)
    check("every status has an item",
          set(measurements.STATUS_ORDER) == set(identifiers),
          set(measurements.STATUS_ORDER) ^ set(identifiers))
    for table, name in ((measurements.STATUS_ICONS, "STATUS_ICONS"),
                        (measurements.STATUS_SHORT, "STATUS_SHORT")):
        check("%s covers every status" % name,
              all(status in table for status in measurements.STATUS_ORDER),
              [s for s in measurements.STATUS_ORDER if s not in table])
    counts, summary = measurements.summarise(
        [measurements.STATUS_DRAFT, measurements.STATUS_VALID])
    check("summarise counts drafts", counts[measurements.STATUS_DRAFT] == 1)
    check("and labels them", "1 draft" in summary, summary)


def test_calculate_all_ignores_drafts():
    print("\n[batch] Calculate All never runs a draft")
    rows = [
        FakeMeasurement(1, 2),                       # real, enabled
        FakeMeasurement(0, 0),                       # draft
        FakeMeasurement(3, 4, enabled=False),        # real, disabled
        FakeMeasurement(5, 6, measurement_type='STRAIGHT'),
        FakeMeasurement(7, 7),                       # draft (same endpoints)
    ]
    plan = measurements.batch_plan(rows)
    check("5 rows in total", plan["total"] == 5)
    check("3 are real measurements", plan["defined"] == 3, plan["defined"])
    check("2 are drafts", plan["drafts"] == 2, plan["drafts"])
    check("2 will be calculated", plan["enabled"] == 2, plan["enabled"])
    check("1 is disabled", plan["disabled"] == 1, plan["disabled"])
    check("1 needs the surface solver", plan["surface"] == 1, plan["surface"])
    check("the summary counts only what will run",
          plan["summary"].startswith("2 measurements"), plan["summary"])
    check("and never mentions the drafts as work",
          "5" not in plan["summary"], plan["summary"])

    empty = measurements.batch_plan([FakeMeasurement(0, 0)])
    check("a list of only drafts calculates nothing", empty["enabled"] == 0)
    check("and says so plainly", empty["summary"] == "Nothing to calculate",
          empty["summary"])

    report = measurements.batch_report(
        [measurements.STATUS_VALID, measurements.STATUS_DRAFT], 1, 0)
    check("the batch report mentions skipped drafts",
          any("draft" in line for line in report), report)


# ---------------------------------------------------------------------------
# readiness (sect. 13)
# ---------------------------------------------------------------------------

CLEAN = dict(mesh_name="A_BSMT", triangle_count=350000, non_manifold=0,
             analysed=True, dense_threshold=1000000, scale_uniform=True,
             landmark_total=4, landmarks_unpicked=0, landmarks_stale=0,
             measurements_defined=2)


def test_ready():
    print("\n[readiness] the ready case")
    result = readiness.evaluate(**CLEAN)
    check("state is READY", result["state"] == readiness.READY)
    check("nothing blocks", not result["blocked"])
    check("the headline is the plain one",
          result["headline"] == "READY FOR MEASUREMENT", result["headline"])
    check("no reasons are listed", not result["reasons"], result["reasons"])
    check("the icon is a tick", result["icon"] == 'CHECKMARK')


def test_blockers():
    print("\n[readiness] what blocks, and what merely remains to do")
    cases = [
        ("no mesh", dict(mesh_name=""), readiness.REASON_NO_MESH, True),
        ("non-manifold", dict(non_manifold=7),
         readiness.REASON_NON_MANIFOLD, True),
        ("too dense", dict(triangle_count=2783068),
         readiness.REASON_DENSE, True),
        ("non-uniform scale", dict(scale_uniform=False),
         readiness.REASON_NON_UNIFORM_SCALE, True),
        ("stale landmarks", dict(landmarks_stale=2),
         readiness.REASON_LANDMARKS_STALE, True),
        ("unpicked landmarks", dict(landmarks_unpicked=2),
         readiness.REASON_LANDMARKS_UNPICKED, False),
        ("no landmarks", dict(landmark_total=0),
         readiness.REASON_NO_LANDMARKS, False),
        ("no measurements", dict(measurements_defined=0),
         readiness.REASON_NO_MEASUREMENTS, False),
    ]
    for label, override, code, blocking in cases:
        arguments = dict(CLEAN)
        arguments.update(override)
        result = readiness.evaluate(**arguments)
        codes = [entry["code"] for entry in result["reasons"]]
        check("%s is reported" % label, code in codes, codes)
        check("  and it %s block" % ("does" if blocking else "does NOT"),
              result["blocked"] == blocking, result["headline"])
        if blocking:
            check("  the headline names it",
                  result["headline"].startswith("NOT READY:"),
                  result["headline"])
        entry = [e for e in result["reasons"] if e["code"] == code][0]
        check("  it points at a panel", bool(entry["panel"]), entry)

    check("the density guard can be turned off",
          not readiness.evaluate(**dict(CLEAN, triangle_count=2783068,
                                        guard_dense=False))["blocked"])


def test_unknown_is_not_an_answer():
    print("\n[readiness] 'not analyzed' is honestly its own state")
    result = readiness.evaluate(**dict(CLEAN, analysed=False))
    check("state is UNKNOWN", result["state"] == readiness.UNKNOWN,
          result["state"])
    check("it does not claim READY", not result["ready"])
    check("nor does it claim a blocker", not result["blocked"])
    check("and it says what to do", "analyzed" in result["headline"],
          result["headline"])

    # A real blocker outranks not-yet-analysed.
    result = readiness.evaluate(**dict(CLEAN, analysed=False, non_manifold=3))
    check("a known blocker still wins", result["state"] == readiness.NOT_READY)


def test_first_blocker_is_the_headline():
    print("\n[readiness] the researcher is told ONE next thing")
    result = readiness.evaluate(**dict(
        CLEAN, non_manifold=7, scale_uniform=False, landmarks_stale=1))
    check("three problems are recorded", len(result["blockers"]) == 3,
          len(result["blockers"]))
    check("but the headline names only the first",
          result["headline"].count(":") == 1, result["headline"])
    check("and that first one is the topology",
          result["blockers"][0]["code"] == readiness.REASON_NON_MANIFOLD)

    lines = readiness.lines(result)
    check("the expanded form lists them", len(lines) >= 4, lines)
    check("each carries its panel",
          all("see " in line for line in lines[1:]), lines)


def test_panel_pointers_are_real_panel_titles():
    print("\n[readiness] every pointer names a panel that exists")
    titles = set()
    source = open(os.path.join(PACKAGE, "panels.py")).read()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("bl_label = "):
            titles.add(stripped.split("=", 1)[1].strip().strip('"'))
    for code, panel in readiness.PANEL_FOR_REASON.items():
        check("%s -> %r is a real panel" % (code, panel), panel in titles,
              sorted(titles))
    check("every reason code has a pointer",
          all(getattr(readiness, name) in readiness.PANEL_FOR_REASON
              for name in dir(readiness) if name.startswith("REASON_")))


def main():
    print("BSMT Milestone 3.7 - draft and readiness tests")
    for test in (
        test_what_counts_as_a_draft,
        test_a_draft_is_never_named,
        test_draft_status_is_reported_specifically,
        test_draft_status_is_registered,
        test_calculate_all_ignores_drafts,
        test_ready,
        test_blockers,
        test_unknown_is_not_an_answer,
        test_first_blocker_is_the_headline,
        test_panel_pointers_are_real_panel_titles,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
