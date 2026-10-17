"""Place Sound Set audio loops into a saved .song.

A song references a loop as package://<sound set package ID>/<path inside the set> (the file
stays encrypted inside the Sound Set; Studio One decodes it). An AudioEvent longer than its
loop does not repeat (the rest is silent), so repeats need one event per loop length. That
length is inside the encrypted file; Studio One writes it into mediapool.xml
(Attributes format@frameCount/sampleRate, AudioTempoMapSegment@tempo) when it saves a song that
uses the loop. The loop index collects those values:

  python3 -m s1bridge.loops fill <empty.song>   put every .audioloop into the song's first audio
                                                track (song closed); open it in Studio One, save
  python3 -m s1bridge.loops harvest [songs...]  read lengths from saved songs (default: all)

The index lives in <data dir>/loops.json: {url: {beats, frames, rate, tempo}}.
"""
import glob
import json
import os
import re
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile

from . import config
from . import library
from .song import _xml, _f
from .songwrite import SongWriteError, backup, rewrite_zip

INDEX = os.path.join(config.DATA_DIR, "loops.json")


# ---- package IDs and URLs ------------------------------------------------------------------

def package_ids():
    """{sound set title: package ID} from the packageInfos of saved songs (every song lists all
    installed sets)."""
    out = {}
    for path in sorted(glob.glob(os.path.join(config.SONGS_ROOT, "**", "*.song"), recursive=True),
                       key=os.path.getmtime, reverse=True):
        try:
            with zipfile.ZipFile(path) as z:
                root = _xml(z.read("Song/mediapool.xml"))
        except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
            continue
        for a in root.iter("Association"):
            out.setdefault(a.get("value"), a.get("key"))
        if out:
            return out
    return out


def loop_url(item, ids=None):
    ids = ids if ids is not None else package_ids()
    pid = ids.get(item["sound_set"])
    if not pid:
        raise SongWriteError("package ID of sound set %r unknown (no saved song lists it)" % item["sound_set"])
    return "package://%s/%s" % (pid, item["path"])


# ---- index ---------------------------------------------------------------------------------

