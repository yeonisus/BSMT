"""User-defined measurement definitions (Milestone 3.1). Pure python - no bpy.

A measurement definition connects two NAMED LANDMARKS chosen by the
researcher. The workflow is protocol-driven, not combinatorial: BSMT never
generates pairwise combinations, and there is deliberately no "calculate every
pair" path anywhere in the code (sect. 20 of the milestone brief). For 50
landmarks BSMT computes the definitions the researcher wrote, not 1,225 pairs.

A row that has been added but not finished is a DRAFT (`is_draft`), not a
measurement: no name is generated for it, it is skipped by Calculate All, and
it never appears in Measurement Results. A measurement exists only once it has
a From landmark, a To landmark, and they are different.

    BSMT_Measurement
        stable_id            monotonic int, unique per scene, never reused
        protocol_id          "M01" - template facing
        name                 researcher's label
        source_stable_id     -> BSMT_Landmark.stable_id   AUTHORITATIVE
        target_stable_id     -> BSMT_Landmark.stable_id   AUTHORITATIVE
        source_protocol_id / source_name   cached, for templates + diagnostics
        measurement_type     STRAIGHT | SURFACE | BOTH
        enabled, notes
        + result and provenance fields

Why references are stable ids, and why the UI picker cannot be trusted
----------------------------------------------------------------------
References are stored as landmark **stable ids**, never as list indices, so
deleting or reordering landmarks cannot silently repoint a measurement.

That is not a theoretical concern. Measured in Blender 4.5.13: a dynamic
``EnumProperty`` whose items are built from the landmark collection remaps by
*index* when the collection changes. With landmarks 1..50 and the picker set
to "42", deleting landmark 42 makes the property read back as **"43"** - a
different, unrelated landmark, with no error. The picker is therefore
write-only: it exists to let a human choose, its update callback writes the
authoritative integer, and nothing in this module or the calculation path ever
reads it back. All resolution goes through ``resolve()`` on the integer.
"""

# --- measurement type ------------------------------------------------------

TYPE_STRAIGHT = 'STRAIGHT'
TYPE_SURFACE = 'SURFACE'
TYPE_BOTH = 'BOTH'

TYPE_ITEMS = (
    (TYPE_STRAIGHT, "Straight", "Straight-line (Euclidean) distance only"),
    (TYPE_SURFACE, "Surface", "Exact geodesic surface distance only"),
    (TYPE_BOTH, "Both", "Both the straight and the exact surface distance"),
)

TYPES = (TYPE_STRAIGHT, TYPE_SURFACE, TYPE_BOTH)

#: The human label for a type. The raw identifier ("BOTH") is an internal
#: value and must never reach a panel.
TYPE_LABELS = {identifier: label for identifier, label, _tip in TYPE_ITEMS}


def needs_straight(measurement_type):
    return measurement_type in (TYPE_STRAIGHT, TYPE_BOTH)


def needs_surface(measurement_type):
    return measurement_type in (TYPE_SURFACE, TYPE_BOTH)


# --- status (sect. 6) ------------------------------------------------------
#
# A measurement's status is NOT a landmark's status. It answers "can this
# measurement be calculated, and is its stored result still trustworthy",
# which depends on the landmarks but is a separate question.

STATUS_DRAFT = 'DRAFT'
STATUS_NOT_READY = 'NOT_READY'
STATUS_READY = 'READY'
STATUS_CALCULATING = 'CALCULATING'
STATUS_VALID = 'VALID'
STATUS_STALE = 'STALE'
STATUS_INVALID_REFERENCE = 'INVALID_REFERENCE'
STATUS_FAILED = 'FAILED'

