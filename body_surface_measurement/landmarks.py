"""Named research landmark logic (Milestone 3.0). Pure numpy - no bpy.

The Landmark Manager is a separate layer above the Phase 1 A/B workflow, which
is unchanged and stays available for quick ad-hoc measurement. A landmark adds
identity (a stable id, a name, notes) to a surface location; it does **not**
add a second way of representing that location.

    BSMT_Landmark
        stable_id      monotonic integer, unique per scene, never reused
        protocol_id    human/protocol facing, e.g. "L01"
        name           researcher's anatomical name, arbitrary
        display_name   optional override for the list
        notes          optional
        surface_point  a nested BSMT_SurfacePoint - the SAME PropertyGroup
                       the A/B workflow uses

Composition, not duplication: the canonical attachment is still
``triangle_index + barycentric coordinates`` against the canonical triangle
array, and every piece of mathematics on it lives in ``surface_point.py``.
This module adds only the things that are about *landmarks* rather than about
*surface points*: status classification, batched world reconstruction for many
landmarks at once, and name/id rules.

PROJECT_SPEC.md sect. 16 requires exactly one implementation of barycentric
reconstruction, validity checking, transform refresh and stale detection.
``stale_reason()`` below is that one implementation of the stale rule, and the
A/B validate operator calls it too.
"""

import re

import numpy as np

# --- status codes (sect. 8) -------------------------------------------------
#
#     NOT_PICKED    no surface location captured yet
#     VALID         verified against the current canonical mesh
#     NEEDS_REFRESH captured, but not currently verifiable - the canonical
#                   mesh is not cached, which is this project's established
#                   "geometry may have changed" signal (Milestone 2.1a).
#                   Validate All resolves it to VALID or STALE.
#     STALE         verified against a canonical mesh that no longer matches.
#                   NEVER silently re-projected.
#     INVALID       structurally unusable: missing object, triangle index out
#                   of range, or barycentric coordinates off their triangle
#
# The sect. 8 transition on a geometry edit is therefore
#     VALID -> NEEDS_REFRESH -> (Validate All) -> STALE
# and a rigid transform changes none of them.

STATUS_NOT_PICKED = 'NOT_PICKED'
STATUS_VALID = 'VALID'
STATUS_NEEDS_REFRESH = 'NEEDS_REFRESH'
STATUS_STALE = 'STALE'
STATUS_INVALID = 'INVALID'

#: Order used for summary counts, worst last.
STATUS_ORDER = (
    STATUS_VALID,
    STATUS_NOT_PICKED,
    STATUS_NEEDS_REFRESH,
    STATUS_STALE,
    STATUS_INVALID,
)

STATUS_ITEMS = (
    (STATUS_NOT_PICKED, "Not Picked", "No surface location captured yet"),
    (STATUS_VALID, "Valid", "Verified against the current canonical mesh"),
    (STATUS_NEEDS_REFRESH, "Needs Refresh",
     "Captured, but not verified against the current canonical mesh"),
    (STATUS_STALE, "Stale",
     "The mesh changed since this landmark was picked. It is never "
     "re-projected automatically"),
    (STATUS_INVALID, "Invalid", "Structurally unusable"),
)

#: Compact status text, for the landmark list and the viewport label. One
#: definition, so the panel and the overlay can never disagree about what a
#: status is called.
STATUS_SHORT = {
    STATUS_NOT_PICKED: "NOT PICKED",
    STATUS_VALID: "VALID",
    STATUS_NEEDS_REFRESH: "NEEDS REFRESH",
    STATUS_STALE: "STALE",
    STATUS_INVALID: "INVALID",
}

#: Icon per status, for the UIList.
STATUS_ICONS = {
    STATUS_NOT_PICKED: 'BLANK1',
    STATUS_VALID: 'CHECKMARK',
    STATUS_NEEDS_REFRESH: 'FILE_REFRESH',
    STATUS_STALE: 'ERROR',
    STATUS_INVALID: 'CANCEL',
}

MAX_NAME_LENGTH = 63
_PROTOCOL_ID_PATTERN = re.compile(r"^L(\d+)$")


class LandmarkError(Exception):
    """A landmark operation cannot be performed as asked."""


# ---------------------------------------------------------------------------
# names and identifiers
# ---------------------------------------------------------------------------

def clean_name(name):
    """Normalise a user-entered name. Raises LandmarkError if unusable.

    Only whitespace is normalised. The researcher's anatomical vocabulary is
    theirs: no anatomical name is ever hard-coded, pattern-matched or
    rewritten by BSMT (sect. 2).
    """
    cleaned = " ".join(str(name or "").split())
    if not cleaned:
        raise LandmarkError("a landmark name cannot be empty")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise LandmarkError(
            "landmark name is %d characters; the limit is %d"
            % (len(cleaned), MAX_NAME_LENGTH)
        )
    return cleaned


def name_exists(existing_names, name):
    """True when `name` already names a landmark, ignoring case.

    Case-insensitive on purpose: "Neck_F" and "neck_f" in one protocol are
    almost certainly a typo, and silently keeping both is the kind of thing
    that is only discovered after the scans are gone.
    """
    lowered = str(name).strip().lower()
    return any(str(other).strip().lower() == lowered for other in existing_names)


