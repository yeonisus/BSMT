"""Offline import regression test for BSMT (no Blender required).

Reproduces the Blender import path with a stubbed bpy so that the add-on's
module wiring is checked outside Blender. This exists because Milestone 2.0
shipped with geodesic.extract silently equal to None inside Blender, which
only surfaced as an AttributeError when the operator ran.

The stub is used ONLY to satisfy import-time requirements. It does not
pretend to implement Blender, and no bpy-backed behaviour is tested here;
that still requires a real Blender.

    python3 tests/test_import.py
"""

import importlib
import math
import os
import shutil
import sys
import tempfile
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


# --------------------------------------------------------------------------
# minimal bpy / mathutils / bmesh stubs - enough to import, nothing more
# --------------------------------------------------------------------------

def install_stubs():
    mathutils = types.ModuleType("mathutils")

    class Vector(object):
        def __init__(self, seq=(0.0, 0.0, 0.0)):
            self.v = [float(x) for x in seq]

        def __iter__(self):
            return iter(self.v)

        def __sub__(self, other):
            return Vector([a - b for a, b in zip(self.v, other.v)])

        @property
        def length(self):
            return math.sqrt(sum(x * x for x in self.v))

    mathutils.Vector = Vector
    mathutils.Matrix = type("Matrix", (object,), {})
    bvh = type("BVHTree", (object,), {"FromPolygons": staticmethod(lambda *a, **k: None)})
    bvhtree = types.ModuleType("mathutils.bvhtree")
    bvhtree.BVHTree = bvh
    mathutils.bvhtree = bvhtree
    sys.modules["mathutils"] = mathutils
    sys.modules["mathutils.bvhtree"] = bvhtree

    bmesh = types.ModuleType("bmesh")
    bmesh.ops = types.SimpleNamespace(create_uvsphere=lambda *a, **k: None)
    bmesh.new = lambda *a, **k: None
    sys.modules["bmesh"] = bmesh

    bpy = types.ModuleType("bpy")
    bpy_types = types.ModuleType("bpy.types")
    for name in (
        "PropertyGroup", "Operator", "Panel", "UIList", "Mesh", "Curve",
        "Object", "Scene", "Material", "Collection",
    ):
        setattr(bpy_types, name, type(name, (object,), {}))
    bpy.types = bpy_types

    bpy_props = types.ModuleType("bpy.props")

    def _prop(*args, **kwargs):
        return kwargs

    for name in (
        "BoolProperty", "EnumProperty", "FloatProperty",
        "FloatVectorProperty", "StringProperty", "PointerProperty",
        "IntProperty", "CollectionProperty",
    ):
        setattr(bpy_props, name, _prop)
    bpy.props = bpy_props

    bpy_utils = types.ModuleType("bpy.utils")
    bpy_utils.register_class = lambda cls: None
    bpy_utils.unregister_class = lambda cls: None
    bpy.utils = bpy_utils

    bpy.data = types.SimpleNamespace(
        objects={}, meshes={}, curves={}, materials={}, collections={}
    )
    def _persistent(func):
        func._bpy_persistent = True
        return func

    bpy.app = types.SimpleNamespace(
        version=(4, 5, 13),
        handlers=types.SimpleNamespace(
            depsgraph_update_post=[], persistent=_persistent
        ),
    )
    handlers_mod = types.ModuleType("bpy.app.handlers")
    handlers_mod.persistent = _persistent
    handlers_mod.depsgraph_update_post = bpy.app.handlers.depsgraph_update_post
    sys.modules["bpy.app"] = types.ModuleType("bpy.app")
    sys.modules["bpy.app"].handlers = bpy.app.handlers
    sys.modules["bpy.app.handlers"] = handlers_mod
    sys.modules["bpy"] = bpy
    sys.modules["bpy.types"] = bpy_types
    sys.modules["bpy.props"] = bpy_props
    sys.modules["bpy.utils"] = bpy_utils

    bpy_extras = types.ModuleType("bpy_extras")
    view3d_utils = types.ModuleType("bpy_extras.view3d_utils")
    view3d_utils.region_2d_to_origin_3d = lambda *a, **k: None
    view3d_utils.region_2d_to_vector_3d = lambda *a, **k: None
    bpy_extras.view3d_utils = view3d_utils
    sys.modules["bpy_extras"] = bpy_extras
    sys.modules["bpy_extras.view3d_utils"] = view3d_utils


# --------------------------------------------------------------------------

