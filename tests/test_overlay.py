"""Offline tests for the landmark overlay (Milestones 3.8, 3.9).

    python3 tests/test_overlay.py

`overlay.py` splits deliberately in three: what to draw (pure), the
screen-space geometry of a marker (pure), and the handful of GPU and blf calls
that put it on screen. The first two are tested here. The third cannot be:
Blender refuses to create a GPU shader in background mode, which is also why
the module never builds one at import time.
"""

import importlib.util
import math
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "body_surface_measurement")

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def install_stubs():
    bpy = types.ModuleType("bpy")
    bpy.data = types.SimpleNamespace(objects={})
    bpy.app = types.SimpleNamespace(driver_namespace={})
    bpy.context = types.SimpleNamespace()
    bpy.types = types.SimpleNamespace(SpaceView3D=object, Mesh=object)
    sys.modules["bpy"] = bpy

    bmesh = types.ModuleType("bmesh")
    bmesh.ops = types.SimpleNamespace()
    sys.modules["bmesh"] = bmesh

    mathutils = types.ModuleType("mathutils")
    mathutils.Vector = tuple
    sys.modules["mathutils"] = mathutils
    return bpy


BPY = install_stubs()

package = types.ModuleType("bsmt_pkg")
package.__path__ = [PACKAGE]
sys.modules["bsmt_pkg"] = package