STATUS_ITEMS = (
    (STATUS_DRAFT, "Draft",
     "Not defined yet: pick a From and a To landmark, and they must differ"),
    (STATUS_NOT_READY, "Not Ready",
     "A referenced landmark has no confirmed surface location yet"),
    (STATUS_READY, "Ready", "Both landmarks are valid; not calculated yet"),
    (STATUS_CALCULATING, "Calculating", "A calculation is in progress"),
    (STATUS_VALID, "Valid", "Calculated, and every dependency is unchanged"),
    (STATUS_STALE, "Stale",
     "Calculated, but a landmark, the geometry or the metric has changed since"),
    (STATUS_INVALID_REFERENCE, "Invalid Reference",
     "A referenced landmark no longer exists. BSMT never substitutes another"),
    (STATUS_FAILED, "Failed", "The calculation could not produce a result"),
)

STATUS_ORDER = (
    STATUS_VALID,
    STATUS_READY,
    STATUS_NOT_READY,
    STATUS_DRAFT,
    STATUS_CALCULATING,
    STATUS_STALE,
    STATUS_FAILED,
    STATUS_INVALID_REFERENCE,
)

STATUS_ICONS = {
    STATUS_DRAFT: 'GREASEPENCIL',
    STATUS_NOT_READY: 'BLANK1',
    STATUS_READY: 'PLAY',
    STATUS_CALCULATING: 'TIME',
    STATUS_VALID: 'CHECKMARK',
    STATUS_STALE: 'FILE_REFRESH',
    STATUS_INVALID_REFERENCE: 'CANCEL',
    STATUS_FAILED: 'ERROR',
}

STATUS_SHORT = {
    STATUS_DRAFT: "DRAFT",
    STATUS_NOT_READY: "NOT READY",
    STATUS_READY: "READY",
    STATUS_CALCULATING: "CALC",
    STATUS_VALID: "VALID",
    STATUS_STALE: "STALE",
    STATUS_INVALID_REFERENCE: "NO REF",
    STATUS_FAILED: "FAILED",
}

#: Landmark statuses, mirrored here so this module stays free of any import
#: that could pull in bpy. Kept in sync by a test.
LANDMARK_VALID = 'VALID'
LANDMARK_NOT_PICKED = 'NOT_PICKED'
LANDMARK_NEEDS_REFRESH = 'NEEDS_REFRESH'
LANDMARK_STALE = 'STALE'
LANDMARK_INVALID = 'INVALID'


class MeasurementError(Exception):
    """A measurement cannot be defined or calculated as asked."""


def next_protocol_id(existing_ids):
    """The next free "M###" id, preserving ids already in use."""
    highest = 0
    for value in existing_ids:
        text = str(value).strip()
        if len(text) > 1 and text[0] in "Mm" and text[1:].isdigit():
            highest = max(highest, int(text[1:]))
    return "M%02d" % (highest + 1)


def is_draft(source_stable_id, target_stable_id, source_named="",
             target_named=""):
    """True while a row is still being written and is not yet a measurement.

    A draft is a row the researcher has added but not FINISHED: one or both
    endpoints never chosen, or both set to the same landmark. Drafts are
    deliberately not measurements - they are skipped by Calculate All, hidden
    from Measurement Results, and left out of the defined count - so that
    adding a row and changing your mind cannot leave a phantom entry in the
    record.

    `source_named` / `target_named` are the cached protocol id or name that
    every chosen endpoint carries. They are what separates "never written"
    from "written, and the landmark has since gone": a measurement loaded
    from a template whose landmark is missing has an unresolved id 0 but a
    cached name, and it must stay a REAL measurement reporting a broken
    reference. Hiding it as a draft would quietly drop it from the batch and
    from the results - exactly the silent substitution BSMT refuses to make.
    """
    source = int(source_stable_id or 0)
    target = int(target_stable_id or 0)
    if source and target:
        return source == target
    if not source and str(source_named or "").strip():
        return False
    if not target and str(target_named or "").strip():
        return False
    return True


def is_defined(item):
    """True when `item` is a real measurement rather than a draft."""
    return not is_draft(
        getattr(item, "source_stable_id", 0),
        getattr(item, "target_stable_id", 0),
        (getattr(item, "source_name", "")
         or getattr(item, "source_protocol_id", "")),
        (getattr(item, "target_name", "")
         or getattr(item, "target_protocol_id", "")),
    )