def load_index():
    try:
        with open(INDEX, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def infer_tempo(url, seconds):
    """(seconds per beat, source) for a loop without a saved tempo map: a number 60..200 in the
    file name that gives whole beats, else the 75..150 BPM tempo that gives 1..64 (power of two) beats."""
    stem = os.path.splitext(os.path.basename(url))[0]
    for n in re.findall(r"(?<!\d)(\d{2,3})(?!\d)", stem):
        bpm = int(n)
        if 60 <= bpm <= 200:
            beats = seconds * bpm / 60
            if beats >= 0.9 and abs(beats - round(beats)) < 0.03:
                return 60.0 / bpm, "name"
    for beats in (1, 2, 4, 8, 16, 32, 64):
        bpm = beats * 60 / seconds
        if 75 <= bpm < 150:
            return seconds / beats, "guess"
    return None, None


def _clips_in(path):
    with zipfile.ZipFile(path) as z:
        root = _xml(z.read("Song/mediapool.xml"))
    for clip in root.iter("AudioClip"):
        url = next((u.get("url") for u in clip.iter("Url") if u.get("xid") == "path"), None)
        fmt = next((a for a in clip.iter("Attributes") if a.get("xid") == "format"), None)
        if not url or not url.startswith("package://") or fmt is None:
            continue
        frames, rate = _f(fmt.get("frameCount")), _f(fmt.get("sampleRate"))
        if frames <= 0 or rate <= 0:
            continue
        seg = next(clip.iter("AudioTempoMapSegment"), None)
        tempo, source = (_f(seg.get("tempo")), "song") if seg is not None else infer_tempo(url, frames / rate)
        if tempo and tempo > 0:
            yield url, {"beats": round(frames / rate / tempo, 4), "frames": int(frames),
                        "rate": int(rate), "tempo": tempo, "tempo_source": source}


def harvest(paths=None):
    """Add loop lengths from saved songs to the index. Returns (new, total)."""
    if not paths:
        paths = glob.glob(os.path.join(config.SONGS_ROOT, "**", "*.song"), recursive=True)
    index = load_index()
    before = len(index)
    for p in paths:
        try:
            for url, info in _clips_in(p):
                if info["tempo_source"] == "song" or index.get(url, {}).get("tempo_source") != "song":
                    index[url] = info
        except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
            continue
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(INDEX, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=0)
    return len(index) - before, len(index)


# ---- song XML editing (text level, keeps Studio One's formatting) ---------------------------

def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _new_id():
    return "{%s}" % str(uuid.uuid4()).upper()


def _audio_track(song_xml, track):
    """(start, end, trackID, name) of an audio MediaTrack element, by name or 0-based index."""
    tracks = list(re.finditer(r"<(MediaTrack|FolderTrack|AutomationTrack)\b[^>]*>", song_xml))
    for idx, m in enumerate(tracks):
        tag = m.group(0)
        name = re.search(r'\bname="([^"]*)"', tag)
        name = name.group(1) if name else ""
        if (isinstance(track, int) and idx == track) or name == _esc(str(track)):
            if m.group(1) != "MediaTrack" or 'mediaType="Audio"' not in tag:
                raise SongWriteError("track %r is not an audio track" % (track,))
            end = song_xml.index("</MediaTrack>", m.end())
            tid = re.search(r'\btrackID="([^"]*)"', tag).group(1)
            return m.start(), end, tid, name
    raise SongWriteError("track not found: %r" % (track,))


def _add_events(song_xml, track, events_xml):
    start, end, _tid, _name = _audio_track(song_xml, track)
    body = song_xml[start:end]
    m = re.search(r'<List x:id="Events">', body)
    if m:
        pos = start + body.rindex("\n", 0, body.index("</List>", m.end())) + 1
    else:
        pos = start + body.rindex("\n") + 1
        events_xml = '\t\t\t\t<List x:id="Events">\n' + events_xml + "\t\t\t\t</List>\n"
    return song_xml[:pos] + events_xml + song_xml[pos:]


def _add_clips(pool_xml, clips_xml):
    m = re.search(r'<MediaFolder name="Audio">', pool_xml)
    if m:
        pos = pool_xml.index("</MediaFolder>", m.end())
        pos = pool_xml.rindex("\n", 0, pos) + 1
        return pool_xml[:pos] + clips_xml + pool_xml[pos:]
    folder = '\t\t<MediaFolder name="Audio">\n' + clips_xml + "\t\t</MediaFolder>\n"
    empty = re.search(r'<Attributes x:id="rootFolder"\s*/>', pool_xml)
    if empty:
        return pool_xml[:empty.start()] + '<Attributes x:id="rootFolder">\n' + folder + "\t</Attributes>" + \
            pool_xml[empty.end():]
    m = re.search(r'<Attributes x:id="rootFolder">', pool_xml)
    if not m:
        raise SongWriteError("mediapool.xml has no rootFolder")
    pos = pool_xml.index("\n\t</Attributes>", m.end()) + 1
    return pool_xml[:pos] + folder + pool_xml[pos:]


def _clip_xml(cid, url, info, uses):
    tempo = '\t\t\t\t<AudioTempoMap x:id="tempoMap" tempoApproved="1">\n' \
            '\t\t\t\t\t<AudioTempoMapSegment offset="0" tempo="%r"/>\n' \
            '\t\t\t\t</AudioTempoMap>\n' % info["tempo"] if info else ""
    # No format attributes: Studio One reads them from the loop and writes them on save (tested).
    return '\t\t\t<AudioClip mediaID="%s" useCount="%d">\n\t\t\t\t<Url x:id="path" type="1" url="%s"/>\n%s' \
           '\t\t\t</AudioClip>\n' % (cid, uses, _esc(url), tempo)


def _event_xml(cid, tid, name, start, length):
    return ('\t\t\t\t\t<AudioEvent clipID="%s" start="%r" timeFormat="2" length="%r"\n'
            '\t\t\t\t\t            name="%s" speed="1" transpose="0" tune="0" normalize="0"\n'
            '\t\t\t\t\t            modification="0">\n'
            '\t\t\t\t\t\t<Attributes x:id="attributes" trackID="%s"/>\n'
            '\t\t\t\t\t</AudioEvent>\n') % (cid, float(start), float(length), _esc(name), tid)


def _read_song(path):
    with zipfile.ZipFile(path) as z:
        return (z.read("Song/song.xml").decode("utf-8-sig"), z.read("Song/mediapool.xml").decode("utf-8-sig"))


def _write_song(path, song_xml, pool_xml):
    rewrite_zip(path, {"Song/song.xml": ("﻿" + song_xml).encode("utf-8"),
                       "Song/mediapool.xml": ("﻿" + pool_xml).encode("utf-8")})


# ---- public ----------------------------------------------------------------------------------

def find_loop(query):
    """One .audioloop by exact path/url/file name, or the only search hit."""
    items = [i for i in library.loops() if i["kind"] == "audioloop"]
    q = query.strip()
    for i in items:
        stem = os.path.splitext(os.path.basename(i["path"]))[0]
        if q in (i["path"], stem, os.path.basename(i["path"])) or q.endswith("/" + i["path"]):
            return i
    words = q.lower().split()
    hits = [i for i in items if all(w in (i["sound_set"] + "/" + i["path"]).lower() for w in words)]
    if len(hits) == 1:
        return hits[0]
    raise SongWriteError("loop %r: %d matches %s" % (query, len(hits), [h["path"] for h in hits[:8]]))


def add_loop(path, track, loop, start_beats, length_beats=None):
    """Place an audio loop on an audio track from start_beats, repeated to fill length_beats
    (default: one loop). Needs the loop's length from the index."""
    item = find_loop(loop)
    url = loop_url(item)
    info = load_index().get(url)
    if not info:
        raise SongWriteError("length of %s is not indexed yet; run python3 -m s1bridge.loops fill/harvest "
                             "(see README)" % item["path"])
    loop_beats = info["beats"]
    total = float(length_beats) if length_beats else loop_beats
    name = os.path.splitext(os.path.basename(item["path"]))[0]

    song_xml, pool_xml = _read_song(path)
    _s, _e, tid, track_name = _audio_track(song_xml, track)
    m = re.search(r'<AudioClip mediaID="([^"]+)"[^>]*>\s*<Url x:id="path" type="1" url="%s"/>' % re.escape(_esc(url)),
                  pool_xml)
    events = []
    t = 0.0
    while t < total - 1e-6:
        events.append((start_beats + t, min(loop_beats, total - t)))
        t += loop_beats
    if m:
        cid = m.group(1)
    else:
        cid = _new_id()
        pool_xml = _add_clips(pool_xml, _clip_xml(cid, url, info, len(events)))
    song_xml = _add_events(song_xml, track, "".join(_event_xml(cid, tid, name, s, l) for s, l in events))
    bak = backup(path)
    _write_song(path, song_xml, pool_xml)
    return {"track": track_name, "loop": item["path"], "sound_set": item["sound_set"], "loop_beats": loop_beats,
            "events": [{"start_beats": s, "length_beats": l} for s, l in events], "backup": bak}


def fill_index_song(path, kind="audioloop"):
    """Put every loop not yet indexed into the first audio track, 1 beat apart."""
    ids = package_ids()
    index = load_index()
    song_xml, pool_xml = _read_song(path)
    first_audio = next((i for i, m in enumerate(re.finditer(r"<(MediaTrack|FolderTrack|AutomationTrack)\b[^>]*>", song_xml))
                        if m.group(1) == "MediaTrack" and 'mediaType="Audio"' in m.group(0)), None)
    if first_audio is None:
        raise SongWriteError("the song needs one audio track")
    _s, _e, tid, _n = _audio_track(song_xml, first_audio)
    clips, events, skipped = [], [], 0
    for item in library.loops():
        if item["kind"] != kind:
            continue
        try:
            url = loop_url(item, ids)
        except SongWriteError:
            skipped += 1
            continue
        if url in index:
            continue
        cid = _new_id()
        clips.append(_clip_xml(cid, url, None, 1))
        events.append(_event_xml(cid, tid, os.path.splitext(os.path.basename(item["path"]))[0], 2 * len(events), 1))
    if not events:
        return {"added": 0, "skipped_unknown_package": skipped}
    pool_xml = _add_clips(pool_xml, "".join(clips))
    song_xml = _add_events(song_xml, first_audio, "".join(events))
    _write_song(path, song_xml, pool_xml)
    return {"added": len(events), "skipped_unknown_package": skipped}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "fill" and len(sys.argv) == 3:
        print(json.dumps(fill_index_song(os.path.expanduser(sys.argv[2]))))
    elif cmd == "harvest":
        new, total = harvest([os.path.expanduser(p) for p in sys.argv[2:]] or None)
        print("indexed %d new loop(s), %d total -> %s" % (new, total, INDEX))
    else:
        sys.exit(__doc__)
