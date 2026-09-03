"""Scan preprocessing decisions and the solver safety gate. Pure python.

No bpy, so the arithmetic and the refusal rules are testable outside Blender.
The Blender work - duplicating, decimating, auditing datablocks - lives in
``scancopy.py``.

Why this exists
---------------
A Design X textured OBJ can arrive at ~2.78M triangles with non-manifold edges,
and handing that to the native exact-geodesic solver has already crashed
Blender with SIGSEGV. A crash loses the operator's whole session, so the mesh
is checked *before* the C++ library is constructed, not after it misbehaves.

Two separate jobs:

1. plan a lighter TEXTURED measurement copy from a target triangle count;
2. refuse, before any solver construction, to hand the solver a mesh whose
   topology makes it unsafe.

What this deliberately does not do
----------------------------------
No welding, no merge-by-distance, no hole filling. On a human scan those
silently fuse anatomically distinct surfaces that happen to touch - arm to
torso, finger to finger, garment to skin - and a fused surface produces a
confidently wrong, systematically SHORT geodesic. Diagnose and report; let the
researcher decide.
"""

#: Quality presets. The exact target stays editable; these are shortcuts.
PRESETS = (
    ('HIGH', "High (500k)", "Around 500,000 triangles", 500000),
    ('STANDARD', "Standard (350k)", "Around 350,000 triangles", 350000),
    ('LIGHT', "Light (200k)", "Around 200,000 triangles", 200000),
    ('CUSTOM', "Custom", "Use the target triangle count below", 0),
)

PRESET_TARGETS = {name: target for name, _label, _desc, target in PRESETS}

DEFAULT_TARGET_TRIANGLES = 350000

#: Operational threshold, not a mathematical limit. Above it the exact solver
#: is slow and has been observed to be unstable on real scans; it says nothing
#: about what the MMP algorithm can represent.
DEFAULT_DENSE_THRESHOLD = 1000000

METHOD_DECIMATE = 'DECIMATE_COLLAPSE'
METHOD_COPY = 'COPY_ONLY'

#: What the generated object IS. Decimation changes the polyhedral surface, so
#: the copy is not the same surface as the original and its geodesic distances
#: are not identical to the original's. Never described as "the same exact
#: surface"; the sensitivity of surface distance to mesh density is a separate
#: question that has to be measured, not assumed.
REPRESENTATION = "Decimated copy, made for measurement"

COPY_SUFFIX = "_BSMT"


class PreprocessError(Exception):
    """A preprocessing request cannot be carried out as asked."""


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------

def decimation_ratio(target_triangles, current_triangles):
    """Collapse ratio for a target triangle count, clamped to (0, 1].

    A ratio at or above 1 means no reduction is wanted; the caller copies
    without decimating rather than running a modifier that would do nothing.
    """
    try:
        target = int(target_triangles)
        current = int(current_triangles)
    except (TypeError, ValueError):
        raise PreprocessError("triangle counts must be whole numbers")
    if current <= 0:
        raise PreprocessError("the source mesh has no triangles")
    if target <= 0:
        raise PreprocessError("the target triangle count must be positive")
    return min(1.0, max(0.0, float(target) / float(current)))


def plan(target_triangles, current_triangles):
    """What Create Measurement Mesh is about to do."""
    ratio = decimation_ratio(target_triangles, current_triangles)
    target = int(target_triangles)
    current = int(current_triangles)
    if target >= current:
        return {
            "method": METHOD_COPY,
            "ratio": 1.0,
            "target_triangles": target,
            "current_triangles": current,
            "expected_triangles": current,
            "reduction_percent": 0.0,
            "summary": ("The target of {:,} is not below the current {:,}, "
                        "so the mesh will be copied without "
                        "decimation.".format(target, current)),
        }
    return {
        "method": METHOD_DECIMATE,
        "ratio": ratio,
        "target_triangles": target,
        "current_triangles": current,
        "expected_triangles": target,
        "reduction_percent": 100.0 * (1.0 - ratio),
        "summary": ("Decimate {:,} to about {:,} triangles "
                    "({:.1f}% reduction).".format(
                        current, target, 100.0 * (1.0 - ratio))),
    }


