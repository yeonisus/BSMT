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


def strip_comments(source):
    """Source with comment lines and docstring prose removed.

    Assertions about what the code DOES must not be satisfied or broken by
    what a comment SAYS - three of these checks first fired on their own
    explanatory prose.
    """
    out = []
    in_docstring = False
    for line in source.splitlines():
        stripped = line.strip()
        # Docstrings are prose too: the first version of these checks was
        # satisfied by a docstring explaining the very rule it asserted had
        # been removed.
        fences = stripped.count('"""') + stripped.count("'''")
        if in_docstring:
            if fences:
                in_docstring = False
            continue
        if fences == 1:
            in_docstring = True
            continue
        if fences >= 2 or stripped.startswith("#"):
            continue
        out.append(line.split("  #")[0])
    return "\n".join(out)


def function_source(filename, name):
    """The body of one top-level or method definition, comments stripped."""
    lines = open(os.path.join(PACKAGE, filename)).read().splitlines()
    start = None
    indent = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("def %s(" % name):
            start = index
            indent = len(line) - len(line.lstrip())
            break
    if start is None:
        return ""
    body = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        body.append(line)
    return strip_comments("\n".join(body))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(PACKAGE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


measurements = load("bsmt_measurements", "measurements.py")
readiness = load("bsmt_readiness", "readiness.py")
# The readiness surfaces are fed by preprocess.classify_ready, so the
# regression tests below exercise the two together rather than a stand-in.
preprocess = load("bsmt_preprocess", "preprocess.py")


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

CLEAN = dict(mesh_name="A_BSMT", triangle_count=350000, mesh_reasons=(),
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
        ("mesh not ready", dict(mesh_reasons=["7 non-manifold edge(s)"]),
         readiness.REASON_MESH_NOT_READY, True),
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
    result = readiness.evaluate(**dict(
        CLEAN, analysed=False, mesh_reasons=["3 non-manifold edge(s)"]))
    check("a known blocker still wins", result["state"] == readiness.NOT_READY)


def test_first_blocker_is_the_headline():
    print("\n[readiness] the researcher is told ONE next thing")
    result = readiness.evaluate(**dict(
        CLEAN, mesh_reasons=["7 non-manifold edge(s)"], scale_uniform=False,
        landmarks_stale=1))
    check("three problems are recorded", len(result["blockers"]) == 3,
          len(result["blockers"]))
    check("but the headline names only the first",
          result["headline"].count(":") == 1, result["headline"])
    check("and that first one is the topology",
          result["blockers"][0]["code"] == readiness.REASON_MESH_NOT_READY)

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


def test_stage_hints():
    print("\n[stages] each stage says what it still needs, in one line")
    ready = dict(has_mesh=True, analysed=True, copy_status="",
                 landmark_total=3, landmarks_picked=3,
                 measurements_defined=2, results_available=2)

    for stage in readiness.STAGE_ORDER:
        check("%s is quiet when everything is done" % stage,
              readiness.stage_hint(stage, **ready) == "",
              readiness.stage_hint(stage, **ready))

    # No scan at all: every stage says the same thing, once.
    nothing = dict(ready, has_mesh=False)
    for stage in readiness.STAGE_ORDER:
        check("%s reports the missing scan" % stage,
              readiness.stage_hint(stage, **nothing) == "No scan selected.",
              readiness.stage_hint(stage, **nothing))


def test_stage_hint_sequence():
    print("\n[stages] the hints follow the workflow forward")
    facts = dict(has_mesh=True, analysed=False, copy_status="",
                 landmark_total=0, landmarks_picked=0,
                 measurements_defined=0, results_available=0)

    check("an unanalysed scan is asked to be analysed",
          readiness.stage_hint(readiness.STAGE_SCAN, **facts)
          == "Analyze the scan before preprocessing.")
    check("and preprocessing says so too, in its own words",
          readiness.stage_hint(readiness.STAGE_PREPROCESS, **facts)
          == "Analyze the scan first.")

    facts["analysed"] = True
    check("once analysed, Scan Setup goes quiet",
          readiness.stage_hint(readiness.STAGE_SCAN, **facts) == "")
    check("landmarks are asked for",
          readiness.stage_hint(readiness.STAGE_LANDMARKS, **facts)
          == "Create or load landmarks before defining measurements.")
    check("measurements ask for landmarks first",
          readiness.stage_hint(readiness.STAGE_MEASUREMENTS, **facts)
          == "Create landmarks first.")
    check("visualization asks for a measurement",
          readiness.stage_hint(readiness.STAGE_VISUALIZATION, **facts)
          == "Define a measurement to visualize.")
    check("export asks for a calculation",
          readiness.stage_hint(readiness.STAGE_EXPORT, **facts)
          == "Calculate measurements before exporting.")

    facts["landmark_total"] = 4
    check("defined but unpicked landmarks are asked to be picked",
          readiness.stage_hint(readiness.STAGE_LANDMARKS, **facts)
          == "Pick each landmark on the scan surface.")

    facts["landmarks_picked"] = 4
    check("picked landmarks end the landmark hint",
          readiness.stage_hint(readiness.STAGE_LANDMARKS, **facts) == "")
    check("and measurements now ask for pairs",
          readiness.stage_hint(readiness.STAGE_MEASUREMENTS, **facts)
          == "Define landmark pairs before calculation.")

    facts["measurements_defined"] = 2
    check("a defined measurement ends that hint",
          readiness.stage_hint(readiness.STAGE_MEASUREMENTS, **facts) == "")
    check("and visualization goes quiet too",
          readiness.stage_hint(readiness.STAGE_VISUALIZATION, **facts) == "")
    check("export still waits for a result",
          readiness.stage_hint(readiness.STAGE_EXPORT, **facts)
          == "Calculate measurements before exporting.")

    facts["results_available"] = 2
    check("and goes quiet once there is one",
          readiness.stage_hint(readiness.STAGE_EXPORT, **facts) == "")


def test_not_ready_mesh_is_named():
    print("\n[stages] a NOT READY measurement mesh is called out")
    facts = dict(has_mesh=True, analysed=True, copy_status='NOT_READY',
                 landmark_total=2, landmarks_picked=2,
                 measurements_defined=1, results_available=0)
    check("preprocessing warns about surface measurement",
          readiness.stage_hint(readiness.STAGE_PREPROCESS, **facts)
          == "Resolve critical mesh issues before exact surface measurement.")
    for state_name in ("READY", "WARNING", ""):
        facts["copy_status"] = state_name
        check("a %r verdict does not raise that warning" % state_name,
              readiness.stage_hint(readiness.STAGE_PREPROCESS, **facts) == "")


def test_repair_stage_hint():
    print("\n[stages] Mesh Repair names the action when the mesh is blocked")
    facts = dict(has_mesh=True, analysed=True, copy_status='NOT_READY',
                 landmark_total=0, landmarks_picked=0,
                 measurements_defined=0, results_available=0)
    check("a blocked mesh is told to repair and re-analyze",
          readiness.stage_hint(readiness.STAGE_REPAIR, **facts)
          == "Repair the blocking defects, then re-analyze.",
          readiness.stage_hint(readiness.STAGE_REPAIR, **facts))
    for state_name in ("READY", "WARNING", ""):
        facts["copy_status"] = state_name
        check("a %r mesh is not nagged to repair" % state_name,
              readiness.stage_hint(readiness.STAGE_REPAIR, **facts) == "",
              readiness.stage_hint(readiness.STAGE_REPAIR, **facts))
    check("Mesh Repair is a stage in its own right",
          readiness.STAGE_REPAIR in readiness.STAGE_ORDER)
    check("named as the panel is named",
          readiness.STAGE_TITLES[readiness.STAGE_REPAIR] == "Mesh Repair")
    order = list(readiness.STAGE_ORDER)
    check("and it sits between preprocessing and alignment",
          order.index(readiness.STAGE_PREPROCESS)
          < order.index(readiness.STAGE_REPAIR)
          < order.index(readiness.STAGE_ALIGNMENT), order)


def test_alignment_never_demands():
    print("\n[stages] alignment is optional by design")
    for analysed in (True, False):
        facts = dict(has_mesh=True, analysed=analysed, copy_status="",
                     landmark_total=0, landmarks_picked=0,
                     measurements_defined=0, results_available=0)
        check("alignment asks for nothing (analysed=%s)" % analysed,
              readiness.stage_hint(readiness.STAGE_ALIGNMENT, **facts) == "",
              readiness.stage_hint(readiness.STAGE_ALIGNMENT, **facts))
    source = open(os.path.join(PACKAGE, "readiness.py")).read()
    check("and the source says why", "Optional by design" in source)


def test_hints_are_short():
    print("\n[stages] messages stay short (sect. 10)")
    seen = set()
    for stage in readiness.STAGE_ORDER:
        for facts in (
            dict(has_mesh=False, analysed=False, copy_status="",
                 landmark_total=0, landmarks_picked=0,
                 measurements_defined=0, results_available=0),
            dict(has_mesh=True, analysed=False, copy_status='NOT_READY',
                 landmark_total=0, landmarks_picked=0,
                 measurements_defined=0, results_available=0),
            dict(has_mesh=True, analysed=True, copy_status='NOT_READY',
                 landmark_total=1, landmarks_picked=0,
                 measurements_defined=0, results_available=0),
        ):
            hint = readiness.stage_hint(stage, **facts)
            if hint:
                seen.add(hint)
    check("several distinct hints exist", len(seen) >= 6, sorted(seen))
    for hint in seen:
        check("%r is one short sentence" % hint,
              len(hint) <= 70 and hint.endswith("."), len(hint))


def test_unknown_stage_is_silent():
    print("\n[stages] an unrecognised stage invents nothing")
    check("an unknown stage returns no hint",
          readiness.stage_hint('NOT_A_STAGE', has_mesh=True) == "")


# ---------------------------------------------------------------------------
# Milestone 3.17 - the surfaces must not carry their own readiness rule
# ---------------------------------------------------------------------------

def test_degenerate_only_mesh_is_not_ready():
    """The reported defect, as a rule: manifold, closed, but degenerate.

    Real measurement mesh, 351,220 triangles, 1 component, 0 boundary edges,
    0 non-manifold edges - and degenerate triangles present. Scan Setup said
    "Topology: Ready" and the headline said READY, because both carried their
    own rule that tested non-manifold edges only.
    """
    print("\n[regression] a degenerate-only mesh is NOT READY everywhere")
    report = {
        "triangle_count": 351220,
        "component_count": 1,
        "boundary_edge_count": 0,
        "nonmanifold_edge_count": 0,
        "degenerate_triangle_count": 3,
        "duplicate_vertex_count": 14,
        "near_coincident_count": 6,
    }

    verdict, reasons = preprocess.classify_ready(report)
    check("the authoritative verdict is NOT READY",
          verdict == preprocess.MEASUREMENT_NOT_READY, verdict)
    check("and it names the degenerate triangles",
          any("degenerate" in reason for reason in reasons), reasons)

    # The readiness headline is fed the same verdict, so it cannot disagree.
    result = readiness.evaluate(**dict(CLEAN, mesh_reasons=reasons))
    check("the readiness line is NOT READY too",
          result["state"] == readiness.NOT_READY, result["state"])
    check("it is blocked", result["blocked"])
    check("the headline names the degenerate triangles",
          "degenerate" in result["headline"], result["headline"])
    check("and points at Mesh Repair",
          result["blockers"][0]["panel"] == "Mesh Repair",
          result["blockers"][0])
    check("the word Ready never appears alone in the headline",
          not result["headline"].startswith("READY"), result["headline"])

    # And the same mesh WITHOUT the degeneracy is ready, so the rule is not
    # simply refusing everything.
    clean_report = dict(report, degenerate_triangle_count=0)
    verdict, reasons = preprocess.classify_ready(clean_report)
    check("the same mesh with no degenerate triangles is READY",
          verdict == preprocess.MEASUREMENT_READY, (verdict, reasons))
    check("and then the readiness line agrees",
          readiness.evaluate(**dict(CLEAN, mesh_reasons=reasons))["state"]
          == readiness.READY)


def test_non_manifold_still_blocks():
    print("\n[regression] routing through the verdict weakened nothing")
    report = {
        "triangle_count": 351220, "component_count": 1,
        "boundary_edge_count": 0, "nonmanifold_edge_count": 5,
        "degenerate_triangle_count": 0,
    }
    verdict, reasons = preprocess.classify_ready(report)
    check("non-manifold is still NOT READY",
          verdict == preprocess.MEASUREMENT_NOT_READY, verdict)
    result = readiness.evaluate(**dict(CLEAN, mesh_reasons=reasons))
    check("and still blocks the readiness line",
          result["state"] == readiness.NOT_READY)
    check("naming non-manifold edges",
          "non-manifold" in result["headline"], result["headline"])


def test_coincident_vertices_do_not_affect_readiness():
    """Sect. 7: report the current policy, do not change it.

    Exact-coincident and near-coincident vertices are DIAGNOSTIC ONLY. They
    are counted and displayed by the topology report, and they are read by
    neither `classify_ready` nor `preflight`, so they change no verdict and
    refuse no solve. Pinned here so the answer to "do they matter?" is a test
    rather than a memory - and so that changing it later is a deliberate act.
    """
    print("\n[policy] coincident vertices are diagnostic only")
    base = {
        "triangle_count": 351220, "component_count": 1,
        "boundary_edge_count": 0, "nonmanifold_edge_count": 0,
        "degenerate_triangle_count": 0,
    }
    clean_verdict, clean_reasons = preprocess.classify_ready(base)

    for field, count in (("duplicate_vertex_count", 14),
                         ("near_coincident_count", 97),
                         ("duplicate_group_count", 7),
                         ("near_coincident_group_count", 3)):
        verdict, reasons = preprocess.classify_ready(dict(base, **{field: count}))
        check("%s=%d does not change the verdict" % (field, count),
              verdict == clean_verdict and reasons == clean_reasons,
              (verdict, reasons))

    gate = preprocess.preflight(dict(base, duplicate_vertex_count=14,
                                     near_coincident_count=97))
    check("nor does it refuse a solve", gate["allowed"], gate["refusals"])
    check("and it raises no warning either",
          not any("coincident" in line.lower() for line in gate["warnings"]),
          gate["warnings"])

    # Asserted against the POLICY functions, not the whole file: the
    # before/after comparison table displays duplicate vertices, and
    # displaying a number is not reading it for a decision.
    for name in ("classify_ready", "preflight"):
        body = function_source("preprocess.py", name)
        for field in ("duplicate_vertex_count", "near_coincident_count"):
            check("%s() never reads %s" % (name, field), field not in body,
                  name)
    table = function_source("preprocess.py", "format_comparison")
    check("but the diagnostics table still shows them to the researcher",
          "_COMPARISON_ROWS" in table)


def test_no_surface_carries_its_own_mesh_rule():
    print("\n[regression] one policy, one place")
    panels_source = open(os.path.join(PACKAGE, "panels.py")).read()

    evaluate_body = function_source("readiness.py", "evaluate")
    check("readiness.evaluate no longer tests non-manifold itself",
          "non_manifold" not in evaluate_body, evaluate_body[:200])
    check("it consumes the verdict it is handed",
          "mesh_reasons" in evaluate_body)

    target_body = function_source("panels.py", "_draw_measurement_target")
    check("the Scan Setup block no longer labels a mesh Ready on its own",
          "non_manifold" not in target_body, target_body[:200])
    check("it reads the verdict instead",
          "state.mesh_verdict(" in target_body)
    check("no panel calls the policy function directly",
          "classify_ready(" not in strip_comments(panels_source),
          "panels must reach it through state.mesh_verdict")

    state_source = open(os.path.join(PACKAGE, "state.py")).read()
    check("state.py has the single accessor",
          "def mesh_verdict(" in state_source)
    verdict_body = function_source("state.py", "mesh_verdict")
    check("which calls the authoritative policy",
          "preprocess.classify_ready(" in verdict_body)
    check("and it is the only call in the module",
          state_source.count("preprocess.classify_ready(") == 1,
          state_source.count("preprocess.classify_ready("))
    check("it reads the cache with peek_current, not peek",
          "meshcache.peek_current(obj)" in verdict_body)
    snapshot_body = function_source("state.py", "readiness_snapshot")
    check("and readiness_snapshot routes through the same accessor",
          "mesh_verdict(context, props, obj)" in snapshot_body)
    check("rather than re-deriving a rule",
          "nonmanifold_edge_count" not in snapshot_body, snapshot_body[:200])


def main():
    print("BSMT Milestone 3.7 - draft and readiness tests")
    for test in (
        test_degenerate_only_mesh_is_not_ready,
        test_non_manifold_still_blocks,
        test_coincident_vertices_do_not_affect_readiness,
        test_no_surface_carries_its_own_mesh_rule,
        test_stage_hints,
        test_stage_hint_sequence,
        test_not_ready_mesh_is_named,
        test_repair_stage_hint,
        test_alignment_never_demands,
        test_hints_are_short,
        test_unknown_stage_is_silent,
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