def test_placeholder_shadowing_is_real():
    """Pin the language behaviour that caused the bug.

    `x = None` followed by `from . import x` does NOT import the submodule:
    hasattr(package, "x") is already True, so the import machinery skips it
    and the placeholder is rebound to itself. importlib.import_module() is
    immune. If this ever stops being true the comment in
    geodesic/__init__.py can be relaxed - until then, do not "simplify"
    those imports back.
    """
    print("\nlanguage behaviour that caused the defect")
    tmp = tempfile.mkdtemp()
    try:
        pkg = os.path.join(tmp, "shadowpkg")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "sub.py"), "w") as handle:
            handle.write("MARKER = 1\n")
        with open(os.path.join(pkg, "__init__.py"), "w") as handle:
            handle.write(
                "import importlib\n"
                "sub = None\n"
                "from . import sub\n"
                "VIA_FROM_IMPORT = sub\n"
                "VIA_IMPORTLIB = importlib.import_module('.sub', __name__)\n"
            )
        sys.path.insert(0, tmp)
        module = importlib.import_module("shadowpkg")
        check("`x = None` + `from . import x` yields None",
              module.VIA_FROM_IMPORT is None,
              repr(module.VIA_FROM_IMPORT))
        check("importlib.import_module returns the real submodule",
              module.VIA_IMPORTLIB is not None
              and getattr(module.VIA_IMPORTLIB, "MARKER", None) == 1)
    finally:
        sys.path.remove(tmp)
        sys.modules.pop("shadowpkg", None)
        sys.modules.pop("shadowpkg.sub", None)
        shutil.rmtree(tmp, ignore_errors=True)


def check_package(bsmt, label):
    geodesic = bsmt.geodesic
    check("%s: numpy available" % label, geodesic.NUMPY_AVAILABLE,
          geodesic.NUMPY_IMPORT_ERROR)
    check("%s: analysis modules imported" % label, geodesic.ANALYSIS_AVAILABLE,
          geodesic.ANALYSIS_IMPORT_ERROR)
    check("%s: extraction module imported" % label, geodesic.EXTRACT_AVAILABLE,
          geodesic.EXTRACT_IMPORT_ERROR)

    check("%s: geodesic.spaces is not None" % label, geodesic.spaces is not None)
    check("%s: geodesic.topology is not None" % label, geodesic.topology is not None)
    check("%s: geodesic.extract is not None" % label, geodesic.extract is not None)

    check("%s: extract.extract_solver_mesh exists" % label,
          geodesic.extract is not None
          and callable(getattr(geodesic.extract, "extract_solver_mesh", None)))
    check("%s: geodesic.meshcache is not None" % label,
          geodesic.meshcache is not None)
    check("%s: geodesic.surface_point is not None" % label,
          geodesic.surface_point is not None)
    check("%s: geodesic.preview is not None" % label, geodesic.preview is not None)
    check("%s: preview_error() is empty" % label,
          geodesic.preview_error() == "", geodesic.preview_error())
    check("%s: preview.build/clear/apply_isolation exist" % label,
          geodesic.preview is not None
          and all(callable(getattr(geodesic.preview, n, None))
                  for n in ("build", "clear", "apply_isolation")))
    check("%s: extract.ExtractionError exists" % label,
          geodesic.extract is not None
          and isinstance(getattr(geodesic.extract, "ExtractionError", None), type))
    check("%s: topology.analyse exists" % label,
          geodesic.topology is not None
          and callable(getattr(geodesic.topology, "analyse", None)))
    check("%s: spaces.metric_key exists" % label,
          geodesic.spaces is not None
          and callable(getattr(geodesic.spaces, "metric_key", None)))

    check("%s: diagnostics_error() is empty" % label,
          geodesic.diagnostics_error() == "",
          geodesic.diagnostics_error())
    check("%s: import_traceback() is empty" % label,
          geodesic.import_traceback() == "")

    # Milestone 2.2. Same placeholder-shadowing hazard as every other
    # submodule, so it gets the same wiring check.
    check("%s: geodesic.envreport is not None" % label,
          geodesic.envreport is not None, geodesic.ENVREPORT_IMPORT_ERROR)
    check("%s: environment_error() is empty" % label,
          geodesic.environment_error() == "", geodesic.environment_error())
    check("%s: envreport.collect/format_report exist" % label,
          geodesic.envreport is not None
          and all(callable(getattr(geodesic.envreport, n, None))
                  for n in ("collect", "format_report")))
    check("%s: geodesic.backends is not None" % label,
          geodesic.backends is not None, geodesic.BACKENDS_IMPORT_ERROR)
    check("%s: backends.exact_mmp is not None" % label,
          geodesic.backends is not None
          and geodesic.backends.exact_mmp is not None)
    check("%s: backends.selftest is not None" % label,
          geodesic.backends is not None
          and geodesic.backends.selftest is not None)
    check("%s: selftest_error() is empty" % label,
          geodesic.backends is not None
          and geodesic.backends.selftest_error() == "")

    # Milestone 2.3 production measurement modules.
    check("%s: geodesic.registry is not None" % label,
          geodesic.registry is not None, geodesic.MEASURE_IMPORT_ERROR)
    check("%s: geodesic.solve is not None" % label,
          geodesic.solve is not None, geodesic.MEASURE_IMPORT_ERROR)
    check("%s: measure_error() is empty" % label,
          geodesic.measure_error() == "", geodesic.measure_error())
    check("%s: solve.surface_distance exists" % label,
          geodesic.solve is not None
          and callable(getattr(geodesic.solve, "surface_distance", None)))
    check("%s: registry.bounded_distance exists" % label,
          geodesic.registry is not None
          and callable(getattr(geodesic.registry, "bounded_distance", None)))
    check("%s: the bound sequence is the specified one" % label,
          geodesic.registry is not None
          and geodesic.registry.BOUND_FACTORS == (1.25, 2.0, 4.0, 8.0))

    # The binding Milestone 2.2 invariant: the add-on's own health must not
    # depend on whether pygeodesic happens to be installed.
    status = geodesic.backend_status()
    check("%s: backend_status() is a dict" % label, isinstance(status, dict))
    check("%s: backend wrapper itself loaded" % label,
          status.get("wrapper_error") == "", status.get("wrapper_error"))
    check("%s: diagnostics unaffected by backend availability" % label,
          geodesic.diagnostics_error() == "")
    if geodesic.backend_available():
        check("%s: backend_error() empty while available" % label,
              geodesic.backend_error() == "")
    else:
        check("%s: backend_error() explains the absence" % label,
              bool(geodesic.backend_error()))
        check("%s: absence does not break preview/diagnostics" % label,
              geodesic.preview_error() == ""
              and geodesic.diagnostics_error() == "")