def accuracy_note(actual_triangles, target_triangles, tolerance=0.05):
    """How close the result landed. Collapse decimation is approximate."""
    target = int(target_triangles)
    actual = int(actual_triangles)
    if target <= 0:
        return "", True
    error = abs(actual - target) / float(target)
    within = error <= tolerance
    return ("{:,} triangles, {:.1f}% from the {:,} requested".format(
        actual, 100.0 * error, target)), within


# ---------------------------------------------------------------------------
# texture and UV preservation
# ---------------------------------------------------------------------------

def texture_facts(uv_layers, material_slots, images, image_paths,
                  color_attributes=(), active_color="", used_materials=()):
    """A comparable record of a mesh's appearance-bearing datablocks.

    `color_attributes` carries (name, domain, data_type) triples. It is what
    makes this workflow usable on a PLY scan at all: a PLY normally arrives
    with no UV map, no material and no image, and its entire appearance is a
    per-vertex colour attribute. A comparison that only looked at UVs and
    materials would call such a scan "nothing to preserve" and then not
    notice if the colour vanished.

    `used_materials` is the subset of slots that at least one face actually
    references AFTER the operation. A slot survives decimation even when every
    face that used it has been collapsed away (measured on Blender 4.5.13), so
    slot presence alone is a weaker check than it looks.

    Later parameters are optional so a caller written against the Milestone
    3.3 signature keeps working.
    """
    return {
        "uv_layers": [str(name) for name in uv_layers],
        "material_slots": [str(name) for name in material_slots],
        "images": [str(name) for name in images],
        "image_paths": [str(path) for path in image_paths],
        "color_attributes": [
            (str(entry[0]), str(entry[1]), str(entry[2]))
            if isinstance(entry, (tuple, list)) and len(entry) >= 3
            else (str(entry), "", "")
            for entry in color_attributes
        ],
        "active_color": str(active_color or ""),
        "used_materials": [str(name) for name in used_materials],
    }


def color_attribute_names(facts):
    """Just the colour attribute names, in order."""
    return [entry[0] for entry in facts.get("color_attributes", ())]


def has_appearance_data(facts):
    """Whether a mesh carries anything that shows what the scan looks like."""
    return bool(facts.get("uv_layers") or facts.get("color_attributes")
                or facts.get("images"))


