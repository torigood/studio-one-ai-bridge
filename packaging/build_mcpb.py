#!/usr/bin/env python3
"""Build a Claude Desktop extension (.mcpb) of this checkout. Standard library only.

  python3 packaging/build_mcpb.py            -> dist/studio-one-ai-bridge-<version>.mcpb

A .mcpb is a ZIP with manifest.json at its root (spec: github.com/modelcontextprotocol/mcpb,
MANIFEST.md, manifest_version 0.3). The bundle keeps the checkout layout (server/, bridge/src,
surface/, re/tools) because setup.py finds its sources relative to the package, so the
bridge_setup tool works from inside the installed extension.

The server is Python with no dependencies; Claude Desktop runs it with the system python3
(it ships no Python of its own as of 2.42304.0). Optional check with the official CLI:
  npx -y @anthropic-ai/mcpb validate dist/manifest.json
"""
import json
import os
import re
import sys
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
    ("LICENSE", None),
    ("DISCLAIMER.md", None),
    ("README.md", None),
]


def project_version():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        return re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M).group(1)


def first_sentence(text):
    m = re.match(r"(.+?\.)(\s|$)", text)
    return m.group(1) if m else text


def manifest(version):
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
            "Silicon. Needs python3 (macOS Command Line Tools)."),
        "author": {"name": "Kwansik"},
        "license": "MIT",
        "keywords": ["studio one", "daw", "music", "midi", "presonus"],
        "server": {
            "type": "python",
            "entry_point": "server/run.py",
            "mcp_config": {
                "command": "python3",
                "args": ["${__dirname}/server/run.py"],
                "env": {
                    "S1BRIDGE_APP": "${user_config.studio_one_app}",
                    "S1BRIDGE_USER_CONTENT": "${user_config.user_content}",
                },
            },
        },
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
        "tools": [{"name": t["name"], "description": first_sentence(t["description"])} for t in tools.TOOLS],
        "compatibility": {
            "platforms": ["darwin"],
            "runtimes": {"python": ">=3.9"},
        },
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
    return out


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


def build(out_dir):
    m = manifest(project_version())
    check(m)
    os.makedirs(out_dir, exist_ok=True)
    text = json.dumps(m, ensure_ascii=False, indent=2) + "\n"
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        f.write(text)  # for `mcpb validate` and for reading
    out = os.path.join(out_dir, "%s-%s.mcpb" % (m["name"], m["version"]))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", text)
        for rel, src in files():
            z.write(src, rel)
    return out


def main():
    out = build(os.path.join(ROOT, "dist"))
    with zipfile.ZipFile(out) as z:
        n = len(z.namelist())
    print("built %s (%d files, %d KB)" % (out, n, os.path.getsize(out) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