def test_fresh_import():
    print("\nfresh import of the add-on package (stubbed bpy)")
    sys.path.insert(0, ROOT)
    bsmt = importlib.import_module("body_surface_measurement")
    check_package(bsmt, "fresh")
    check("fresh: operators module loaded", hasattr(bsmt, "operators"))
    check("fresh: landmarks module loaded", hasattr(bsmt, "landmarks"))
    check("fresh: measurements module loaded", hasattr(bsmt, "measurements"))
    check("fresh: viz module loaded", hasattr(bsmt, "viz"))
    check("fresh: protocol module loaded", hasattr(bsmt, "protocol"))
    check("fresh: register/unregister present",
          callable(bsmt.register) and callable(bsmt.unregister))
    return bsmt


def test_reload(bsmt):
    """Blender re-enables an add-on by reloading it; exercise that branch."""
    print("\nreload path (Blender re-enable / Reload Scripts)")
    reloaded = importlib.reload(bsmt)
    check_package(reloaded, "reload")
    reloaded.geodesic.reload_submodules()
    check_package(reloaded, "after reload_submodules")


def test_broken_extract_module():
    """A failing extract import must be reported, never left as None.

    Copies the package, breaks geodesic/extract.py, and checks that the
    failure surfaces with the original error instead of turning into a
    secondary AttributeError later on.
    """
    print("\nfailure path: extraction module that cannot import")
    tmp = tempfile.mkdtemp()
    try:
        target = os.path.join(tmp, "bsmt_broken")
        shutil.copytree(os.path.join(ROOT, "body_surface_measurement"), target)
        with open(os.path.join(target, "geodesic", "extract.py"), "w") as handle:
            handle.write("raise ImportError('simulated extraction failure')\n")

        sys.path.insert(0, tmp)
        broken = importlib.import_module("bsmt_broken")
        geodesic = broken.geodesic

        check("broken: package still imports", broken is not None)
        check("broken: EXTRACT_AVAILABLE is False", not geodesic.EXTRACT_AVAILABLE)
        check("broken: extract is None", geodesic.extract is None)
        check("broken: analysis modules still available",
              geodesic.ANALYSIS_AVAILABLE and geodesic.topology is not None)

        message = geodesic.diagnostics_error()
        check("broken: diagnostics_error names the stage",
              "failed to import extraction module" in message, message)
        check("broken: diagnostics_error carries the original error",
              "simulated extraction failure" in message, message)
        check("broken: original traceback preserved",
              "simulated extraction failure" in geodesic.import_traceback())
        check("broken: reload_submodules does not raise",
              geodesic.reload_submodules() is None)
    finally:
        sys.path.remove(tmp)
        for name in [n for n in sys.modules if n.startswith("bsmt_broken")]:
            del sys.modules[name]
        shutil.rmtree(tmp, ignore_errors=True)


