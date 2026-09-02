"""User-defined measurement definitions (Milestone 3.1). Pure python - no bpy.

A measurement definition connects two NAMED LANDMARKS chosen by the
researcher. The workflow is protocol-driven, not combinatorial: BSMT never
generates pairwise combinations, and there is deliberately no "calculate every
pair" path anywhere in the code (sect. 20 of the milestone brief). For 50
landmarks BSMT computes the definitions the researcher wrote, not 1,225 pairs.

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


def needs_straight(measurement_type):
    return measurement_type in (TYPE_STRAIGHT, TYPE_BOTH)


def needs_surface(measurement_type):
    return measurement_type in (TYPE_SURFACE, TYPE_BOTH)


# --- status (sect. 6) ------------------------------------------------------
#
# A measurement's status is NOT a landmark's status. It answers "can this
# measurement be calculated, and is its stored result still trustworthy",
# which depends on the landmarks but is a separate question.

STATUS_NOT_READY = 'NOT_READY'
STATUS_READY = 'READY'
STATUS_CALCULATING = 'CALCULATING'
STATUS_VALID = 'VALID'
STATUS_STALE = 'STALE'
STATUS_INVALID_REFERENCE = 'INVALID_REFERENCE'
STATUS_FAILED = 'FAILED'

STATUS_ITEMS = (
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
    STATUS_CALCULATING,
    STATUS_STALE,
    STATUS_FAILED,
    STATUS_INVALID_REFERENCE,
)

STATUS_ICONS = {
    STATUS_NOT_READY: 'BLANK1',
    STATUS_READY: 'PLAY',
    STATUS_CALCULATING: 'TIME',
    STATUS_VALID: 'CHECKMARK',
    STATUS_STALE: 'FILE_REFRESH',
    STATUS_INVALID_REFERENCE: 'CANCEL',
    STATUS_FAILED: 'ERROR',
}

STATUS_SHORT = {
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


def default_name(source_label, target_label):
    """A starting name the researcher is expected to edit."""
    return "%s to %s" % (source_label or "?", target_label or "?")


def resolve(landmark_by_stable_id, stable_id):
    """The landmark for a stable id, or None. Never falls back to another.

    `landmark_by_stable_id` is any callable id -> landmark-or-None.
    """
    if not stable_id:
        return None
    return landmark_by_stable_id(int(stable_id))


def readiness(source, target, source_stable_id=0, target_stable_id=0):
    """Whether a measurement can be calculated. Returns (status, detail).

    Depends only on the landmarks' own statuses, so it can be evaluated
    cheaply and without touching geometry. It says nothing about whether a
    previously stored result is still valid - that is decided separately, by
    the dependency fingerprint recorded at calculation time.
    """
    missing = []
    if source is None:
        missing.append("source (landmark id %s)" % (source_stable_id or "unset"))
    if target is None:
        missing.append("target (landmark id %s)" % (target_stable_id or "unset"))
    if missing:
        return STATUS_INVALID_REFERENCE, "missing " + " and ".join(missing)

    if source is target:
        # Not an error: A->A is a legitimate degenerate measurement whose
        # answer is exactly zero. It is reported as ready so the calculation
        # path, not this function, produces that zero.
        pass

    problems = []
    for label, landmark in (("source", source), ("target", target)):
        status = getattr(landmark, "status", LANDMARK_NOT_PICKED)
        name = getattr(landmark, "name", "?")
        if status == LANDMARK_VALID:
            continue
        if status == LANDMARK_NOT_PICKED:
            problems.append(("NOT_READY", "%s '%s' is not picked" % (label, name)))
        elif status == LANDMARK_NEEDS_REFRESH:
            problems.append((
                "NOT_READY",
                "%s '%s' needs refresh - run Validate All Landmarks" % (label, name),
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
    """What `Calculate All Defined` is about to do (sect. 10).

    Counts ENABLED definitions only. Disabled definitions are not calculated,
    and undefined landmark pairs do not exist as far as BSMT is concerned.
    """
    enabled = [item for item in definitions if getattr(item, "enabled", True)]
    surface = sum(1 for item in enabled if needs_surface(item.measurement_type))
    straight_only = sum(
        1 for item in enabled if item.measurement_type == TYPE_STRAIGHT
    )
    return {
        "total": len(list(definitions)),
        "enabled": len(enabled),
        "disabled": len(list(definitions)) - len(enabled),
        "surface": surface,
        "straight_only": straight_only,
        "summary": "%d enabled measurement%s, %d require surface distance, "
                   "%d straight-only"
                   % (len(enabled), "" if len(enabled) == 1 else "s",
                      surface, straight_only),
    }


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