def load(name):
    spec = importlib.util.spec_from_file_location(
        "bsmt_pkg." + name, os.path.join(PACKAGE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["bsmt_pkg." + name] = module
    setattr(package, name, module)
    spec.loader.exec_module(module)
    return module


geodesic_stub = types.ModuleType("bsmt_pkg.geodesic")
geodesic_stub.MESHCACHE_AVAILABLE = False
geodesic_stub.meshcache = None
sys.modules["bsmt_pkg.geodesic"] = geodesic_stub
package.geodesic = geodesic_stub

landmarks = load("landmarks")
measurement = load("measurement")
visualization = load("visualization")
overlay = load("overlay")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

class FakePoint(object):
    def __init__(self, valid=True, world=(0.0, 0.0, 0.0), source="A_BSMT"):
        self.valid = valid
        self.world_xyz = world
        self.source_object = source


class FakeLandmark(object):
    def __init__(self, stable_id, name, status=landmarks.STATUS_VALID,
                 valid=True, world=(0.0, 0.0, 0.0)):
        self.stable_id = stable_id
        self.name = name
        self.display_name = ""
        self.status = status
        self.surface_point = FakePoint(valid, world)

    @property
    def label(self):
        return self.display_name or self.name or "(unnamed)"


class FakeProps(object):
    def __init__(self, **overrides):
        self.landmark_visibility = 'ALWAYS'
        self.show_landmarks = True
        self.show_landmark_labels = True
        self.landmark_marker_color = (0.15, 0.9, 0.35, 1.0)
        self.landmark_marker_size_px = overlay.DEFAULT_MARKER_SIZE
        self.landmark_label_color = (1.0, 1.0, 1.0, 1.0)
        self.landmark_label_size = overlay.DEFAULT_LABEL_SIZE
        self.landmark_label_offset = overlay.DEFAULT_LABEL_OFFSET
        self.landmark_label_shadow = True
        self.landmark_label_scope = 'ALL'
        self.landmark_index = 0
        for key, value in overrides.items():
            setattr(self, key, value)


def three_landmarks():
    return [
        FakeLandmark(1, "P01", world=(10.0, 0.0, 0.0)),
        FakeLandmark(2, "P02", world=(0.0, 20.0, 0.0)),
        FakeLandmark(3, "P03", world=(0.0, 0.0, 30.0)),
    ]


# ---------------------------------------------------------------------------
# every marker is the same size (3.9 sect. 2, 4)
# ---------------------------------------------------------------------------

def test_every_marker_is_identical():
    print("\n[marker] all landmarks get exactly the same marker")
    props = FakeProps()
    items = three_landmarks()
    entries = overlay.entries(props, items, 0)
    radii = {entry["radius"] for entry in entries}
    check("three landmarks, one radius", len(radii) == 1, radii)
    check("and it is half the pixel diameter",
          radii.pop() == props.landmark_marker_size_px * 0.5)

    # The selected landmark is the whole point of this test: 3.8 made its
    # marker bigger, which meant markers no longer read as one size.
    for selected in range(3):
        sizes = {entry["radius"]
                 for entry in overlay.entries(props, items, selected)}
        check("selecting index %d changes no radius" % selected,
              len(sizes) == 1, sizes)
    check("the core radius is identical whatever is selected",
          overlay.entries(props, items, 0)[2]["radius"]
          == overlay.entries(props, items, 2)[2]["radius"])
    check("there is no marker scale factor left in visualization",
          not hasattr(visualization, "SELECTED_MARKER_SCALE"))


def test_marker_size_is_pixels():
    print("\n[marker] the size is screen pixels, and can go small")
    items = three_landmarks()
    for pixels in (2, 3, 6, 12, 20):
        props = FakeProps(landmark_marker_size_px=pixels)
        entry = overlay.entries(props, items, -1)[0]
        check("%2d px -> radius %.1f" % (pixels, pixels * 0.5),
              entry["radius"] == max(1.0, pixels * 0.5))
    check("the default is small enough to be a point marker",
          2 <= overlay.DEFAULT_MARKER_SIZE <= 8, overlay.DEFAULT_MARKER_SIZE)

    source = open(os.path.join(PACKAGE, "overlay.py")).read()
    check("no millimetre conversion reaches a marker",
          "mm_to_units" not in source and "unit_multiplier" not in source)
    check("and the module says the size is in screen pixels",
          "SCREEN PIXELS" in source or "screen pixels" in source.lower())

    state_source = open(os.path.join(PACKAGE, "state.py")).read()
    check("the mm marker property is gone",
          "landmark_marker_size_mm" not in state_source)
    check("the px property is there",
          "landmark_marker_size_px" in state_source)
    check("Point A/B keep their own millimetre setting",
          "marker_size_mm: FloatProperty" in state_source)


def test_selection_is_a_ring():
    print("\n[marker] the selection is shown with a ring, not a bigger dot")
    props = FakeProps()
    items = three_landmarks()
    entries = overlay.entries(props, items, 1)
    check("exactly one entry is marked selected",
          [e["selected"] for e in entries] == [False, True, False])

    drawn = [dict(e, screen=(100.0 + 30 * i, 200.0))
             for i, e in enumerate(entries)]
    discs, rings = overlay.marker_batches(drawn)
    check("every landmark gets a disc",
          sum(len(v) for _c, v in discs) == 3 * overlay.DISC_SEGMENTS * 3,
          sum(len(v) for _c, v in discs))
    check("only the selected one gets a ring", len(rings) == 1, len(rings))
    check("the ring is a closed loop of segments",
          len(rings[0][1]) == overlay.DISC_SEGMENTS * 2)

    radius = entries[1]["radius"]
    ring_radius = overlay.selected_ring_radius(radius)
    check("the ring sits OUTSIDE the core", ring_radius > radius)
    check("and the core is untouched by the selection",
          entries[1]["radius"] == entries[0]["radius"])

    center = drawn[1]["screen"]
    for x, y in rings[0][1]:
        distance = math.hypot(x - center[0], y - center[1])
        if abs(distance - ring_radius) > 1e-9:
            check("every ring vertex is on the ring", False,
                  "%.6f vs %.6f" % (distance, ring_radius))
            break
    else:
        check("every ring vertex is on the ring", True)

    check("nothing is emphasised -> no ring",
          overlay.marker_batches(
              [dict(e, screen=(0.0, 0.0), emphasised=False)
               for e in entries])[1] == [])


def test_emphasis_can_be_switched_off_without_touching_the_markers():
    print("\n[marker] the selection ring follows the workflow stage")
    #
    # The ring says "this is the row your next pick belongs to", which is only
    # true while the researcher is in Landmark Manager. Once they are building
    # measurements or exporting it marks one landmark out for no reason a
    # reader of the viewport could reconstruct, so it goes - and NOTHING else
    # about the landmarks may change with it.
    props = FakeProps()
    items = three_landmarks()
    on = overlay.entries(props, items, 1)
    off = overlay.entries(props, items, 1, emphasise=False)

    check("the same landmarks are drawn either way", len(on) == len(off) == 3)
    check("selection itself is unchanged - it is display state that moved",
          [e["selected"] for e in off] == [False, True, False])
    check("but nothing is emphasised",
          [e["emphasised"] for e in off] == [False, False, False])
    check("and with the emphasis on, exactly the selected one is",
          [e["emphasised"] for e in on] == [False, True, False])

    for index in range(3):
        check("landmark %d keeps its configured radius" % index,
              off[index]["radius"] == on[index]["radius"])
        check("landmark %d keeps its colour" % index,
              off[index]["marker_color"] == on[index]["marker_color"])
        check("landmark %d keeps its world position" % index,
              off[index]["world"] == on[index]["world"])
        check("landmark %d still has its label" % index,
              "text" in off[index] and off[index]["text"] == on[index]["text"])
    check("only the SELECTED label loses its size bonus",
          [e["label_size"] for e in off]
          == [on[0]["label_size"],
              on[1]["label_size"] - overlay.SELECTED_LABEL_BONUS,
              on[2]["label_size"]])

    drawn_on = [dict(e, screen=(100.0 + 30 * i, 200.0))
                for i, e in enumerate(on)]
    drawn_off = [dict(e, screen=(100.0 + 30 * i, 200.0))
                 for i, e in enumerate(off)]
    discs_on, rings_on = overlay.marker_batches(drawn_on)
    discs_off, rings_off = overlay.marker_batches(drawn_off)
    check("the ring batch is empty with the emphasis off", rings_off == [],
          rings_off)
    check("and present with it on", len(rings_on) == 1)
    # Sect. 6 of the request: disable the highlight, do not rebuild markers.
    check("the disc batches are byte-identical - no marker was rebuilt",
          discs_off == discs_on)

    check("SELECTED label scope still shows the selected label",
          [e.get("text") for e in overlay.entries(
              FakeProps(landmark_label_scope='SELECTED'), items, 1,
              emphasise=False) if "text" in e] != [])


# ---------------------------------------------------------------------------
# screen-space geometry
# ---------------------------------------------------------------------------

def test_disc_geometry():
    print("\n[geometry] a marker is a disc of triangles in screen pixels")
    center = (320.0, 240.0)
    radius = 3.0
    vertices = overlay.disc_triangles(center, radius)
    check("three vertices per segment",
          len(vertices) == overlay.DISC_SEGMENTS * 3)
    check("the vertex count is a whole number of triangles",
          len(vertices) % 3 == 0)

    worst = 0.0
    for index in range(0, len(vertices), 3):
        first = vertices[index]
        check_center = (abs(first[0] - center[0]) < 1e-9
                        and abs(first[1] - center[1]) < 1e-9)
        if not check_center:
            check("every triangle starts at the center", False, first)
            break
        for point in vertices[index + 1:index + 3]:
            worst = max(worst, abs(math.hypot(point[0] - center[0],
                                              point[1] - center[1]) - radius))
    else:
        check("every triangle starts at the center", True)
    check("every rim vertex is exactly `radius` away (worst %.2e)" % worst,
          worst < 1e-9)

    check("the disc is centred where it is asked to be",
          abs(sum(v[0] for v in vertices) / len(vertices) - center[0]) < 1e-9)
    check("a bigger radius makes a bigger disc",
          max(v[0] for v in overlay.disc_triangles(center, 10.0))
          > max(v[0] for v in vertices))

    # TRI_FAN and LINE_LOOP were removed from Blender's GPU module in 3.2.
    source = open(os.path.join(PACKAGE, "overlay.py")).read()
    check("no TRI_FAN is used", "'TRI_FAN'" not in source)
    check("no LINE_LOOP is used", "'LINE_LOOP'" not in source)
    check("the disc is drawn as TRIS", "'TRIS'" in source)
    check("the ring is drawn as LINES", "'LINES'" in source)


def test_batches_are_grouped():
    print("\n[geometry] one batch per colour, not one per landmark")
    green = (0.0, 1.0, 0.0, 1.0)
    red = (1.0, 0.0, 0.0, 1.0)
    grouped = overlay.group_by_color([
        (green, [(0, 0)]), (red, [(1, 1)]), (green, [(2, 2)]),
        (red, [(3, 3)]), (green, [(4, 4)]),
    ])
    check("five inputs collapse to two groups", len(grouped) == 2)
    check("in first-seen order", grouped[0][0] == green)
    check("green kept all three", len(grouped[0][1]) == 3)
    check("red kept both", len(grouped[1][1]) == 2)

    props = FakeProps()
    items = [FakeLandmark(i, "P%02d" % i) for i in range(1, 101)]
    drawn = [dict(e, screen=(float(i), 0.0))
             for i, e in enumerate(overlay.entries(props, items, 0))]
    discs, rings = overlay.marker_batches(drawn)
    check("100 identical landmarks are ONE disc batch", len(discs) == 1,
          len(discs))
    check("plus one ring for the selection", len(rings) == 1)

    items[7].status = landmarks.STATUS_STALE
    drawn = [dict(e, screen=(float(i), 0.0))
             for i, e in enumerate(overlay.entries(props, items, 0))]
    discs, _rings = overlay.marker_batches(drawn)
    check("a stale landmark adds exactly one more batch", len(discs) == 2,
          len(discs))


# ---------------------------------------------------------------------------
# anchoring (sect. 2) and visibility
# ---------------------------------------------------------------------------

def test_anchoring():
    print("\n[anchor] marker and label come from the same stored position")
    props = FakeProps()
    items = three_landmarks()
    entries = overlay.entries(props, items, 0)
    check("the anchor is the stored world position",
          [e["world"] for e in entries]
          == [(10.0, 0.0, 0.0), (0.0, 20.0, 0.0), (0.0, 0.0, 30.0)])

    # A rigid transform reaches the overlay by moving world_xyz, which is what
    # attach.refresh_landmarks maintains. Nothing else is involved.
    items[0].surface_point.world_xyz = (99.0, 98.0, 97.0)
    check("moving the landmark moves its marker",
          overlay.entries(props, items, 0)[0]["world"] == (99.0, 98.0, 97.0))

    check("the marker and its label share one entry",
          all("text" in e for e in overlay.entries(props, items, 0)))
    source = open(os.path.join(PACKAGE, "overlay.py")).read()
    check("and one projection, so they cannot separate",
          source.count("location_3d_to_region_2d(region, rv3d") == 1)
    entries_source = source.split("def entries(")[1].split("\ndef ")[0]
    check("no helper object is consulted for the position",
          "bpy.data.objects" not in entries_source)
    check("the position is the stored surface position",
          "point.world_xyz" in entries_source)


def test_visibility():
    print("\n[visibility] the two switches are independent")
    items = three_landmarks()

    props = FakeProps(show_landmarks=False, show_landmark_labels=False)
    check("both off draws nothing", overlay.entries(props, items, 0) == [])

    props = FakeProps(show_landmarks=True, show_landmark_labels=False)
    entries = overlay.entries(props, items, 0)
    check("markers only: three entries", len(entries) == 3)
    check("  with no text", not any("text" in e for e in entries))
    check("  and marker drawing on", all(e["show_marker"] for e in entries))

    props = FakeProps(show_landmarks=False, show_landmark_labels=True)
    entries = overlay.entries(props, items, 0)
    check("labels only: three entries", len(entries) == 3)
    check("  with text", all("text" in e for e in entries))
    check("  and marker drawing off",
          not any(e["show_marker"] for e in entries))
    drawn = [dict(e, screen=(0.0, 0.0)) for e in entries]
    check("  so no disc is batched", overlay.marker_batches(drawn)[0] == [])

    props = FakeProps()
    items[1].surface_point.valid = False
    entries = overlay.entries(props, items, 0)
    check("an unpicked landmark gets neither", len(entries) == 2)
    check("and it is the right one missing",
          {e["stable_id"] for e in entries} == {1, 3})
    items[1].surface_point.valid = True

    props.landmark_label_scope = 'SELECTED'
    entries = overlay.entries(props, items, 2)
    check("SELECTED label scope still draws every MARKER", len(entries) == 3)
    check("but exactly one label",
          sum(1 for e in entries if "text" in e) == 1)
    check("and it is the selected one",
          [e for e in entries if "text" in e][0]["stable_id"] == 3)
    check("labelled() is the labels-only view",
          len(overlay.labelled(props, items, 2)) == 1)

    many = [FakeLandmark(i, "P%03d" % i)
            for i in range(1, overlay.MAX_LANDMARKS + 60)]
    check("a pathological count is capped",
          len(overlay.entries(FakeProps(), many, 0)) == overlay.MAX_LANDMARKS)
    check("and an absent collection is not an error",
          overlay.entries(FakeProps(), None, 0) == [])


# ---------------------------------------------------------------------------
# text and status (sect. 5)
# ---------------------------------------------------------------------------

def test_text_and_status():
    print("\n[status] valid uses the chosen color, the rest do not")
    props = FakeProps()
    items = three_landmarks()

    check("a valid landmark shows just its name",
          overlay.label_text("P01", landmarks.STATUS_VALID) == "P01")
    for status, word in ((landmarks.STATUS_STALE, "STALE"),
                         (landmarks.STATUS_INVALID, "INVALID"),
                         (landmarks.STATUS_NEEDS_REFRESH, "NEEDS REFRESH")):
        text = overlay.label_text("P01", status)
        check("%s is said in words" % status, word in text, text)
        check("  and the name is still there", text.startswith("P01"), text)

    entries = overlay.entries(props, items, 0)
    check("a valid MARKER uses the chosen marker color",
          entries[0]["marker_color"] == props.landmark_marker_color)
    check("a valid LABEL uses the chosen label color",
          entries[0]["label_color"] == props.landmark_label_color)

    items[1].status = landmarks.STATUS_STALE
    entries = overlay.entries(props, items, 0)
    stale = entries[1]
    check("a stale marker ignores the chosen color",
          stale["marker_color"][:3] != props.landmark_marker_color[:3])
    check("  and takes the authoritative status color",
          stale["marker_color"][:3]
          == tuple(float(v)
                   for v in visualization.LANDMARK_STALE_COLOR[:3]))
    check("a stale label does the same",
          stale["label_color"][:3] == stale["marker_color"][:3])
    check("its text says STALE", "STALE" in stale["text"], stale["text"])
    check("and its neighbours are unaffected",
          entries[0]["marker_color"] == props.landmark_marker_color)

    source = open(os.path.join(PACKAGE, "overlay.py")).read()
    check("no status color is hard-coded here",
          "(1.0, 0.45, 0.0" not in source)
    check("the status words come from landmarks.STATUS_SHORT",
          "landmarks.STATUS_SHORT" in source)


def test_text_is_never_cached():
    print("\n[text] renaming shows immediately")
    props = FakeProps()
    items = three_landmarks()
    check("P03 is labelled P03",
          overlay.entries(props, items, 0)[2]["text"] == "P03")
    items[2].name = "Acromion_L"
    check("after renaming, the very next read shows the new name",
          overlay.entries(props, items, 0)[2]["text"] == "Acromion_L")

    # The point of the guard is that no LABEL TEXT is remembered between
    # redraws, so the check is against caching machinery, not the word.
    source = open(os.path.join(PACKAGE, "overlay.py")).read()
    for forbidden in ("_CACHE", "lru_cache", "functools.cache", "@cache"):
        check("overlay.py holds no %s" % forbidden, forbidden not in source)
    check("and the text is read from the landmark each time",
          "item.label" in source.split("def entries(")[1].split("\ndef ")[0])


# ---------------------------------------------------------------------------
# the overlay never writes to the scene
# ---------------------------------------------------------------------------

def test_occlusion_rule():
    print("\n[visibility] what counts as being hidden by the mesh")
    # The ray misses the body entirely: nothing is in the way.
    check("a ray that misses hides nothing",
          not overlay.is_occluded(None, 2.0))

    # The ray hits the landmark's own surface. This is the case that makes a
    # tolerance necessary: without one, every landmark would hide itself.
    check("a hit exactly at the landmark does not hide it",
          not overlay.is_occluded(2.0, 2.0))
    check("nor does floating-point noise around it",
          not overlay.is_occluded(2.0 - 1e-9, 2.0))
    check("nor a hit just behind it", not overlay.is_occluded(2.1, 2.0))

    # The far side of a body: the near surface is ~200 mm in front.
    check("the near surface hides a far-side landmark",
          overlay.is_occluded(1.8, 2.0))
    check("and so does anything clearly in front",
          overlay.is_occluded(0.1, 2.0))

    # The tolerance is a FRACTION of the view distance, so it means the same
    # thing at any zoom and in any unit.
    for distance in (0.02, 2.0, 2000.0):
        margin = distance * overlay.OCCLUSION_TOLERANCE
        check("at distance %g, %g in front is still visible"
              % (distance, margin * 0.5),
              not overlay.is_occluded(distance - margin * 0.5, distance))
        check("  but %g in front is hidden" % (margin * 2.0),
              overlay.is_occluded(distance - margin * 2.0, distance))
    check("the tolerance is millimetres at a metre, not microns",
          0.0005 <= overlay.OCCLUSION_TOLERANCE <= 0.005,
          overlay.OCCLUSION_TOLERANCE)

    check("a degenerate distance hides nothing",
          not overlay.is_occluded(0.5, 0.0) and not overlay.is_occluded(0.5, -1.0))
    check("the tolerance can be overridden for a caller that needs to",
          overlay.is_occluded(1.999, 2.0, tolerance=1e-6))


def test_occlusion_needs_the_owning_mesh():
    print("\n[visibility] each landmark is tested against its own mesh")
    props = FakeProps()
    items = three_landmarks()
    items[0].surface_point.source_object = "A_BSMT"
    items[1].surface_point.source_object = "A_BSMT"
    items[2].surface_point.source_object = "Other"
    entries = overlay.entries(props, items, 0)
    check("every entry names the mesh it was picked on",
          [e["source_object"] for e in entries] == ["A_BSMT", "A_BSMT", "Other"])

    source = open(os.path.join(PACKAGE, "overlay.py")).read()
    check("the mesh is read with peek(), which never builds one",
          "meshcache.peek(" in source)
    check("and never with get(), which would rebuild inside a draw call",
          "meshcache.get(" not in source)
    check("the view ray comes from the same projection the marker uses",
          'entry["screen"]' in source.split("def hide_occluded")[1]
          .split("def _set_font_size")[0])
    check("which is what makes it correct in an orthographic view",
          "region_2d_to_origin_3d" in source)
    check("a visibility failure cannot blank the overlay",
          "traceback.print_exc()" in
          source.split("landmark_visibility == 'OCCLUDED'")[1][:400])
    check("Always on Top does no ray casting at all",
          "landmark_visibility == 'OCCLUDED'" in source)


def test_the_overlay_is_read_only():
    print("\n[safety] drawing cannot change anything")
    source = open(os.path.join(PACKAGE, "overlay.py")).read()
    for forbidden in ("bpy.data.objects.new", "bpy.data.texts",
                      "bpy.data.curves", "bpy.data.meshes", "bpy.ops",
                      "bmesh"):
        check("overlay.py never uses %s" % forbidden, forbidden not in source)
    check("no object of any kind is created",
          "objects.new" not in source and "font_add" not in source
          and "type='FONT'" not in source)
    # The overlay may read the canonical mesh - occlusion needs geometry - but
    # it must never reach the solver, and never build a mesh from a draw call.
    for forbidden in ("registry.", "bounded_distance", "surface_path",
                      "solve.", "meshcache.get("):
        check("overlay.py never uses %s" % forbidden, forbidden not in source)
    check("the draw callback is POST_PIXEL", "'POST_PIXEL'" in source)
    check("the handle survives Reload Scripts",
          "driver_namespace" in source)
    check("the GPU shader is built on first use, never at import",
          "def _shader" in source
          and "from_builtin" not in source.split("def _shader")[0])
    check("a drawing failure cannot take the viewport down",
          "traceback.print_exc()" in source)


def main():
    print("BSMT landmark overlay tests (Milestones 3.8, 3.9)")
    for test in (
        test_every_marker_is_identical,
        test_marker_size_is_pixels,
        test_selection_is_a_ring,
        test_emphasis_can_be_switched_off_without_touching_the_markers,
        test_disc_geometry,
        test_batches_are_grouped,
        test_anchoring,
        test_visibility,
        test_text_and_status,
        test_text_is_never_cached,
        test_occlusion_rule,
        test_occlusion_needs_the_owning_mesh,
        test_the_overlay_is_read_only,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