def defined(definitions):
    """Only the real measurements, in order. Drafts are left out."""
    return [item for item in definitions if is_defined(item)]


def default_name(source_label, target_label):
    """The automatic name for a measurement, or "" while it is a draft.

    An incomplete definition gets no name at all. Naming it "? to ?" or
    "None to None" would put a meaningless label in the list, in a template,
    and eventually in an exported record.
    """
    if not source_label or not target_label:
        return ""
    return "%s to %s" % (source_label, target_label)


def resolve(landmark_by_stable_id, stable_id):
    """The landmark for a stable id, or None. Never falls back to another.

    `landmark_by_stable_id` is any callable id -> landmark-or-None.
    """
    if not stable_id:
        return None
    return landmark_by_stable_id(int(stable_id))


def readiness(source, target, source_stable_id=0, target_stable_id=0,
              source_named="", target_named=""):
    """Whether a measurement can be calculated. Returns (status, detail).

    Depends only on the landmarks' own statuses, so it can be evaluated
    cheaply and without touching geometry. It says nothing about whether a
    previously stored result is still valid - that is decided separately, by
    the dependency fingerprint recorded at calculation time.
    """
    # A draft is reported before anything else, because "you have not chosen
    # the landmarks yet" is a different and more useful thing to say than
    # "a reference could not be resolved".
    if is_draft(source_stable_id, target_stable_id, source_named,
                target_named):
        if not int(source_stable_id or 0) and not int(target_stable_id or 0):
            return STATUS_DRAFT, "choose a From and a To landmark"
        if not int(source_stable_id or 0):
            return STATUS_DRAFT, "choose a From landmark"
        if not int(target_stable_id or 0):
            return STATUS_DRAFT, "choose a To landmark"
        return STATUS_DRAFT, "From and To are the same landmark"

    missing = []
    if source is None:
        missing.append("From (%s)" % (source_named
                                      or "landmark id %s" % source_stable_id))
    if target is None:
        missing.append("To (%s)" % (target_named
                                    or "landmark id %s" % target_stable_id))
    if missing:
        return STATUS_INVALID_REFERENCE, "missing " + " and ".join(missing)

    problems = []
    for label, landmark in (("From", source), ("To", target)):
        status = getattr(landmark, "status", LANDMARK_NOT_PICKED)
        name = getattr(landmark, "name", "?")
        if status == LANDMARK_VALID:
            continue
        if status == LANDMARK_NOT_PICKED:
            problems.append(("NOT_READY", "%s '%s' is not picked" % (label, name)))
        elif status == LANDMARK_NEEDS_REFRESH:
            problems.append((
                "NOT_READY",
                "%s '%s' needs refreshing - run Validate Landmarks" % (label, name),
            ))
        elif status == LANDMARK_STALE:
            problems.append(("STALE", "%s '%s' is stale" % (label, name)))
        else:
            problems.append(("STALE", "%s '%s' is %s" % (label, name, status)))

    if not problems:
        return STATUS_READY, ""
    # A stale landmark outranks an unpicked one: it is the more serious state
    # and the one that must not be quietly measured.
    if any(kind == "STALE" for kind, _ in problems):
        return STATUS_STALE, "; ".join(
            detail for kind, detail in problems if kind == "STALE"
        )
    return STATUS_NOT_READY, "; ".join(detail for _kind, detail in problems)


def summarise(statuses):
    """Counts per status and a one-line summary."""
    counts = {status: 0 for status in STATUS_ORDER}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    labels = {
        STATUS_DRAFT: "draft",
        STATUS_VALID: "valid",
        STATUS_READY: "ready",
        STATUS_NOT_READY: "not ready",
        STATUS_CALCULATING: "calculating",
        STATUS_STALE: "stale",
        STATUS_FAILED: "failed",
        STATUS_INVALID_REFERENCE: "invalid reference",
    }
    parts = [
        "%d %s" % (counts[status], labels[status])
        for status in STATUS_ORDER if counts.get(status)
    ]
    return counts, ", ".join(parts) if parts else "no measurements"


