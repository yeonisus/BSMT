"""One repair backup per target, not N (Milestone 3.37).

    /path/to/blender -b --factory-startup --python \\
        tests/test_repair_backup_lifecycle_blender.py

Reported: opening a real .blend became extremely slow. Profiling found the
load path innocent - BSMT's `load_post` costs 0.0001 s, 0.007% of the open -
and the file itself guilty: 444.5 MB of a 554.5 MB scan file was eighteen
repair backups, 80.2% of it, one full 350,000-polygon copy per repair.

The cause was a cleanup branch that could never run:

    previous = bpy.data.meshes.get(obj.data.name + BACKUP_SUFFIX)
    if previous is not None and previous.users == 0:      # unreachable
        bpy.data.meshes.remove(previous)
    ...
    backup.use_fake_user = True                          # users >= 1, always

The fake user that lets a backup survive a save/reload is the same thing that
makes its own reclamation impossible, so Blender name-suffixed each new copy
(`_BSMT_backup.001`, `.002`, ...) and every one of them stayed in the file.
Only the most recent is reachable through `props.repair_backup_mesh`; the
other seventeen could not be restored by any code path and could not be freed
by Blender either.

So these checks are about the LIFECYCLE, not about repair itself. No repair
algorithm, acceptance rule, topology analysis or measurement content is
exercised or changed here - only how many backups exist, which one Undo
Repair uses, and what pruning is allowed to touch.

A backup is identified by explicit ownership metadata written when it is
created, never by its name alone, so a mesh that merely *looks* like a backup
is never removed.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bmesh
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_repair_backup_lifecycle_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_repair_backup_lifecycle_blender.py")
    raise SystemExit(0)

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        mesh.use_fake_user = False
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def make_target(name="Body_BSMT", segments=16, rings=8):
    """A measurement-copy object with a small closed mesh."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings,
                              radius=100.0)
    mesh = bpy.data.meshes.new(name + "_Mesh")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.bsmt_scan.is_measurement_copy = True
    return obj


def mesh_fingerprint(mesh):
    """Everything Undo Repair has to restore, in a comparable form."""
    verts = [tuple(round(c, 9) for c in v.co) for v in mesh.vertices]
    polys = [tuple(p.vertices) for p in mesh.polygons]
    edges = sorted(tuple(sorted(e.vertices)) for e in mesh.edges)
    loops = [loop.vertex_index for loop in mesh.loops]
    return {
        "name": mesh.name,
        "vertices": verts,
        "polygons": polys,
        "edges": edges,
        "loops": loops,
        "uv_layers": [layer.name for layer in mesh.uv_layers],
        "colors": [layer.name for layer in mesh.color_attributes],
        "materials": [m.name if m else None for m in mesh.materials],
    }


