"""Remove insert effects from a saved .song (the scripting API cannot remove a device; docs/re-findings.md).

Devices/audiomixer.xml, inside the channel element (AudioTrackChannel, AudioSynthChannel, ...):
  <Attributes x:id="Inserts">
    <Attributes name="FX01">                      one slot per effect
      <Attributes x:id="deviceData" name="Compressor"> ...
      <String x:id="presetPath" text="Presets/Channels/AI Loop/1 - Compressor.fxpreset"/>
    </Attributes>
    ...
The slot block is cut out of the XML text (everything else stays byte for byte) and its preset file is
dropped from the ZIP.
"""
import re
import zipfile

from .song import Song
from .songwrite import SongWriteError, backup, rewrite_zip

_TAG = re.compile(r"<(/?)Attributes\b[^>]*?(/?)>")


def _block_end(xml, start):
    """Index just past the </Attributes> that closes the <Attributes ...> opened at start."""
    depth = 0
    for m in _TAG.finditer(xml, start):
        if m.group(2):          # <Attributes .../>
            continue
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return m.end()
    raise SongWriteError("unbalanced Attributes in audiomixer.xml")


def _channel_span(xml, tag, name):
    m = re.search(r'<%s\b[^>]*\bname="%s"' % (re.escape(tag), re.escape(name)), xml)
    if not m:
        raise SongWriteError("channel %s %s not found in audiomixer.xml" % (tag, name))
    end = xml.index("</%s>" % tag, m.end())
    return m.start(), end


def _slots(xml, start, end):
    """[(slot name, plug-in name, preset path, block start, block end)] of the Inserts folder in xml[start:end]."""
    m = re.compile(r'<Attributes x:id="Inserts">').search(xml, start, end)
    if not m:
        return []
    folder_end = _block_end(xml, m.start())
    out, pos = [], m.end()
    while True:
        s = re.compile(r'<Attributes name="([^"]+)">').search(xml, pos, folder_end)
        if not s:
            return out
        e = _block_end(xml, s.start())
        block = xml[s.start():e]
        dev = re.search(r'x:id="deviceData" name="([^"]*)"', block)
        preset = re.search(r'x:id="presetPath" text="([^"]*)"', block)
        out.append((s.group(1), dev.group(1) if dev else None, preset.group(1) if preset else None, s.start(), e))
        pos = e


def _resolve_channel(song, track=None, mixer_channel=None):
    """(xml tag, channel name, label) for a track (its own channel, or an instrument track's output
    channel) or a mixer channel given by name/label."""
    chans = list(song._channels.values())
    if mixer_channel is not None:
        hits = [c for c in chans if c["name"] == mixer_channel] or [c for c in chans if c["label"] == mixer_channel]
        hits = [c for c in hits if c["type"] != "MusicTrackChannel"] or hits
        if not hits:
            raise SongWriteError("mixer channel not found: %r" % (mixer_channel,))
        if len(hits) > 1:
            raise SongWriteError("mixer channel %r is ambiguous: %s" % (
                mixer_channel, ["%s %s (%s)" % (c["type"], c["name"], c["label"]) for c in hits]))
        c = hits[0]
    else:
        tracks = song.tracks()
        t = next((t for t in tracks if (isinstance(track, int) and t["index"] == track) or t["name"] == track), None)
        if t is None:
            raise SongWriteError("track not found: %r" % (track,))
        c = t["mixer"]
        if not c:
            raise SongWriteError("track %r has no mixer channel in the saved song" % t["name"])
    return c["type"], c["name"], c["label"]


def list_effects(path, track=None, mixer_channel=None):
    with Song(path) as song:
        tag, name, label = _resolve_channel(song, track, mixer_channel)
    with zipfile.ZipFile(path) as z:
        xml = z.read("Devices/audiomixer.xml").decode("utf-8-sig")
    start, end = _channel_span(xml, tag, name)
    return {"channel": name, "label": label,
            "effects": [{"slot": s, "plugin": p} for s, p, _, _, _ in _slots(xml, start, end)]}


def remove_effect(path, track=None, mixer_channel=None, slot=None, plugin=None):
    """Remove the insert named slot ("FX02") or the only insert running plugin ("Compressor")."""
    if not slot and not plugin:
        raise SongWriteError("pass slot or plugin")
    with Song(path) as song:
        tag, name, label = _resolve_channel(song, track, mixer_channel)
    with zipfile.ZipFile(path) as z:
        raw = z.read("Devices/audiomixer.xml")
        names = set(z.namelist())
    bom = raw.startswith(b"\xef\xbb\xbf")
    xml = raw.decode("utf-8-sig")
    start, end = _channel_span(xml, tag, name)
    slots = _slots(xml, start, end)
    hits = [s for s in slots if (slot and s[0] == slot) or (not slot and (s[1] or "").lower() == plugin.lower())]
    if not hits:
        raise SongWriteError("no insert %s on %s; inserts: %s" % (
            slot or plugin, label, [{"slot": s[0], "plugin": s[1]} for s in slots]))
    if len(hits) > 1:
        raise SongWriteError("%d inserts run %s on %s (%s); pass slot" % (
            len(hits), plugin, label, ", ".join(s[0] for s in hits)))
    slot_name, plugin_name, preset, b0, b1 = hits[0]
    # drop the whitespace before the block too, so the file keeps its layout
    while b0 > 0 and xml[b0 - 1] in " \t":
        b0 -= 1
    if b0 > 0 and xml[b0 - 1] == "\n":
        b0 -= 1
    xml = xml[:b0] + xml[b1:]
    members = {"Devices/audiomixer.xml": ((u"﻿" if bom else u"") + xml).encode("utf-8")}
    if preset and preset in names:
        members[preset] = None
    bak = backup(path)
    rewrite_zip(path, members)
    return {"channel": name, "label": label, "removed": {"slot": slot_name, "plugin": plugin_name},
            "remaining": [{"slot": s[0], "plugin": s[1]} for s in _slots(xml, *_channel_span(xml, tag, name))],
            "backup": bak}
