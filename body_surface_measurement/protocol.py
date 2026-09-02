"""Landmark protocol files: names and order only (Milestone 3.0). Pure stdlib.

A **protocol** is a reusable definition of *what* is to be measured: an
ordered list of landmark ids, names and optional notes. It is scan
independent, so one protocol drives every posture scan in a study.

**Scan landmark data** - the actual triangle indices and barycentric
coordinates for one particular scan - is a different thing entirely, and is
deliberately NOT in this file format (sect. 10, sect. 12). Mixing them is how
a landmark set from one subject silently ends up interpreted against another
subject's mesh.

That separation is enforced in both directions:

* ``dump()`` writes only identity fields, and asserts that nothing
  position-shaped can reach the output;
* ``load()`` refuses a file that carries position data rather than quietly
  ignoring it, because such a file is not a protocol and treating it as one
  would hide the mistake.

    {
      "format": "bsmt-landmark-protocol",
      "version": 1,
      "protocol_name": "Body Surface Protocol 01",
      "landmarks": [
        {"id": "L01", "name": "Neck_F", "notes": ""},
        {"id": "L02", "name": "Neck_B", "notes": ""}
      ]
    }
"""

import json

FORMAT = "bsmt-landmark-protocol"
VERSION = 1

MEASUREMENT_FORMAT = "bsmt-measurement-protocol"
MEASUREMENT_VERSION = 1

#: Keys that would make the file scan-specific. Never written; refused on read.
POSITION_KEYS = frozenset({
    "triangle_index", "barycentric", "barycentric_coordinates",
    "component_id", "geometry_hash", "canonical_mesh_hash",
    "local_xyz", "world_xyz", "physical_mm_xyz", "source_object",
    "surface_point", "position", "xyz", "coordinates",
})

#: Identity keys a landmark entry may carry.
ENTRY_KEYS = frozenset({"id", "name", "notes"})

#: Keys that would make a measurement template scan-specific or result
#: bearing. A template is a DEFINITION; a result belongs to one scan.
RESULT_KEYS = frozenset({
    "straight_distance_mm", "surface_distance_mm", "straight_mm", "surface_mm",
    "ratio", "surface_straight_ratio", "elapsed", "elapsed_seconds",
    "elapsed_s", "bound_factor", "attempts", "backend", "backend_name",
    "backend_version", "status", "result", "results", "metric_key",
    "metric_tensor", "unbounded_fallback",
})

#: Keys a measurement entry may carry. Human-readable landmark names are
#: included deliberately (sect. 16): the stable protocol id is authoritative,
#: the name is for diagnosing an unresolved reference.
MEASUREMENT_KEYS = frozenset({
    "id", "name", "from_landmark_id", "to_landmark_id",
    "from_landmark_name", "to_landmark_name", "type", "enabled", "notes",
})


class ProtocolError(Exception):
    """The file is not a usable landmark protocol."""


def build(protocol_name, entries):
    """Assemble a protocol dict from (id, name, notes) triples.

    Raises ProtocolError rather than writing something ambiguous.
    """
    landmarks = []
    seen = set()
    for index, entry in enumerate(entries):
        identifier, name, notes = entry
        name = " ".join(str(name or "").split())
        if not name:
            raise ProtocolError(
                "landmark %d has an empty name; a protocol must name every "
                "landmark" % (index + 1)
            )
        identifier = str(identifier or "").strip() or "L%02d" % (index + 1)
        if identifier in seen:
            raise ProtocolError(
                "duplicate landmark id '%s' at position %d" % (identifier, index + 1)
            )
        seen.add(identifier)
        landmarks.append({
            "id": identifier,
            "name": name,
            "notes": str(notes or ""),
        })

    if not landmarks:
        raise ProtocolError("a protocol must contain at least one landmark")

    document = {
        "format": FORMAT,
        "version": VERSION,
        "protocol_name": " ".join(str(protocol_name or "").split())
                         or "Untitled Protocol",
        "landmarks": landmarks,
    }
    _assert_no_position_data(document)
    return document


