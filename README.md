# BSMT — Body Surface Measurement Tool

A Blender add-on for **anthropometric measurement on textured 3D human body
scans**. BSMT measures the distance between two points *across the surface* of
a scan — the exact geodesic distance — as well as the straight line between
them, and keeps the record needed to reproduce a measurement later.

**Research software.** BSMT is written for one lab's own studies. It is not a
validated medical or clinical instrument, it has not been through any
regulatory process, and its numbers should be treated as research data with
the caveats in [Limitations](#limitations).

---

## What it does

| Stage | What happens |
|---|---|
| **Import** | A textured OBJ (with MTL and image) or a PLY with vertex colors |
| **Scan Preprocessing** | Builds a lighter, still-textured *measurement mesh*. The source scan is never modified |
| **Mesh Repair** | Finds and conservatively repairs non-manifold edges and small holes, one region at a time, reverting anything that does not improve the topology |
| **Alignment** | Rigidly aligns the scan to an anatomical frame, by hand or from four reference points. Object transform only — no vertex is moved |
| **Landmarks** | Named points picked on the surface, stored as triangle + barycentric coordinates rather than as an XYZ, so they survive any rigid transform |
| **Measurements** | Researcher-defined pairs of landmarks. Straight distance, exact surface distance, or both |
| **Surface Path** | The exact geodesic polyline a surface distance follows, drawn in the viewport. Solved only when you press the button, then cached per measurement — showing, hiding, restyling or moving it never re-solves |
| **Export** | Measurements and landmarks as CSV, plus a reusable protocol as JSON |

Distances are always reported in **millimetres**.

## Requirements

- **Blender 4.5 LTS** (developed and validated on 4.5.13). Blender 4.2 is the
  minimum the extension package declares, but only 4.5 has been tested.
- No manual Python setup. The exact geodesic solver ships inside the release
  package as a platform wheel and Blender installs it for you.

## Validated platforms

| Platform | Status |
|---|---|
| macOS 26, Apple Silicon (ARM64), Blender 4.5.13 | **Validated** — full workflow, automated and manual |
| Windows 10/11 x64, Blender 4.5.13 | **Packaged, not yet verified on hardware.** The Windows wheel is the correct ABI and the source has been audited for platform assumptions, but no one has run BSMT on Windows. See [docs/windows_acceptance.md](docs/windows_acceptance.md) |
| Linux x64 | Not packaged. A wheel exists upstream; nothing has been tested |

## Installation

See **[INSTALL.md](INSTALL.md)**. In short: download the release ZIP, then in
Blender use *Preferences → Get Extensions → Install from Disk*, and enable it.
No Terminal or PowerShell is needed.

## Typical workflow

1. Import the scan.
2. **Scan Preprocessing** → *Create Measurement Mesh*. Work on the copy from
   here on; the panel names which mesh a measurement belongs to.
3. **Mesh Repair** → *Analyze Mesh*, then *Repair Local Defects* if the
   readiness line reports non-manifold edges.
4. **Alignment** → align the scan, if the study needs a common frame.
5. **Landmark Manager** → add landmarks, pick each one on the surface.
6. **Measurement Manager** → *Add Measurement*, choose From and To, then
   *Calculate All*.
7. **Session and Export** → fill in Subject ID / Condition / Scan ID, then
   *Measurements CSV* and *Landmarks CSV*.
8. *Save Protocol* once, and load it for every later subject.

The top of the sidebar shows a single readiness line — `READY FOR MEASUREMENT`
or `NOT READY: <reason>` — which is the fastest way to find out what is
missing.

## Limitations

- **The measurement mesh is a representation, not the scan.** Decimation
  changes the polyhedral surface, so a surface distance on the copy is not
  identical to one on the original. The triangle counts and the method are
  recorded in every export.
- **Exact geodesic distance is exact for the mesh, not for the body.** It is
  the true shortest path across the triangulated surface it is given.
- **Non-manifold topology is refused, not worked around.** BSMT will not
  produce a surface distance on a mesh the solver is not safe on.
- **Nothing is silently approximated.** A measurement that cannot be computed
  reports a failure state; an uncalculated value exports as a blank field, not
  a zero.
- **A rigid alignment restores to single precision**, not bit-exactly:
  Blender's transform setters decompose to float32, so an Apply/Reset round
  trip leaves ~6e-08 units, about 1e-4 mm on a 1.7 m body. It does not affect
  any stored measurement.
- **No automatic landmark detection.** Every landmark is placed by a human.

## The exact geodesic dependency

Surface distance and surface paths use
[pygeodesic](https://github.com/mhogg/pygeodesic) 0.1.11, a Cython wrapper of
Kirsanov's implementation of the exact MMP algorithm (Mitchell, Mount and
Papadimitriou, 1987). It is a **native** extension module, so it must match the
platform and the Python ABI — which is why the release package carries one
wheel per platform.

If the solver is missing, BSMT still enables and everything except surface
distance and surface paths keeps working; those report a clear dependency
error. The *About BSMT* section of *Session and Export* says which state you
are in.

## Licensing

**Not yet decided.** See [docs/LICENSING.md](docs/LICENSING.md) for the
third-party licences involved and the decision the project owner needs to
make. The extension manifest currently carries a **provisional** value that
must be confirmed before BSMT is distributed to anyone.

## Documentation

- [INSTALL.md](INSTALL.md) — installation, and what to do when it goes wrong
- [CHANGELOG.md](CHANGELOG.md) — what changed in each version
- [docs/LICENSING.md](docs/LICENSING.md) — third-party licences, open decision
- [docs/windows_acceptance.md](docs/windows_acceptance.md) — the checklist a
  Windows machine must pass
- `PROJECT_SPEC.md` — the full design record, including every defect found and
  why each decision was made