def compare_texture(before, after):
    """Did the copy keep everything the original had? (ok, problems, notes).

    A lost UV layer, a dropped material, a dropped image reference or a lost
    COLOR ATTRIBUTE means the copy cannot show the scan's appearance, which is
    the whole point of this workflow. That is a FAILURE, not a warning: a
    measurement copy the researcher cannot visually register against the
    original is not measurement-ready (sect. 4).

    Not every difference is a loss. A changed colour domain, a changed active
    colour, and a material slot no longer referenced by any face are all
    reported as notes - the appearance data is still there.
    """
    problems = []
    notes = []

    missing_uv = [name for name in before["uv_layers"]
                  if name not in after["uv_layers"]]
    if missing_uv:
        problems.append("UV layer(s) lost: %s" % ", ".join(missing_uv))
    elif before["uv_layers"]:
        notes.append("UV layer(s) preserved: %s"
                     % ", ".join(after["uv_layers"]))

    missing_material = [name for name in before["material_slots"]
                        if name not in after["material_slots"]]
    if missing_material:
        problems.append("material(s) lost: %s" % ", ".join(missing_material))
    elif before["material_slots"]:
        notes.append("material(s) preserved: %s"
                     % ", ".join(after["material_slots"]))

    missing_image = [name for name in before["images"]
                     if name not in after["images"]]
    if missing_image:
        problems.append("image texture(s) no longer referenced: %s"
                        % ", ".join(missing_image))
    elif before["images"]:
        notes.append("image texture(s) preserved: %s"
                     % ", ".join(after["images"]))

    # --- colour attributes (PLY scans live or die on these) --------------
    before_colors = {name: (domain, kind)
                     for name, domain, kind in before.get("color_attributes", ())}
    after_colors = {name: (domain, kind)
                    for name, domain, kind in after.get("color_attributes", ())}
    missing_color = [name for name in before_colors if name not in after_colors]
    if missing_color:
        problems.append("color attribute(s) lost: %s"
                        % ", ".join(sorted(missing_color)))
    elif before_colors:
        notes.append("color attribute(s) preserved: %s"
                     % ", ".join(sorted(after_colors)))

    # A surviving name whose domain or type changed is NOT a loss - the colour
    # is still there - but it is worth saying, because a POINT attribute that
    # became CORNER will export differently.
    for name, signature in before_colors.items():
        if name in after_colors and after_colors[name] != signature:
            notes.append(
                "color attribute '%s' changed from %s/%s to %s/%s"
                % (name, signature[0], signature[1],
                   after_colors[name][0], after_colors[name][1])
            )

    before_active = before.get("active_color", "")
    after_active = after.get("active_color", "")
    if before_active and after_active != before_active:
        notes.append("the active color attribute changed from '%s' to '%s'"
                     % (before_active, after_active or "none"))

    # A material slot survives decimation even when every face that used it
    # has been collapsed away. Reported as a note, never as a loss: the
    # appearance data is intact and only the assignment is gone.
    used_after = set(after.get("used_materials", ()))
    if used_after:
        unused = [name for name in after["material_slots"]
                  if name not in used_after]
        if unused:
            notes.append("material slot(s) no longer used by any face: %s"
                         % ", ".join(unused))

    if not before["uv_layers"]:
        notes.append("the source had no UV layer, so none was expected")
    if not before["images"]:
        notes.append("the source referenced no image texture")
    if not before_colors:
        notes.append("the source had no color attribute")
    if not has_appearance_data(before):
        notes.append("the source carried no appearance data at all - "
                     "the copy shows geometry only")

    return (not problems), problems, notes


# ---------------------------------------------------------------------------
# the blocking-defect policy - ONE list, two presentations (Milestone 3.18)
# ---------------------------------------------------------------------------
#
# Until 0.22.1 there were two answers to "is this mesh safe to measure on".
# `classify_ready` called degenerate triangles blocking; `preflight` called
# them a warning and let the solve run. The product therefore said NOT READY
# in the sidebar and then handed the same mesh to pygeodesic anyway.
#
# The conditions now live here, once. `classify_ready` renders them as status
# reasons and `preflight` renders them as refusals - different wording for
# different contexts, but never a different SET. Neither decides membership.
#
# What is deliberately NOT on this list, and stays warning-only:
#   * boundary edges          - a hole makes a geodesic questionable, not unsafe
#   * connected components    - enforced per landmark PAIR, not per mesh
#   * coincident vertices     - diagnostic; they matter only when they produce
#                               degenerate triangles, which this list catches
#   * density                 - operational, and guarded separately by
#                               `guard_dense` so it stays overridable

DEFECT_NO_CANONICAL = 'NO_CANONICAL'
DEFECT_NO_TRIANGLES = 'NO_TRIANGLES'
DEFECT_NON_MANIFOLD = 'NON_MANIFOLD'
DEFECT_DEGENERATE = 'DEGENERATE'

#: Order matters: the first defect is the one a compact line reports.
BLOCKING_DEFECT_CODES = (
    DEFECT_NO_CANONICAL,
    DEFECT_NO_TRIANGLES,
    DEFECT_NON_MANIFOLD,
    DEFECT_DEGENERATE,
)

