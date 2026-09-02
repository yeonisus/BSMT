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
REPRESENTATION = "decimated measurement representation"

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
    """What Create Measurement Copy is about to do."""
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
            "summary": ("target %d is not below the current %d - copying "
                        "without decimation" % (target, current)),
        }
    return {
        "method": METHOD_DECIMATE,
        "ratio": ratio,
        "target_triangles": target,
        "current_triangles": current,
        "expected_triangles": target,
        "reduction_percent": 100.0 * (1.0 - ratio),
        "summary": ("decimate %d -> about %d triangles (ratio %.5f, %.1f%% "
                    "reduction)" % (current, target, ratio,
                                    100.0 * (1.0 - ratio))),
    }


def accuracy_note(actual_triangles, target_triangles, tolerance=0.05):
    """How close the result landed. Collapse decimation is approximate."""
    target = int(target_triangles)
    actual = int(actual_triangles)
    if target <= 0:
        return "", True
    error = abs(actual - target) / float(target)
    within = error <= tolerance
    return ("%d triangles, %.1f%% from the %d requested"
            % (actual, 100.0 * error, target)), within


# ---------------------------------------------------------------------------
# texture and UV preservation
# ---------------------------------------------------------------------------

def texture_facts(uv_layers, material_slots, images, image_paths):
    """A comparable record of a mesh's texture-bearing datablocks."""
    return {
        "uv_layers": [str(name) for name in uv_layers],
        "material_slots": [str(name) for name in material_slots],
        "images": [str(name) for name in images],
        "image_paths": [str(path) for path in image_paths],
    }


def compare_texture(before, after):
    """Did the copy keep everything the original had? (ok, problems, notes).

    A lost UV layer or a dropped material means the copy cannot show the scan's
    texture, which is the whole point of this workflow. That is a FAILURE, not
    a warning: a measurement copy the researcher cannot visually register
    against the original is not measurement-ready (sect. 4).
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

    if not before["uv_layers"]:
        notes.append("the source had no UV layer, so none was expected")
    if not before["images"]:
        notes.append("the source referenced no image texture")

    return (not problems), problems, notes


# ---------------------------------------------------------------------------
# solver safety gate
# ---------------------------------------------------------------------------

REFUSE_NON_MANIFOLD = (
    "Surface calculation refused: mesh contains %d non-manifold edge(s). "
    "Run Scan Preprocessing / repair before exact geodesic measurement."
)
REFUSE_DENSE = (
    "Surface calculation refused: %s triangles exceeds the %s safety "
    "threshold. Create a measurement copy first, or untick the density guard "
    "to proceed anyway."
)
WARN_DENSE = (
    "High-density mesh: exact geodesic computation may be slow or unstable. "
    "Create a measurement copy first. (%s triangles, threshold %s.)"
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
              guard_dense=True):
    """Decide whether this mesh may be handed to the native solver.

    `report` is a geodesic.topology.analyse() summary. Returns a dict with
    `allowed`, `refusals` and `warnings`.

    Only non-manifold topology is an unconditional refusal: it is the
    condition that has actually crashed Blender, and a SIGSEGV takes the
    session with it. Density is a guarded refusal rather than a hard limit -
    the threshold is operational, so it can be overridden deliberately, but
    it defaults to blocking because losing an unsaved session is worse than
    being asked to make a measurement copy first.
    """
    refusals = []
    warnings = []

    non_manifold = int(report.get("nonmanifold_edge_count", 0) or 0)
    triangles = int(report.get("triangle_count", 0) or 0)
    components = int(report.get("component_count", 0) or 0)
    boundary = int(report.get("boundary_edge_count", 0) or 0)
    degenerate = int(report.get("degenerate_triangle_count", 0) or 0)

    if non_manifold > 0:
        refusals.append(REFUSE_NON_MANIFOLD % non_manifold)

    if triangles > int(dense_threshold):
        if guard_dense:
            refusals.append(REFUSE_DENSE % (_thousands(triangles),
                                            _thousands(dense_threshold)))
        else:
            warnings.append(WARN_DENSE % (_thousands(triangles),
                                          _thousands(dense_threshold)))

    if components > 1:
        warnings.append(WARN_COMPONENTS % components)
    if boundary > 0:
        warnings.append(WARN_BOUNDARY % boundary)
    if degenerate > 0:
        warnings.append(
            "This mesh has %d degenerate (zero-area) triangle(s)." % degenerate
        )

    return {
        "allowed": not refusals,
        "refusals": refusals,
        "warnings": warnings,
        "triangle_count": triangles,
        "nonmanifold_edge_count": non_manifold,
        "component_count": components,
        "boundary_edge_count": boundary,
        "message": " ".join(refusals) if refusals else " ".join(warnings),
    }


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
                      after_label="Measurement Copy"):
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
