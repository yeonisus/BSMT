# Installing BSMT

BSMT installs like any other Blender extension. **No Terminal, no PowerShell,
no pip.**

## What you need

- **Blender 4.5 LTS.** Download from <https://www.blender.org/download/lts/>.
  BSMT is developed and validated on 4.5.13.
- The BSMT release ZIP: **`bsmt-<version>.zip`**.

Take the file named `bsmt-...zip`. The other file in a release,
`body_surface_measurement-...zip`, is the older-style add-on package and is
only for the fallback below.

## Install

1. Download `bsmt-<version>.zip`. **Do not unzip it.**
   - On Windows, Edge and Chrome sometimes unzip automatically. If you end up
     with a *folder*, download again and choose *Save*, or re-zip the folder.
2. Open Blender.
3. **Edit → Preferences → Get Extensions**.
4. Click the **▾** dropdown at the top right of that panel → **Install from
   Disk…**
5. Select the ZIP.
6. BSMT installs and enables itself. Blender also installs the exact geodesic
   solver for your platform at this point — that is why the ZIP is large.
7. Close Preferences. Press **N** in the 3D viewport and choose the **BSMT**
   tab.

## Check it worked

In the sidebar: **Measurement Manager → Session and Export → About BSMT**.

You should see something like:

```
BSMT 0.24.2
Blender 4.5.13 LTS
Windows x64
Python 3.11.15
NumPy 1.26.4
Exact Geodesic: Available (pygeodesic 0.1.11)
```

**`Exact Geodesic: Available`** is the line that matters. If it says
*Unavailable*, surface distance and surface paths will not run; everything
else, including straight distance, still works.

## If it does not work

**"Install from Disk" is not in the menu.**
You are in the old *Add-ons* panel, or on Blender 4.1 or earlier. BSMT needs
4.2+ for the extension package. Use the fallback below on an older Blender.

**The extension installs but `Exact Geodesic: Unavailable`.**
Blender could not find a wheel for your platform. Check the *About* line for
the platform BSMT detected. The release carries wheels for **Windows x64** and
**macOS ARM64** only — an Intel Mac or Linux is not covered by this package.

**Blender says the extension is for a different platform.**
The manifest declares only the platforms with a bundled wheel. On any other
platform, use the fallback.

**Windows SmartScreen or antivirus blocks the ZIP.**
The package contains a compiled `.pyd`, which some scanners flag on first use.
The file comes from the official pygeodesic release on PyPI; its SHA-256 is
published in the release notes so you can verify it.

## Fallback: the legacy add-on package

For a Blender without extension repositories, or an unsupported platform:

1. **Edit → Preferences → Add-ons → Install…**
2. Select `body_surface_measurement-<version>.zip`.
3. Enable *Body Surface Measurement Tool (BSMT)*.

This package contains **no solver**. Straight distance and everything except
surface distance and surface paths will work. To add the solver you must
install pygeodesic into Blender's own Python yourself — BSMT's *Geodesic
Backend (Developer)* panel prints the exact command for your machine, including
the correct target directory. Note that `pip install --user` does **not** work:
Blender runs Python with user site-packages disabled.

## Uninstalling

**Edit → Preferences → Get Extensions**, find BSMT, and use the dropdown →
*Uninstall*. That removes the solver wheel too.

## Your data

BSMT never modifies the scan you import. Preprocessing creates a separate
*measurement mesh*, and every repair is made on that copy, backed up first and
reverted if it does not improve the topology.
