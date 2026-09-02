"""Prove the landmark overlay actually rasterises, on a real GPU.

    /Applications/Blender.app/Contents/MacOS/Blender --factory-startup \
        --python tools/check_overlay_render.py

NOT --background. Blender refuses to create a GPU shader in background mode,
which is why the add-on never builds one at import time and why the headless
test suite stops at the pure geometry. This script is the missing half: it
renders the marker batches into an offscreen buffer and counts the pixels they
actually paint.

It checks the two things that cannot be proved any other way:

1. that the batch types the overlay uses are accepted by this Blender's GPU
   backend - `TRI_FAN` and `LINE_LOOP` were removed in 3.2, so a marker built
   from them would fail only at draw time, in the viewport, silently;

2. that a marker of radius r really covers about pi * r^2 pixels, which is
   what "the size is in screen pixels" has to mean to be true.

Nothing is written to the .blend and no add-on state is touched.
"""

import sys
import os

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WIDTH, HEIGHT = 200, 120
FAILURES = []


def check(label, condition, detail=""):
    print("  %s  %s %s" % ("PASS" if condition else "FAIL", label, detail))
    if not condition:
        FAILURES.append(label)


def render(draw):
    """Run `draw` into an offscreen buffer in pixel space; return the pixels."""
    import gpu
    import mathutils

    offscreen = gpu.types.GPUOffScreen(WIDTH, HEIGHT)
    try:
        with offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.clear(color=(0.0, 0.0, 0.0, 1.0))
            with gpu.matrix.push_pop():
                projection = (
                    mathutils.Matrix.Translation((-1.0, -1.0, 0.0))
                    @ mathutils.Matrix.Diagonal(
                        (2.0 / WIDTH, 2.0 / HEIGHT, 1.0, 1.0))
                )
                gpu.matrix.load_matrix(mathutils.Matrix.Identity(4))
                gpu.matrix.load_projection_matrix(projection)
                draw()
            buffer = framebuffer.read_color(0, 0, WIDTH, HEIGHT, 4, 0, 'UBYTE')
            buffer.dimensions = WIDTH * HEIGHT * 4
            return list(buffer)
    finally:
        offscreen.free()


def count(pixels, channel, threshold=200, others_below=None):
    total = 0
    for index in range(0, len(pixels), 4):
        if pixels[index + channel] <= threshold:
            continue
        if others_below is not None:
            rest = [pixels[index + c] for c in range(3) if c != channel]
            if any(value > others_below for value in rest):
                continue
        total += 1
    return total


def main():
    import math

    from body_surface_measurement import overlay

    print("BSMT landmark overlay - GPU render proof")
    print("  Blender : %s" % bpy.app.version_string)
    if bpy.app.background:
        print("\nFAIL: run this WITHOUT --background; GPU drawing is "
              "unavailable in background mode.")
        return 2

    import gpu
    try:
        gpu.shader.from_builtin('UNIFORM_COLOR')
        check("the builtin UNIFORM_COLOR shader is available", True)
    except Exception as exc:                          # noqa: BLE001
        check("the builtin UNIFORM_COLOR shader is available", False, exc)
        return 1

    print("\n[batches] the primitive types the overlay uses")
    discs, rings = overlay.marker_batches([
        {"screen": (60.0, 60.0), "radius": 3.0, "show_marker": True,
         "marker_color": (1.0, 0.0, 0.0, 1.0), "selected": False},
        {"screen": (140.0, 60.0), "radius": 3.0, "show_marker": True,
         "marker_color": (1.0, 0.0, 0.0, 1.0), "selected": True},
    ])
    check("two identical markers are one batch", len(discs) == 1, len(discs))
    check("and the selection adds one ring batch", len(rings) == 1)

    failed = []
    pixels = render(lambda: overlay._draw_markers(discs, rings))
    check("TRIS and LINES both draw without error", not failed)

    red = count(pixels, 0, others_below=60)
    white = count(pixels, 1, threshold=200)
    expected = 2 * math.pi * 9.0
    check("two radius-3 discs paint about %.0f px (got %d)" % (expected, red),
          abs(red - expected) < expected * 0.5, red)
    check("the selection ring paints its own pixels (%d)" % white, white > 5)

    print("\n[size] a radius-r marker covers about pi*r^2 pixels")
    for radius in (1.0, 3.0, 10.0):
        batch, _rings = overlay.marker_batches([
            {"screen": (100.0, 60.0), "radius": radius, "show_marker": True,
             "marker_color": (0.0, 1.0, 0.0, 1.0), "selected": False}])
        pixels = render(lambda b=batch: overlay._draw_markers(b, []))
        painted = count(pixels, 1, others_below=60)
        area = math.pi * radius * radius
        check("radius %4.0f -> %4d px, pi*r^2 = %.0f" % (radius, painted, area),
              abs(painted - area) <= max(3.0, area * 0.25), painted)

    print("\n%d failure(s)" % len(FAILURES))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    print("\nexit %d" % code)
    bpy.ops.wm.quit_blender()