def batch_plan(definitions):
    """What `Calculate All` is about to do (sect. 10).

    Counts ENABLED, fully DEFINED measurements only. Drafts are not
    measurements yet and disabled rows were switched off on purpose; neither
    is calculated. Undefined landmark pairs do not exist as far as BSMT is
    concerned - there is still no all-pairs path anywhere.
    """
    rows = list(definitions)
    real = defined(rows)
    drafts = len(rows) - len(real)
    enabled = [item for item in real if getattr(item, "enabled", True)]
    surface = sum(1 for item in enabled if needs_surface(item.measurement_type))
    straight_only = sum(
        1 for item in enabled if item.measurement_type == TYPE_STRAIGHT
    )
    if enabled:
        summary = ("%d measurement%s will be calculated: %d with surface "
                   "distance, %d straight only"
                   % (len(enabled), "" if len(enabled) == 1 else "s",
                      surface, straight_only))
    else:
        summary = "Nothing to calculate"
    return {
        "total": len(rows),
        "defined": len(real),
        "drafts": drafts,
        "enabled": len(enabled),
        "disabled": len(real) - len(enabled),
        "surface": surface,
        "straight_only": straight_only,
        "summary": summary,
    }


def batch_report(statuses, enabled_count, disabled_count):
    """Explicit per-outcome counts for a finished batch.

    Returns a list of lines. Spelled out rather than compressed, because
    "3 enabled / 3 calculated / 3 valid / 0 not ready / 0 failed" answers the
    question a researcher actually has - did everything I asked for run, and
    did any of it quietly not happen.
    """
    counts, _summary = summarise(statuses)
    calculated = counts.get(STATUS_VALID, 0) + counts.get(STATUS_FAILED, 0)
    lines = [
        "%d enabled" % enabled_count,
        "%d calculated" % calculated,
        "%d valid" % counts.get(STATUS_VALID, 0),
        "%d not ready" % counts.get(STATUS_NOT_READY, 0),
        "%d failed" % counts.get(STATUS_FAILED, 0),
    ]
    if counts.get(STATUS_DRAFT):
        lines.append("%d draft, skipped" % counts[STATUS_DRAFT])
    if counts.get(STATUS_STALE):
        lines.append("%d stale" % counts[STATUS_STALE])
    if counts.get(STATUS_INVALID_REFERENCE):
        lines.append("%d invalid reference" % counts[STATUS_INVALID_REFERENCE])
    if disabled_count:
        lines.append("%d disabled, skipped" % disabled_count)
    return lines


def one_line_result(protocol_id, source_label, target_label, straight_mm,
                    straight_valid, surface_mm, surface_valid, status):
    """Compact single-line row: "M01 P01>P02 | 27.99 / 28.00 | VALID"."""
    numbers = format_result(straight_mm, straight_valid, surface_mm,
                            surface_valid).replace(" mm", "")
    return "%s %s\u2192%s | %s | %s" % (
        protocol_id, source_label or "?", target_label or "?",
        numbers, STATUS_SHORT.get(status, status),
    )


def format_result(straight_mm, straight_valid, surface_mm, surface_valid,
                  decimals=2):
    """Compact "straight / surface mm" for a list row (sect. 11)."""
    fmt = "%%.%df" % decimals
    left = (fmt % straight_mm) if straight_valid else "—"
    right = (fmt % surface_mm) if surface_valid else "—"
    return "%s / %s mm" % (left, right)


def ratio(straight_mm, surface_mm):
    """surface / straight, or 0.0 when it is not defined."""
    if straight_mm and straight_mm > 0.0:
        return float(surface_mm) / float(straight_mm)
    return 0.0
