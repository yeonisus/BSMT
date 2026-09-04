"""Compact "can I measure yet?" summary (Milestone 3.7). Pure python - no bpy.

The Landmark Manager, the Mesh Repair panel and the Scan Preprocessing report
each already answer part of the question, in detail. What they do not answer is
the one a researcher asks before pressing Calculate: *is this scan ready at
all, and if not, what is the single next thing to fix?*

This module is that one line. It deliberately does NOT restate the diagnostics
it summarises (sect. 13): it names the blocker and points at the panel that
explains it. Everything it reports is already computed elsewhere, so producing
it costs nothing and it can never disagree with the detailed report.

Nothing here decides whether a solve may run. That gate lives in
`preprocess.preflight`, which this module reads from but never replaces.
"""

READY = 'READY'
NOT_READY = 'NOT_READY'
UNKNOWN = 'UNKNOWN'

#: Reason codes, in the order they are reported. The first blocker is the one
#: shown in the compact line; the rest are available for the expanded view.
REASON_NO_MESH = 'NO_MESH'
REASON_NOT_ANALYSED = 'NOT_ANALYSED'
REASON_MESH_NOT_READY = 'MESH_NOT_READY'
REASON_DENSE = 'DENSE'
REASON_NON_UNIFORM_SCALE = 'NON_UNIFORM_SCALE'
REASON_NO_LANDMARKS = 'NO_LANDMARKS'
REASON_LANDMARKS_UNPICKED = 'LANDMARKS_UNPICKED'
REASON_LANDMARKS_STALE = 'LANDMARKS_STALE'
REASON_NO_MEASUREMENTS = 'NO_MEASUREMENTS'

#: Which panel explains a reason in full. Kept here so the wording of the
#: pointer and the wording of the panel title cannot drift apart.
PANEL_FOR_REASON = {
    REASON_NO_MESH: "Scan Setup",
    REASON_NOT_ANALYSED: "Scan Setup",
    REASON_MESH_NOT_READY: "Mesh Repair",
    REASON_DENSE: "Scan Preprocessing",
    REASON_NON_UNIFORM_SCALE: "Alignment",
    REASON_NO_LANDMARKS: "Landmark Manager",
    REASON_LANDMARKS_UNPICKED: "Landmark Manager",
    REASON_LANDMARKS_STALE: "Landmark Manager",
    REASON_NO_MEASUREMENTS: "Measurement Manager",
}


# ---------------------------------------------------------------------------
# per-stage guidance (Milestone 3.16)
# ---------------------------------------------------------------------------
#
# The sidebar is ordered as the research workflow runs, and each stage says
# in one short line what is missing before it can be useful. Deliberately NOT
# a wizard: nothing is hidden, nothing is stepped through, and a stage with
# nothing to say says nothing at all rather than nagging.
#
# This is guidance, never enforcement. What may actually RUN is decided by
# each operator's own poll() and by `preprocess.preflight`, which this module
# does not touch (sect. 11, sect. 12).

STAGE_SCAN = 'SCAN'
STAGE_PREPROCESS = 'PREPROCESS'
STAGE_REPAIR = 'REPAIR'
STAGE_ALIGNMENT = 'ALIGNMENT'
STAGE_LANDMARKS = 'LANDMARKS'
STAGE_MEASUREMENTS = 'MEASUREMENTS'
STAGE_VISUALIZATION = 'VISUALIZATION'
STAGE_EXPORT = 'EXPORT'

def landmark_emphasis_visible(ui_stage):
    """Whether the SELECTED-landmark ring belongs on screen at this stage.

    It does while Landmark Manager is what the researcher is working in, and
    not afterwards: once they are building measurements, computing paths or
    exporting, a ring around one landmark is a leftover from an earlier stage
    that marks it out for a reason nobody looking at the viewport could
    reconstruct.

    Lives here because the stage vocabulary lives here, and because this
    module imports nothing - which lets the overlay ask the question without
    reaching for `state`, whose own import of `overlay` would close a cycle.
    """
    return str(ui_stage) == STAGE_LANDMARKS


#: Stages in the order the sidebar shows them. The panel order is derived
#: from this, so the two cannot drift apart.
STAGE_ORDER = (
    STAGE_SCAN,
    STAGE_PREPROCESS,
    STAGE_REPAIR,
    STAGE_ALIGNMENT,
    STAGE_LANDMARKS,
    STAGE_MEASUREMENTS,
    STAGE_VISUALIZATION,
    STAGE_EXPORT,
)

STAGE_TITLES = {
    STAGE_SCAN: "Scan Setup",
    STAGE_PREPROCESS: "Scan Preprocessing",
    STAGE_REPAIR: "Mesh Repair",
    STAGE_ALIGNMENT: "Alignment",
    STAGE_LANDMARKS: "Landmark Manager",
    STAGE_MEASUREMENTS: "Measurement Manager",
    STAGE_VISUALIZATION: "Measurement Visualization",
    STAGE_EXPORT: "Results and Export",
}


