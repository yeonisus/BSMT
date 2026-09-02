"""Research-data export: measurements and landmarks as CSV. Pure stdlib.

No bpy, so every decision about what a row contains can be tested without
Blender, and no pandas: a dependency that has to be installed into Blender's
Python is a dependency a researcher will one day not have.

Two rules govern everything here.

**A blank is not a zero.** A measurement that was never calculated, a surface
distance that was never solved, a landmark that was never picked - all export
as an EMPTY FIELD. Writing 0.0 for "not calculated" is the one failure mode
that turns an export into wrong data rather than missing data, because a zero
survives every downstream check a blank would fail.

**A number is only exported when it is current.** BSMT already clears a stored
result the moment a dependency changes, and the status says so. The export
asks for the status and the validity flags together, and writes a number only
when both agree, so a STALE row carries its status and no distance.

Encoding
--------
UTF-8 **with a BOM** (``utf-8-sig``). Without it Excel on Windows reads a
UTF-8 CSV as the system code page, and a landmark named 목_앞 arrives as
mojibake - a silent corruption of the researcher's own labels. Python's own
``csv`` reader handles the BOM when told ``encoding="utf-8-sig"``, R needs
``fileEncoding="UTF-8-BOM"``, and both are one argument. Losing the labels is
the worse trade.

Numbers are formatted with ``repr``-style Python floats, so the decimal
separator is always ``.`` regardless of locale.
"""

import csv
import datetime
import re

#: Excel needs the BOM to read UTF-8 reliably; see the module docstring.
ENCODING = "utf-8-sig"

#: Anything that is not a word character, a dot or a dash. `\w` is Unicode
#: aware, so a Korean or accented subject id survives intact - macOS, Linux
#: and modern Windows all store UTF-8 filenames without complaint, and
#: stripping them to nothing would give every subject in a Korean-labelled
#: study the SAME fallback filename. What it does remove is everything that
#: makes a name dangerous or unportable: path separators, the characters
#: Windows reserves (: * ? " < > |), and whitespace.
_UNSAFE = re.compile(r"[^\w.-]+", re.UNICODE)

#: How many decimals a millimetre distance is written with. Six is far beyond
#: the accuracy of any scan; it exists so a value round-trips unchanged rather
#: than to claim precision.
DECIMALS = 6


class ExportError(Exception):
    """The export cannot be written as asked."""


# ---------------------------------------------------------------------------
# columns - fixed order, because a moving column breaks every script that
# ever read one of these files
# ---------------------------------------------------------------------------

MEASUREMENT_COLUMNS = (
    "subject_id",
    "condition",
    "scan_id",

    "measurement_id",
    "measurement_name",
    "notes",

    "from_landmark_id",
    "from_landmark_name",
    "to_landmark_id",
    "to_landmark_name",

    "measurement_type",
    "enabled",

    "straight_distance_mm",
    "surface_distance_mm",
    "surface_to_straight_ratio",

    "status",

    "measurement_mesh",
    "source_mesh",
    "representation",
    "source_triangles",
    "measurement_triangles",
    "preprocessing_method",

    "geometry_hash",
    "backend",
    "backend_version",
    "bsmt_version",
    "exported_utc",
)

LANDMARK_COLUMNS = (
    "subject_id",
    "condition",
    "scan_id",

    "landmark_id",
    "landmark_name",
    "status",
    "notes",

    "triangle_index",
    "barycentric_u",
    "barycentric_v",
    "barycentric_w",
    "component_id",

    "world_x",
    "world_y",
    "world_z",
    "coordinate_unit",

    "physical_mm_x",
    "physical_mm_y",
    "physical_mm_z",

    "measurement_mesh",
    "geometry_hash",
    "bsmt_version",
    "exported_utc",
)


# ---------------------------------------------------------------------------
# values
# ---------------------------------------------------------------------------

def number(value, valid=True, decimals=DECIMALS):
    """A millimetre value, or "" when there is no value to write.

    `valid` is the flag BSMT already keeps beside the number. When it is
    False the field is BLANK - never 0.0, which would read downstream as a
    measured zero.
    """
    if not valid or value is None:
        return ""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return ""
    if result != result:                              # NaN
        return ""
    return "%.*f" % (decimals, result)


def integer(value, present=True):
    """An integer field, or "" when the value does not exist."""
    if not present or value is None:
        return ""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return ""


def text(value):
    """A text field. Never None; the csv module quotes what needs quoting."""
    return "" if value is None else str(value)


