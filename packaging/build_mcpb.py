#!/usr/bin/env python3
"""Build a Claude Desktop extension (.mcpb) of this checkout. Standard library only.

  python3 packaging/build_mcpb.py              -> dist/studio-one-ai-bridge-<version>.mcpb
  python3 packaging/build_mcpb.py --no-python  -> same, small, runs on the system python3

A .mcpb is a ZIP with manifest.json at its root (spec: github.com/modelcontextprotocol/mcpb,
MANIFEST.md, manifest_version 0.3). The bundle keeps the checkout layout (server/, bridge/src,
surface/, re/tools) because setup.py finds its sources relative to the package, so the
bridge_setup tool works from inside the installed extension.

The server is Python with no dependencies. Claude Desktop ships no Python of its own (as of
2.42304.0), and a Mac without the Command Line Tools has only a stub /usr/bin/python3 that asks
to install them. So the bundle carries a standalone CPython (python-build-standalone, pinned by
SHA-256 below) for arm64 and x86_64, trimmed to what the server needs; bin/start.sh picks the
one for the Mac and falls back to the system python3. Downloads are cached in build/python-cache.

Optional check with the official CLI:
  npx -y @anthropic-ai/mcpb validate dist/manifest.json
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge import tools  # noqa: E402

# (folder or file relative to the checkout, file name filter)
INCLUDE = [
    ("server/run.py", None),
    ("server/midi_port.py", None),
    ("server/s1bridge", lambda n: n.endswith(".py")),
    ("bridge/src", lambda n: not n.startswith(".")),
    ("surface", lambda n: not n.startswith(".")),
    ("re/tools", lambda n: n.endswith(".py")),
    ("packaging/start.sh", None),
    ("LICENSE", None),
    ("DISCLAIMER.md", None),
    ("README.md", None),
]


PYTHON_RELEASE = "20260929"
PYTHON_VERSION = "3.12.14"
PYTHONS = {  # arch -> SHA-256 of cpython-<version>+<release>-<triple>-install_only_stripped.tar.gz
    "arm64": ("aarch64-apple-darwin", "1bb3e53d231ee2c8881e8daf6426f4dd95bff0dda496af0f3af300357aa998d0"),
    "x86_64": ("x86_64-apple-darwin", "62891cf4a32ed18b6b4174def999184b5186371422c52dd92d941401db0f0e41"),
}
PYTHON_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
              "{release}/cpython-{version}%2B{release}-{triple}-install_only_stripped.tar.gz")
CACHE = os.path.join(ROOT, "build", "python-cache")

# Parts of the standard library the server never uses (GUI, tests, packaging tools, docs).
STDLIB = "python/lib/python3.12/"
DROP_STDLIB = ("test/", "idlelib/", "tkinter/", "turtledemo/", "ensurepip/", "lib2to3/", "pydoc_data/",
               "site-packages/", "config-3.12-darwin/", "unittest/", "turtle.py", "lib-dynload/_tkinter")


def fetch_python(arch):
    """Path of the verified archive for arch, downloading it once."""
    triple, sha = PYTHONS[arch]
    path = os.path.join(CACHE, "cpython-%s+%s-%s.tar.gz" % (PYTHON_VERSION, PYTHON_RELEASE, triple))
    if not os.path.isfile(path):
        os.makedirs(CACHE, exist_ok=True)
        url = PYTHON_URL.format(release=PYTHON_RELEASE, version=PYTHON_VERSION, triple=triple)
        print("download", url)
        with urllib.request.urlopen(url) as r, open(path + ".part", "wb") as f:
            f.write(r.read())
        os.replace(path + ".part", path)
    with open(path, "rb") as f:
        got = hashlib.sha256(f.read()).hexdigest()
    if got != sha:
        os.remove(path)
        raise RuntimeError("SHA-256 mismatch for %s: %s" % (path, got))
    return path


def keep_python_member(name):
    """Bundle path (below python/darwin-<arch>/) for a member of the archive, or None to drop it."""
    if name == "python/bin/python3.12":
        return "bin/python3"
    if name == "python/lib/libpython3.12.dylib":
        return "lib/libpython3.12.dylib"
    if name.startswith(STDLIB):
        rest = name[len(STDLIB):]
        if rest.startswith(DROP_STDLIB) or "/__pycache__/" in "/" + rest:
            return None
        return "lib/python3.12/" + rest
    return None


def python_files(arch):
    """(path in bundle, bytes, mode) for the trimmed Python of one architecture."""
    out = []
    with tarfile.open(fetch_python(arch)) as tar:
        for m in tar.getmembers():
            if not m.isfile():  # symlinks (bin/python3 -> python3.12) are re-created as real files
                continue
            rel = keep_python_member(m.name)
            if rel:
                out.append(("python/darwin-%s/%s" % (arch, rel), tar.extractfile(m).read(), m.mode))
    names = {p for p, _, _ in out}
    for need in ("bin/python3", "lib/libpython3.12.dylib", "lib/python3.12/os.py", "lib/python3.12/LICENSE.txt"):
        if "python/darwin-%s/%s" % (arch, need) not in names:
            raise RuntimeError("bundled Python for %s misses %s" % (arch, need))
    return out


def project_version():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        return re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M).group(1)


def first_sentence(text):
    m = re.match(r"(.+?\.)(\s|$)", text)
    return m.group(1) if m else text


def server_config(bundled):
    """manifest "server": bin/start.sh with the bundled Python, else the system python3."""
    env = {
        "S1BRIDGE_APP": "${user_config.studio_one_app}",
        "S1BRIDGE_USER_CONTENT": "${user_config.user_content}",
    }
    if bundled:
        return {"type": "binary", "entry_point": "bin/start.sh",
                "mcp_config": {"command": "/bin/sh", "args": ["${__dirname}/bin/start.sh"], "env": env}}
    return {"type": "python", "entry_point": "server/run.py",
            "mcp_config": {"command": "python3", "args": ["${__dirname}/server/run.py"], "env": env}}


def manifest(version, bundled=True):
    return {
        "manifest_version": "0.3",
        "name": "studio-one-ai-bridge",
        "display_name": "Studio One AI Bridge",
        "version": version,
        "description": "Read and operate PreSonus Studio One 5: tracks, notes, instruments, effects, mix. Unofficial.",
        "long_description": (
            "Lets Claude read your saved Studio One songs and edit the open song: add tracks, write and edit "
            "notes, load instruments and effects, set volume, pan and plug-in parameters, tempo and more.\n\n"
            "After installing, quit Studio One and ask Claude to set up Studio One (bridge_setup). Then start "
            "Studio One and open a song.\n\n"
            "Unofficial, not affiliated with PreSonus or Fender. Tested only on Studio One 5.5.2, macOS Apple "
            "Silicon." + ("" if bundled else " Needs python3 (macOS Command Line Tools).")),
        "author": {"name": "Kwansik"},
        "license": "MIT",
        "keywords": ["studio one", "daw", "music", "midi", "presonus"],
        "server": server_config(bundled),
        "user_config": {
            "studio_one_app": {
                "type": "string",
                "title": "Studio One application",
                "description": "Path of Studio One 5.app",
                "default": "/Applications/Studio One 5.app",
                "required": False,
            },
            "user_content": {
                "type": "directory",
                "title": "Studio One user data folder",
                "description": "Studio One's User Data Location (Preferences > Locations)",
                "default": "${HOME}/Music/Studio One 5",
                "required": False,
            },
        },
        "tools": [{"name": t["name"], "description": first_sentence(t["description"])} for t in tools.listed_tools(dev=False)],
        "compatibility": dict({"platforms": ["darwin"]}, **({} if bundled else {"runtimes": {"python": ">=3.9"}})),
    }


def files():
    """(path in bundle, path on disk), sorted."""
    out = []
    for rel, keep in INCLUDE:
        src = os.path.join(ROOT, rel)
        if os.path.isfile(src):
            out.append((rel, src))
            continue
        for name in sorted(os.listdir(src)):
            path = os.path.join(src, name)
            if os.path.isfile(path) and keep(name):
                out.append(("%s/%s" % (rel, name), path))
    # the launcher sits at bin/start.sh in the bundle
    return [("bin/start.sh" if p == "packaging/start.sh" else p, s) for p, s in out]


REQUIRED = ("manifest_version", "name", "version", "description", "author", "server")


def check(m):
    missing = [k for k in REQUIRED if k not in m]
    if missing:
        raise ValueError("manifest misses %s" % ", ".join(missing))
    if not re.match(r"^\d+\.\d+\.\d+", m["version"]):
        raise ValueError("version is not semver: %s" % m["version"])
    entry = m["server"]["entry_point"]
    if entry not in {p for p, _ in files()}:
        raise ValueError("entry_point %s is not in the bundle" % entry)


def build(out_dir, python=True):
    m = manifest(project_version(), bundled=python)
    check(m)
    extra = []
    if python:
        for arch in PYTHONS:
            extra += python_files(arch)
    os.makedirs(out_dir, exist_ok=True)
    text = json.dumps(m, ensure_ascii=False, indent=2) + "\n"
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        f.write(text)  # for `mcpb validate` and for reading
    out = os.path.join(out_dir, "%s-%s.mcpb" % (m["name"], m["version"]))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", text)
        for rel, src in files():
            z.write(src, rel)  # keeps the file mode (start.sh is executable)
        for rel, data, mode in extra:
            info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100000 | (mode & 0o777)) << 16
            z.writestr(info, data)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-python", action="store_true", help="do not bundle Python (needs the system python3)")
    args = ap.parse_args()
    out = build(os.path.join(ROOT, "dist"), python=not args.no_python)
    with zipfile.ZipFile(out) as z:
        n = len(z.namelist())
    print("built %s (%d files, %d KB)" % (out, n, os.path.getsize(out) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
