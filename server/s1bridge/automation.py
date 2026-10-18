"""Write mixer automation (volume, pan, mute envelopes) into a saved .song. Standard library only.

Format (observed in the user's songs, 2026-10-18):
  Song/song.xml, inside the track's MediaTrack:
    <Attributes x:id="AutomationRegionList" visible="0">
      <AutomationRegion title="Volume" identity="param:///AudioMixer/{channel uid}/volume"
                        metaIdentity="AudioTrackChannel/volume" color=".." mute="0">
        <Url x:id="points" type="1" url="media:///Envelopes/<track name>/Volume.envelopex"/>
  Envelopes/<track name>/<title>.envelopex = UBJSON {"events": [{"start", "value"}]}
The channel uid and class (AudioTrackChannel, AudioSynthChannel for instrument tracks) come from
Devices/audiomixer.xml, where the channel's <Attributes x:id="Automation" mode=".."/> is the
automation mode (0 Off, 1 Read; the live channel parameter automationMode shows "Auto: Off"/"Read").
An envelope only plays in Read mode and only while the transport runs (a stopped playhead keeps the
fader value), so the writer switches the channel to Read. Start times are quarter notes from song
start (verified 2026-10-18: a step written at beat 64 played at bar 17 at 128 BPM). Values are the parameter's normalized value: volume = linear gain / 3.1622
(the fader's +10 dB maximum, live getNormalized of gain 0.5 was 0.158), pan 0..1 with values below
0.5 to the right (live 0.25 shows R50), mute 0/1. Points are joined by straight lines.
"""
import re
from xml.sax.saxutils import escape

from . import songwrite
from . import ubjson
from .song import Song

MAX_GAIN = 3.1621999740600586
PARAMS = {"volume": "Volume", "pan": "Pan", "mute": "Mute"}
COLORS = {"volume": "FFFFD829", "pan": "FFC7FF3A", "mute": "FFFF5C5C"}


def _attr(v):
    return '"%s"' % escape(v, {'"': "&quot;"})


def normalize(param, value):
    """Normalized envelope value from a user value: volume in dB ("-inf" or None = silence),
    pan -1 (left) .. 1 (right), mute 0/1."""
    if param == "volume":
        if value is None or str(value).lower() in ("-inf", "-infinity"):
            return 0.0
        db = float(value)
        if db > 10:
            raise songwrite.SongWriteError("volume above +10 dB: %s" % value)
        return min(1.0, (10 ** (db / 20.0)) / MAX_GAIN)
    if param == "pan":
        pan = float(value)
        if not -1 <= pan <= 1:
            raise songwrite.SongWriteError("pan must be -1 (left) .. 1 (right): %s" % value)
        return 0.5 - pan / 2.0
    if param == "mute":
        return 1.0 if float(value) else 0.0
    raise songwrite.SongWriteError("unknown parameter %r (volume, pan, mute)" % param)


def _track_element(xml, name):
    """(start, end) of the MediaTrack element called name in song.xml."""
    for m in re.finditer(r'<MediaTrack\b[^>]*>', xml):
        if re.search(r'\sname=%s' % re.escape(_attr(name)), m.group(0)):
            end = xml.find("</MediaTrack>", m.end())
            return m.start(), end + len("</MediaTrack>")
    raise songwrite.SongWriteError("no track named %r" % name)


def _read_mode(mixer_xml, uid):
    """audiomixer.xml with the channel uid's automation mode set to Read (1); (xml, changed)."""
    m = re.search(r'<UID x:id="uniqueID" uid="%s"/>' % re.escape(uid), mixer_xml)
    if not m:
        return mixer_xml, False
    start = mixer_xml.rfind("<Audio", 0, m.start())
    tag = re.match(r"<(\w+)", mixer_xml[start:]).group(1)
    end = mixer_xml.find("</%s>" % tag, m.end())
    el = mixer_xml[start:end]
    mode = re.search(r'<Attributes x:id="Automation" mode="(\d+)"/>', el)
    if mode and mode.group(1) != "0":
        return mixer_xml, False
    if mode:
        el = el[:mode.start()] + '<Attributes x:id="Automation" mode="1"/>' + el[mode.end():]
    else:
        el = el.replace(m.group(0), m.group(0) + '\n\t\t\t\t<Attributes x:id="Automation" mode="1"/>', 1)
    return mixer_xml[:start] + el + mixer_xml[end:], True


def _beats(points, sigs, convert):
    new = []
    for p in points:
        if "beat" in p:
            beat = float(p["beat"])
        elif "bar" in p:
            beat = songwrite.bar_to_beats(sigs, int(p["bar"]))
        else:
            raise songwrite.SongWriteError("each point needs beat or bar")
        if beat < 0:
            raise songwrite.SongWriteError("point before the song start: %s" % beat)
        new.append({"start": beat, "value": convert(p.get("value"))})
    if not new:
        raise songwrite.SongWriteError("no points")
    return sorted(new, key=lambda e: e["start"])


def _track(path, track):
    with Song(path) as s:
        tracks = s.tracks()
        sigs = s.time_signatures()
    t = next((x for x in tracks if x["name"] == track or x["index"] == track), None)
    if t is None:
        raise songwrite.SongWriteError("no track %r" % track)
    return t, sigs