def stage_hint(stage, has_mesh=True, analysed=True, copy_status="",
               landmark_total=0, landmarks_picked=0, measurements_defined=0,
               results_available=0, non_manifold=0):
    """One short line telling the researcher what this stage still needs.

    Returns "" when the stage has nothing useful to say - which is the normal
    case once the workflow is under way. Pure: every argument is a number or
    a flag the add-on already holds, so this is safe from a panel draw and is
    testable without Blender.
    """
    if not has_mesh:
        # Every stage depends on there being a scan at all, and saying so
        # once per panel is less confusing than each stage inventing its own
        # way to be empty.
        return "No scan selected."

    if stage == STAGE_SCAN:
        if not analysed:
            return "Analyze the scan before preprocessing."
        return ""

    if stage == STAGE_PREPROCESS:
        if not analysed:
            return "Analyze the scan first."
        if copy_status == 'NOT_READY':
            return "Resolve critical mesh issues before exact surface measurement."
        return ""

    if stage == STAGE_REPAIR:
        # Repair is where a blocking defect is ACTED on, so this names the
        # action; the preprocessing stage names the consequence. Both read
        # the same verdict - neither decides anything.
        if copy_status == 'NOT_READY':
            return "Repair the blocking defects, then re-analyze."
        return ""

    if stage == STAGE_ALIGNMENT:
        # Optional by design: a study that does not need a common anatomical
        # frame skips it entirely, so this stage never demands anything.
        return ""

    if stage == STAGE_LANDMARKS:
        if landmark_total <= 0:
            return "Create or load landmarks before defining measurements."
        if landmarks_picked <= 0:
            return "Pick each landmark on the scan surface."
        return ""

    if stage == STAGE_MEASUREMENTS:
        if landmark_total <= 0:
            return "Create landmarks first."
        if measurements_defined <= 0:
            return "Define landmark pairs before calculation."
        return ""

    if stage == STAGE_VISUALIZATION:
        if measurements_defined <= 0:
            return "Define a measurement to visualize."
        return ""

    if stage == STAGE_EXPORT:
        if results_available <= 0:
            return "Calculate measurements before exporting."
        return ""

    return ""


def _plural(count, word):
    return "%d %s%s" % (count, word, "" if count == 1 else "s")


def evaluate(mesh_name="", triangle_count=0, mesh_reasons=(), analysed=True,
             dense_threshold=0, guard_dense=True, scale_uniform=True,
             landmark_total=0, landmarks_unpicked=0, landmarks_stale=0,
             measurements_defined=0):
    """The readiness of the current measurement target.

    Every argument is a number, a flag or a list of strings already held by
    the add-on, so this function reads no geometry and can be called from a
    panel draw.

    `mesh_reasons` is the blocking-reason list from
    ``preprocess.classify_ready`` - the ONE place that decides whether a mesh
    is fit to measure on. This module does not re-derive that verdict and
    must never start to: it used to test ``non_manifold > 0`` itself, which
    silently disagreed with the policy the rest of BSMT applies and reported
    READY on a mesh carrying degenerate triangles.

    Returns a dict with `state`, `headline`, `reasons` (each a dict with
    `code`, `text` and `panel`), `blocked` and `icon`.
    """
    reasons = []

    def add(code, text, blocking):
        reasons.append({
            "code": code,
            "text": text,
            "panel": PANEL_FOR_REASON.get(code, ""),
            "blocking": bool(blocking),
        })

    if not mesh_name:
        add(REASON_NO_MESH, "no measurement mesh selected", True)
    elif not analysed:
        # Unknown is honestly different from not-ready: nothing has been
        # measured about this mesh yet, so claiming either answer would be
        # inventing one.
        add(REASON_NOT_ANALYSED, "topology not analyzed yet", False)

    # The mesh verdict, as decided elsewhere. Passed through verbatim so the
    # readiness line, the Scan Setup topology line and the preprocessing
    # report can never disagree about the same mesh.
    for text in mesh_reasons:
        add(REASON_MESH_NOT_READY, str(text), True)

    if (guard_dense and dense_threshold and triangle_count
            and triangle_count > dense_threshold):
        add(REASON_DENSE,
            "{:,} triangles is over the {:,} safety threshold".format(
                int(triangle_count), int(dense_threshold)), True)

    if not scale_uniform:
        add(REASON_NON_UNIFORM_SCALE,
            "the object has non-uniform scale", True)

    if landmark_total <= 0:
        add(REASON_NO_LANDMARKS, "no landmarks defined", False)
    else:
        if landmarks_stale > 0:
            add(REASON_LANDMARKS_STALE,
                "%s" % _plural(landmarks_stale, "stale landmark"), True)
        if landmarks_unpicked > 0:
            add(REASON_LANDMARKS_UNPICKED,
                "%s not picked" % _plural(landmarks_unpicked, "landmark"),
                False)

    if measurements_defined <= 0:
        add(REASON_NO_MEASUREMENTS, "no measurements defined", False)

    blocking = [entry for entry in reasons if entry["blocking"]]
    unknown = any(entry["code"] == REASON_NOT_ANALYSED for entry in reasons)

    if blocking:
        state = NOT_READY
        headline = "NOT READY: %s" % blocking[0]["text"]
        icon = 'ERROR'
    elif unknown:
        state = UNKNOWN
        headline = "Topology not analyzed yet"
        icon = 'QUESTION'
    elif reasons:
        # Nothing blocks measurement; what is left is work still to do.
        state = READY
        headline = "READY: %s" % reasons[0]["text"]
        icon = 'INFO'
    else:
        state = READY
        headline = "READY FOR MEASUREMENT"
        icon = 'CHECKMARK'

    return {
        "state": state,
        "ready": state == READY,
        "blocked": bool(blocking),
        "headline": headline,
        "icon": icon,
        "reasons": reasons,
        "blockers": blocking,
    }


def lines(result, limit=4):
    """The expanded form: the headline plus each remaining reason."""
    out = [result["headline"]]
    for entry in result["reasons"][:limit]:
        pointer = (" - see %s" % entry["panel"]) if entry["panel"] else ""
        out.append("  %s%s" % (entry["text"], pointer))
    return out