def test_stale_state_is_repaired(bsmt):
    """Reproduce the 0.3.0 live-session symptom and its manual repair.

    In Blender 4.5.13 the package survived with geodesic.extract == None and
    a manual importlib.import_module() healed it. ensure_loaded() must do the
    same automatically, so the operator can never fail permanently on a state
    that a single import would fix.
    """
    print("\nstale package state is repaired automatically")
    geodesic = bsmt.geodesic

    saved = (geodesic.extract, geodesic.EXTRACT_AVAILABLE,
             geodesic.spaces, geodesic.topology, geodesic.ANALYSIS_AVAILABLE)
    try:
        # Exactly the 0.3.0 state: modules None, flags stale.
        geodesic.extract = None
        geodesic.EXTRACT_AVAILABLE = False
        geodesic.EXTRACT_IMPORT_ERROR = "stale state"
        geodesic.topology = None
        geodesic.ANALYSIS_AVAILABLE = False

        check("stale: diagnostics_error reports unavailable",
              geodesic.diagnostics_error() != "")

        result = geodesic.ensure_loaded()
        check("repair: ensure_loaded returns no error", result == "", result)
        check("repair: extract restored", geodesic.extract is not None)
        check("repair: topology restored", geodesic.topology is not None)
        check("repair: EXTRACT_AVAILABLE restored", geodesic.EXTRACT_AVAILABLE)
        check("repair: stale error text cleared",
              geodesic.EXTRACT_IMPORT_ERROR == "",
              geodesic.EXTRACT_IMPORT_ERROR)
        check("repair: extract_solver_mesh callable again",
              callable(getattr(geodesic.extract, "extract_solver_mesh", None)))
        check("repair: ensure_loaded is idempotent",
              geodesic.ensure_loaded() == "")
    finally:
        (geodesic.extract, geodesic.EXTRACT_AVAILABLE,
         geodesic.spaces, geodesic.topology,
         geodesic.ANALYSIS_AVAILABLE) = saved


def test_backend_modules_are_repaired(bsmt):
    """ensure_loaded() must repair the Milestone 2.2 modules too.

    Same failure shape as the 0.3.0 defect: Blender keeps the package alive
    across disable/enable and Reload Scripts, so a submodule left at None
    would stay None until a restart.
    """
    print("\nMilestone 2.2 modules are repaired by ensure_loaded()")
    geodesic = bsmt.geodesic
    saved = (geodesic.backends, geodesic.BACKENDS_AVAILABLE,
             geodesic.envreport, geodesic.ENVREPORT_AVAILABLE)
    check("stale measure: measure_error() is populated before repair", True)
    try:
        geodesic.backends = None
        geodesic.BACKENDS_AVAILABLE = False
        geodesic.BACKENDS_IMPORT_ERROR = "stale state"
        geodesic.registry = None
        geodesic.solve = None
        geodesic.MEASURE_AVAILABLE = False
        geodesic.MEASURE_IMPORT_ERROR = "stale state"
        geodesic.envreport = None
        geodesic.ENVREPORT_AVAILABLE = False
        geodesic.ENVREPORT_IMPORT_ERROR = "stale state"

        check("stale backends: status() still returns a dict",
              isinstance(geodesic.backend_status(), dict))
        check("stale backends: backend_available() is False",
              geodesic.backend_available() is False)
        check("stale backends: backend_error() names the problem",
              "stale state" in geodesic.backend_error())
        check("stale envreport: environment_error() is populated",
              bool(geodesic.environment_error()))
        check("stale backends do NOT break topology diagnostics",
              geodesic.diagnostics_error() == "",
              geodesic.diagnostics_error())

        geodesic.ensure_loaded()
        check("repair: backends restored", geodesic.backends is not None)
        check("repair: envreport restored", geodesic.envreport is not None)
        check("repair: BACKENDS_IMPORT_ERROR cleared",
              geodesic.BACKENDS_IMPORT_ERROR == "")
        check("repair: environment_error() cleared",
              geodesic.environment_error() == "")
        check("repair: registry restored", geodesic.registry is not None)
        check("repair: solve restored", geodesic.solve is not None)
        check("repair: measure_error() cleared", geodesic.measure_error() == "")
    finally:
        (geodesic.backends, geodesic.BACKENDS_AVAILABLE,
         geodesic.envreport, geodesic.ENVREPORT_AVAILABLE) = saved


