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


def _rewrite_zip(path, member, data):
    tmp = path + ".aibridge-tmp"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w") as dst:
        for info in src.infolist():
            raw = data if info.filename == member else src.read(info.filename)
            dst.writestr(info, raw, compress_type=info.compress_type)
    os.replace(tmp, path)


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

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stem = "%s %s" % (os.path.splitext(os.path.basename(path))[0], time.strftime("%Y%m%d-%H%M%S"))
    backup = os.path.join(BACKUP_DIR, stem + ".song")
    n = 1
    while os.path.exists(backup):
        n += 1
        backup = os.path.join(BACKUP_DIR, "%s (%d).song" % (stem, n))
    shutil.copy2(path, backup)
    _rewrite_zip(path, member, ubjson.dumps(data))
    return {"track": track_name, "part": part, "part_start_beats": p_start, "cc": cc, "lane": title,
            "points_written": len(new), "lane_points": len(lane["events"]), "clip_file": member,
            "shared_clip": shared, "backup": backup}
