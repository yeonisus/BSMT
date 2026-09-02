# Licensing — an open decision for the project owner

**BSMT has no licence yet, and this document does not choose one.** It sets out
what the third-party components require and what Blender's ecosystem expects,
so the owner (and, if the university owns the work, the university) can decide.

Nothing here is legal advice.

---

## 1. What BSMT redistributes

| Component | Version | Licence | Redistributed in the release ZIP? |
|---|---|---|---|
| **pygeodesic** | 0.1.11 | **MIT** (© 2021 Michael Hogg) | **Yes** — one compiled wheel per platform |
| Kirsanov exact-geodesic C++ | bundled inside pygeodesic | **MIT**, per the pygeodesic README: *"licensed under MIT license similar to the original Kirsanov C++ code, rather than GPL"* | Yes, inside the wheel |
| NumPy | 1.26.4 | BSD-3-Clause | **No** — Blender provides it; BSMT never bundles or installs NumPy |
| Blender Python API (`bpy`, `bmesh`, `mathutils`, `gpu`, `blf`) | 4.5 | GPL-2.0-or-later (Blender itself) | No — provided by Blender at runtime |
| BSMT's own source | 0.19.0 | **undecided** | Yes |

MIT is permissive and GPL-compatible. Redistributing the pygeodesic wheel is
allowed under any licence BSMT chooses, **provided the MIT notice travels with
it** — it does: the wheel carries `pygeodesic-0.1.11.dist-info/LICENSE` and
Blender installs that alongside the module.

## 2. The Blender question

This is the substantive one.

Blender is GPL. The Blender Foundation's long-standing position is that the
Python API is an integral part of Blender, and that **add-ons which import
`bpy` are derivative works and must be released under a GPL-compatible
licence**. Blender's extension platform enforces a related rule: the
`blender_manifest.toml` **requires** a `license` field, and extensions.blender.org
only accepts GPL-compatible licences.

BSMT imports `bpy` extensively, so:

- If BSMT is **distributed outside the lab** — a public GitHub release, the
  Blender extensions platform, a paper's supplementary material — the
  conventional and expected choice is **GPL-3.0-or-later** (or GPL-2.0-or-later).
- If BSMT is **only ever used inside the lab** and never distributed, the GPL's
  obligations are not triggered at all; the GPL governs *distribution*, not use.
  A licence is still worth choosing, for the lab's own clarity.

Note the direction of the constraint: BSMT choosing GPL is compatible with
bundling MIT-licensed pygeodesic. The reverse — a proprietary BSMT bundling
`bpy`-dependent code — is what the Foundation's position rules out.

## 3. Institutional ownership

Work produced as part of university research is frequently owned by the
university, not the individual. **Who is entitled to license BSMT at all is a
question for the owner and, if relevant, the university's IP office.** It is
outside anything that can be settled here.

## 4. What is in the package right now

`tools/build_release.py` writes:

```toml
license = ["SPDX:GPL-3.0-or-later"]
```

with a `PROVISIONAL` comment beside it, and the build prints a warning on every
run. It is a **placeholder chosen so the package can be built and tested at
all**, because the manifest field is mandatory. It is not a decision.

To change it:

```
python3 tools/build_release.py --license "SPDX:MIT"
```

or edit `PROVISIONAL_LICENSE` at the top of that file.

## 5. What has to happen before BSMT is given to anyone

1. Confirm who owns the work.
2. Choose a licence, taking §2 into account.
3. Set it in `tools/build_release.py` and rebuild.
4. Add a `LICENSE` file at the repository root with the full text.
5. Add a copyright line naming the owner and year.

**No `LICENSE` file has been created**, deliberately: writing one would assert
both a licence and an owner, and neither is settled.

## 6. Attribution worth carrying regardless

Whatever licence is chosen, the exact geodesic algorithm should be cited in any
publication using BSMT's surface distances:

> Mitchell, J. S. B., Mount, D. M., & Papadimitriou, C. H. (1987).
> The discrete geodesic problem. *SIAM Journal on Computing*, 16(4), 647–668.

implemented by Kirsanov's C++ code and wrapped by
[pygeodesic](https://github.com/mhogg/pygeodesic).
