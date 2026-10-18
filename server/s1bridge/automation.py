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


def write_automation(path, track, param, points, replace=True, read_mode=True):
    """Write an automation envelope for a track's mixer channel (instrument tracks: their instrument
    channel). points: [{"beat" (quarter notes from song start) or "bar" (1-based), "value"}]; replace
    (default) drops the old points between the first and last new point, else all old points stay."""
    param = param.lower()
    if param not in PARAMS:
        raise songwrite.SongWriteError("unknown parameter %r (volume, pan, mute)" % param)
    with Song(path) as s:
        tracks = s.tracks()
        sigs = s.time_signatures()
    t = next((x for x in tracks if x["name"] == track or x["index"] == track), None)
    if t is None:
        raise songwrite.SongWriteError("no track %r" % track)
    mixer = t.get("mixer")
    if not mixer or not mixer.get("uid"):
        raise songwrite.SongWriteError("track %r has no mixer channel in the saved song" % t["name"])
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
        new.append({"start": beat, "value": normalize(param, p.get("value"))})
    if not new:
        raise songwrite.SongWriteError("no points")
    new.sort(key=lambda e: e["start"])

    title = PARAMS[param]
    identity = "param:///AudioMixer/%s/%s" % (mixer["uid"], param)
    member = "Envelopes/%s/%s.envelopex" % (t["name"], title)
    with songwrite.zipfile.ZipFile(path) as z:
        xml = z.read("Song/song.xml").decode("utf-8-sig")
        names = set(z.namelist())
        a, b = _track_element(xml, t["name"])
        el = xml[a:b]
        region = re.search(r'<AutomationRegion\b[^>]*identity=%s[^>]*>.*?</AutomationRegion>' % re.escape(_attr(identity)),
                           el, re.S)
        if region:
            url = re.search(r'url="media:///([^"]+)"', region.group(0))
            member = url.group(1) if url else member
        mixer_xml = z.read("Devices/audiomixer.xml").decode("utf-8-sig") if "Devices/audiomixer.xml" in names else None
        old = []
        if member in names:
            old = (ubjson.loads(z.read(member)) or {}).get("events") or []
    if replace:
        lo, hi = new[0]["start"], new[-1]["start"]
        old = [e for e in old if not lo <= float(e.get("start", 0)) <= hi]
    events = sorted(old + new, key=lambda e: float(e["start"]))

    if not region:
        reg = ('\n\t\t\t\t\t<AutomationRegion title=%s identity=%s\n\t\t\t\t\t                  metaIdentity=%s color="%s" mute="0">'
               '\n\t\t\t\t\t\t<Url x:id="points" type="1" url=%s/>\n\t\t\t\t\t</AutomationRegion>'
               % (_attr(title), _attr(identity), _attr("%s/%s" % (mixer["type"], param)), COLORS[param],
                  _attr("media:///" + member)))
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
    mode_set = False
    if read_mode and mixer_xml is not None:
        mixer_xml, mode_set = _read_mode(mixer_xml, mixer["uid"])
        if mode_set:
            members["Devices/audiomixer.xml"] = ("\ufeff" + mixer_xml).encode("utf-8")
    bak = songwrite.backup(path)
    songwrite.rewrite_zip(path, members)
    return {"track": t["name"], "param": param, "channel": mixer.get("label"), "points_written": len(new),
            "envelope_points": len(events), "envelope_file": member, "new_lane": not region,
            "read_mode_set": mode_set, "backup": bak}