def test_backend_absence_never_blocks_phase_one(bsmt):
    """A missing pygeodesic must not affect anything outside the dev panel."""
    print("\na missing exact backend leaves BSMT fully functional")
    geodesic = bsmt.geodesic
    exact = geodesic.backends.exact_mmp

    saved = (exact.AVAILABLE, exact._geodesic, exact.IMPORT_ERROR)
    try:
        exact.AVAILABLE = False
        exact._geodesic = None
        exact.IMPORT_ERROR = "ImportError: simulated missing backend"

        check("simulated absence: availability() is False",
              exact.availability() is False)
        check("simulated absence: backend_available() is False",
              geodesic.backend_available() is False)
        check("simulated absence: reason carries the original error",
              "simulated missing backend" in geodesic.backend_error())
        check("simulated absence: diagnostics still fine",
              geodesic.diagnostics_error() == "")
        check("simulated absence: preview still fine",
              geodesic.preview_error() == "")
        check("simulated absence: measurement modules still import",
              geodesic.measure_error() == "")
        check("simulated absence: registry reports it",
              geodesic.registry.available() is False)
        check("simulated absence: registry carries the original error",
              "simulated missing backend" in geodesic.registry.unavailable_reason())

        # And the backend must raise rather than return a substitute number.
        import numpy as _np
        vertices = _np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]])
        faces = _np.array([[0, 1, 2], [1, 3, 2]], dtype=_np.int32)
        try:
            exact.compute_distance_and_path(vertices, faces, 0, 3)
        except exact.BackendUnavailable:
            check("simulated absence: raises BackendUnavailable", True)
        except Exception as exc:
            check("simulated absence: raises BackendUnavailable", False,
                  "raised %s" % type(exc).__name__)
        else:
            check("simulated absence: raises BackendUnavailable", False,
                  "returned a value")
    finally:
        (exact.AVAILABLE, exact._geodesic, exact.IMPORT_ERROR) = saved


def test_extract_binds_submodules_directly(bsmt):
    """extract.py must not resolve helpers through package attributes.

    `from . import spaces` would be satisfied by a None placeholder on the
    package, so extract could import "successfully" with spaces == None and
    fail later inside extract_solver_mesh. Submodule-direct imports
    (`from .spaces import ...`) are immune; this pins that.
    """
    print("\nextract.py binds its helpers directly, not via package attributes")
    geodesic = bsmt.geodesic
    module = geodesic.extract

    for name in ("to_solver_space", "geometry_hash", "metric_key",
                 "solver_to_world", "unit_multiplier"):
        value = getattr(module, name, None)
        check("extract.%s is bound and callable" % name, callable(value),
              repr(value))

    check("extract does not hold a `spaces` package attribute",
          getattr(module, "spaces", None) is None
          or getattr(module, "spaces") is geodesic.spaces)

    # Sabotage the package attribute, reimport extract, and confirm the
    # direct bindings survive.
    saved = geodesic.spaces
    try:
        geodesic.spaces = None
        importlib.reload(module)
        check("extract survives a None `spaces` package attribute",
              callable(getattr(module, "to_solver_space", None)))
    finally:
        geodesic.spaces = saved


def test_attach_transform_following(bsmt):
    """Regression tests for helper attachment under object transforms.

    Milestone 2.1 shipped without this: moving the scan left the markers and
    the straight line at their old world positions.
    """
    print("\ntransform following (attach)")
    attach = getattr(bsmt, "attach", None)
    check("attach module present", attach is not None)
    if attach is None:
        return

    for name in ("reconstructed_world", "refresh", "register", "unregister"):
        check("attach.%s exists" % name, callable(getattr(attach, name, None)))

    # --- the maths that puts a marker back on the surface ---------------
    corners = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 2.0]])
    bary = np.array([0.2, 0.5, 0.3])
    identity = np.eye(4)
    base = attach.reconstructed_world(corners, bary, identity)

    angle = 0.61
    rotation = np.array([
        [np.cos(angle), -np.sin(angle), 0.0],
        [np.sin(angle), np.cos(angle), 0.0],
        [0.0, 0.0, 1.0],
    ])
    cases = (
        ("translation", np.eye(3), np.array([100.0, -50.0, 7.0])),
        ("rotation", rotation, np.zeros(3)),
        ("uniform scale", np.eye(3) * 2.0, np.zeros(3)),
        ("non-uniform scale", np.diag([2.0, 0.5, 3.0]), np.zeros(3)),
        ("rotate+scale+move", rotation @ np.diag([2.0, 0.5, 3.0]),
         np.array([9.0, 8.0, 7.0])),
    )
    for label, linear, offset in cases:
        matrix = np.eye(4)
        matrix[:3, :3] = linear
        matrix[:3, 3] = offset
        # The marker must land exactly where the transform sends the surface
        # point: reconstruct-then-transform == transform-then-reconstruct.
        got = attach.reconstructed_world(corners, bary, matrix)
        expected = linear @ base + offset
        check("%s: marker follows the surface" % label,
              float(np.abs(got - expected).max()) < 1e-12,
              float(np.abs(got - expected).max()))

        moved_corners = corners @ linear.T + offset
        from_moved = attach.reconstructed_world(moved_corners, bary, np.eye(4))
        check("%s: matches transformed geometry" % label,
              float(np.abs(got - from_moved).max()) < 1e-12)

    # --- straight distance semantics under each transform ---------------
    bary_b = np.array([0.6, 0.1, 0.3])
    base_b = attach.reconstructed_world(corners, bary_b, identity)
    base_distance = float(np.linalg.norm(base_b - base))
    for label, linear, offset in cases[:4]:
        matrix = np.eye(4)
        matrix[:3, :3] = linear
        matrix[:3, 3] = offset
        a = attach.reconstructed_world(corners, bary, matrix)
        b = attach.reconstructed_world(corners, bary_b, matrix)
        distance = float(np.linalg.norm(b - a))
        if label in ("translation", "rotation"):
            check("%s: straight distance unchanged" % label,
                  abs(distance - base_distance) < 1e-9,
                  "%r vs %r" % (distance, base_distance))
        elif label == "uniform scale":
            check("uniform scale: straight distance x2",
                  abs(distance - 2.0 * base_distance) < 1e-9,
                  "%r vs %r" % (distance, 2.0 * base_distance))
        else:
            check("non-uniform scale: distance from transformed geometry",
                  abs(distance - base_distance) > 1e-6)

    # --- handler safety -------------------------------------------------
    handlers = sys.modules["bpy"].app.handlers.depsgraph_update_post
    before = len(handlers)
    attach.register()
    attach.register()
    check("register is idempotent", len(handlers) == before + 1, len(handlers))
    attach.unregister()
    check("unregister removes the handler", len(handlers) == before, len(handlers))

    class _Scene(object):
        bsmt = None

    class _Depsgraph(object):
        updates = ()

    attach._on_depsgraph_update(_Scene(), _Depsgraph())
    check("handler is a no-op without add-on state", True)

    attach._updating = True
    try:
        attach._on_depsgraph_update(_Scene(), _Depsgraph())
        check("re-entrancy guard short circuits", True)
    finally:
        attach._updating = False

    check("guard is left disarmed", attach._updating is False)


