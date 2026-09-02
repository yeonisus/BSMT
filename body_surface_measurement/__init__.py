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
    "version": (0, 7, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > BSMT",
    "description": (
        "Phase 1: straight-line distance between two picked surface points. "
        "Phase 2 milestone 2.1: canonical mesh, BVH picking and SurfacePoint. "
        "Milestone 2.3: exact bounded MMP surface (geodesic) distance"
    ),
    "category": "3D View",
}

if "bpy" in locals():
    # Support Blender's "Reload Scripts" without a restart.
    import importlib

    from . import (
        attach, geodesic, measurement, panels, picking, state, visualization,
        operators,
    )

    importlib.reload(geodesic)
    geodesic.reload_submodules()
    importlib.reload(measurement)
    importlib.reload(visualization)
    importlib.reload(state)
    importlib.reload(picking)
    importlib.reload(operators)
    importlib.reload(panels)
    importlib.reload(attach)
else:
    from . import (
        attach, geodesic, measurement, operators, panels, picking, state,
        visualization,
    )

import bpy  # noqa: E402  (kept after the reload guard on purpose)


# Order matters: state defines the PropertyGroup the UI reads from.
_MODULES = (state, operators, panels)


def _version_string():
    return ".".join(str(part) for part in bl_info["version"])


def register():
    for module in _MODULES:
        module.register()

    # Canonical mesh cache invalidation on geometry change (never on transform).
    # Order matters: the cache handler runs first, so by the time the
    # attachment handler runs a cleared cache already means "geometry changed".
    if geodesic.MESHCACHE_AVAILABLE and geodesic.meshcache is not None:
        geodesic.meshcache.register_handlers()
    attach.register()

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
    attach.unregister()
    if geodesic.MESHCACHE_AVAILABLE and geodesic.meshcache is not None:
        geodesic.meshcache.unregister_handlers()
    for module in reversed(_MODULES):
        module.unregister()


if __name__ == "__main__":
    register()
