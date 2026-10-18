"""Install, check and remove the Studio One side of the AI Bridge. Standard library only.

  python3 -m s1bridge setup              dry run: print what would be written
  python3 -m s1bridge setup --apply      install (Studio One must not be running)
  python3 -m s1bridge doctor             check every piece, say how to fix what is missing
  python3 -m s1bridge uninstall --apply  remove what setup installed

What setup installs (paths from config.py):
  extension   <settings>/Extensions/local.s1aibridge/   built from bridge/src
  surface     <settings>/User Devices/AI Bridge/        copied from surface/
  device      <settings>/MusicDevices.settings          AI Bridge entry (see devices.py), replaces
                                                        Options > External Devices > Add
  catalog     <data dir>/commands.json                  built from your Studio One installation
  MIDI daemon started at the end so Studio One finds the "AI Bridge" port on its next start

Every apply first copies what it will touch into <data dir>/backups/<timestamp>/. The Studio One
application itself is never modified (except with target "app-scripts", which needs an explicit
flag). Sources come from the checkout this module lives in (bridge/src, surface/, re/tools).
"""
import argparse
import glob
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

from . import config, devices

EXT_ID = "local.s1aibridge"
BRIDGE_SRC = os.path.join(config.PROJECT_ROOT, "bridge", "src")
SURFACE_SRC = os.path.join(config.PROJECT_ROOT, "surface")
RE_TOOLS = os.path.join(config.PROJECT_ROOT, "re", "tools")

EXT_METAINFO = """<?xml version="1.0" encoding="UTF-8"?>
<MetaInformation>
	<Attribute id="Package:ID" value="%s"/>
	<Attribute id="Package:Name" value="AI Bridge"/>
	<Attribute id="Package:Description" value="Mailbox bridge to an external MCP server"/>
	<Attribute id="Package:Version" value="%s"/>
	<Attribute id="Package:Vendor" value="local"/>
</MetaInformation>
"""

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


class Paths:
    """Every location setup reads or writes. Defaults from config.py; tests pass temp dirs."""

    def __init__(self, app=None, settings_dir=None, data_dir=None):
        self.app = app or config.APP
        self.settings_dir = settings_dir or config.SETTINGS_DIR
        self.data_dir = data_dir or config.DATA_DIR
        self.ext_dir = os.path.join(self.settings_dir, "Extensions", EXT_ID)
        self.ext_settings = os.path.join(self.settings_dir, "Extensions", "Extensions.settings")
        self.surface_dir = os.path.join(self.settings_dir, "User Devices", "AI Bridge")
        self.devices = os.path.join(self.settings_dir, "MusicDevices.settings")
        self.catalog = config.CATALOG if data_dir is None else os.path.join(data_dir, "commands.json")
        self.backups = os.path.join(self.data_dir, "backups")

    def app_resource(self, *parts):
        return os.path.join(self.app, "Contents", *parts) if config.IS_MAC else os.path.join(self.app, *parts)

    @property
    def app_script(self):
        return self.app_resource("scripts", "aibridge.package")


# --- pieces ----------------------------------------------------------------------------

def bridge_version(js_text):
    m = re.search(r'kBridgeVersion\s*=\s*"([^"]+)"', js_text)
    return m.group(1) if m else None


def source_version():
    with open(os.path.join(BRIDGE_SRC, "bridge.js"), encoding="utf-8") as f:
        return bridge_version(f.read())


def build_package():
    """aibridge.package (a plain ZIP, same as macros.package) from bridge/src, as bytes."""
    if not os.path.isdir(BRIDGE_SRC):
        raise RuntimeError("bridge sources not found at %s; run setup from a checkout" % BRIDGE_SRC)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(BRIDGE_SRC)):
            path = os.path.join(BRIDGE_SRC, name)
            if os.path.isfile(path) and not name.startswith("."):
                z.write(path, name)
    return buf.getvalue()