def write_automation(path, track, param, points, replace=True, read_mode=True):
    """Write an automation envelope for a track's mixer channel (instrument tracks: their instrument
    channel). points: [{"beat" (quarter notes from song start) or "bar" (1-based), "value"}]; replace
    (default) drops the old points between the first and last new point, else all old points stay."""
    param = param.lower()
    if param not in PARAMS:
        raise songwrite.SongWriteError("unknown parameter %r (volume, pan, mute)" % param)
    t, sigs = _track(path, track)
    mixer = t.get("mixer")
    if not mixer or not mixer.get("uid"):
        raise songwrite.SongWriteError("track %r has no mixer channel in the saved song" % t["name"])
    new = _beats(points, sigs, lambda v: normalize(param, v))

    def mode(z_read):
        xml = z_read("Devices/audiomixer.xml")
        if xml is None:
            return {}, False
        xml, changed = _read_mode(xml, mixer["uid"])
        return ({"Devices/audiomixer.xml": xml} if changed else {}), changed
    res = _write_lane(path, t, "param:///AudioMixer/%s/%s" % (mixer["uid"], param), "%s/%s" % (mixer["type"], param),
                      PARAMS[param], COLORS[param], new, replace, mode if read_mode else None)
    res.update(param=param, channel=mixer.get("label"))
    return res


# ---- plug-in parameters -----------------------------------------------------------------------

def _effect_device(z_read, mixer, slot=None, plugin=None):
    """(device uid, class id, slot name, plug-in name) of an insert on the track's channel."""
    from . import effects
    xml = z_read("Devices/audiomixer.xml")
    a, b = effects._channel_span(xml, mixer["type"], mixer["name"])
    slots = effects._slots(xml, a, b)
    hits = [x for x in slots if (slot and x[0] == slot) or (plugin and x[1] and x[1].lower() == plugin.lower())]
    if not hits and not slot and not plugin and len(slots) == 1:
        hits = slots
    if len(hits) != 1:
        raise songwrite.SongWriteError("insert %r not found on %s (slots: %s)" % (
            slot or plugin, mixer.get("label"), ["%s %s" % (x[0], x[1]) for x in slots]))
    name, dev, _preset, s0, s1 = hits[0]
    block = xml[s0:s1]
    cls = re.search(r'<UID x:id="deviceClassID" uid="([^"]+)"/>', block)
    uid = re.search(r'x:id="deviceData"[^>]*>\s*<UID x:id="uniqueID" uid="([^"]+)"/>', block)
    if not (cls and uid):
        raise songwrite.SongWriteError("insert %s has no device IDs in the saved song" % name)
    return uid.group(1), cls.group(1), name, dev


def _instrument_device(z_read, track_name):
    """(device uid, class id, slot name, plug-in name, slot span) of the instrument a music track plays."""
    mt = z_read("Devices/musictrackdevice.xml") or ""
    m = re.search(r'<MusicTrackChannel\b[^>]*\blabel=%s[^>]*>(.*?)</MusicTrackChannel>' % re.escape(_attr(track_name)),
                  mt, re.S)
    dest = m and re.search(r'x:id="destination" objectID="(\{[^}]+\})/', m.group(1))
    if not dest:
        raise songwrite.SongWriteError("track %r plays no instrument in the saved song" % track_name)
    uid = dest.group(1)
    from . import effects
    sy = z_read("Devices/audiosynthfolder.xml") or ""
    i = sy.find('uid="%s"' % uid)
    if i < 0:
        raise songwrite.SongWriteError("instrument %s of track %r not found" % (uid, track_name))
    s0 = max(x.start() for x in re.finditer(r'<Attributes name="Inst\d+">', sy[:i]))
    s1 = effects._block_end(sy, s0)
    block = sy[s0:s1]
    cls = re.search(r'<UID x:id="deviceClassID" uid="([^"]+)"/>', block)
    dev = re.search(r'x:id="deviceData" name="([^"]*)"', block)
    slot = re.match(r'<Attributes name="([^"]+)">', block).group(1)
    return uid, cls.group(1) if cls else None, slot, dev.group(1) if dev else None, (s0, s1)


