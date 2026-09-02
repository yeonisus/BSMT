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

#: Keys that would make the file scan-specific. Never written; refused on read.
POSITION_KEYS = frozenset({
    "triangle_index", "barycentric", "barycentric_coordinates",
    "component_id", "geometry_hash", "canonical_mesh_hash",
    "local_xyz", "world_xyz", "physical_mm_xyz", "source_object",
    "surface_point", "position", "xyz", "coordinates",
})

#: Identity keys a landmark entry may carry.
ENTRY_KEYS = frozenset({"id", "name", "notes"})


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