def installed_version(package_path):
    """Bridge version inside an installed aibridge.package, or None."""
    try:
        with zipfile.ZipFile(package_path) as z:
            return bridge_version(z.read("bridge.js").decode("utf-8"))
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def surface_files():
    return [n for n in sorted(os.listdir(SURFACE_SRC)) if not n.startswith(".")]


def studio_one_version(paths):
    try:
        with open(os.path.join(paths.app, "Contents", "Info.plist"), "rb") as f:
            return plistlib.load(f).get("CFBundleShortVersionString")
    except (OSError, ValueError):
        return None


def studio_one_running():
    if config.IS_WINDOWS:  # untested
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Studio One.exe"], capture_output=True, text=True)
        return "Studio One.exe" in r.stdout
    return subprocess.run(["pgrep", "-x", "studioapp"], capture_output=True).returncode == 0


def build_catalog(paths, out):
    """Unpack Studio One's own script packages into a temp dir and extract the command catalog.
    Reads the local installation only; writes nothing but `out`."""
    sys.path.insert(0, RE_TOOLS)
    import extract_catalog
    import s1pkg

    packages = glob.glob(paths.app_resource("scripts", "*.package"))
    packages.append(paths.app_resource("Extensions", "studioonemacros", "scripts", "macros.package"))
    with tempfile.TemporaryDirectory(prefix="s1bridge-catalog-") as tmp:
        for pkg in packages:
            if not os.path.isfile(pkg) or os.path.basename(pkg) == "aibridge.package":
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
        os.makedirs(os.path.dirname(out), exist_ok=True)
        rc = extract_catalog.main(["extract_catalog.py", paths.app, tmp, out])
    if rc != 0:
        raise RuntimeError("catalog extraction failed")


# --- plan / apply ----------------------------------------------------------------------
# A step is (action, path, data): write (bytes), remove-dir, remove-file, device-add,
# device-remove, catalog, daemon.

def plan_install(paths, target="extension", surface=True, device=True, catalog=True, daemon=True):
    steps = []
    package = build_package()
    if target == "extension":
        meta = EXT_METAINFO % (EXT_ID, source_version())
        steps.append(("write", os.path.join(paths.ext_dir, "metainfo.xml"), meta.encode("utf-8")))
        steps.append(("write", os.path.join(paths.ext_dir, "installdata.xml"), EXT_INSTALLDATA.encode("utf-8")))
        steps.append(("write", os.path.join(paths.ext_dir, "scripts", "aibridge.package"), package))
    else:
        steps.append(("write", paths.app_script, package))
    if surface:
        for name in surface_files():
            steps.append(("write", os.path.join(paths.surface_dir, name), _read_bytes(os.path.join(SURFACE_SRC, name))))
    if device:
        steps.append(("device-add", paths.devices, None))
    if catalog:
        steps.append(("catalog", paths.catalog, None))
    if daemon and config.IS_MAC:
        steps.append(("daemon", "", None))
    return steps


def plan_uninstall(paths, target="extension", surface=True, device=True, catalog=True):
    steps = [("remove-dir", paths.ext_dir, None) if target == "extension"
             else ("remove-file", paths.app_script, None)]
    if surface:
        steps.append(("remove-dir", paths.surface_dir, None))
    if device:
        steps.append(("device-remove", paths.devices, None))
    if catalog:
        steps.append(("remove-file", paths.catalog, None))
    return steps


def describe(steps):
    out = []
    for action, path, data in steps:
        if action == "write":
            out.append("  write        %s (%d bytes)" % (path, len(data)))
        elif action == "daemon":
            out.append("  daemon       start the \"AI Bridge\" MIDI port")
        else:
            out.append("  %-12s %s" % (action, path))
    return "\n".join(out)


