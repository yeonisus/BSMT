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
REASON_NON_MANIFOLD = 'NON_MANIFOLD'
REASON_DENSE = 'DENSE'
REASON_NON_UNIFORM_SCALE = 'NON_UNIFORM_SCALE'
REASON_NO_LANDMARKS = 'NO_LANDMARKS'
REASON_LANDMARKS_UNPICKED = 'LANDMARKS_UNPICKED'
REASON_LANDMARKS_STALE = 'LANDMARKS_STALE'
REASON_NO_MEASUREMENTS = 'NO_MEASUREMENTS'

#: Which panel explains a reason in full. Kept here so the wording of the
#: pointer and the wording of the panel title cannot drift apart.
PANEL_FOR_REASON = {
    REASON_NO_MESH: "Scan Preprocessing",
    REASON_NOT_ANALYSED: "Mesh Repair",
    REASON_NON_MANIFOLD: "Mesh Repair",
    REASON_DENSE: "Scan Preprocessing",
    REASON_NON_UNIFORM_SCALE: "Alignment",
    REASON_NO_LANDMARKS: "Landmark Manager",
    REASON_LANDMARKS_UNPICKED: "Landmark Manager",
    REASON_LANDMARKS_STALE: "Landmark Manager",
    REASON_NO_MEASUREMENTS: "Measurement Manager",
}


def _plural(count, word):
    return "%d %s%s" % (count, word, "" if count == 1 else "s")


def evaluate(mesh_name="", triangle_count=0, non_manifold=0, analysed=True,
             dense_threshold=0, guard_dense=True, scale_uniform=True,
             landmark_total=0, landmarks_unpicked=0, landmarks_stale=0,
             measurements_defined=0):
    """The readiness of the current measurement target.

    Every argument is a number or a flag already held by the add-on, so this
    function reads no geometry and can be called from a panel draw.

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

    if non_manifold > 0:
        add(REASON_NON_MANIFOLD,
            "%s - the exact solver is refused"
            % _plural(non_manifold, "non-manifold edge"), True)

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
