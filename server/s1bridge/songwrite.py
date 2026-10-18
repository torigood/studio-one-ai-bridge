"""Write MIDI CC into a saved .song (the scripting API cannot create CC; docs/re-findings.md).

A part's CC lives in its clip's Performances/**.musicx as
  envelopes: [{identity: "Control/<cc>", title: "MIDI CC 0|<cc>", events: [{start, value}]}]
with start in quarter notes from clip start and value 0..1. Points are joined by straight
lines; a jump is two points at the same start.

Only that .musicx entry changes; every other ZIP entry is copied byte for byte. The original
file is copied to <data dir>/backups first. Studio One must not have the song open, or its
next save overwrites the change.
"""
import os
import shutil
import time
import zipfile

from . import config
from . import ubjson
from .song import Song, _f

BACKUP_DIR = os.path.join(config.DATA_DIR, "backups")


class SongWriteError(ValueError):
    pass


def _music_parts(song, track_ref):
    for idx, (el, _folder) in enumerate(song._iter_tracks()):
        if (isinstance(track_ref, int) and idx == track_ref) or el.get("name") == track_ref:
            parts = []
            for lst in el.findall("List"):
                if lst.get("xid") == "Events":
                    parts += [ev for ev in lst if ev.tag == "MusicPart"]
            return el.get("name"), parts
    raise SongWriteError("track not found: %r" % (track_ref,))


def _lane_title(song, identity, cc):
    """Title of an existing lane with this identity anywhere in the song, else Vital/JUCE style."""
    for name in song._names:
        if name.endswith(".musicx"):
            for env in (ubjson.loads(song._read(name)) or {}).get("envelopes", []):
                if env.get("identity") == identity and env.get("title"):
                    return env["title"]
    return "MIDI CC 0|%d" % cc


def rewrite_zip(path, members):
    """Replace ZIP entries {name: bytes}, None drops the entry, a name not in the ZIP is added; every
    other entry is copied as is."""
    tmp = path + ".aibridge-tmp"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w") as dst:
        seen = set()
        for info in src.infolist():
            seen.add(info.filename)
            if info.filename in members and members[info.filename] is None:
                continue
            raw = members[info.filename] if info.filename in members else src.read(info.filename)
            dst.writestr(info, raw, compress_type=info.compress_type)
        for name, raw in members.items():
            if name not in seen and raw is not None:
                dst.writestr(name, raw, compress_type=zipfile.ZIP_DEFLATED)
    os.replace(tmp, path)