REFUSE_DEGENERATE = (
    "Surface calculation refused: the measurement mesh contains %d "
    "degenerate (zero-area) triangle(s). Use the Mesh Repair panel and "
    "re-analyze before exact geodesic measurement."
)
REFUSE_NO_TRIANGLES = (
    "Surface calculation refused: the measurement mesh has no triangles."
)
REFUSE_NO_CANONICAL = (
    "Surface calculation refused: the canonical mesh could not be built from "
    "this geometry."
)


def blocking_defects(report, canonical_built=True):
    """Every condition that makes exact surface measurement unsafe.

    THE list. `classify_ready` turns it into a NOT READY verdict and
    `preflight` turns it into a solver refusal; neither is entitled to decide
    what belongs on it, and a condition added here is enforced in both places
    at once by construction.

    Returns a list of dicts with `code`, `count`, `status` (status wording)
    and `refusal` (refusal wording). Empty means nothing blocks.
    """
    if not canonical_built:
        return [{
            "code": DEFECT_NO_CANONICAL,
            "count": 0,
            "status": ("the canonical mesh could not be built from this "
                       "geometry, so nothing can be measured on it"),
            "refusal": REFUSE_NO_CANONICAL,
        }]

    triangles = int(report.get("triangle_count", 0) or 0)
    non_manifold = int(report.get("nonmanifold_edge_count", 0) or 0)
    degenerate = int(report.get("degenerate_triangle_count", 0) or 0)

    defects = []
    if triangles <= 0:
        # A report with no triangle count is a report that cannot be trusted,
        # and an unknown mesh is exactly what this gate exists to keep away
        # from a native solver.
        defects.append({
            "code": DEFECT_NO_TRIANGLES,
            "count": 0,
            "status": "the mesh has no triangles",
            "refusal": REFUSE_NO_TRIANGLES,
        })
    if non_manifold > 0:
        defects.append({
            "code": DEFECT_NON_MANIFOLD,
            "count": non_manifold,
            "status": ("%d non-manifold edge(s). The exact solver has crashed "
                       "Blender on non-manifold input, so it is refused "
                       "outright" % non_manifold),
            "refusal": REFUSE_NON_MANIFOLD % non_manifold,
        })
    if degenerate > 0:
        # A zero-area triangle makes barycentric reconstruction ill-defined
        # at the point a landmark lands on one. That is a WRONG measurement
        # rather than a slow one, which is why it blocks rather than warns.
        defects.append({
            "code": DEFECT_DEGENERATE,
            "count": degenerate,
            "status": "%d degenerate (zero-area) triangle(s)" % degenerate,
            "refusal": REFUSE_DEGENERATE % degenerate,
        })
    return defects


# ---------------------------------------------------------------------------
# solver safety gate
# ---------------------------------------------------------------------------

REFUSE_NON_MANIFOLD = (
    "Surface calculation refused: mesh contains %d non-manifold edge(s). "
    "Use the Mesh Repair panel before measuring on this mesh."
)
REFUSE_DENSE = (
    "Surface calculation refused: %s triangles exceeds the %s safety "
    "threshold. Create a measurement mesh first, or untick the density guard "
    "to proceed anyway."
)
#: What to do about a scan above the density threshold. One string, so the
#: panel, the report and the test all quote the same advice - and so the
#: project's single vocabulary ("measurement mesh", never "measurement copy")
#: is applied to it in one place.
DENSE_SCAN_ADVICE = (
    "High-density scan. Create a measurement mesh before exact surface-path "
    "computation."
)

WARN_DENSE = (
    "High-density mesh: exact geodesic computation may be slow or unstable. "
    "Create a measurement mesh first. (%s triangles, threshold %s.)"
)
WARN_COMPONENTS = (
    "This mesh has %d connected components. Measurement is still allowed; a "
    "landmark pair on different components will be refused individually."
)
WARN_BOUNDARY = (
    "This mesh has %d boundary edge(s). A geodesic near a hole or a cropped "
    "edge may take a long detour that looks plausible but is an artefact."
)


def _thousands(value):
    return "{:,}".format(int(value))


