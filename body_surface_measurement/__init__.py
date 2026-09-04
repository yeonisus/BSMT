"""Body Surface Measurement Tool (BSMT) - Phase 1.

Phase 1 scope: pick two points on a mesh surface by ray casting from the
viewport, show markers and a connecting line, and report the straight-line
(Euclidean) distance in millimetres.

Deliberately NOT in this phase: geodesic / surface distance, preprocessing,
automatic landmark detection, mesh repair, cropping, measurement templates.
"""

#: The single source of truth for the version.
#:
#: NOT bl_info. Blender REMOVES bl_info from a module installed as an
#: extension - the manifest is authoritative there - so anything that read
#: bl_info at runtime raised NameError the moment BSMT was installed the
#: modern way. This constant is defined by the module itself, survives both
#: packaging modes, and is what tools/build_release.py reads to stamp the
#: extension manifest, so the two can never disagree.
VERSION = (0, 25, 0)

bl_info = {
    "name": "Body Surface Measurement Tool (BSMT)",
    "author": "BSMT",
    "version": VERSION,
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > BSMT",
    "description": (
        "Phase 1: straight-line distance between two picked surface points. "
        "Phase 2 milestone 2.1: canonical mesh, BVH picking and SurfacePoint. "
        "Milestone 2.3: exact bounded MMP surface (geodesic) distance. "
        "Milestone 3.0: named research landmark manager. "
        "Milestone 3.1: user-defined measurement manager. "
        "Milestone 3.2: measurement visualization and surface paths. "
        "Milestone 3.3: scan preprocessing and solver safety gate. "
        "Milestone 3.4: controlled mesh repair. "
        "Milestone 3.5: automatic local non-manifold repair. "
        "Milestone 3.6: rigid anatomical alignment. "
        "Milestone 3.7: UI wording, measurement drafts, readiness. "
        "Milestone 3.8: landmark labels and display controls. "
        "Milestone 3.9: screen-space landmark markers, with an "
        "optional visible-surface-only mode. "
        "Milestone 3.11: CSV export, session metadata and "
        "reusable protocols. "
        "Milestone 3.14: per-measurement surface path cache, so display "
        "never re-solves. "
        "Milestone 3.15: scan preprocessing v1 - appearance-verified "
        "measurement mesh with a measurement-ready verdict. "
        "Milestone 3.16: workflow-ordered sidebar. "
        "Milestone 3.18: one blocking-defect policy for readiness and the "
        "solver gate. "
        "Milestone 3.19: Mesh Repair v1 - bounded local repair of degenerate "
        "triangles. "
        "Milestone 3.22: alignment validates its own result against the "
        "anatomical axis contract"
    ),
    "category": "3D View",
}

if "bpy" in locals():
    # Support Blender's "Reload Scripts" without a restart.
    import importlib

    from . import (
        alignment, attach, export, geodesic, landmarks, measurement,
        measurements, meshrepair, overlay, panels, pathcache, picking,
        preprocess, protocol, readiness, repair, scancopy, state, timing,
        visualization, viz, operators,
    )

    importlib.reload(geodesic)
    geodesic.reload_submodules()
    importlib.reload(landmarks)
    importlib.reload(alignment)
    importlib.reload(measurements)
    importlib.reload(readiness)
    importlib.reload(export)
    importlib.reload(preprocess)
    importlib.reload(repair)
    importlib.reload(protocol)
    importlib.reload(measurement)
    importlib.reload(timing)
    importlib.reload(visualization)
    importlib.reload(pathcache)
    importlib.reload(overlay)
    importlib.reload(state)
    importlib.reload(scancopy)
    importlib.reload(meshrepair)
    importlib.reload(viz)
    importlib.reload(picking)
    importlib.reload(operators)
    importlib.reload(panels)
    importlib.reload(attach)
else:
    from . import (
        alignment, attach, export, geodesic, landmarks, measurement,
        measurements, meshrepair, operators, overlay, panels, pathcache,
        picking, preprocess, protocol, readiness, repair, scancopy, state,
        timing, visualization, viz,
    )

