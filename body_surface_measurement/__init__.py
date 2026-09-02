"""Body Surface Measurement Tool (BSMT) - Phase 1.

Phase 1 scope: pick two points on a mesh surface by ray casting from the
viewport, show markers and a connecting line, and report the straight-line
(Euclidean) distance in millimetres.

Deliberately NOT in this phase: geodesic / surface distance, preprocessing,
automatic landmark detection, mesh repair, cropping, measurement templates.
"""

bl_info = {
    "name": "Body Surface Measurement Tool (BSMT)",
    "author": "BSMT",
    "version": (0, 18, 0),
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
        "reusable protocols"
    ),
    "category": "3D View",
}

if "bpy" in locals():
    # Support Blender's "Reload Scripts" without a restart.
    import importlib

    from . import (
        alignment, attach, export, geodesic, landmarks, measurement,
        measurements, meshrepair, overlay, panels, picking, preprocess,
        protocol, readiness, repair, scancopy, state, visualization, viz,
        operators,
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
    importlib.reload(visualization)
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
        measurements, meshrepair, operators, overlay, panels, picking,
        preprocess, protocol, readiness, repair, scancopy, state,
        visualization, viz,
    )

import bpy  # noqa: E402  (kept after the reload guard on purpose)


# Order matters: state defines the PropertyGroup the UI reads from.
_MODULES = (state, operators, panels)


def _version_string():
    return ".".join(str(part) for part in bl_info["version"])


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


@bpy.app.handlers.persistent
def _on_load_post(_path):
    """Sweep legacy markers in a file opened after the add-on registered."""
    _sweep_legacy_landmark_markers()


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
