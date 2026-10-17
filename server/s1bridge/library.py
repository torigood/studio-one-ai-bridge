"""Offline catalog of what the user owns: instrument plug-ins and Sound Set loops.

Instruments: Studio One's plug-in cache
  <settings dir>/ARM64/Plugins-en.settings (see config.py)
  <ClassDescription classID=... category="AudioSynth" name=... subCategory="VST3/Synth">
The same plug-in can appear once per format (VST3, VST2, AU) with the same classID.

Loops: names come from the unencrypted directory of each *.soundset. File contents
inside sound sets are encrypted by PreSonus and are not read here; Studio One loads them.
"""
import os
import re

import zipfile
import xml.etree.ElementTree as ET

from . import config
from . import soundset

APP_PRESETS_DIR = config.APP_PRESETS_DIR
USER_PRESETS_DIR = config.USER_PRESETS_DIR
SOUNDSETS_DIR = config.SOUNDSETS_DIR

_CLASS = re.compile(r'<ClassDescription classID="([^"]+)" category="(AudioSynth|AudioEffect)" name="([^"]+)"'
                    r'(?:\s+subCategory="([^"]*)")?(.*?)(?:/>|</ClassDescription>)', re.S)
_VENDOR = re.compile(r'id="Class:Vendor" value="([^"]*)"')
_FORMAT_RANK = {"VST3": 0, "(Native)": 1, "AU": 2, "VST2": 3}

LOOP_KINDS = {".audioloop": "audioloop", ".musicloop": "musicloop", ".wav": "audio",
              ".mid": "midi", ".mp3": "audio"}
_BPM = re.compile(r"(\d{2,3})\s*bpm", re.I)


def _format(sub):
    head = (sub or "").split("/")[0]
    return head if head in _FORMAT_RANK else (head or "unknown")


def instruments():
    """Installed instrument plug-ins, one entry per name (preferred format first)."""
    return _plugins("AudioSynth")


def effects():
    """Installed audio effect plug-ins (Studio One's own and third party)."""
    return _plugins("AudioEffect")


def _plugins(category):
    path = config.plugin_cache()
    if not os.path.isfile(path):
        return []
    text = open(path, encoding="utf-8-sig", errors="replace").read()
    best = {}
    for m in _CLASS.finditer(text):
        class_id, cat, name, sub, rest = m.groups()
        if cat != category:
            continue
        v = _VENDOR.search(rest or "")
        item = {"name": name, "class_id": class_id, "format": _format(sub),
                "vendor": v.group(1) if v else None}
        old = best.get(name)
        if old is None or _FORMAT_RANK.get(item["format"], 9) < _FORMAT_RANK.get(old["format"], 9):
            best[name] = item
    return sorted(best.values(), key=lambda i: i["name"].lower())


def _parse_loop_name(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    info = {}
    m = _BPM.search(stem)
    if m:
        info["bpm"] = int(m.group(1))
    return info


_loop_cache = {}


def loops():
    """[{sound_set, path, kind, bpm?}] for every loop-like file in installed sound sets."""
    out = []
    if not os.path.isdir(SOUNDSETS_DIR):
        return out
    for f in sorted(os.listdir(SOUNDSETS_DIR)):
        if not f.endswith(".soundset"):
            continue
        full = os.path.join(SOUNDSETS_DIR, f)
        key = (full, os.path.getmtime(full))
        if key not in _loop_cache:
            items = []
            try:
                entries = soundset.list_entries(full)
            except (OSError, ValueError):
                entries = []
            for e in entries:
                kind = LOOP_KINDS.get(os.path.splitext(e["path"])[1].lower())
                if not kind:
                    continue
                item = {"sound_set": f[:-len(".soundset")], "path": e["path"], "kind": kind}
                item.update(_parse_loop_name(e["path"]))
                items.append(item)
            _loop_cache[key] = items
        out.extend(_loop_cache[key])
    return out


def search_loops(query="", kind=None, bpm=None, limit=50):
    words = [w for w in (query or "").lower().split() if w]
    res = []
    for item in loops():
        if kind and item["kind"] != kind:
            continue
        if bpm and abs(item.get("bpm", -100) - bpm) > 3:
            continue
        hay = (item["sound_set"] + "/" + item["path"]).lower()
        if all(w in hay for w in words):
            res.append(item)
    return {"total": len(res), "loops": res[:limit]}


def _preset_files(plugin_name):
    out = []
    for base in (os.path.join(APP_PRESETS_DIR, plugin_name), os.path.join(USER_PRESETS_DIR, "PreSonus", plugin_name),
                 os.path.join(USER_PRESETS_DIR, plugin_name)):
        for root, _dirs, files in os.walk(base):
            out.extend(os.path.join(root, f) for f in files if f.endswith(".preset"))
    return sorted(out)


def _preset_params(path):
    """{param: value} from a Studio One native preset (ZIP with data.fxpreset / *.xml)."""
    try:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.endswith(".fxpreset") or n.endswith("preset.xml"):
                    text = z.read(n).decode("utf-8-sig", errors="replace").replace(" x:id=", " xid=")
                    root = ET.fromstring(text)
                    for a in root.iter("Attributes"):
                        if a.get("xid") == "ParameterData":
                            return {k: v for k, v in a.attrib.items() if k != "xid"}
    except (OSError, zipfile.BadZipFile, ET.ParseError):
        pass
    return {}


def presets(plugin_name):
    """Factory and user presets for a Studio One native plug-in (path, name)."""
    return [{"name": os.path.splitext(os.path.basename(p))[0], "folder": os.path.basename(os.path.dirname(p)),
             "path": p} for p in _preset_files(plugin_name)]


def native_params(plugin_name):
    """Parameter IDs of a Studio One native plug-in with the value range seen across its presets.
    Values are in the plug-in's own units (e.g. Hz, dB), as stored in presets."""
    seen = {}
    for p in _preset_files(plugin_name):
        for k, v in _preset_params(p).items():
            try:
                f = float(v)
            except ValueError:
                continue
            lo, hi, n = seen.get(k, (f, f, 0))
            seen[k] = (min(lo, f), max(hi, f), n + 1)
    return {k: {"min_seen": lo, "max_seen": hi} for k, (lo, hi, _n) in sorted(seen.items())}