import bpy  # noqa: E402  (kept after the reload guard on purpose)


# Order matters: state defines the PropertyGroup the UI reads from.
_MODULES = (state, operators, panels)


def _version_string():
    return ".".join(str(part) for part in VERSION)


def _sweep_legacy_landmark_markers():
    """Delete landmark marker OBJECTS left by BSMT 0.16.0 and earlier.

    Since Milestone 3.9 a landmark marker is drawn in screen space, not built.
    A file saved by an older version still contains one sphere per landmark;
    left alone, the researcher would see two markers for every landmark, one
    of them at the wrong size and selectable in the viewport.

    Only objects carrying BSMT's own helper tag AND the landmark prefix are
    touched, so nothing of the researcher's can be caught by this.
    """
    try:
        removed = visualization.clear_landmark_markers()
    except Exception:                                 # pragma: no cover
        return 0
    if removed:
        print("[BSMT] removed %d legacy landmark marker object(s) - markers "
              "are drawn in screen space since 0.17.0" % removed)
    return removed


def _prune_orphan_path_caches():
    """Drop cached polylines whose measurement is gone from this file.

    A cache entry is kept alive by a fake user precisely so it survives with
    no object attached, which also means nothing else will ever collect one.
    Pruning is therefore explicit, and it is keyed on the measurement
    definitions actually present - never on whether a helper is drawn.
    """
    try:
        wanted = set()
        found = False
        # EVERY scene, not just the active one. Measurements live on a Scene,
        # so pruning against one scene's list would delete another scene's
        # solves - and a cached path is minutes of work, not a temp file.
        for scene in bpy.data.scenes:
            collection = getattr(scene, "bsmt_measurements", None)
            if collection is None:
                continue
            found = True
            wanted.update(int(item.stable_id) for item in collection)
        if not found:
            return 0
        removed = pathcache.keep_only(wanted)
    except Exception:                                 # pragma: no cover
        return 0
    if removed:
        print("[BSMT] released %d cached surface path(s) with no measurement"
              % removed)
    return removed


@bpy.app.handlers.persistent
def _on_load_post(_path):
    """Sweep legacy markers in a file opened after the add-on registered."""
    _sweep_legacy_landmark_markers()
    _prune_orphan_path_caches()


def _purge_load_handler():
    handlers = bpy.app.handlers.load_post
    for existing in list(handlers):
        if existing is _on_load_post or (
            getattr(existing, "__name__", "") == "_on_load_post"
            and getattr(existing, "__module__", "").endswith(
                "body_surface_measurement")
        ):
            handlers.remove(existing)


def _register_load_handler():
    _purge_load_handler()
    bpy.app.handlers.load_post.append(_on_load_post)


def _unregister_load_handler():
    _purge_load_handler()


def register():
    for module in _MODULES:
        module.register()

    # Canonical mesh cache invalidation on geometry change (never on transform).
    # Order matters: the cache handler runs first, so by the time the
    # attachment handler runs a cleared cache already means "geometry changed".
    if geodesic.MESHCACHE_AVAILABLE and geodesic.meshcache is not None:
        geodesic.meshcache.register_handlers()
    attach.register()

    # The landmark overlay: markers and name labels, drawn in screen space by
    # one viewport handler rather than built as objects. See overlay.py.
    overlay.register()
    _sweep_legacy_landmark_markers()
    _register_load_handler()

    # Repair and report Phase 2 module state at startup, so a fresh launch
    # never reaches the operator with a partially initialised package.
    status = geodesic.ensure_loaded()
    if status:
        print("[BSMT] %s" % status)
        original = geodesic.import_traceback()
        if original:
            print(original)
    else:
        print(
            "[BSMT] %s registered - topology diagnostics ready"
            % _version_string()
        )


def unregister():
    _unregister_load_handler()
    overlay.unregister()
    attach.unregister()
    if geodesic.MESHCACHE_AVAILABLE and geodesic.meshcache is not None:
        geodesic.meshcache.unregister_handlers()
    for module in reversed(_MODULES):
        module.unregister()


if __name__ == "__main__":
    register()