def _assert_no_position_data(document):
    """Fail loudly if anything position-shaped reached the document.

    A belt-and-braces check on our own output. If a future edit ever passes a
    SurfacePoint field through here, this raises at save time rather than
    producing a protocol file that quietly carries one scan's coordinates.
    """
    for entry in document.get("landmarks", []):
        offending = sorted(set(entry) & POSITION_KEYS)
        if offending:
            raise ProtocolError(
                "refusing to write scan-specific data into a protocol: %s"
                % ", ".join(offending)
            )
        unknown = sorted(set(entry) - ENTRY_KEYS)
        if unknown:
            raise ProtocolError(
                "unexpected key(s) in a protocol landmark entry: %s"
                % ", ".join(unknown)
            )


def dumps(protocol_name, entries, indent=2):
    """Serialise a protocol to a JSON string."""
    return json.dumps(build(protocol_name, entries), indent=indent,
                      ensure_ascii=False) + "\n"


def save(path, protocol_name, entries, indent=2):
    """Write a protocol file. The caller is responsible for overwrite consent."""
    text = dumps(protocol_name, entries, indent=indent)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return text


def loads(text):
    """Parse a protocol from JSON text. Returns (protocol_name, entries).

    `entries` is a list of (id, name, notes) triples in file order. Order is
    part of the protocol: it is the order a researcher picks in.
    """
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise ProtocolError("not valid JSON: %s" % exc)

    if not isinstance(document, dict):
        raise ProtocolError(
            "expected a JSON object at the top level, found %s"
            % type(document).__name__
        )

    declared = document.get("format")
    if declared is not None and declared != FORMAT:
        raise ProtocolError(
            "this file declares format '%s'; expected '%s'. A scan landmark "
            "data file is not a protocol." % (declared, FORMAT)
        )

    version = document.get("version", VERSION)
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise ProtocolError("version is not a number: %r" % (version,))
    if version > VERSION:
        raise ProtocolError(
            "this protocol is version %d; this BSMT understands up to %d"
            % (version, VERSION)
        )

    raw = document.get("landmarks")
    if not isinstance(raw, list):
        raise ProtocolError("'landmarks' must be a list")
    if not raw:
        raise ProtocolError("the protocol contains no landmarks")

    entries = []
    seen_ids = set()
    seen_names = set()
    for index, item in enumerate(raw):
        position = index + 1
        if not isinstance(item, dict):
            raise ProtocolError(
                "landmark %d is %s, expected an object"
                % (position, type(item).__name__)
            )

        offending = sorted(set(item) & POSITION_KEYS)
        if offending:
            # Refused, not ignored: a file carrying coordinates is scan data,
            # and loading it as a protocol would attach one scan's positions
            # to a different scan without anyone noticing.
            raise ProtocolError(
                "landmark %d carries scan-specific data (%s). This looks like "
                "scan landmark data, not a protocol; protocols contain names "
                "and order only." % (position, ", ".join(offending))
            )

        name = " ".join(str(item.get("name") or "").split())
        if not name:
            raise ProtocolError("landmark %d has no name" % position)
        lowered = name.lower()
        if lowered in seen_names:
            raise ProtocolError(
                "duplicate landmark name '%s' at position %d" % (name, position)
            )
        seen_names.add(lowered)

        identifier = str(item.get("id") or "").strip() or "L%02d" % position
        if identifier in seen_ids:
            raise ProtocolError(
                "duplicate landmark id '%s' at position %d" % (identifier, position)
            )
        seen_ids.add(identifier)

        entries.append((identifier, name, str(item.get("notes") or "")))

    name = " ".join(str(document.get("protocol_name") or "").split())
    return name or "Untitled Protocol", entries