def write_plugin_automation(path, track, param, points, instrument=False, slot=None, plugin=None, title=None,
                            replace=True, read_mode=True):
    """Automate a plug-in parameter: an insert effect on the track's channel (slot FX01.. or plugin
    name) or, with instrument=true, the instrument the track plays. param: ID as in
    library_plugin_params ('lmffreq', 'vstp54'). values: normalized 0..1 (the knob's travel)."""
    t, sigs = _track(path, track)

    def norm(v):
        f = float(v)
        if not 0 <= f <= 1:
            raise songwrite.SongWriteError("plug-in automation values are normalized 0..1: %s" % v)
        return f
    new = _beats(points, sigs, norm)
    with songwrite.zipfile.ZipFile(path) as z:
        names = set(z.namelist())

        def z_read(n):
            return z.read(n).decode("utf-8-sig") if n in names else None
        if instrument:
            uid, cls, where, dev, span = _instrument_device(z_read, t["name"])
            identity = "param:///Synths/%s/%s" % (uid, param)
        else:
            mixer = t.get("mixer")
            if not mixer or not mixer.get("uid"):
                raise songwrite.SongWriteError("track %r has no mixer channel in the saved song" % t["name"])
            uid, cls, where, dev = _effect_device(z_read, mixer, slot, plugin)
            identity = "param:///AudioMixer/%s/Inserts/%s/%s" % (mixer["uid"], uid, param)

    def mode(z_read):
        if instrument:
            sy = z_read("Devices/audiosynthfolder.xml")
            s0, s1 = span
            block = sy[s0:s1]
            m = re.search(r'<Attributes x:id="Automation" mode="0"/>', block)
            if not m:
                return {}, False
            block = block[:m.start()] + '<Attributes x:id="Automation" mode="1"/>' + block[m.end():]
            return {"Devices/audiosynthfolder.xml": sy[:s0] + block + sy[s1:]}, True
        xml, changed = _read_mode(z_read("Devices/audiomixer.xml"), t["mixer"]["uid"])
        return ({"Devices/audiomixer.xml": xml} if changed else {}), changed
    _check_param(dev, param)
    res = _write_lane(path, t, identity, "%s/%s" % (cls, param), title or param, "FF42FFC8", new, replace,
                      mode if read_mode else None)
    res.update(param=param, device=dev, slot=where)
    return res


def _check_param(device, param):
    """Native plug-ins: the ID must be one of the parameters seen in its presets (VST 'vstp<n>' IDs and
    plug-ins without presets are not checked)."""
    import difflib
    from . import library
    if not device or re.match(r"vstp\d+$", param):
        return
    known = library.native_params(re.sub(r"\s+\d+$", "", device))
    if known and param not in known:
        close = difflib.get_close_matches(param, list(known), 5, 0.4)
        raise songwrite.SongWriteError("%s has no parameter %r (library_plugin_params lists them)%s" % (
            device, param, "; close: %s" % ", ".join(close) if close else ""))


def _write_lane(path, t, identity, meta, title, color, new, replace, mode):
    """Merge points into the track's automation lane with this identity (creating the lane) and
    write song.xml, the envelope and, via mode(z_read) -> ({member: xml}, changed), the Read mode."""
    member = "Envelopes/%s/%s.envelopex" % (t["name"], title.replace("/", "-"))
    with songwrite.zipfile.ZipFile(path) as z:
        names = set(z.namelist())

        def z_read(n):
            return z.read(n).decode("utf-8-sig") if n in names else None
        xml = z_read("Song/song.xml")
        a, b = _track_element(xml, t["name"])
        el = xml[a:b]
        region = re.search(r'<AutomationRegion\b[^>]*identity=%s[^>]*>.*?</AutomationRegion>' % re.escape(_attr(identity)),
                           el, re.S)
        if region:
            url = re.search(r'url="media:///([^"]+)"', region.group(0))
            member = url.group(1) if url else member
        old = []
        if member in names:
            old = (ubjson.loads(z.read(member)) or {}).get("events") or []
        extra, mode_set = mode(z_read) if mode else ({}, False)
    if replace:
        lo, hi = new[0]["start"], new[-1]["start"]
        old = [e for e in old if not lo <= float(e.get("start", 0)) <= hi]
    events = sorted(old + new, key=lambda e: float(e["start"]))

    if not region:
        reg = ('\n\t\t\t\t\t<AutomationRegion title=%s identity=%s\n\t\t\t\t\t                  metaIdentity=%s color="%s" mute="0">'
               '\n\t\t\t\t\t\t<Url x:id="points" type="1" url=%s/>\n\t\t\t\t\t</AutomationRegion>'
               % (_attr(title), _attr(identity), _attr(meta), color, _attr("media:///" + member)))
        lst = re.search(r'(<Attributes x:id="AutomationRegionList"[^>]*?)(/>|>)', el)
        if lst and lst.group(2) == ">":
            el = el[:lst.end()] + reg + el[lst.end():]
        elif lst:  # <Attributes .../> -> open it
            el = el[:lst.start()] + lst.group(1) + ">" + reg + "\n\t\t\t\t</Attributes>" + el[lst.end():]
        else:
            uid = re.search(r'<UID x:id="channelID"[^>]*/>', el)
            at = uid.end() if uid else el.find(">") + 1
            el = (el[:at] + '\n\t\t\t\t<Attributes x:id="AutomationRegionList" visible="0">' + reg +
                  "\n\t\t\t\t</Attributes>" + el[at:])
        xml = xml[:a] + el + xml[b:]
    members = {"Song/song.xml": ("\ufeff" + xml).encode("utf-8"), member: ubjson.dumps({"events": events})}
    for n, text in extra.items():
        members[n] = ("\ufeff" + text).encode("utf-8")
    bak = songwrite.backup(path)
    songwrite.rewrite_zip(path, members)
    return {"track": t["name"], "points_written": len(new), "envelope_points": len(events), "envelope_file": member,
            "new_lane": not region, "read_mode_set": mode_set, "backup": bak}
