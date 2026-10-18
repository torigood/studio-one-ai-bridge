"""Instrument presets: find preset files and load one into an instrument of a saved .song.

A song keeps each instrument's whole state as a preset inside its ZIP. Devices/audiosynthfolder.xml:
  <Attributes name="Inst01">
    <UID x:id="deviceClassID" uid="{class id}"/>
    <Attributes x:id="deviceData" name="Mai Tai"> <UID x:id="uniqueID" .../> </Attributes>
    <Attributes x:id="Presets" pname="default" dirty="1"/>
    <String x:id="presetPath" text="Presets/Synths/1 - Mai Tai.fxpreset"/>
Native instruments store an AudioEffectPreset XML (.fxpreset, cid = class id); VST3 and AU instruments
store .vstpreset / .aupreset files. Loading a preset replaces that ZIP entry:
  - Studio One presets (.preset) are ZIPs whose data.fxpreset is the same XML (checked: same cid),
  - .vstpreset / .aupreset files are copied as they are (the plug-in's own preset format).
Presets saved inside a plug-in's own browser (Vital .vital files, BBC SO patches) are not files Studio
One can load this way.
"""
import os
import re
import zipfile
from xml.sax.saxutils import escape

from . import config
from . import songwrite
from .songwrite import SongWriteError

EXTS = (".preset", ".vstpreset", ".aupreset")


def _dirs(plugin):
    home = os.path.expanduser("~")
    out = [os.path.join(config.APP_PRESETS_DIR, plugin)]
    for base in (config.USER_PRESETS_DIR, os.path.join(home, "Library/Audio/Presets"), "/Library/Audio/Presets"):
        out.append(os.path.join(base, plugin))
        try:
            vendors = os.listdir(base)
        except OSError:
            continue
        out += [os.path.join(base, v, plugin) for v in vendors]  # <vendor>/<plug-in>/
    return out


def find(plugin, query="", limit=60):
    """Preset files for a plug-in by name (e.g. 'Mai Tai'), filtered by words in name/folder."""
    seen, items = set(), []
    for d in _dirs(plugin):
        for root, _dirs_, files in os.walk(d):
            for f in files:
                p = os.path.join(root, f)
                if f.lower().endswith(EXTS) and p not in seen:
                    seen.add(p)
                    rel = os.path.relpath(root, d)
                    items.append({"name": os.path.splitext(f)[0], "folder": "" if rel == "." else rel, "path": p,
                                  "format": os.path.splitext(f)[1].lower().lstrip(".")})
    words = query.lower().split()
    hits = [i for i in items if all(w in (i["folder"] + "/" + i["name"]).lower() for w in words)]
    hits.sort(key=lambda i: (i["folder"].lower(), i["name"].lower()))
    return {"plugin": plugin, "total": len(hits), "presets": hits[:limit]}


def _pick(plugin, preset):
    if os.path.isabs(os.path.expanduser(preset)):
        p = os.path.expanduser(preset)
        if not os.path.isfile(p):
            raise SongWriteError("no such preset file: %s" % preset)
        return p
    found = find(plugin, "", 100000)["presets"]
    exact = [i for i in found if i["name"].lower() == preset.lower() or
             (i["folder"] + "/" + i["name"]).lower() == preset.lower()]
    hits = exact or [i for i in found if all(w in (i["folder"] + "/" + i["name"]).lower() for w in preset.lower().split())]
    if len(hits) != 1:
        raise SongWriteError("preset %r for %s: %d matches %s" % (
            preset, plugin, len(hits), [(h["folder"] + "/" + h["name"]).lstrip("/") for h in hits[:10]]))
    return hits[0]["path"]


def _preset_data(path, class_id, member_ext):
    """Bytes to store in the song for this preset file, checked against the instrument."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".preset":
        if member_ext != ".fxpreset":
            raise SongWriteError("a Studio One preset fits only Studio One instruments (this one stores %s)" % member_ext)
        with zipfile.ZipFile(path) as z:
            data = z.read("data.fxpreset")
        cid = re.search(rb'<AudioEffectPreset cid="(\{[^}]+\})"', data)
        if not cid or cid.group(1).decode().upper() != class_id.upper():
            raise SongWriteError("preset is for another plug-in (%s)" % (cid.group(1).decode() if cid else "unknown"))
        return data
    if ext != member_ext:
        raise SongWriteError("this instrument stores %s presets, not %s" % (member_ext, ext))
    with open(path, "rb") as f:
        return f.read()


def load_preset(path, track, preset):
    """Load a preset (name from find() or a file path) into the instrument a track plays."""
    from .automation import _instrument_device
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())

        def z_read(n):
            return z.read(n).decode("utf-8-sig") if n in names else None
        from .song import Song
        with Song(path) as s:
            t = next((x for x in s.tracks() if x["name"] == track or x["index"] == track), None)
        if t is None:
            raise SongWriteError("no track %r" % (track,))
        _uid, class_id, slot, plugin, (s0, s1) = _instrument_device(z_read, t["name"])
        sy = z_read("Devices/audiosynthfolder.xml")
    block = sy[s0:s1]
    member = re.search(r'<String x:id="presetPath" text="([^"]+)"/>', block)
    if not member:
        raise SongWriteError("instrument %s has no stored state in the song" % slot)
    member = member.group(1)
    base_plugin = re.sub(r"\s+\d+$", "", plugin or "")  # "SampleOne 2" -> "SampleOne"
    src = _pick(base_plugin, preset)
    data = _preset_data(src, class_id, os.path.splitext(member)[1].lower())
    name = os.path.splitext(os.path.basename(src))[0]
    new_block, n = re.subn(r'<Attributes x:id="Presets" pname="[^"]*" dirty="\d"/>',
                           '<Attributes x:id="Presets" pname=%s dirty="0"/>' % _attr(name), block, count=1)
    sy = sy[:s0] + (new_block if n else block) + sy[s1:]
    bak = songwrite.backup(path)
    songwrite.rewrite_zip(path, {member: data, "Devices/audiosynthfolder.xml": ("﻿" + sy).encode("utf-8")})
    return {"track": t["name"], "instrument": plugin, "slot": slot, "preset": name, "file": src, "backup": bak}


def _attr(v):
    return '"%s"' % escape(v, {'"': "&quot;"})