def backup(path):
    """Copy the song to <data dir>/backups before changing it. Returns the copy's path."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stem = "%s %s" % (os.path.splitext(os.path.basename(path))[0], time.strftime("%Y%m%d-%H%M%S"))
    dest = os.path.join(BACKUP_DIR, stem + ".song")
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(BACKUP_DIR, "%s (%d).song" % (stem, n))
    shutil.copy2(path, dest)
    return dest


def write_cc(path, track, cc, points, part=None, replace=True, step=False):
    """points: [{"beat": quarter notes from song start, "value": 0..127}].

    The part is `part` (0-based among the track's instrument parts) or the one containing
    the first point. replace drops the lane's existing points between the first and last new
    point. step holds each value until the next point instead of ramping.
    """
    if not 0 <= cc <= 127:
        raise SongWriteError("cc must be 0..127")
    if not points:
        raise SongWriteError("no points")
    pts = sorted(((float(p["beat"]), float(p["value"])) for p in points), key=lambda p: p[0])
    for _, v in pts:
        if not 0 <= v <= 127:
            raise SongWriteError("value must be 0..127")

    with Song(path) as song:
        track_name, parts = _music_parts(song, track)
        if not parts:
            raise SongWriteError("track %r has no instrument parts" % track_name)
        if part is None:
            first = pts[0][0]
            hit = [i for i, ev in enumerate(parts)
                   if _f(ev.get("start")) <= first < _f(ev.get("start")) + _f(ev.get("length"))]
            if not hit:
                raise SongWriteError("no part of %r contains beat %g; parts: %s" % (
                    track_name, first, [(_f(e.get("start")), _f(e.get("length"))) for e in parts]))
            part = hit[0]
        if not 0 <= part < len(parts):
            raise SongWriteError("part index out of range (0..%d)" % (len(parts) - 1))
        ev = parts[part]
        p_start, p_len, p_off = _f(ev.get("start")), _f(ev.get("length")), _f(ev.get("offset"))
        outside = [b for b, _ in pts if not p_start <= b <= p_start + p_len]
        if outside:
            raise SongWriteError("beats %s are outside the part (%g..%g)" % (outside, p_start, p_start + p_len))
        clip = song._clips.get(ev.get("clipID"))
        url = clip and clip.get("url") or ""
        if not url.startswith("media:///") or url[len("media:///"):] not in song._names:
            raise SongWriteError("part has no music clip in the song file")
        member = url[len("media:///"):]
        shared = sum(1 for _el, _f2 in song._iter_tracks() for lst in _el.findall("List")
                     if lst.get("xid") == "Events" for e in lst if e.get("clipID") == ev.get("clipID")) > 1
        data = ubjson.loads(song._read(member))
        identity = "Control/%d" % cc
        title = _lane_title(song, identity, cc)

    # song beats -> clip time
    new = []
    prev = None
    for beat, value in pts:
        t = beat - p_start + p_off
        v = value / 127.0
        if step and prev is not None and prev != v:
            new.append({"start": t, "value": prev})
        new.append({"start": t, "value": v})
        prev = v

    envs = data.setdefault("envelopes", [])
    lane = next((e for e in envs if e.get("identity") == identity), None)
    if lane is None:
        lane = {"identity": identity, "title": title, "events": []}
        envs.append(lane)
    old = lane.get("events", [])
    if replace:
        lo, hi = new[0]["start"], new[-1]["start"]
        old = [e for e in old if not lo <= _f(e.get("start")) <= hi]
    lane["events"] = sorted(old + new, key=lambda e: _f(e.get("start")))

    bak = backup(path)
    rewrite_zip(path, {member: ubjson.dumps(data)})
    return {"track": track_name, "part": part, "part_start_beats": p_start, "cc": cc, "lane": title,
            "points_written": len(new), "lane_points": len(lane["events"]), "clip_file": member,
            "shared_clip": shared, "backup": bak}


def write_time_signatures(path, signatures):
    """Replace the song's time signature map. signatures: [{"bar": 1-based, "numerator", "denominator"}],
    bar 1 required. song.xml: TimeSignatureMap/TimeSignatureMapSegment start (quarter notes) numerator denominator.
    Live setting through the transport panel was unreliable (docs/re-findings.md), so this is offline."""
    import re
    sigs = sorted(signatures, key=lambda s: int(s["bar"]))
    if not sigs or int(sigs[0]["bar"]) != 1:
        raise SongWriteError("the first signature must be at bar 1")
    segs, beat, bar, cur = [], 0.0, 1, None
    for s in sigs:
        num, den, target = int(s["numerator"]), int(s["denominator"]), int(s["bar"])
        if not 1 <= num <= 32 or den not in (2, 4, 8, 16):
            raise SongWriteError("bad signature %s/%s" % (num, den))
        if cur:
            beat += (target - bar) * cur[0] * 4.0 / cur[1]
        elif target != 1:
            raise SongWriteError("the first signature must be at bar 1")
        bar, cur = target, (num, den)
        segs.append((beat, num, den, target))
    with zipfile.ZipFile(path) as z:
        xml = z.read("Song/song.xml").decode("utf-8-sig")
    m = re.search(r'(<TimeSignatureMap x:id="timeSignatureMap">)(.*?)(\s*</TimeSignatureMap>)', xml, re.S)
    if not m:
        raise SongWriteError("song has no TimeSignatureMap")
    body = "".join('\n\t\t\t\t<TimeSignatureMapSegment start="%s" numerator="%d" denominator="%d"/>'
                   % (("%.6f" % b).rstrip("0").rstrip("."), n, d) for b, n, d, _ in segs)
    xml = xml[:m.start(2)] + body + xml[m.end(2):]
    bak = backup(path)
    rewrite_zip(path, {"Song/song.xml": ("﻿" + xml).encode("utf-8")})
    return {"signatures": [{"bar": t, "beat": b, "time_signature": "%d/%d" % (n, d)} for b, n, d, t in segs],
            "backup": bak}


TEMPO_MIN, TEMPO_MAX = 10.0, 400.0  # the transport's tempo range; the tempo envelope is normalized to it
TEMPO_END = "1.7976931348623156386368421785227527018228425601508e+200"  # Studio One's open end


def write_tempo_map(path, changes):
    """Replace the song's tempo map. changes: [{"bar" (1-based) or "beat", "bpm", "ramp": bool}];
    the first is moved to the song start. ramp=true glides linearly from this point to the next one.
    song.xml: TempoMap > Attributes envelope (EnvelopePoint value = (bpm - 10) / 390) and one
    TempoMapSegment per point: start/end in quarter notes, tempo in seconds per quarter, curveType
    0 constant, 1 linear."""
    import re
    with Song(path) as song:
        sigs = song.time_signatures()
    pts = []
    for c in changes:
        if "beat" in c:
            beat = float(c["beat"])
        elif "bar" in c:
            beat = bar_to_beats(sigs, int(c["bar"]))
        else:
            raise SongWriteError("each tempo change needs bar or beat")
        bpm = float(c["bpm"])
        if not TEMPO_MIN <= bpm <= TEMPO_MAX:
            raise SongWriteError("tempo %s outside %g..%g BPM" % (bpm, TEMPO_MIN, TEMPO_MAX))
        pts.append((beat, bpm, bool(c.get("ramp"))))
    if not pts:
        raise SongWriteError("no tempo changes")
    pts.sort(key=lambda p: p[0])
    if len({round(p[0], 6) for p in pts}) != len(pts):
        raise SongWriteError("two tempo changes at the same position")
    pts[0] = (0.0,) + pts[0][1:]

    def num(v):
        return ("%.10f" % v).rstrip("0").rstrip(".")

    env, segs = [], []
    for i, (beat, bpm, ramp) in enumerate(pts):
        last = i == len(pts) - 1
        env.append('\n\t\t\t\t\t<EnvelopePoint%s value="%s"/>'
                   % ("" if beat == 0 else ' start="%s"' % num(beat), num((bpm - TEMPO_MIN) / (TEMPO_MAX - TEMPO_MIN))))
        segs.append('\n\t\t\t\t<TempoMapSegment curveType="%d" start="%s" end="%s" tempo="%s"/>'
                    % (1 if ramp and not last else 0, num(beat), TEMPO_END if last else num(pts[i + 1][0]),
                       repr(60.0 / bpm)))
    with zipfile.ZipFile(path) as z:
        xml = z.read("Song/song.xml").decode("utf-8-sig")
    m = re.search(r'(<TempoMap x:id="tempoMap">)(.*?)(\s*</TempoMap>)', xml, re.S)
    if not m:
        raise SongWriteError("song has no TempoMap")
    body = '\n\t\t\t\t<Attributes x:id="envelope">%s\n\t\t\t\t</Attributes>%s' % ("".join(env), "".join(segs))
    xml = xml[:m.start(2)] + body + xml[m.end(2):]
    bak = backup(path)
    rewrite_zip(path, {"Song/song.xml": ("\ufeff" + xml).encode("utf-8")})
    return {"tempo_map": [{"beat": b, "bpm": t, "ramp_to_next": r and i < len(pts) - 1}
                          for i, (b, t, r) in enumerate(pts)], "backup": bak}


def bar_to_beats(signatures, bar):
    """Quarter notes from song start to the first beat of a 1-based bar.
    signatures: Song.time_signatures() ([{start (quarter notes), numerator, denominator}])."""
    sigs = sorted(signatures, key=lambda s: s["start"]) or [{"start": 0.0, "numerator": 4, "denominator": 4}]
    beat, cur_bar, i = 0.0, 1, 0
    cur = sigs[0]
    while cur_bar < bar:
        beat += cur["numerator"] * 4.0 / cur["denominator"]
        cur_bar += 1
        while i + 1 < len(sigs) and sigs[i + 1]["start"] <= beat + 1e-9:
            i += 1
            cur = sigs[i]
    return beat


def write_markers(path, markers, replace=True):
    """Write named markers into the song's Marker Track. markers: [{"name", "bar" (1-based) or "beat"
    (quarter notes from song start)}]. replace (default) drops the old named markers first; the song's
    Start/End markers always stay. song.xml: MarkerTrack/MarkerEvent markerType="0" start name."""
    import re
    from xml.sax.saxutils import escape

    def quoteattr(v):  # always double quotes, as Studio One writes them
        return '"%s"' % escape(v, {'"': "&quot;"})
    sigs = Song(path).time_signatures()
    new = []
    for m in markers:
        name = str(m.get("name") or "").strip()
        if not name:
            raise SongWriteError("every marker needs a name")
        if "beat" in m:
            beat = float(m["beat"])
        elif "bar" in m:
            if int(m["bar"]) < 1:
                raise SongWriteError("bar is 1-based: %s" % m["bar"])
            beat = bar_to_beats(sigs, int(m["bar"]))
        else:
            raise SongWriteError("marker %r needs bar or beat" % name)
        if beat < 0:
            raise SongWriteError("marker %r starts before the song" % name)
        new.append((beat, name))
    with zipfile.ZipFile(path) as z:
        xml = z.read("Song/song.xml").decode("utf-8-sig")
    m = re.search(r'(<MarkerTrack\b[^>]*?)(/>|>(.*?)(\s*)</MarkerTrack>)', xml, re.S)
    if not m:
        raise SongWriteError("song has no Marker Track")
    head, body = m.group(1), m.group(3) or ""
    if replace:  # named markers only; Start/End (markerType 2/3) and anything else stay as they are
        body = re.sub(r'\s*<MarkerEvent\b(?=[^>]*markerType="0")[^>]*/>', "", body)
    kept = len(re.findall(r'<MarkerEvent\b', body))
    indent = "\n\t\t\t\t"
    added = [indent + '<MarkerEvent markerType="0" start=%s timeFormat="2" name=%s/>'
             % (quoteattr(("%.6f" % b).rstrip("0").rstrip(".")), quoteattr(n)) for b, n in sorted(new)]
    block = head + ">" + body + "".join(added) + "\n\t\t\t</MarkerTrack>"
    xml = xml[:m.start()] + block + xml[m.end():]
    bak = backup(path)
    rewrite_zip(path, {"Song/song.xml": ("﻿" + xml).encode("utf-8")})
    return {"markers": [{"beat": b, "name": n} for b, n in sorted(new)],
            "kept": kept, "replaced": replace, "backup": bak}


def write_sections(path, sections, replace=True):
    """Write arranger sections into the song's Arranger Track and show the track.
    sections: [{"name", start "bar" (1-based) or "beat", and "bars" or "beats" (length) or "end_bar"}].
    song.xml (verified 2026-10-18; Studio One loads, shows and re-saves these):
      <ArrangerTrack ...><Attributes x:id="attributes" hidden="0"/>
        <ArrangerEvent start="16" timeFormat="2" length="16" name="Verse"/>"""
    import re
    from xml.sax.saxutils import escape
    sigs = Song(path).time_signatures()
    new = []
    for s in sections:
        name = str(s.get("name") or "").strip()
        if not name:
            raise SongWriteError("every section needs a name")
        if "beat" in s:
            start = float(s["beat"])
        elif "bar" in s:
            start = bar_to_beats(sigs, int(s["bar"]))
        else:
            raise SongWriteError("section %r needs bar or beat" % name)
        if "beats" in s:
            length = float(s["beats"])
        elif "end_bar" in s:
            length = bar_to_beats(sigs, int(s["end_bar"])) - start
        elif "bars" in s and "bar" in s:
            length = bar_to_beats(sigs, int(s["bar"]) + int(s["bars"])) - start
        elif "bars" in s:
            raise SongWriteError("section %r: bars needs a start bar (or give beats)" % name)
        else:
            raise SongWriteError("section %r needs a length: bars, beats or end_bar" % name)
        if start < 0 or length <= 0:
            raise SongWriteError("section %r has no length or starts before the song" % name)
        new.append((start, length, name))
    new.sort()
    for (s1, l1, n1), (s2, _, n2) in zip(new, new[1:]):
        if s1 + l1 > s2 + 1e-9:
            raise SongWriteError("sections %r and %r overlap" % (n1, n2))
    with zipfile.ZipFile(path) as z:
        xml = z.read("Song/song.xml").decode("utf-8-sig")
    m = re.search(r'(<ArrangerTrack\b[^>]*?)(/>|>(.*?)</ArrangerTrack>)', xml, re.S)
    if not m:
        raise SongWriteError("song has no Arranger Track")
    body = m.group(3) or ""
    if replace:
        body = re.sub(r'\s*<ArrangerEvent\b[^>]*/>', "", body)
    elif any(True for _ in re.finditer(r'<ArrangerEvent\b', body)):
        old = [(float(re.search(r'start="([^"]+)"', e).group(1)) if 'start="' in e else 0.0,
                float(re.search(r'length="([^"]+)"', e).group(1)))
               for e in re.findall(r'<ArrangerEvent\b[^>]*/>', body)]
        for s, l, n in new:
            if any(s < os_ + ol and os_ < s + l for os_, ol in old):
                raise SongWriteError("section %r overlaps an existing section; use replace" % n)
    if re.search(r'<Attributes x:id="attributes"[^>]*hidden="1"', body):
        body = re.sub(r'(<Attributes x:id="attributes"[^>]*)hidden="1"', r'\1hidden="0"', body, count=1)
    elif 'x:id="attributes"' not in body:
        body = '\n\t\t\t\t<Attributes x:id="attributes" hidden="0"/>' + body

    def num(v):
        return ("%.6f" % v).rstrip("0").rstrip(".")
    added = "".join('\n\t\t\t\t<ArrangerEvent start="%s" timeFormat="2" length="%s" name="%s"/>'
                    % (num(s), num(l), escape(n, {'"': "&quot;"})) for s, l, n in new)
    block = m.group(1) + ">" + body.rstrip() + added + "\n\t\t\t</ArrangerTrack>"
    xml = xml[:m.start()] + block + xml[m.end():]
    bak = backup(path)
    rewrite_zip(path, {"Song/song.xml": ("﻿" + xml).encode("utf-8")})
    return {"sections": [{"name": n, "start_beat": s, "length_beats": l} for s, l, n in new],
            "replaced": replace, "backup": bak}
