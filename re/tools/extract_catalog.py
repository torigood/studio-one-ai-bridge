#!/usr/bin/env python3
"""Build a command catalog from files shipped with Studio One (read-only).

Sources, each tagged in the output:
  keymap   Contents/Resources/commands.xml            (category, name, default keys)
  surface  Contents/devices/**/*.xml                  (command.category/.name/.args)
  macro    macros.package resources/library/*.studioonemacro (CommandElement + args)
  edittask */classfactory.xml ScriptClass category="EditTask" (subCategory, name)

A (category, name) pair seen in any source is a command that
Host.GUI.Commands.interpretCommand(category, name, false, args) can be asked to run.
Edit tasks are listed separately: their command category is not always the
subCategory (e.g. MusicEdit "Randomize Notes" runs as "Musical Functions").

Usage: extract_catalog.py <Studio One.app> <extracted-packages-dir> <out.json>
"""
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET


def _parse(path):
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig", errors="replace")
    # Studio One XML uses processing instructions like <?platform win?>; strip them.
    text = re.sub(r"<\?(?!xml)[^>]*\?>", "", text)
    return ET.fromstring(text)


def from_keymap(app, cmds):
    root = _parse(os.path.join(app, "Contents/Resources/commands.xml"))
    for c in root.iter("Command"):
        key = (c.get("category"), c.get("name"))
        e = cmds.setdefault(key, {"sources": set(), "args": {}, "keys": []})
        e["sources"].add("keymap")
        e["keys"] += [k.get("name") for k in c.iter("Key") if k.get("name")]


def from_surfaces(app, cmds):
    pat = re.compile(r'command\.category="([^"]+)"\s+command\.name="([^"]+)"(?:\s+command\.args="([^"]*)")?')
    for path in glob.glob(os.path.join(app, "Contents/devices/**/*.xml"), recursive=True):
        with open(path, encoding="utf-8", errors="replace") as f:
            for m in pat.finditer(f.read()):
                e = cmds.setdefault((m.group(1), m.group(2)), {"sources": set(), "args": {}, "keys": []})
                e["sources"].add("surface")
                if m.group(3):
                    for part in m.group(3).split(","):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            e["args"].setdefault(k.strip(), v.strip())


def from_macros(extracted, cmds):
    for path in glob.glob(os.path.join(extracted, "macros/resources/library/*.studioonemacro")):
        root = _parse(path)
        for el in root.iter("CommandElement"):
            e = cmds.setdefault((el.get("category"), el.get("name")), {"sources": set(), "args": {}, "keys": []})
            e["sources"].add("macro")
            for a in el.iter("CommandArgument"):
                e["args"].setdefault(a.get("name"), a.get("value"))


def edit_tasks(extracted):
    tasks = []
    for path in glob.glob(os.path.join(extracted, "*/classfactory.xml")):
        root = _parse(path)
        for sc in root.iter("ScriptClass"):
            if sc.get("category") != "EditTask":
                continue
            attrs = {a.get("id"): a.get("value") for a in sc.iter("Attribute")}
            tasks.append({
                "package": os.path.basename(os.path.dirname(path)),
                "classID": sc.get("classID"),
                "subCategory": sc.get("subCategory"),
                "name": sc.get("name"),
                "classDescription": "EditTask:%s:%s" % (sc.get("subCategory"), sc.get("name")),
                "arguments": attrs.get("arguments"),
            })
    return sorted(tasks, key=lambda t: (t["subCategory"] or "", t["name"] or ""))


def main(argv):
    if len(argv) != 4:
        print(__doc__)
        return 2
    app, extracted, out = argv[1:]
    cmds = {}
    from_keymap(app, cmds)
    from_surfaces(app, cmds)
    from_macros(extracted, cmds)
    commands = [
        {"category": c, "name": n, "sources": sorted(v["sources"]), "args": v["args"], "keys": v["keys"]}
        for (c, n), v in sorted(cmds.items())
        if c and n
    ]
    data = {"commands": commands, "editTasks": edit_tasks(extracted)}
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("commands: %d, edit tasks: %d -> %s" % (len(commands), len(data["editTasks"]), out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