def load(path):
    """Read a protocol file. Returns (protocol_name, entries)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise ProtocolError("could not read '%s': %s" % (path, exc))
    return loads(text)


# ---------------------------------------------------------------------------
# measurement templates (Milestone 3.1)
# ---------------------------------------------------------------------------
#
# A measurement template is a DEFINITION file: which landmark pairs to
# measure, how, and in what order. It is scan independent, exactly like a
# landmark protocol, and for the same reason - one template drives a whole
# study.
#
# It therefore carries no distances, no timings, no backend provenance and no
# surface-point coordinates. Both directions are enforced, as for landmark
# protocols: writing asserts, and reading refuses rather than ignores.
#
# Landmark references are the landmark PROTOCOL ids ("L01"), not scene-local
# stable ids, so a template loaded alongside its landmark protocol resolves
# naturally. The human-readable name travels with the reference for
# diagnostics, but the id is authoritative: an id that does not resolve is
# reported, never matched to a similarly named landmark (sect. 16).

VALID_TYPES = ("STRAIGHT", "SURFACE", "BOTH")


def build_measurements(protocol_name, entries):
    """Assemble a measurement template from definition tuples.

    Each entry is
    ``(id, name, from_id, from_name, to_id, to_name, type, enabled, notes)``.
    """
    measurements = []
    seen_ids = set()
    for index, entry in enumerate(entries):
        position = index + 1
        (identifier, name, from_id, from_name, to_id, to_name,
         measurement_type, enabled, notes) = entry

        name = " ".join(str(name or "").split())
        if not name:
            raise ProtocolError("measurement %d has an empty name" % position)

        identifier = str(identifier or "").strip() or "M%02d" % position
        if identifier in seen_ids:
            raise ProtocolError(
                "duplicate measurement id '%s' at position %d"
                % (identifier, position)
            )
        seen_ids.add(identifier)

        measurement_type = str(measurement_type or "").strip().upper()
        if measurement_type not in VALID_TYPES:
            raise ProtocolError(
                "measurement %d has type '%s'; expected one of %s"
                % (position, measurement_type, ", ".join(VALID_TYPES))
            )

        from_id = str(from_id or "").strip()
        to_id = str(to_id or "").strip()
        if not from_id or not to_id:
            raise ProtocolError(
                "measurement %d ('%s') is missing a landmark reference. A "
                "template must record which landmarks it measures between."
                % (position, name)
            )

        measurements.append({
            "id": identifier,
            "name": name,
            "from_landmark_id": from_id,
            "from_landmark_name": " ".join(str(from_name or "").split()),
            "to_landmark_id": to_id,
            "to_landmark_name": " ".join(str(to_name or "").split()),
            "type": measurement_type,
            "enabled": bool(enabled),
            "notes": str(notes or ""),
        })

    if not measurements:
        raise ProtocolError("a measurement template must contain at least one "
                            "measurement")

    document = {
        "format": MEASUREMENT_FORMAT,
        "version": MEASUREMENT_VERSION,
        "measurement_protocol_name":
            " ".join(str(protocol_name or "").split()) or "Untitled Measurements",
        "measurements": measurements,
    }
    _assert_no_result_data(document)
    return document


def _assert_no_result_data(document):
    """Fail loudly if a result or a coordinate reached the template."""
    for entry in document.get("measurements", []):
        offending = sorted(set(entry) & (RESULT_KEYS | POSITION_KEYS))
        if offending:
            raise ProtocolError(
                "refusing to write scan-specific data into a measurement "
                "template: %s" % ", ".join(offending)
            )
        unknown = sorted(set(entry) - MEASUREMENT_KEYS)
        if unknown:
            raise ProtocolError(
                "unexpected key(s) in a measurement entry: %s"
                % ", ".join(unknown)
            )


def dumps_measurements(protocol_name, entries, indent=2):
    return json.dumps(build_measurements(protocol_name, entries), indent=indent,
                      ensure_ascii=False) + "\n"


def save_measurements(path, protocol_name, entries, indent=2):
    text = dumps_measurements(protocol_name, entries, indent=indent)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return text


def loads_measurements(text):
    """Parse a measurement template. Returns (protocol_name, entries).

    `entries` are dicts with the keys of MEASUREMENT_KEYS, in file order.
    """
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise ProtocolError("not valid JSON: %s" % exc)

    if not isinstance(document, dict):
        raise ProtocolError(
            "expected a JSON object at the top level, found %s"
            % type(document).__name__
        )

    declared = document.get("format")
    if declared is not None and declared != MEASUREMENT_FORMAT:
        if declared == FORMAT:
            raise ProtocolError(
                "this is a LANDMARK protocol, not a measurement template. "
                "Load it with Load Landmark Protocol."
            )
        raise ProtocolError(
            "this file declares format '%s'; expected '%s'"
            % (declared, MEASUREMENT_FORMAT)
        )

    version = document.get("version", MEASUREMENT_VERSION)
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise ProtocolError("version is not a number: %r" % (version,))
    if version > MEASUREMENT_VERSION:
        raise ProtocolError(
            "this template is version %d; this BSMT understands up to %d"
            % (version, MEASUREMENT_VERSION)
        )

    raw = document.get("measurements")
    if not isinstance(raw, list):
        raise ProtocolError("'measurements' must be a list")
    if not raw:
        raise ProtocolError("the template contains no measurements")

    entries = []
    seen_ids = set()
    for index, item in enumerate(raw):
        position = index + 1
        if not isinstance(item, dict):
            raise ProtocolError(
                "measurement %d is %s, expected an object"
                % (position, type(item).__name__)
            )

        offending = sorted(set(item) & (RESULT_KEYS | POSITION_KEYS))
        if offending:
            # Refused, not ignored. A file carrying results or coordinates is
            # scan output; loading it as a template would present one scan's
            # numbers as another scan's definitions.
            raise ProtocolError(
                "measurement %d carries scan-specific data (%s). A template "
                "is a definition; results and coordinates belong to a "
                "specific scan." % (position, ", ".join(offending))
            )

        name = " ".join(str(item.get("name") or "").split())
        if not name:
            raise ProtocolError("measurement %d has no name" % position)

        identifier = str(item.get("id") or "").strip() or "M%02d" % position
        if identifier in seen_ids:
            raise ProtocolError(
                "duplicate measurement id '%s' at position %d"
                % (identifier, position)
            )
        seen_ids.add(identifier)

        measurement_type = str(item.get("type") or "").strip().upper()
        if measurement_type not in VALID_TYPES:
            raise ProtocolError(
                "measurement %d ('%s') has type '%s'; expected one of %s"
                % (position, name, measurement_type, ", ".join(VALID_TYPES))
            )

        from_id = str(item.get("from_landmark_id") or "").strip()
        to_id = str(item.get("to_landmark_id") or "").strip()
        if not from_id or not to_id:
            raise ProtocolError(
                "measurement %d ('%s') is missing a landmark reference"
                % (position, name)
            )

        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ProtocolError(
                "measurement %d ('%s') has a non-boolean 'enabled' value: %r"
                % (position, name, enabled)
            )

        entries.append({
            "id": identifier,
            "name": name,
            "from_landmark_id": from_id,
            "from_landmark_name":
                " ".join(str(item.get("from_landmark_name") or "").split()),
            "to_landmark_id": to_id,
            "to_landmark_name":
                " ".join(str(item.get("to_landmark_name") or "").split()),
            "type": measurement_type,
            "enabled": enabled,
            "notes": str(item.get("notes") or ""),
        })

    name = " ".join(str(document.get("measurement_protocol_name") or "").split())
    return name or "Untitled Measurements", entries


def load_measurements(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise ProtocolError("could not read '%s': %s" % (path, exc))
    return loads_measurements(text)


# ---------------------------------------------------------------------------
# the unified protocol (Milestone 3.11)
# ---------------------------------------------------------------------------
#
# The two formats above split a study's definitions across two files: names in
# one, the measurements between them in the other. In practice they are one
# thing - "what this study measures" - and keeping them apart made it possible
# to load half of it.
#
# This format carries both, keyed by STABLE ID rather than by the "L01"
# protocol id. Within a scene the stable id is already the authoritative
# reference (Milestone 3.1: a dynamic enum remaps by index, so nothing else
# can be trusted). Writing stable ids and RESTORING them on load makes that
# same guarantee hold across files: a measurement that referenced landmark 7
# still references landmark 7 after the protocol is loaded onto a new scan.
#
#     {
#       "format": "bsmt-protocol",
#       "version": 1,
#       "protocol_name": "Design X posture study",
#       "landmarks":   [{"stable_id": 1, "id": "L01", "name": "Neck_F",
#                        "notes": ""}],
#       "measurements": [{"stable_id": 1, "id": "M01", "name": "Neck to Waist",
#                         "from_stable_id": 1, "to_stable_id": 2,
#                         "type": "BOTH", "enabled": true, "notes": ""}]
#     }

PROTOCOL_FORMAT = "bsmt-protocol"
PROTOCOL_VERSION = 1

#: Everything a protocol entry may carry. Anything else is refused on read
#: rather than ignored: a file with a triangle index in it is not a protocol,
#: and treating it as one would hide the mistake that produced it.
PROTOCOL_LANDMARK_KEYS = frozenset({"stable_id", "id", "name", "notes"})
PROTOCOL_MEASUREMENT_KEYS = frozenset({
    "stable_id", "id", "name", "from_stable_id", "to_stable_id",
    "from_landmark_name", "to_landmark_name", "type", "enabled", "notes",
})

#: Keys that would make the file about one subject rather than one study.
SUBJECT_KEYS = frozenset({
    "subject_id", "subject", "condition", "scan_id", "scan", "session",
    "participant", "participant_id",
})


def build_protocol(protocol_name, landmark_entries, measurement_entries):
    """Assemble a unified protocol. Definitions only.

    `landmark_entries` are (stable_id, protocol_id, name, notes).
    `measurement_entries` are (stable_id, protocol_id, name, from_stable_id,
    to_stable_id, type, enabled, notes).

    Every measurement reference must resolve to a landmark IN THIS FILE. A
    protocol that references a landmark it does not define is not portable,
    and finding that out at load time on someone else's scan is too late.
    """
    landmarks = []
    seen_stable = set()
    seen_ids = set()
    for index, entry in enumerate(landmark_entries):
        position = index + 1
        stable_id, protocol_id, name, notes = entry
        try:
            stable_id = int(stable_id)
        except (TypeError, ValueError):
            raise ProtocolError("landmark %d has a non-numeric stable id"
                                % position)
        if stable_id <= 0:
            raise ProtocolError("landmark %d has stable id %d; stable ids "
                                "start at 1" % (position, stable_id))
        if stable_id in seen_stable:
            raise ProtocolError("duplicate landmark stable id %d at position "
                                "%d" % (stable_id, position))
        seen_stable.add(stable_id)

        name = " ".join(str(name or "").split())
        if not name:
            raise ProtocolError("landmark %d has an empty name" % position)

        protocol_id = str(protocol_id or "").strip() or "L%02d" % position
        if protocol_id in seen_ids:
            raise ProtocolError("duplicate landmark id '%s' at position %d"
                                % (protocol_id, position))
        seen_ids.add(protocol_id)

        landmarks.append({
            "stable_id": stable_id,
            "id": protocol_id,
            "name": name,
            "notes": str(notes or ""),
        })

    if not landmarks:
        raise ProtocolError("a protocol must define at least one landmark")

    by_stable = {entry["stable_id"]: entry for entry in landmarks}

    measurements = []
    seen_stable = set()
    seen_ids = set()
    for index, entry in enumerate(measurement_entries):
        position = index + 1
        (stable_id, protocol_id, name, from_stable, to_stable,
         measurement_type, enabled, notes) = entry
        try:
            stable_id = int(stable_id)
            from_stable = int(from_stable)
            to_stable = int(to_stable)
        except (TypeError, ValueError):
            raise ProtocolError("measurement %d has a non-numeric id"
                                % position)
        if stable_id in seen_stable:
            raise ProtocolError("duplicate measurement stable id %d at "
                                "position %d" % (stable_id, position))
        seen_stable.add(stable_id)

        name = " ".join(str(name or "").split())
        if not name:
            raise ProtocolError("measurement %d has an empty name" % position)

        protocol_id = str(protocol_id or "").strip() or "M%02d" % position
        if protocol_id in seen_ids:
            raise ProtocolError("duplicate measurement id '%s' at position %d"
                                % (protocol_id, position))
        seen_ids.add(protocol_id)

        measurement_type = str(measurement_type or "").strip().upper()
        if measurement_type not in VALID_TYPES:
            raise ProtocolError(
                "measurement %d has type '%s'; expected one of %s"
                % (position, measurement_type, ", ".join(VALID_TYPES)))

        for label, reference in (("from", from_stable), ("to", to_stable)):
            if reference not in by_stable:
                raise ProtocolError(
                    "measurement %d ('%s') references landmark stable id %d, "
                    "which this protocol does not define"
                    % (position, name, reference))
        if from_stable == to_stable:
            raise ProtocolError(
                "measurement %d ('%s') has the same landmark at both ends"
                % (position, name))

        measurements.append({
            "stable_id": stable_id,
            "id": protocol_id,
            "name": name,
            "from_stable_id": from_stable,
            "to_stable_id": to_stable,
            # Names travel for diagnosis only; the stable id is authoritative.
            "from_landmark_name": by_stable[from_stable]["name"],
            "to_landmark_name": by_stable[to_stable]["name"],
            "type": measurement_type,
            "enabled": bool(enabled),
            "notes": str(notes or ""),
        })

    document = {
        "format": PROTOCOL_FORMAT,
        "version": PROTOCOL_VERSION,
        "protocol_name": (" ".join(str(protocol_name or "").split())
                          or "Untitled Protocol"),
        "landmarks": landmarks,
        "measurements": measurements,
    }
    _assert_protocol_is_portable(document)
    return document


def _assert_protocol_is_portable(document):
    """Fail loudly if anything scan-, result- or subject-specific got in.

    Checked on the way out as well as on the way in. A protocol that carries
    one subject's data is the failure this whole format exists to prevent, so
    it is worth catching at the moment it is created rather than the moment
    someone else loads it.
    """
    for key in sorted(set(document) & (POSITION_KEYS | RESULT_KEYS
                                       | SUBJECT_KEYS)):
        raise ProtocolError(
            "a protocol must not carry '%s'; it describes a study, not a scan"
            % key)
    for section, allowed in (("landmarks", PROTOCOL_LANDMARK_KEYS),
                             ("measurements", PROTOCOL_MEASUREMENT_KEYS)):
        for entry in document.get(section, []):
            offending = sorted(set(entry) - allowed)
            if offending:
                raise ProtocolError(
                    "%s entry carries %s, which a protocol must not hold; a "
                    "protocol describes a study, not a scan"
                    % (section[:-1], ", ".join(offending)))


def dumps_protocol(protocol_name, landmark_entries, measurement_entries,
                   indent=2):
    return json.dumps(
        build_protocol(protocol_name, landmark_entries, measurement_entries),
        indent=indent, ensure_ascii=False,
    ) + "\n"


def save_protocol(path, protocol_name, landmark_entries, measurement_entries,
                  indent=2):
    text = dumps_protocol(protocol_name, landmark_entries,
                          measurement_entries, indent)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        raise ProtocolError("could not write '%s': %s" % (path, exc))
    return path


def loads_protocol(text):
    """Parse a unified protocol. Returns (name, landmarks, measurements).

    Landmarks are (stable_id, protocol_id, name, notes).
    Measurements are (stable_id, protocol_id, name, from_stable_id,
    to_stable_id, type, enabled, notes).
    """
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise ProtocolError("not valid JSON: %s" % exc)
    if not isinstance(document, dict):
        raise ProtocolError("a protocol file must contain a JSON object")

    fmt = document.get("format")
    if fmt != PROTOCOL_FORMAT:
        raise ProtocolError(
            "this is not a BSMT protocol (format is %r, expected %r)"
            % (fmt, PROTOCOL_FORMAT))
    version = document.get("version")
    if not isinstance(version, int) or version > PROTOCOL_VERSION:
        raise ProtocolError(
            "protocol version %r is newer than this BSMT understands (%d)"
            % (version, PROTOCOL_VERSION))

    _assert_protocol_is_portable(document)

    raw_landmarks = document.get("landmarks")
    if not isinstance(raw_landmarks, list) or not raw_landmarks:
        raise ProtocolError("the protocol defines no landmarks")

    landmarks = []
    by_stable = {}
    for index, entry in enumerate(raw_landmarks):
        position = index + 1
        if not isinstance(entry, dict):
            raise ProtocolError("landmark %d is not an object" % position)
        stable_id = entry.get("stable_id")
        if not isinstance(stable_id, int) or stable_id <= 0:
            raise ProtocolError("landmark %d has no usable stable id"
                                % position)
        if stable_id in by_stable:
            raise ProtocolError("duplicate landmark stable id %d" % stable_id)
        name = " ".join(str(entry.get("name") or "").split())
        if not name:
            raise ProtocolError("landmark %d has an empty name" % position)
        record = (stable_id,
                  str(entry.get("id") or "").strip() or "L%02d" % position,
                  name, str(entry.get("notes") or ""))
        by_stable[stable_id] = name
        landmarks.append(record)

    measurements = []
    seen = set()
    for index, entry in enumerate(document.get("measurements") or []):
        position = index + 1
        if not isinstance(entry, dict):
            raise ProtocolError("measurement %d is not an object" % position)
        stable_id = entry.get("stable_id")
        if not isinstance(stable_id, int) or stable_id <= 0:
            raise ProtocolError("measurement %d has no usable stable id"
                                % position)
        if stable_id in seen:
            raise ProtocolError("duplicate measurement stable id %d"
                                % stable_id)
        seen.add(stable_id)
        name = " ".join(str(entry.get("name") or "").split())
        if not name:
            raise ProtocolError("measurement %d has an empty name" % position)
        from_stable = entry.get("from_stable_id")
        to_stable = entry.get("to_stable_id")
        for label, reference in (("from", from_stable), ("to", to_stable)):
            if not isinstance(reference, int) or reference not in by_stable:
                raise ProtocolError(
                    "measurement %d ('%s') references %s landmark %r, which "
                    "this protocol does not define"
                    % (position, name, label, reference))
        measurement_type = str(entry.get("type") or "").strip().upper()
        if measurement_type not in VALID_TYPES:
            raise ProtocolError(
                "measurement %d ('%s') has type %r; expected one of %s"
                % (position, name, entry.get("type"), ", ".join(VALID_TYPES)))
        measurements.append((
            stable_id,
            str(entry.get("id") or "").strip() or "M%02d" % position,
            name, from_stable, to_stable, measurement_type,
            bool(entry.get("enabled", True)),
            str(entry.get("notes") or ""),
        ))

    name = " ".join(str(document.get("protocol_name") or "").split())
    return name or "Untitled Protocol", landmarks, measurements


def load_protocol(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise ProtocolError("could not read '%s': %s" % (path, exc))
    return loads_protocol(text)