def timestamp(now=None):
    """UTC, second resolution, ISO 8601. One value per export, not per row."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------

def measurement_row(session, measurement, mesh, version, exported):
    """One measurement, as a dict keyed by MEASUREMENT_COLUMNS.

    `measurement` is a plain dict of already-read values, so this function
    never touches Blender and can be tested directly. `straight_valid` and
    `surface_valid` decide whether a number is written at all.
    """
    straight = number(measurement.get("straight_mm"),
                      measurement.get("straight_valid", False))
    surface = number(measurement.get("surface_mm"),
                     measurement.get("surface_valid", False))
    # The ratio is only meaningful when BOTH distances are present. A ratio
    # against a missing straight distance would be a number with no meaning.
    ratio = number(measurement.get("ratio"),
                   bool(straight) and bool(surface))

    return {
        "subject_id": text(session.get("subject_id")),
        "condition": text(session.get("condition")),
        "scan_id": text(session.get("scan_id")),

        "measurement_id": text(measurement.get("protocol_id")),
        "measurement_name": text(measurement.get("name")),
        "notes": text(measurement.get("notes")),

        "from_landmark_id": text(measurement.get("from_landmark_id")),
        "from_landmark_name": text(measurement.get("from_landmark_name")),
        "to_landmark_id": text(measurement.get("to_landmark_id")),
        "to_landmark_name": text(measurement.get("to_landmark_name")),

        "measurement_type": text(measurement.get("measurement_type")),
        "enabled": "1" if measurement.get("enabled", True) else "0",

        "straight_distance_mm": straight,
        "surface_distance_mm": surface,
        "surface_to_straight_ratio": ratio,

        "status": text(measurement.get("status")),

        "measurement_mesh": text(mesh.get("measurement_mesh")),
        "source_mesh": text(mesh.get("source_mesh")),
        "representation": text(mesh.get("representation")),
        "source_triangles": integer(mesh.get("source_triangles"),
                                    bool(mesh.get("source_triangles"))),
        "measurement_triangles": integer(
            mesh.get("measurement_triangles"),
            bool(mesh.get("measurement_triangles"))),
        "preprocessing_method": text(mesh.get("preprocessing_method")),

        # The hash the RESULT was computed against, not the mesh's current
        # one: that is what makes the number reproducible.
        "geometry_hash": text(measurement.get("result_geometry_hash")),
        "backend": text(measurement.get("backend_name")),
        "backend_version": text(measurement.get("backend_version")),
        "bsmt_version": text(version),
        "exported_utc": text(exported),
    }


def landmark_row(session, landmark, mesh, version, exported, unit=""):
    """One landmark, as a dict keyed by LANDMARK_COLUMNS.

    An unpositioned landmark keeps its DEFINITION row - id, name, status,
    notes - and every geometric field is blank. That is deliberate: a protocol
    of 40 landmarks of which 38 were picked should export 40 rows, so the two
    that were missed are visible in the data rather than absent from it.
    """
    positioned = bool(landmark.get("valid", False))
    barycentric = landmark.get("barycentric") or ()
    world = landmark.get("world_xyz") or ()
    physical = landmark.get("physical_mm_xyz") or ()

    def component(values, index):
        if not positioned or len(values) <= index:
            return ""
        return number(values[index], True)

    return {
        "subject_id": text(session.get("subject_id")),
        "condition": text(session.get("condition")),
        "scan_id": text(session.get("scan_id")),

        "landmark_id": text(landmark.get("protocol_id")),
        "landmark_name": text(landmark.get("name")),
        "status": text(landmark.get("status")),
        "notes": text(landmark.get("notes")),

        "triangle_index": integer(landmark.get("triangle_index"), positioned),
        "barycentric_u": component(barycentric, 0),
        "barycentric_v": component(barycentric, 1),
        "barycentric_w": component(barycentric, 2),
        "component_id": integer(landmark.get("component_id"), positioned),

        "world_x": component(world, 0),
        "world_y": component(world, 1),
        "world_z": component(world, 2),
        "coordinate_unit": text(unit) if positioned else "",

        # Exported alongside the world position on purpose. Distances in the
        # measurement file are always millimetres; without these, a landmark
        # coordinate would be in whatever unit the scene happens to use, and
        # the two files would silently disagree.
        "physical_mm_x": component(physical, 0),
        "physical_mm_y": component(physical, 1),
        "physical_mm_z": component(physical, 2),

        "measurement_mesh": text(landmark.get("source_object")),
        "geometry_hash": text(landmark.get("geometry_hash")),
        "bsmt_version": text(version),
        "exported_utc": text(exported),
    }


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def write_csv(path, columns, rows, encoding=ENCODING):
    """Write `rows` under `columns`, in that exact order. Returns the count.

    `extrasaction='raise'` on purpose: a row carrying a key the header does
    not name is a bug, and silently dropping the value would lose data.
    """
    try:
        with open(path, "w", newline="", encoding=encoding) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns),
                                    extrasaction='raise')
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
    except OSError as exc:
        raise ExportError("could not write '%s': %s" % (path, exc))
    return len(rows)


# ---------------------------------------------------------------------------
# file names
# ---------------------------------------------------------------------------

def sanitize(value, fallback="bsmt"):
    """A filename-safe fragment: letters, digits, dot, dash, underscore.

    Runs of anything else collapse to a single underscore. A fragment that
    sanitises to nothing returns "", so the caller can fall back rather than
    write a file named "_".
    """
    cleaned = _UNSAFE.sub("_", str(value or "")).strip("._-")
    if not cleaned:
        return ""
    return cleaned[:80]


def default_filename(kind, subject_id="", condition="", scan_id="",
                     fallback=""):
    """A default export name: "S01_SV2_measurements.csv".

    Built from Session Info when it is filled in, and from the mesh name when
    it is not, so an export never lands on a name that says nothing about
    what it holds.
    """
    parts = [sanitize(subject_id), sanitize(condition)]
    parts = [part for part in parts if part]
    if not parts:
        scan = sanitize(scan_id)
        parts = [scan] if scan else []
    if not parts:
        name = sanitize(fallback)
        parts = [name] if name else ["bsmt"]
    return "%s_%s.csv" % ("_".join(parts), kind)


# ---------------------------------------------------------------------------
# the report the researcher reads afterwards
# ---------------------------------------------------------------------------

def summarise_measurements(statuses):
    """Counts for the post-export report. Returns (counts, lines)."""
    counts = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    lines = ["Measurements exported: %d" % len(statuses)]
    for status in sorted(counts):
        lines.append("%s: %d" % (status, counts[status]))
    return counts, lines


def summarise_landmarks(statuses):
    counts = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    lines = ["Landmarks exported: %d" % len(statuses)]
    for status in sorted(counts):
        lines.append("%s: %d" % (status, counts[status]))
    return counts, lines
