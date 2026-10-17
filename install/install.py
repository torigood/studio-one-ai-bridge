#!/usr/bin/env python3
"""Install / uninstall the AI Bridge into Studio One 5.

DEFAULT IS A DRY RUN: it only prints what would happen. Nothing is written
unless --apply is given. Studio One must not be running.

Targets
  extension (default)  ~/Library/Application Support/PreSonus/Studio One 5/Extensions/local.s1aibridge/
                       Does not touch the app bundle. Whether Studio One loads an
                       unsigned extension from here is UNVERIFIED (see docs/re-findings.md).
  app-scripts          /Applications/Studio One 5.app/Contents/scripts/aibridge.package
                       Community-proven location, but modifying the app bundle breaks its
                       code signature. Requires --i-understand-app-bundle-change.

  catalog              <data dir>/commands.json (S1BRIDGE_CATALOG), built from files of your own
                       Studio One installation; used by the commands_search tool. Not shipped.

Paths come from server/s1bridge/config.py (environment overrides documented there).
Every --apply first copies what it will touch into install/backups/<timestamp>/.

  python3 install/install.py                    # dry run, extension target
  python3 install/install.py --apply            # install
  python3 install/install.py --uninstall        # dry run of removal
  python3 install/install.py --uninstall --apply
  python3 install/install.py --catalog --apply  # only (re)build the command catalog
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "re", "tools"))

from s1bridge import config  # noqa: E402

PACKAGE = os.path.join(ROOT, "bridge", "dist", "aibridge.package")
EXT_ID = "local.s1aibridge"
APP = config.APP
USER_DIR = config.SETTINGS_DIR
EXT_DIR = os.path.join(config.EXTENSIONS_DIR, EXT_ID)
APP_SCRIPT = config.app_resource("scripts", "aibridge.package")
SURFACE_SRC = os.path.join(ROOT, "surface")
SURFACE_DIR = os.path.join(config.USER_DEVICES_DIR, "AI Bridge")
CATALOG = config.CATALOG

EXT_METAINFO = """<?xml version="1.0" encoding="UTF-8"?>
<MetaInformation>
	<Attribute id="Package:ID" value="%s"/>
	<Attribute id="Package:Name" value="AI Bridge"/>
	<Attribute id="Package:Description" value="Mailbox bridge to an external MCP server"/>
	<Attribute id="Package:Version" value="0.3.4"/>
	<Attribute id="Package:Vendor" value="local"/>
</MetaInformation>
""" % EXT_ID

# Without this file the Extension Manager lists the extension but marks it
# "not compatible with Studio One" and does not load it (observed 2026-10-17).
# Same form as the bundled extensions' installdata.xml.
EXT_INSTALLDATA = """<?xml version="1.0" encoding="utf-8"?>
<InstallManifest>
	<InstallFile>
		<RequiredApp id="studioapp.*" minVersion="5.0.0.0" maxVersion="*"/>
	</InstallFile>