def backup(paths, steps):
    """Copy everything the steps will touch (that exists) into a new backup folder."""
    dest = base = os.path.join(paths.backups, time.strftime("%Y%m%d-%H%M%S"))
    n = 1
    while os.path.exists(dest):  # two runs in the same second
        n += 1
        dest = "%s-%d" % (base, n)
    touched = set()
    for action, path, _ in steps:
        if action in ("daemon", "catalog") or not path:
            continue
        for d in (paths.ext_dir, paths.surface_dir):
            if path.startswith(d + os.sep) or path == d:
                path = d
        touched.add(path)
    touched.add(paths.ext_settings)
    os.makedirs(dest, exist_ok=True)
    for src in sorted(touched):
        name = src.strip(os.sep).replace(os.sep, "__")
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest, name))
        elif os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, name))
    return dest


def apply(paths, steps, log=print):
    for action, path, data in steps:
        if action == "write":
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        elif action == "remove-dir":
            if os.path.isdir(path):
                shutil.rmtree(path)
        elif action == "remove-file":
            if os.path.isfile(path):
                os.remove(path)
        elif action == "device-add":
            log("  device: %s" % devices.register(path))
        elif action == "device-remove":
            log("  device: %s" % devices.unregister(path))
        elif action == "catalog":
            if not os.path.isdir(paths.app):
                log("  Studio One not found at %s (set S1BRIDGE_APP); commands_search stays empty." % paths.app)
            elif not os.path.isdir(RE_TOOLS):
                log("  catalog tools missing (%s); commands_search stays empty." % RE_TOOLS)
            else:
                build_catalog(paths, path)
        elif action == "daemon":
            from . import midi
            try:
                log("  MIDI daemon: %s" % midi.status())
            except OSError as e:
                log("  MIDI daemon did not start: %s" % e)


def run(steps, paths, do_apply, running=studio_one_running, log=print):
    """Print the plan; with do_apply, back up and perform it. Returns a process exit code."""
    log(describe(steps))
    if not do_apply:
        log("Dry run, nothing changed. Add --apply to perform these steps.")
        return 0
    if running():
        log("Studio One is running. Quit it first (it rewrites its settings when it quits).")
        return 1
    log("backup -> %s" % backup(paths, steps))
    apply(paths, steps, log)
    log("done.")
    return 0


# --- doctor ----------------------------------------------------------------------------

def _read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _check(name, ok, detail="", fix=""):
    return {"check": name, "ok": ok, "detail": detail, "fix": fix}


def doctor(paths=None, ping=True, running=studio_one_running, system=True):
    """List of checks: ok True (fine), False (broken, see fix) or None (optional / skipped).
    system=False leaves out the checks that start processes (MIDI daemon, numpy)."""
    paths = paths or Paths()
    setup_fix = "quit Studio One, then run: python3 -m s1bridge setup --apply"
    out = []

    version = studio_one_version(paths)
    out.append(_check("Studio One installed", os.path.isdir(paths.app),
                      "%s %s" % (paths.app, version or ""),
                      "install Studio One 5, or set S1BRIDGE_APP to where it is"))
    is_running = running()
    out.append(_check("Studio One running", None, "yes" if is_running else "no"))

    want = source_version()
    have = installed_version(os.path.join(paths.ext_dir, "scripts", "aibridge.package"))
    if have and not os.path.isfile(os.path.join(paths.ext_dir, "installdata.xml")):
        out.append(_check("bridge extension", False, "installdata.xml missing, Studio One will not load it",
                          setup_fix))
    elif (have or installed_version(paths.app_script)) not in (None, want):
        out.append(_check("bridge extension", False, "installed %s, this server expects %s"
                          % (have or installed_version(paths.app_script), want),
                          setup_fix + " (then start Studio One again)"))
    else:
        have = have or installed_version(paths.app_script)
        out.append(_check("bridge extension", have is not None, have or "not installed", setup_fix))

    missing = [name for name in surface_files()
               if _read_bytes(os.path.join(paths.surface_dir, name)) != _read_bytes(os.path.join(SURFACE_SRC, name))]
    out.append(_check("control surface", not missing,
                      "outdated or missing: %s" % ", ".join(missing) if missing else paths.surface_dir, setup_fix))

    out.append(_check("device registered", devices.is_registered(paths.devices), paths.devices, setup_fix))

    if not system:
        pass
    elif config.IS_MAC:
        from . import midi
        try:
            out.append(_check("MIDI port \"AI Bridge\"", True, midi.status()))
        except OSError as e:
            out.append(_check("MIDI port \"AI Bridge\"", False, str(e),
                              "run: python3 server/midi_port.py status"))
    else:
        out.append(_check("MIDI port \"AI Bridge\"", False, "virtual MIDI port is macOS only",
                          "run 'AI Bridge - Run Jobs' in Studio One by hand for each request"))

    mailbox = config.MAILBOX
    parent = mailbox if os.path.isdir(mailbox) else os.path.dirname(mailbox)
    out.append(_check("mailbox folder", os.path.isdir(parent) and os.access(parent, os.W_OK), mailbox,
                      "start Studio One once (it creates %s), or set S1BRIDGE_USER_CONTENT" % parent))

    if ping and is_running:
        from . import tools
        try:
            r = tools.call("daw_ping", {"wait": 5})
        except Exception as e:  # report every failure the same way
            r = {"error": str(e)}
        ok = isinstance(r, dict) and r.get("pong") is True
        out.append(_check("bridge answers", ok, json.dumps(r, ensure_ascii=False),
                          "open a song in Studio One; if it still does not answer, run doctor again "
                          "after restarting Studio One"))
    else:
        out.append(_check("bridge answers", None, "skipped (Studio One not running)" if ping else "skipped"))

    out.append(_check("command catalog", os.path.isfile(paths.catalog) or None, paths.catalog,
                      "optional, for commands_search: python3 -m s1bridge setup --apply"))
    if not system:
        return out
    numpy = subprocess.run([config.analysis_python(), "-c", "import numpy"], capture_output=True).returncode == 0
    out.append(_check("numpy (audio analysis)", True if numpy else None, config.analysis_python(),
                      "optional: pip install numpy, or set S1BRIDGE_ANALYSIS_PYTHON"))
    return out