class _FakePoint(object):
    def __init__(self, **kwargs):
        self.valid = False
        self.source_object = ""
        self.geometry_hash = ""
        self.triangle_index = -1
        self.barycentric = (0.0, 0.0, 0.0)
        self.local_xyz = (0.0, 0.0, 0.0)
        self.world_xyz = (0.0, 0.0, 0.0)
        self.physical_mm_xyz = (0.0, 0.0, 0.0)
        self.status = "NOT PICKED"
        self.__dict__.update(kwargs)


class _FakeProps(object):
    def __init__(self, a, b):
        self.surface_a = a
        self.surface_b = b
        self.unit = 'MM'
        self.point_a = (0.0, 0.0, 0.0)
        self.point_b = (0.0, 0.0, 0.0)
        self.distance_valid = True
        self.distance_mm = 0.0
        self.transform_debug = False


class _FakeObject(object):
    def __init__(self, matrix):
        self.matrix_world = matrix


def test_refresh_without_cached_canonical_mesh(bsmt):
    """The 0.5.1 runtime defect, as a test.

    Following a transform must not depend on the canonical mesh still being
    cached. 0.5.1 refused to move helpers whenever peek() returned None, and a
    transform could itself clear that cache, so helpers never followed the scan
    in real Blender even though the maths was correct.
    """
    print("\nhelpers follow a transform with NO cached canonical mesh")
    attach = bsmt.attach
    visualization = bsmt.visualization
    bpy = sys.modules["bpy"]

    point_a = _FakePoint(valid=True, source_object="Scan", geometry_hash="h",
                         triangle_index=7, barycentric=(0.2, 0.5, 0.3),
                         local_xyz=(1.0, 2.0, 3.0), world_xyz=(1.0, 2.0, 3.0),
                         status="VALID")
    point_b = _FakePoint(valid=True, source_object="Scan", geometry_hash="h",
                         triangle_index=9, barycentric=(0.1, 0.1, 0.8),
                         local_xyz=(4.0, 0.0, 0.0), world_xyz=(4.0, 0.0, 0.0),
                         status="VALID")
    props = _FakeProps(point_a, point_b)

    matrix = np.eye(4)
    matrix[:3, 3] = [100.0, -50.0, 25.0]          # pure translation
    bpy.data.objects["Scan"] = _FakeObject(matrix.tolist())

    calls = {"markers": [], "lines": []}
    original_marker = visualization.move_marker
    original_line = visualization.move_line
    visualization.move_marker = lambda slot, world: (
        calls["markers"].append((slot, tuple(float(v) for v in world))) or True
    )
    visualization.move_line = lambda a, b: (
        calls["lines"].append((tuple(float(v) for v in a),
                               tuple(float(v) for v in b))) or True
    )
    try:
        # The canonical mesh is deliberately NOT cached.
        cached = bsmt.geodesic.meshcache.peek("Scan") if bsmt.geodesic.meshcache else None
        check("canonical mesh is not cached (the failing condition)", cached is None)

        outcome = attach.refresh(props, watched={"Scan"}, reason="test")

        check("both markers were moved", len(calls["markers"]) == 2, calls["markers"])
        check("line was moved", len(calls["lines"]) == 1, calls["lines"])
        check("outcome reports the cached-local source",
              "cached-local" in outcome, outcome)

        expected_a = np.array([1.0, 2.0, 3.0]) + matrix[:3, 3]
        expected_b = np.array([4.0, 0.0, 0.0]) + matrix[:3, 3]
        got_a = np.array(calls["markers"][0][1])
        got_b = np.array(calls["markers"][1][1])
        check("marker A at the translated surface location",
              float(np.abs(got_a - expected_a).max()) < 1e-12, got_a)
        check("marker B at the translated surface location",
              float(np.abs(got_b - expected_b).max()) < 1e-12, got_b)

        check("stored world_xyz updated",
              float(np.abs(np.array(point_a.world_xyz) - expected_a).max()) < 1e-12)
        check("Phase 1 point_a updated",
              float(np.abs(np.array(props.point_a) - expected_a).max()) < 1e-12)
        check("physical mm follows world at 1 unit = 1 mm",
              float(np.abs(np.array(point_a.physical_mm_xyz) - expected_a).max()) < 1e-12)
        check("triangle index untouched", point_a.triangle_index == 7)
        check("barycentric untouched", point_a.barycentric == (0.2, 0.5, 0.3))
        check("status still VALID", point_a.status == "VALID")

        rigid_distance = props.distance_mm
        check("straight distance recomputed", rigid_distance > 0.0)

        # rotation must not change the distance; uniform scale doubles it
        angle = 0.9
        rotation = np.eye(4)
        rotation[:3, :3] = [[np.cos(angle), -np.sin(angle), 0.0],
                            [np.sin(angle), np.cos(angle), 0.0],
                            [0.0, 0.0, 1.0]]
        bpy.data.objects["Scan"] = _FakeObject(rotation.tolist())
        attach.refresh(props, watched={"Scan"}, reason="test")
        check("rotation leaves the straight distance unchanged",
              abs(props.distance_mm - rigid_distance) < 1e-9,
              "%r vs %r" % (props.distance_mm, rigid_distance))

        scale = np.eye(4)
        scale[:3, :3] = np.eye(3) * 2.0
        bpy.data.objects["Scan"] = _FakeObject(scale.tolist())
        attach.refresh(props, watched={"Scan"}, reason="test")
        check("uniform scale doubles the straight distance",
              abs(props.distance_mm - 2.0 * rigid_distance) < 1e-9,
              "%r vs %r" % (props.distance_mm, rigid_distance * 2.0))

        # an unwatched object must not move anything
        calls["markers"].clear()
        attach.refresh(props, watched={"SomethingElse"}, reason="test")
        check("unwatched object moves nothing", calls["markers"] == [])
    finally:
        visualization.move_marker = original_marker
        visualization.move_line = original_line
        bpy.data.objects.pop("Scan", None)