def preflight(report, dense_threshold=DEFAULT_DENSE_THRESHOLD,
              guard_dense=True, canonical_built=True):
    """Decide whether this mesh may be handed to the native solver.

    `report` is a geodesic.topology.analyse() summary. Returns a dict with
    `allowed`, `refusals` and `warnings`.

    The unconditional refusals are `blocking_defects` - the same list that
    makes `classify_ready` say NOT READY. This function does not decide which
    conditions are unsafe and must never start to: until 0.22.1 it kept its
    own view, under which degenerate triangles were merely a warning, so the
    sidebar said NOT READY and the solver was handed that very mesh anyway.

    Density is separate and stays a GUARDED refusal rather than a defect: the
    threshold is operational, so it can be overridden deliberately, but it
    defaults to blocking because losing an unsaved session is worse than
    being asked to make a measurement mesh first.
    """
    warnings = []

    defects = blocking_defects(report, canonical_built=canonical_built)
    refusals = [defect["refusal"] for defect in defects]
    blocked_codes = [defect["code"] for defect in defects]

    non_manifold = int(report.get("nonmanifold_edge_count", 0) or 0)
    triangles = int(report.get("triangle_count", 0) or 0)
    components = int(report.get("component_count", 0) or 0)
    boundary = int(report.get("boundary_edge_count", 0) or 0)

    if triangles > int(dense_threshold):
        if guard_dense:
            refusals.append(REFUSE_DENSE % (_thousands(triangles),
                                            _thousands(dense_threshold)))
        else:
            warnings.append(WARN_DENSE % (_thousands(triangles),
                                          _thousands(dense_threshold)))

    # Warnings only from here down; every one of these leaves the solve
    # ALLOWED. A hole makes a geodesic questionable rather than unsafe, and
    # connectivity is enforced per landmark PAIR by the solver's own
    # validation rather than per mesh.
    if components > 1:
        warnings.append(WARN_COMPONENTS % components)
    if boundary > 0:
        warnings.append(WARN_BOUNDARY % boundary)

    return {
        "allowed": not refusals,
        "refusals": refusals,
        "warnings": warnings,
        "blocking_codes": blocked_codes,
        "triangle_count": triangles,
        "nonmanifold_edge_count": non_manifold,
        "component_count": components,
        "boundary_edge_count": boundary,
        "message": " ".join(refusals) if refusals else " ".join(warnings),
    }


# ---------------------------------------------------------------------------
# measurement-ready classification (Milestone 3.15)
# ---------------------------------------------------------------------------
#
# Three states, deliberately conservative, and deliberately NOT the same
# question as `preflight`. `preflight` decides whether a single solve may be
# handed to the native library right now. This decides whether a measurement
# copy is fit to landmark and measure on at all, which is what the researcher
# is looking at immediately after preprocessing.
#
# The two can never contradict each other, because NOT_READY includes every
# preflight refusal that is about the mesh itself.

MEASUREMENT_READY = 'READY'
MEASUREMENT_WARNING = 'WARNING'
MEASUREMENT_NOT_READY = 'NOT_READY'

READY_LABELS = {
    MEASUREMENT_READY: "MEASUREMENT READY",
    MEASUREMENT_WARNING: "WARNING",
    MEASUREMENT_NOT_READY: "NOT READY",
}

#: Order matters: the first reason is the one the compact line shows.
_READY_ORDER = (MEASUREMENT_NOT_READY, MEASUREMENT_WARNING, MEASUREMENT_READY)