def deform(obj, amount=3.0):
    """Change the mesh the way a repair would: move geometry, drop a face."""
    mesh = obj.data
    for index, vert in enumerate(mesh.vertices):
        if index % 7 == 0:
            vert.co.z += amount
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    if len(bm.faces) > 4:
        bmesh.ops.delete(bm, geom=[bm.faces[0]], context='FACES')
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def main():
    import body_surface_measurement as bsmt
    from body_surface_measurement import meshrepair, state

    bsmt.register()
    context = bpy.context
    props = state.get_props(context)
    SUFFIX = meshrepair.BACKUP_SUFFIX

    def backups_in_file():
        """Every datablock BSMT would call one of its repair backups."""
        return [m for m in bpy.data.meshes if meshrepair.is_backup(m)]

    # ================================================================== A ==
    print("\nA. N repairs leave exactly ONE backup datablock")
    for n in (1, 2, 5, 18):
        wipe()
        obj = make_target()
        check("A%d.0: no backup exists before the first repair" % n,
              len(backups_in_file()) == 0, len(backups_in_file()))
        names = []
        for _ in range(n):
            names.append(meshrepair.make_backup(obj))
            deform(obj, 0.5)
        found = backups_in_file()
        check("A%d.1: after %d repairs exactly one backup remains"
              % (n, n), len(found) == 1, [m.name for m in found])
        check("A%d.2: and it is the one the last repair created" % n,
              found and found[0].name == names[-1],
              (found[0].name if found else None, names[-1]))
        check("A%d.3: which is still fake-user, so a save keeps it" % n,
              found and found[0].use_fake_user)

    # ================================================================== B ==
    print("\nB. Undo Repair restores the immediately preceding mesh exactly")
    wipe()
    obj = make_target()
    obj.data.uv_layers.new(name="UVMap")
    before = mesh_fingerprint(obj.data)
    live_name = obj.data.name
    backup = meshrepair.make_backup(obj)
    deform(obj, 9.0)
    after_repair = mesh_fingerprint(obj.data)
    check("B1: the repair really changed the mesh",
          after_repair["vertices"] != before["vertices"]
          or after_repair["polygons"] != before["polygons"])
    check("B2: restore_backup reports success",
          meshrepair.restore_backup(obj, backup))
    restored = mesh_fingerprint(obj.data)
    check("B3: vertex coordinates are restored exactly",
          restored["vertices"] == before["vertices"])
    check("B4: polygon connectivity is restored exactly",
          restored["polygons"] == before["polygons"])
    check("B5: edges are restored exactly",
          restored["edges"] == before["edges"])
    check("B6: the loop array is restored exactly",
          restored["loops"] == before["loops"])
    check("B7: UV layers and colour attributes come back",
          restored["uv_layers"] == before["uv_layers"]
          and restored["colors"] == before["colors"],
          (restored["uv_layers"], before["uv_layers"]))
    check("B8: the live mesh keeps its own datablock name",
          obj.data.name == live_name, obj.data.name)
    check("B9: the object points at the restored mesh, not at the backup",
          obj.data.name != backup and obj.data is not None)
    check("B10: and the restored LIVE mesh is not itself marked as a backup "
          "- otherwise the next prune could delete the mesh in use",
          not meshrepair.is_backup(obj.data))
    check("B11: exactly one backup still exists after an undo",
          len(backups_in_file()) == 1, len(backups_in_file()))

    print("\n  Undo Repair is one slot, and pressing it twice is idempotent")
    check("B12: the backup is still restorable after being used once",
          meshrepair.restore_backup(obj, backup))
    check("B13: and gives the same mesh again",
          mesh_fingerprint(obj.data)["vertices"] == before["vertices"])

    # ================================================================== C ==
    print("\nC. the current backup survives save -> reload")
    wipe()
    obj = make_target()
    original = mesh_fingerprint(obj.data)
    backup = meshrepair.make_backup(obj)
    props.repair_backup_mesh = backup
    deform(obj, 7.0)
    path = os.path.join(bpy.app.tempdir, "bsmt_backup_lifecycle.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    context = bpy.context
    props = state.get_props(context)
    obj = bpy.data.objects.get("Body_BSMT")
    check("C1: the backup datablock survived the reload",
          bpy.data.meshes.get(backup) is not None, backup)
    check("C2: it is still recognised as a BSMT repair backup",
          meshrepair.is_backup(bpy.data.meshes.get(backup)))
    check("C3: props still points at it",
          props.repair_backup_mesh == backup, props.repair_backup_mesh)
    check("C4: and Undo Repair still works after a reload",
          meshrepair.restore_backup(obj, props.repair_backup_mesh))
    check("C5: restoring the reloaded backup gives the original mesh",
          mesh_fingerprint(obj.data)["vertices"] == original["vertices"])
    check("C6: still exactly one backup in the reloaded file",
          len(backups_in_file()) == 1, len(backups_in_file()))

    # ================================================================== D ==
    print("\nD. pruning never touches anything it does not own")
    wipe()
    obj = make_target("Body_BSMT")
    other = make_target("Other_BSMT")            # a second, independent target
    innocent = bpy.data.meshes.new("Body_BSMT_backup_of_mine")
    innocent.use_fake_user = True                # a similar SUBSTRING, not ours
    decoy = bpy.data.meshes.new("something" + SUFFIX + "_but_not_ours")
    decoy_obj = bpy.data.objects.new("Decoy", decoy)   # in use by an object
    bpy.context.scene.collection.objects.link(decoy_obj)
    source = bpy.data.meshes.new("Body_Source_Scan")
    source_obj = bpy.data.objects.new("Body_Source", source)
    bpy.context.scene.collection.objects.link(source_obj)

    other_backup = meshrepair.make_backup(other)
    first = meshrepair.make_backup(obj)
    # By DATABLOCK, not by name: the prune frees the canonical name and the
    # new backup takes it, so a name comparison would silently pass.
    first_ref = bpy.data.meshes[first]
    live_before = obj.data.name
    deform(obj, 1.0)
    second = meshrepair.make_backup(obj)

    def gone(datablock):
        try:
            datablock.name
        except ReferenceError:
            return True
        return datablock.name not in bpy.data.meshes

    check("D1: the stale backup datablock of THIS target is gone",
          gone(first_ref), first)
    check("D2: the current backup of this target is kept",
          bpy.data.meshes.get(second) is not None, second)
    check("D2b: and it reuses the canonical name rather than drifting to "
          ".001, .002 with every repair",
          second == obj.data.name + SUFFIX, second)
    check("D3: the other target's backup is untouched",
          bpy.data.meshes.get(other_backup) is not None, other_backup)
    check("D4: the live measurement mesh is untouched",
          bpy.data.meshes.get(live_before) is not None
          and obj.data.name == live_before, obj.data.name)
    check("D5: the source scan mesh is untouched",
          bpy.data.meshes.get("Body_Source_Scan") is not None)
    check("D6: a mesh with a merely similar name is untouched",
          bpy.data.meshes.get("Body_BSMT_backup_of_mine") is not None)
    check("D7: a suffixed mesh that an object still uses is untouched",
          bpy.data.meshes.get(decoy.name) is not None, decoy.name)
    check("D8: the other target still has exactly one backup",
          len(meshrepair.backups_for(other)) == 1,
          [m.name for m in meshrepair.backups_for(other)])
    check("D9: and this target has exactly one",
          len(meshrepair.backups_for(obj)) == 1,
          [m.name for m in meshrepair.backups_for(obj)])

    print("\n  renaming the object between repairs must not orphan a backup")
    obj.name = "Body_Renamed_BSMT"
    deform(obj, 1.0)
    third = meshrepair.make_backup(obj)
    check("D10: the backup taken under the old object name was still found "
          "and released",
          len(meshrepair.backups_for(obj)) == 1,
          [m.name for m in meshrepair.backups_for(obj)])
    check("D11: and the other target is STILL untouched by that",
          bpy.data.meshes.get(other_backup) is not None, other_backup)
    check("D12: the renamed target's backup is the newest one",
          bpy.data.meshes.get(third) is not None, third)

    # ================================================================== E ==
    print("\nE. failed and reverted repair paths still restore")
    wipe()
    obj = make_target()
    pristine = mesh_fingerprint(obj.data)
    backup = meshrepair.make_backup(obj)
    deform(obj, 12.0)
    check("E1: a revert restores the pre-repair mesh",
          meshrepair.restore_backup(obj, backup)
          and mesh_fingerprint(obj.data)["vertices"] == pristine["vertices"])
    check("E2: discard_backup removes it despite the fake user",
          meshrepair.discard_backup(backup)
          and bpy.data.meshes.get(backup) is None)
    check("E3: discarding a backup that is already gone is not an error",
          meshrepair.discard_backup(backup) is False)
    check("E4: restoring from a missing backup reports failure, and does "
          "not raise", meshrepair.restore_backup(obj, backup) is False)
    check("E5: the mesh is still the restored one after that failure",
          mesh_fingerprint(obj.data)["vertices"] == pristine["vertices"])

    # ================================================================== F ==
    print("\nF. file size grows by one backup, not by N")
    wipe()
    obj = make_target(segments=48, rings=24)
    base_path = os.path.join(bpy.app.tempdir, "bsmt_size_base.blend")
    bpy.ops.wm.save_as_mainfile(filepath=base_path, compress=False)
    base = os.path.getsize(base_path)

    meshrepair.make_backup(obj)
    one_path = os.path.join(bpy.app.tempdir, "bsmt_size_one.blend")
    bpy.ops.wm.save_as_mainfile(filepath=one_path, compress=False)
    one = os.path.getsize(one_path)
    one_backup_cost = one - base

    for _ in range(17):
        deform(obj, 0.1)
        meshrepair.make_backup(obj)
    many_path = os.path.join(bpy.app.tempdir, "bsmt_size_many.blend")
    bpy.ops.wm.save_as_mainfile(filepath=many_path, compress=False)
    many = os.path.getsize(many_path)

    print("    scan only            : %8.3f MB" % (base / 1048576.0))
    print("    + 1 backup           : %8.3f MB (+%.3f MB)"
          % (one / 1048576.0, one_backup_cost / 1048576.0))
    print("    + 18 backups         : %8.3f MB (+%.3f MB)"
          % (many / 1048576.0, (many - base) / 1048576.0))

    # Structural first: the count is the real invariant, and it cannot drift
    # with Blender's on-disk layout the way a byte threshold can.
    check("F1: 18 repairs leave one backup datablock",
          len(backups_in_file()) == 1, len(backups_in_file()))
    # Then a generous size band, as corroboration only.
    check("F2: and the file grew by about one backup, not eighteen",
          many - base < one_backup_cost * 3 + 262144,
          "grew %d bytes, one backup costs %d" % (many - base,
                                                  one_backup_cost))
    check("F3: which is far below what 18 backups would have cost",
          many - base < one_backup_cost * 18 * 0.5,
          (many - base, one_backup_cost * 18))

    # ================================================================== G ==
    print("\nG. legacy files: stale backups are identifiable and removable")
    wipe()
    obj = make_target()
    # Recreate exactly what an older BSMT left behind: suffixed copies with a
    # fake user and NO ownership metadata.
    legacy = []
    for index in range(5):
        copy = obj.data.copy()
        copy.name = obj.data.name + SUFFIX
        copy.use_fake_user = True
        legacy.append(copy.name)
    keep = legacy[-1]
    props = state.get_props(bpy.context)
    props.repair_backup_mesh = keep
    check("G1: five legacy backups are present",
          len(legacy) == 5 and all(bpy.data.meshes.get(n) for n in legacy))
    check("G2: they are recognised as BSMT repair backups",
          all(meshrepair.is_backup(bpy.data.meshes[n]) for n in legacy))

    removed, freed = meshrepair.purge_stale_backups(keep_name=keep)
    check("G3: the purge removed the four stale ones",
          removed == 4, removed)
    check("G4: and reported roughly what it freed",
          freed > 0, freed)
    check("G5: the referenced backup is still there",
          bpy.data.meshes.get(keep) is not None)
    check("G6: the live mesh is untouched",
          obj.data is not None and len(obj.data.vertices) > 0)
    check("G7: Undo Repair still works on the legacy file",
          meshrepair.restore_backup(obj, keep))

    print("\n  the operator is explicit and reports what it did")
    check("G8: the cleanup operator exists",
          hasattr(bpy.types, "BSMT_OT_clean_repair_backups"))

    # ================================================================== H ==
    print("\nH. what this change is NOT allowed to touch")
    package = os.path.join(ROOT, "body_surface_measurement")
    operators_source = open(os.path.join(package, "operators.py")).read()
    check("H1: every repair still backs up first, through the same call",
          "meshrepair.make_backup(obj)" in operators_source)
    check("H2: the backup is still kept alive across a save",
          "use_fake_user = True" in open(
              os.path.join(package, "meshrepair.py")).read())
    # In CODE, not in prose: the defect is quoted in a comment on purpose, so
    # that the next reader knows why a fake user is no longer taken as proof
    # that a datablock is wanted.
    repair_code = [line.split("#", 1)[0]
                   for line in open(os.path.join(package,
                                                 "meshrepair.py")).readlines()]
    check("H3: the unreachable users==0 cleanup is gone from the code",
          not any("previous.users == 0" in line for line in repair_code))
    check("H4: and it is still explained in a comment",
          "previous.users == 0" in open(
              os.path.join(package, "meshrepair.py")).read())

    print("\n%s" % ("-" * 60))
    print("checks: %d, failures: %d" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    print("BSMT_BACKUP_LIFECYCLE_RESULT=%d" % (1 if FAILURES else 0))
    raise SystemExit(1 if FAILURES else 0)


main()