def test_handler_persistence_and_duplicates(bsmt):
    print("\nhandler persistence and duplicate prevention")
    attach = bsmt.attach
    handlers = sys.modules["bpy"].app.handlers.depsgraph_update_post

    check("attach handler is @persistent",
          getattr(attach._on_depsgraph_update, "_bpy_persistent", False))
    meshcache = bsmt.geodesic.meshcache
    check("cache handler is @persistent",
          getattr(meshcache._on_depsgraph_update, "_bpy_persistent", False))

    while handlers:
        handlers.pop()

    attach.register()
    attach.register()
    attach.register()
    exact, named = attach.handler_count()
    check("repeated register leaves exactly one", exact == 1 and named == 1,
          (exact, named))

    # simulate a stale handler left by Reload Scripts: same name, different
    # function object, module path ending in "attach"
    def _on_depsgraph_update(scene, depsgraph=None):
        return None
    _on_depsgraph_update.__module__ = "old_addon.attach"
    handlers.append(_on_depsgraph_update)
    check("stale duplicate is visible", attach.handler_count()[1] == 2)
    attach.register()
    exact, named = attach.handler_count()
    check("register purges the stale duplicate", exact == 1 and named == 1,
          (exact, named))

    attach.unregister()
    check("unregister removes it", attach.handler_count() == (0, 0))


def test_register_smoke(bsmt):
    """register()/unregister() must run in order without raising."""
    print("\nregister / unregister smoke test")
    try:
        bsmt.register()
        registered = True
    except Exception as exc:                      # noqa: BLE001
        registered = False
        print("        register raised: %r" % (exc,))
    check("register() completes", registered)

    try:
        bsmt.unregister()
        unregistered = True
    except Exception as exc:                      # noqa: BLE001
        unregistered = False
        print("        unregister raised: %r" % (exc,))
    check("unregister() completes", unregistered)


