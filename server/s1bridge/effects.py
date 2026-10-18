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


def add_send(path, destination, track=None, mixer_channel=None, level_db=-6.0, pan=0.0, prefader=False):
    """Add a send from a channel to an existing FX or bus channel (the script API can only send to a new
    FX channel, docs/re-findings.md). Devices/audiomixer.xml, in the source channel:
      <Attributes x:id="Sends">
        <Attributes name="Send01" bypass="0" prefader="0" level="<linear gain>" pan="<0..1, below 0.5 = right>">
          <Connection x:id="destination" objectID="{destination uid}/Input" friendlyName="<label>"/>
    pan here is -1 (left) .. 1 (right)."""
    from xml.sax.saxutils import escape
    with Song(path) as song:
        tag, name, label = _resolve_channel(song, track, mixer_channel)
        chans = list(song._channels.values())
    dests = [c for c in chans if c["type"] in ("AudioEffectChannel", "AudioGroupChannel")
             and (c["label"] == destination or c["name"] == destination)]
    if len(dests) != 1:
        raise SongWriteError("destination %r matches %d FX/bus channels (%s)" % (
            destination, len(dests), ", ".join(c["label"] for c in chans
                                               if c["type"] in ("AudioEffectChannel", "AudioGroupChannel")) or "none"))
    dest = dests[0]
    if dest["label"] == label and dest["type"] == tag:
        raise SongWriteError("a channel cannot send to itself")
    if not -1 <= float(pan) <= 1:
        raise SongWriteError("pan must be -1 (left) .. 1 (right)")
    gain = 10 ** (float(level_db) / 20.0)
    if gain > 3.1622:
        raise SongWriteError("send level above +10 dB")
    with zipfile.ZipFile(path) as z:
        xml = z.read("Devices/audiomixer.xml").decode("utf-8-sig")
    start, end = _channel_span(xml, tag, name)
    m = re.compile(r'<Attributes x:id="Sends"(/?)>').search(xml, start, end)
    sends = xml[m.start():_block_end(xml, m.start())] if m and not m.group(1) else ""
    existing = re.findall(r'<Attributes name="Send(\d+)"', sends)
    if dest["uid"] in sends:
        raise SongWriteError("%s already sends to %s" % (label, dest["label"]))
    slot = "Send%02d" % (max([int(n) for n in existing] or [0]) + 1)
    block = ('\n\t\t\t\t\t<Attributes name="%s" bypass="0" prefader="%d" level="%r" pan="%r">'
             '\n\t\t\t\t\t\t<Connection x:id="destination" objectID="%s/Input" friendlyName="%s"/>'
             '\n\t\t\t\t\t</Attributes>' % (slot, 1 if prefader else 0, gain, 0.5 - float(pan) / 2.0,
                                            dest["uid"], escape(dest["label"], {'"': "&quot;"})))
    if m and not m.group(1):
        close = _block_end(xml, m.start()) - len("</Attributes>")
        xml = xml[:close].rstrip() + block + "\n\t\t\t\t" + xml[close:]
    elif m:  # <Attributes x:id="Sends"/>
        xml = xml[:m.start()] + '<Attributes x:id="Sends">' + block + "\n\t\t\t\t</Attributes>" + xml[m.end():]
    else:
        xml = xml[:end].rstrip() + '\n\t\t\t\t<Attributes x:id="Sends">' + block + "\n\t\t\t\t</Attributes>\n\t\t\t" + xml[end:]
    bak = backup(path)
    rewrite_zip(path, {"Devices/audiomixer.xml": ("﻿" + xml).encode("utf-8")})
    return {"channel": label, "send": int(slot[4:]), "slot": slot, "destination": dest["label"],
            "level_db": round(float(level_db), 2), "pan": float(pan), "prefader": bool(prefader), "backup": bak}