def classify_ready(report, canonical_built=True, appearance_ok=True,
                   appearance_problems=(), dense_threshold=DEFAULT_DENSE_THRESHOLD):
    """Is this mesh fit to landmark and measure on? (state, reasons).

    `report` is a geodesic.topology.analyse() summary - the SAME diagnostics
    every other panel reads, never a second definition of them (sect. 5).
    `canonical_built` says whether the canonical mesh could be constructed at
    all, which is the one condition that makes everything else moot.

    Deliberately NOT a rule: connected components == 1. A real scan can
    legitimately contain more than one component - a separate hair cap, a
    prop, a stray island - and refusing to measure such a scan would be wrong.
    Connectivity is a property of a landmark PAIR, and it is enforced there,
    per-measurement, by the solver's own validation (sect. 6).
    """
    warnings = []

    # The same list the solver gate refuses on, rendered as status wording.
    # This function does not decide which conditions are unsafe.
    defects = blocking_defects(report, canonical_built=canonical_built)
    blocking = [defect["status"] for defect in defects]
    if not canonical_built:
        return MEASUREMENT_NOT_READY, blocking

    triangles = int(report.get("triangle_count", 0) or 0)
    components = int(report.get("component_count", 0) or 0)
    boundary = int(report.get("boundary_edge_count", 0) or 0)

    # Appearance is preprocessing's own concern, judged against the facts
    # recorded at copy time rather than anything in a topology report, so it
    # is not a mesh defect and does not belong on the shared list.
    if not appearance_ok:
        blocking.extend(str(problem) for problem in appearance_problems)

    if blocking:
        return MEASUREMENT_NOT_READY, blocking

    if triangles > int(dense_threshold):
        warnings.append(
            "%s triangles is above the %s operational threshold. Exact "
            "surface paths will be slow, and are guarded"
            % (_thousands(triangles), _thousands(dense_threshold))
        )
    if components > 1:
        warnings.append(
            "%d connected components. Measuring is allowed; a landmark pair "
            "on two different components is refused individually" % components
        )
    if boundary > 0:
        warnings.append(
            "%d boundary edge(s). A geodesic near a hole or a cropped edge "
            "can take a plausible-looking detour" % boundary
        )

    if warnings:
        return MEASUREMENT_WARNING, warnings
    return MEASUREMENT_READY, []


def ready_lines(state, reasons, limit=4):
    """The classification as displayable lines, worst reason first."""
    lines = ["Status: %s" % READY_LABELS.get(state, state)]
    for reason in list(reasons)[:int(limit)]:
        lines.append("  - %s" % reason)
    remaining = len(list(reasons)) - int(limit)
    if remaining > 0:
        lines.append("  - and %d more" % remaining)
    return lines


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

_COMPARISON_ROWS = (
    ("Vertices", "vertex_count"),
    ("Triangles", "triangle_count"),
    ("Components", "component_count"),
    ("Boundary edges", "boundary_edge_count"),
    ("Non-manifold edges", "nonmanifold_edge_count"),
    ("Degenerate triangles", "degenerate_triangle_count"),
    ("Duplicate vertices", "duplicate_vertex_count"),
)


def format_comparison(before, after, before_label="Original",
                      after_label="Measurement Mesh"):
    """Before / after diagnostics as aligned lines."""
    lines = ["%-22s %14s %14s" % ("", before_label, after_label)]
    for label, key in _COMPARISON_ROWS:
        lines.append("%-22s %14s %14s" % (
            label,
            _thousands(before.get(key, 0) or 0),
            _thousands(after.get(key, 0) or 0),
        ))
    return lines


def provenance_lines(record):
    """The sect. 11 provenance record, as displayable lines."""
    return [
        "Source object:   %s" % record.get("source_name", "?"),
        "Source mesh:     %s" % record.get("source_mesh_name", "?"),
        "Original:        %s triangles"
        % _thousands(record.get("original_triangles", 0)),
        "Target:          %s triangles"
        % _thousands(record.get("target_triangles", 0)),
        "Actual:          %s triangles"
        % _thousands(record.get("actual_triangles", 0)),
        "Method:          %s" % record.get("method", "?"),
        "Ratio:           %.5f" % float(record.get("ratio", 0.0)),
        "BSMT version:    %s" % record.get("bsmt_version", "?"),
        "Created:         %s" % record.get("created", "?"),
        "Representation:  %s" % REPRESENTATION,
    ]