def unique_name(existing_names, name):
    """`name`, suffixed with .001, .002 ... until it does not collide."""
    base = clean_name(name)
    if not name_exists(existing_names, base):
        return base
    for index in range(1, 1000):
        candidate = "%s.%03d" % (base, index)
        if not name_exists(existing_names, candidate):
            return candidate
    raise LandmarkError("could not find a free name based on '%s'" % base)


def next_protocol_id(existing_ids):
    """The next free "L###" id, preserving any ids already in use."""
    highest = 0
    for value in existing_ids:
        match = _PROTOCOL_ID_PATTERN.match(str(value).strip())
        if match:
            highest = max(highest, int(match.group(1)))
    return "L%02d" % (highest + 1)


def helper_object_name(stable_id):
    """Blender object name for a landmark's marker.

    Derived from the stable id, never from the researcher's name: an
    anatomical name may contain characters Blender mangles, may be renamed
    later, and may collide after a protocol load (sect. 7).
    """
    return "BSMT_Landmark_%06d" % int(stable_id)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def stale_reason(point_geometry_hash, triangle_index,
                 canonical_geometry_hash, triangle_count):
    """Why a stored surface location no longer describes the current mesh.

    Returns '' when it still does. This is the single implementation of the
    stale rule required by sect. 16; both the Landmark Manager and the A/B
    validate operator call it, so the two can never disagree about what
    "stale" means.
    """
    if canonical_geometry_hash != point_geometry_hash:
        return "geometry changed (%s -> %s)" % (
            (point_geometry_hash or "?")[:8],
            (canonical_geometry_hash or "?")[:8],
        )
    if not 0 <= int(triangle_index) < int(triangle_count):
        return "triangle index %d is outside the canonical array (%d triangles)" % (
            int(triangle_index), int(triangle_count)
        )
    return ""


def classify(picked, point_geometry_hash, triangle_index, source_object,
             object_exists, canonical_geometry_hash=None, triangle_count=None):
    """Status and detail for one landmark. Returns (status, detail).

    `canonical_geometry_hash` is None when the canonical mesh is not currently
    cached. That is not evidence of a change, but it is not evidence of
    sameness either, so the landmark is reported NEEDS_REFRESH rather than
    asserted VALID. Validate All is what resolves it.
    """
    if not picked:
        return STATUS_NOT_PICKED, ""
    if not source_object:
        return STATUS_INVALID, "no source object recorded"
    if not object_exists:
        return STATUS_INVALID, "source object '%s' is missing" % source_object
    if canonical_geometry_hash is None or triangle_count is None:
        return STATUS_NEEDS_REFRESH, "canonical mesh not loaded"
    reason = stale_reason(point_geometry_hash, triangle_index,
                          canonical_geometry_hash, triangle_count)
    if reason:
        return STATUS_STALE, reason
    return STATUS_VALID, ""


def summarise(statuses):
    """Counts per status, and a one-line summary string."""
    counts = {status: 0 for status in STATUS_ORDER}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    parts = []
    labels = {
        STATUS_VALID: "valid",
        STATUS_NOT_PICKED: "not picked",
        STATUS_NEEDS_REFRESH: "needs refresh",
        STATUS_STALE: "stale",
        STATUS_INVALID: "invalid",
    }
    for status in STATUS_ORDER:
        if counts.get(status):
            parts.append("%d %s" % (counts[status], labels[status]))
    return counts, ", ".join(parts) if parts else "no landmarks"


# ---------------------------------------------------------------------------
# batched transform following (sect. 17)
# ---------------------------------------------------------------------------

def local_positions(triangle_indices, barycentrics, canonical_vertices,
                    canonical_triangles):
    """Object-local positions of many canonical locations, in one pass.

    Vectorised on purpose. Following a transform with 50 landmarks must not
    cost 50 python-level round trips, and it must never rebuild topology, the
    BVH or a geometry hash - none of which is even reachable from here.
    """
    triangle_indices = np.asarray(triangle_indices, dtype=np.int64)
    barycentrics = np.asarray(barycentrics, dtype=np.float64)
    if triangle_indices.size == 0:
        return np.zeros((0, 3), dtype=np.float64)
    corners = np.asarray(canonical_vertices, dtype=np.float64)[
        np.asarray(canonical_triangles, dtype=np.int64)[triangle_indices]
    ]                                             # (k, 3, 3)
    return np.einsum('ij,ijk->ik', barycentrics, corners)


def to_world(local_positions_array, matrix_world):
    """Apply an object matrix to many local positions at once."""
    local = np.asarray(local_positions_array, dtype=np.float64)
    matrix = np.asarray(matrix_world, dtype=np.float64)
    if local.size == 0:
        return np.zeros((0, 3), dtype=np.float64)
    return local @ matrix[:3, :3].T + matrix[:3, 3]


def world_positions(triangle_indices, barycentrics, canonical_vertices,
                    canonical_triangles, matrix_world):
    """Convenience: canonical locations straight to world space."""
    return to_world(
        local_positions(triangle_indices, barycentrics,
                        canonical_vertices, canonical_triangles),
        matrix_world,
    )