def format_doctor(checks):
    lines = []
    for c in checks:
        mark = {True: "ok  ", False: "FAIL", None: "--  "}[c["ok"]]
        lines.append("%s %-24s %s" % (mark, c["check"], c["detail"]))
        if c["ok"] is False and c["fix"]:
            lines.append("     fix: %s" % c["fix"])
    return "\n".join(lines)


# --- CLI -------------------------------------------------------------------------------

def cli(argv):
    ap = argparse.ArgumentParser(prog="python3 -m s1bridge", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("setup", "uninstall"):
        p = sub.add_parser(name)
        p.add_argument("--apply", action="store_true", help="actually change files (default: dry run)")
        p.add_argument("--target", choices=["extension", "app-scripts"], default="extension")
        p.add_argument("--i-understand-app-bundle-change", action="store_true")
        p.add_argument("--no-catalog", action="store_true", help="skip the command catalog")
    d = sub.add_parser("doctor")
    d.add_argument("--json", action="store_true")
    d.add_argument("--no-ping", action="store_true", help="do not send a ping to Studio One")
    args = ap.parse_args(argv)

    paths = Paths()
    if args.cmd == "doctor":
        checks = doctor(paths, ping=not args.no_ping)
        print(json.dumps(checks, ensure_ascii=False, indent=1) if args.json else format_doctor(checks))
        return 1 if any(c["ok"] is False for c in checks) else 0
    if args.target == "app-scripts" and not args.i_understand_app_bundle_change:
        print("app-scripts modifies %s and breaks its code signature.\n"
              "Re-run with --i-understand-app-bundle-change if you really want this." % paths.app)
        return 1
    if args.cmd == "setup":
        steps = plan_install(paths, args.target, catalog=not args.no_catalog)
    else:
        steps = plan_uninstall(paths, args.target, catalog=not args.no_catalog)
    print("%s %s" % ("APPLY" if args.apply else "DRY RUN", args.cmd))
    code = run(steps, paths, args.apply)
    if code == 0 and args.apply and args.cmd == "setup":
        print("Start Studio One, open a song and ask the AI to run daw_ping.")
    return code