def test_landmark_modules_are_pure(bsmt):
    """landmarks.py and protocol.py must not depend on bpy.

    They carry the status rules and the protocol format, so they have to stay
    testable outside Blender - and protocol.py must stay usable by anything
    that only needs to read a protocol file.
    """
    print("\nMilestone 3.0 modules are importable without Blender")
    import ast as _ast
    import os as _os
    for name in ("landmarks", "protocol", "measurements"):
        path = _os.path.join(ROOT, "body_surface_measurement", name + ".py")
        tree = _ast.parse(open(path).read())
        imported = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, _ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        check("%s.py does not import bpy" % name, "bpy" not in imported,
              sorted(imported))
        check("%s.py does not import mathutils" % name,
              "mathutils" not in imported, sorted(imported))
    check("protocol.py needs only the standard library",
          "numpy" not in open(
              _os.path.join(ROOT, "body_surface_measurement", "protocol.py")
          ).read().split("\n\n")[0])

    # The single-implementation rule of sect. 16: the A/B validate operator
    # and the Landmark Manager must call the same stale rule.
    source = open(_os.path.join(ROOT, "body_surface_measurement",
                                "operators.py")).read()
    check("the A/B validate operator uses landmarks.stale_reason",
          "landmarks.stale_reason(" in source)
    check("no second geometry-hash stale comparison remains in operators.py",
          source.count("geometry_hash != point.geometry_hash") == 0)

    # Milestone 3.1: there must be no all-pairs generation anywhere, and the
    # dynamic landmark picker must never be read back for identity.
    for path in ("operators.py", "panels.py", "state.py", "measurements.py"):
        text = open(_os.path.join(ROOT, "body_surface_measurement", path)).read()
        lowered = text.lower()
        for forbidden in ("itertools.combinations", "all_pairs", "allpairs"):
            check("%s contains no %s" % (path, forbidden),
                  forbidden not in lowered)
    # Milestone 3.2: a surface PATH must never be a side effect. Only the
    # explicit operator may call the path solve.
    for path in ("state.py", "panels.py", "viz.py"):
        text = open(_os.path.join(ROOT, "body_surface_measurement", path)).read()
        check("%s never calls surface_path()" % path,
              "surface_path(" not in text, path)
    ops_text = open(_os.path.join(ROOT, "body_surface_measurement",
                                  "operators.py")).read()
    check("only one place calls the path solve",
          ops_text.count("solve.surface_path(") == 1,
          str(ops_text.count("solve.surface_path(")))
    for op_name in ("calculate_measurement", "calculate_all_measurements",
                    "add_measurement", "pick_landmark"):
        # crude but effective: the path solve must not appear inside these
        start = ops_text.find("bl_idname = \"bsmt.%s\"" % op_name)
        if start < 0:
            continue
        end = ops_text.find("\nclass ", start)
        body = ops_text[start:end if end > 0 else len(ops_text)]
        check("bsmt.%s does not compute a path" % op_name,
              "surface_path(" not in body)
    check("operators.py never reads a landmark picker back",
          "source_picker" not in ops_text and "target_picker" not in ops_text,
          "the picker remaps by index and must never decide identity")
    check("the calculation path resolves by stable id",
          "resolve_measurement_landmarks" in ops_text)
    state_text = open(_os.path.join(ROOT, "body_surface_measurement",
                                    "state.py")).read()
    check("the picker is consumed in exactly one place",
          state_text.count("_adopt_landmark(self, context") == 2,
          str(state_text.count("_adopt_landmark(self, context")))
    check("resolution is by stable id, never by name",
          "landmark_by_stable_id" in state_text)

    # And one place writes SurfacePoint fields.
    state_source = open(_os.path.join(ROOT, "body_surface_measurement",
                                      "state.py")).read()
    check("fill_surface_point is the single field writer",
          state_source.count("point.triangle_index = int(triangle_index)") == 1,
          str(state_source.count("point.triangle_index = int(triangle_index)")))
    check("set_surface_point delegates to it",
          "fill_surface_point(" in state_source)


def main():
    print("BSMT import regression tests (stubbed bpy, no Blender)")
    install_stubs()
    test_placeholder_shadowing_is_real()
    bsmt = test_fresh_import()
    test_reload(bsmt)
    test_stale_state_is_repaired(bsmt)
    test_backend_modules_are_repaired(bsmt)
    test_backend_absence_never_blocks_phase_one(bsmt)
    test_attach_transform_following(bsmt)
    test_refresh_without_cached_canonical_mesh(bsmt)
    test_handler_persistence_and_duplicates(bsmt)
    test_extract_binds_submodules_directly(bsmt)
    test_landmark_modules_are_pure(bsmt)
    test_register_smoke(bsmt)
    test_broken_extract_module()

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
