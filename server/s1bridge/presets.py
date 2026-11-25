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
  - Vital .vital presets (JSON) are put into the Vital state the song already has (vital_vstpreset).
Other presets saved inside a plug-in's own browser (Surge XT .fxp, BBC SO patches) cannot be loaded this way.
"""
import os
import re
import struct
import zipfile
from xml.sax.saxutils import escape

from . import config
from . import songwrite
from .songwrite import SongWriteError

EXTS = (".preset", ".vstpreset", ".aupreset", ".vital")

# Folders a plug-in keeps its own presets in (besides Studio One's and the Audio/Presets folders)
OWN_DIRS = {"Vital": ["~/Music/Vital", "~/Documents/Vital"]}


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
    out += [os.path.expanduser(d) for d in OWN_DIRS.get(plugin, [])]
    return out


def _style(path):
    """Vital's preset_style (Bass, Pad, Lead, Keys ...) from the start of a .vital file."""
    try:
        with open(path, "rb") as f:
            m = re.search(rb'"preset_style"\s*:\s*"([^"]*)"', f.read(8192))
        return m.group(1).decode("utf-8", "replace") if m else ""
    except OSError:
        return ""


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
                    item = {"name": os.path.splitext(f)[0], "folder": "" if rel == "." else rel, "path": p,
                            "format": os.path.splitext(f)[1].lower().lstrip(".")}
                    if item["format"] == "vital":
                        item["style"] = _style(p)
                    items.append(item)
    words = query.lower().split()
    hits = [i for i in items if all(w in (i["folder"] + "/" + i["name"] + "/" + i.get("style", "")).lower()
                                    for w in words)]
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


def vital_vstpreset(current, vital_json):
    """A Vital .vstpreset with vital_json as its sound, built from the one the song has (current).

    Layout (observed 2026-11-25, Vital 1.5 VST3 via JUCE): 'VST3', version, class id (32 chars), int64 LE
    offset of the chunk list; component state = 'VstW' header (16 bytes), 'CcnK' + BE size, 'FBCh' bank
    (fxID 'Vita', 128 reserved bytes), BE chunk size at 0xdc, chunk at 0xe0 = preset JSON then a JUCE
    trailer; then MetaInfo XML and 'List' with Comp/Cont/Info entries (id, int64 offset, int64 size)."""
    if current[:4] != b"VST3" or current[0x40:0x44] != b"CcnK" or current[0x48:0x4c] != b"FBCh" \
            or current[0x50:0x54] != b"Vita":
        raise SongWriteError("the song's Vital state has an unknown layout; load the preset in Vital itself")
    start = 0xe0
    try:
        _obj, n = __import__("json").JSONDecoder().raw_decode(current[start:].decode("utf-8", "replace"))
    except ValueError:
        raise SongWriteError("the song's Vital state has no JSON sound; load the preset in Vital itself")
    old_end = start + len(current[start:].decode("utf-8", "replace")[:n].encode("utf-8"))
    new = vital_json.strip()
    delta = len(new) - (old_end - start)
    out = bytearray(current[:start] + new + current[old_end:])
    list_off = struct.unpack("<q", current[0x28:0x30])[0]

    def bump_be(off):
        struct.pack_into(">I", out, off, struct.unpack(">I", out[off:off + 4])[0] + delta)
    struct.pack_into("<q", out, 0x28, list_off + delta)
    bump_be(0x44)  # CcnK size
    bump_be(0xdc)  # FBCh chunk size
    lst = list_off + delta
    if out[lst:lst + 4] != b"List":
        raise SongWriteError("the song's Vital state has no chunk list where expected")
    count = struct.unpack("<i", out[lst + 4:lst + 8])[0]
    for k in range(count):
        e = lst + 8 + k * 20
        cid, off, size = out[e:e + 4], struct.unpack("<q", out[e + 4:e + 12])[0], struct.unpack("<q", out[e + 12:e + 20])[0]
        if cid == b"Comp":
            struct.pack_into("<q", out, e + 12, size + delta)
        elif off > 0x30:
            struct.pack_into("<q", out, e + 4, off + delta)
    return bytes(out)


def _preset_data(path, class_id, member_ext, current=None):
    """Bytes to store in the song for this preset file, checked against the instrument."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".vital":
        if member_ext != ".vstpreset" or current is None:
            raise SongWriteError("a .vital preset fits only Vital (VST3)")
        with open(path, "rb") as f:
            data = f.read()
        try:
            __import__("json").loads(data.decode("utf-8"))
        except ValueError:
            raise SongWriteError("not a Vital preset (no JSON): %s" % path)
        return vital_vstpreset(current, data)
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
        current = z.read(member) if member in names else None
    base_plugin = re.sub(r"\s+\d+$", "", plugin or "")  # "SampleOne 2" -> "SampleOne"
    src = _pick(base_plugin, preset)
    data = _preset_data(src, class_id, os.path.splitext(member)[1].lower(), current)
    name = os.path.splitext(os.path.basename(src))[0]
    new_block, n = re.subn(r'<Attributes x:id="Presets" pname="[^"]*" dirty="\d"/>',
                           '<Attributes x:id="Presets" pname=%s dirty="0"/>' % _attr(name), block, count=1)
    sy = sy[:s0] + (new_block if n else block) + sy[s1:]
    bak = songwrite.backup(path)
    songwrite.rewrite_zip(path, {member: data, "Devices/audiosynthfolder.xml": ("﻿" + sy).encode("utf-8")})
    return {"track": t["name"], "instrument": plugin, "slot": slot, "preset": name, "file": src, "backup": bak}


def _attr(v):
    return '"%s"' % escape(v, {'"': "&quot;"})