</InstallManifest>
"""

GENERATED = {"metainfo.xml": EXT_METAINFO, "installdata.xml": EXT_INSTALLDATA}


def studio_one_running():
    if config.IS_WINDOWS:  # untested
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Studio One.exe"], capture_output=True, text=True)
        return "Studio One.exe" in r.stdout
    r = subprocess.run(["pgrep", "-x", "studioapp"], capture_output=True)
    return r.returncode == 0


def build_catalog(out):
    """Unpack Studio One's own script packages into a temp dir and extract the command catalog.
    Reads the local installation only; writes nothing but `out`."""
    import extract_catalog
    import s1pkg

    packages = glob.glob(config.app_resource("scripts", "*.package"))
    packages.append(config.app_resource("Extensions", "studioonemacros", "scripts", "macros.package"))
    with tempfile.TemporaryDirectory(prefix="s1bridge-catalog-") as tmp:
        for pkg in packages:
            if not os.path.isfile(pkg):
                continue
            dst = os.path.join(tmp, os.path.splitext(os.path.basename(pkg))[0])
            try:
                files = s1pkg.read_package(pkg)
            except ValueError as e:
                print("  skip %s: %s" % (pkg, e))
                continue
            for rel, data in files:
                target = os.path.normpath(os.path.join(dst, rel))
                if not target.startswith(os.path.normpath(dst) + os.sep):
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as f:
                    f.write(data)
        rc = extract_catalog.main(["extract_catalog.py", APP, tmp, out])
    if rc != 0:
        raise RuntimeError("catalog extraction failed")


def plan(args):
    """Return a list of (action, path, detail)."""
    steps = []
    if args.catalog:
        return [("catalog", CATALOG, APP)]
    if args.target == "extension":
        if args.uninstall:
            steps.append(("remove-dir", EXT_DIR, ""))
        else:
            for name in GENERATED:
                steps.append(("write", os.path.join(EXT_DIR, name), name))
            steps.append(("copy", os.path.join(EXT_DIR, "scripts", "aibridge.package"), PACKAGE))
    else:
        if args.uninstall:
            steps.append(("remove-file", APP_SCRIPT, ""))
        else:
            steps.append(("copy", APP_SCRIPT, PACKAGE))
    if args.surface:
        if args.uninstall:
            steps.append(("remove-dir", SURFACE_DIR, ""))
        else:
            for name in sorted(os.listdir(SURFACE_SRC)):
                if not name.startswith("."):
                    steps.append(("copy", os.path.join(SURFACE_DIR, name), os.path.join(SURFACE_SRC, name)))
    if args.uninstall:
        steps.append(("remove-file", CATALOG, ""))
    else:
        steps.append(("catalog", CATALOG, APP))
    return steps


def backup(steps):
    dest = os.path.join(HERE, "backups", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dest, exist_ok=True)
    touched = {d for d in (EXT_DIR, SURFACE_DIR) if any(p.startswith(d) for _, p, _ in steps)}
    touched |= {p for a, p, _ in steps
                if a != "catalog" and p != CATALOG and not p.startswith(EXT_DIR) and not p.startswith(SURFACE_DIR)}
    touched.add(os.path.join(config.EXTENSIONS_DIR, "Extensions.settings"))
    for src in touched:
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest, src.strip("/").replace("/", "__")))
        elif os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, src.strip("/").replace("/", "__")))
    return dest


def apply(steps):
    for action, path, detail in steps:
        if action == "write":
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(GENERATED[detail])
        elif action == "copy":
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copy2(detail, path)
        elif action == "remove-dir" and os.path.isdir(path):
            shutil.rmtree(path)
        elif action == "remove-file" and os.path.isfile(path):
            os.remove(path)
        elif action == "catalog":
            if os.path.isdir(APP):
                build_catalog(path)
            else:
                print("  Studio One not found at %s (set S1BRIDGE_APP); commands_search stays empty." % APP)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", choices=["extension", "app-scripts"], default="extension")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--surface", action="store_true", help="also install the AI Bridge MIDI control surface")
    ap.add_argument("--catalog", action="store_true", help="only build the command catalog")
    ap.add_argument("--apply", action="store_true", help="actually change files (default: dry run)")
    ap.add_argument("--i-understand-app-bundle-change", action="store_true")
    args = ap.parse_args()

    if not args.uninstall and not args.catalog and not os.path.isfile(PACKAGE):
        print("missing %s - run: python3 bridge/build.py" % PACKAGE)
        return 1
    if args.target == "app-scripts" and not args.i_understand_app_bundle_change:
        print("app-scripts modifies %s and breaks its code signature.\n"
              "Re-run with --i-understand-app-bundle-change if you really want this." % APP)
        return 1

    steps = plan(args)
    print("%s (%s, target=%s)" % ("APPLY" if args.apply else "DRY RUN", "catalog" if args.catalog else "uninstall" if args.uninstall else "install",
                                  args.target))
    for action, path, detail in steps:
        print("  %-11s %s %s" % (action, path, ("<- " + detail) if action in ("copy", "catalog") else ""))

    if not args.apply:
        print("Nothing changed. Add --apply to perform these steps.")
        return 0
    if args.catalog:
        apply(steps)
        print("done.")
        return 0
    if studio_one_running():
        print("Studio One is running. Quit it first.")
        return 1
    print("backup ->", backup(steps))
    apply(steps)
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
