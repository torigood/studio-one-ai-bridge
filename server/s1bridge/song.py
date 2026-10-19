"""Read-only parser for Studio One 5 .song files.

A .song is a ZIP. The parts used here (observed in Studio One 5.5):
  Song/song.xml         tracks, parts, tempo/time-signature maps, markers
  Song/mediapool.xml    clip id -> media:///... or file:/// url
  Devices/audiomixer.xml  channels: gain (linear), pan (0..1, 0.5 = centre), mute, solo
  Performances/**.musicx  UBJSON {timeFormat, events:[{start,pitch,length,velocity,noteId}]}

Units (inference, consistent across inspected files):
  timeFormat="2" positions are quarter notes from song start.
  TempoMapSegment@tempo is seconds per quarter note (bpm = 60 / tempo).
  AudioEvent timeFormat="0" (audio imported by drag and drop): start, length and offset (into the file)
  are seconds.
  musicx event start is quarter notes from clip start; MusicPart@offset is the
  clip position at the part's left edge.

The file is opened read-only and never written.
"""
import math
import re
import xml.etree.ElementTree as ET
import zipfile

from . import notegrid, ubjson

_PI = re.compile(r"<\?(?!xml)[^>]*\?>")


def _xml(raw):
    text = raw.decode("utf-8-sig", errors="replace")
    text = _PI.sub("", text)
    # Studio One uses the "x:" prefix without declaring it.
    text = text.replace(" x:id=", " xid=")
    return ET.fromstring(text)


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def gain_to_db(g):
    return round(20 * math.log10(g), 2) if g and g > 0 else float("-inf")


def fifths_pc(f):
    """Circle-of-fifths position (C=0, G=1, F=-1) -> pitch class."""
    return (f * 7) % 12


def fifths_name(f):
    """6 -> 'F#', -2 -> 'Bb'."""
    i = f + 1
    return "FCGDAEB"[i % 7] + ("#" * (i // 7) if i >= 0 else "b" * (-(i // 7)))


def chord_name(root, intervals, bass=None):
    from .chords import QUALITIES
    q = next((k for k, v in QUALITIES.items() if v == tuple(intervals)), None)
    name = fifths_name(root) + (q if q is not None else "(%s)" % ",".join(map(str, intervals)))
    return name + ("/" + fifths_name(bass) if bass is not None and bass != root else "")


class Song:
    def __init__(self, path):
        self.path = path
        self._zip = zipfile.ZipFile(path, "r")
        self._names = set(self._zip.namelist())
        self.root = _xml(self._read("Song/song.xml"))
        self._clips = self._load_clips()
        self._channels = self._load_channels()

    def close(self):
        self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read(self, name):
        return self._zip.read(name)

    # ---- media pool -------------------------------------------------------
    def _load_clips(self):
        clips = {}
        if "Song/mediapool.xml" not in self._names:
            return clips
        root = _xml(self._read("Song/mediapool.xml"))
        for el in root.iter():
            mid = el.get("mediaID")
            if not mid:
                continue
            url = None
            for u in el.iter("Url"):
                url = u.get("url")
                break
            clips[mid] = {"kind": el.tag, "url": url}
        return clips

    def _musicx(self, url):
        if not url or not url.startswith("media:///"):
            return None
        name = url[len("media:///"):]
        if name not in self._names:
            return None
        return ubjson.loads(self._read(name))

    # ---- mixer ------------------------------------------------------------
    def _load_channels(self):
        chans = {}
        if "Devices/audiomixer.xml" not in self._names:
            return chans
        root = _xml(self._read("Devices/audiomixer.xml"))
        for el in root.iter():
            if not el.tag.endswith("Channel") or el.get("gain") is None:
                continue
            uid = None
            for u in el.findall("UID"):
                if u.get("xid") == "uniqueID":
                    uid = u.get("uid")
            g = _f(el.get("gain"), 1.0)
            chans[uid] = {
                "uid": uid,
                "type": el.tag,
                "name": el.get("name"),  # e.g. "Channel03"; same as the live mixer channel name
                "label": el.get("label"),
                "volume_db": gain_to_db(g),
                "pan": round((_f(el.get("pan"), 0.5) - 0.5) * 2, 3),  # UNVERIFIED sign: live setValue(0.25) shows R50, so this may be mirrored
                "mute": el.get("mute") == "1",
                "solo": el.get("solo") == "1",  # inference: "2" seen on muted/non-soloed channels
                "inserts": [a.get("name") for a in el.iter("Attributes")
                            if a.get("xid") == "deviceData" and a.get("name")],
            }
        return chans

    def instrument_output_channel(self, track_label):
        """Mixer channel an instrument track plays through, or None.

        Devices/musictrackdevice.xml: MusicTrackChannel@label + Connection xid="instrumentOut"
        objectID="{uid}/Input"  ->  audiomixer.xml AudioSynthChannel UID xid="uniqueID".
        """
        if "Devices/musictrackdevice.xml" not in self._names:
            return None
        root = _xml(self._read("Devices/musictrackdevice.xml"))
        for el in root.iter("MusicTrackChannel"):
            if el.get("label") != track_label:
                continue
            for c in el.findall("Connection"):
                if c.get("xid") == "instrumentOut":
                    uid = (c.get("objectID") or "").split("/")[0]
                    return self._channels.get(uid)
        return None

    # ---- song-level -------------------------------------------------------
    def tempo_map(self):
        out = []
        for seg in self.root.iter("TempoMapSegment"):
            spq = _f(seg.get("tempo"))
            seg_out = {"start_beats": _f(seg.get("start")), "bpm": round(60.0 / spq, 3) if spq else None}
            if seg.get("curveType") == "1":
                seg_out["ramp"] = True
            out.append(seg_out)
        return out

    def time_signatures(self):
        return [{"start": _f(s.get("start")), "numerator": int(_f(s.get("numerator"), 4)),
                 "denominator": int(_f(s.get("denominator"), 4))}
                for s in self.root.iter("TimeSignatureMapSegment")]

    def markers(self):
        out = []
        for m in self.root.iter("MarkerEvent"):
            out.append({"name": m.get("name"), "start_beats": _f(m.get("start")),
                        "type": m.get("markerType")})
        return out

    def chords(self):
        """Chord Track events: [{start_beats, length_beats, name, root, bass, pcs}], pitch classes 0 = C.
        ChordEvent Attributes: root/bass on the circle of fifths, intervals = 12 flags above the root
        (non-zero = in the chord; chords.py)."""
        out = []
        for ev in self.root.iter("ChordEvent"):
            a = ev.find("Attributes")
            if a is None or a.get("root") is None:
                continue
            root = int(_f(a.get("root")))
            bass = int(_f(a.get("bass"), root))
            flags = (a.get("intervals") or "").split()
            ivs = tuple(i for i, v in enumerate(flags) if v not in ("0", ""))
            out.append({"start_beats": _f(ev.get("start")), "length_beats": _f(ev.get("length")),
                        "name": chord_name(root, ivs, bass), "root": fifths_pc(root), "bass": fifths_pc(bass),
                        "pcs": sorted({(fifths_pc(root) + i) % 12 for i in ivs})})
        return sorted(out, key=lambda c: c["start_beats"])

    def arranger_sections(self):
        out = []
        for tr in self.root.iter("ArrangerTrack"):
            for ev in tr.iter():
                if ev is tr or ev.get("name") is None or ev.get("length") is None:
                    continue
                out.append({"name": ev.get("name"), "start_beats": _f(ev.get("start")),
                            "length_beats": _f(ev.get("length"))})
        return out

    # ---- tracks -----------------------------------------------------------
    def _iter_tracks(self):
        # Tracks are one flat list; folder membership is MediaTrack@parentFolder = FolderTrack@trackID.
        lst = next((l for l in self.root.iter("List") if l.get("xid") == "Tracks"), None)
        if lst is None:
            return
        folders = {el.get("trackID"): el.get("name") for el in lst if el.tag == "FolderTrack"}
        for el in lst:
            if el.tag in ("MediaTrack", "FolderTrack", "AutomationTrack"):
                yield el, folders.get(el.get("parentFolder"))

    def tracks(self, include_notes=False):
        out = []
        for idx, (el, folder) in enumerate(self._iter_tracks()):
            chan_id = None
            for u in el.findall("UID"):
                if u.get("xid") == "channelID":
                    chan_id = u.get("uid")
            t = {
                "index": idx,
                "name": el.get("name"),
                "kind": el.tag,
                "mediaType": el.get("mediaType"),
                "folder": folder,
                "muted_track": el.get("mute") == "1",
                "color": el.get("color"),
                "channelID": chan_id,
                "mixer": self._channels.get(chan_id),
                "parts": [],
            }
            if t["mixer"] is None and el.get("mediaType") == "Music":
                # instrument tracks play through the instrument's AudioSynthChannel: follow the
                # instrumentOut connection, else take the AudioSynthChannel labelled like the track
                t["mixer"] = self.instrument_output_channel(el.get("name"))
                for c in self._channels.values():
                    if t["mixer"] is None and c["type"] == "AudioSynthChannel" and c["label"] == el.get("name"):
                        t["mixer"] = c
            for lst in el.findall("List"):
                if lst.get("xid") != "Events":
                    continue
                for ev in lst:
                    t["parts"].append(self._part(ev, include_notes))
            out.append(t)
        return out

    def _part(self, ev, include_notes):
        start = _f(ev.get("start"))
        length = _f(ev.get("length"))
        offset = _f(ev.get("offset"))
        p = {"type": ev.tag, "name": ev.get("name"), "start_beats": start,
             "length_beats": length, "muted": ev.get("mute") == "1"}
        clip = self._clips.get(ev.get("clipID"))
        if ev.tag == "MusicPart" and clip:
            data = self._musicx(clip["url"])
            notes = []
            for n in (data or {}).get("events", []):
                ns = _f(n.get("start"))
                if offset <= ns < offset + length:
                    notes.append({"start_beats": round(start + ns - offset, 6),
                                  "pitch": n.get("pitch"),
                                  "length_beats": n.get("length"),
                                  "velocity": round(_f(n.get("velocity")), 3)})
            p["note_count"] = len(notes)
            if include_notes:
                p["notes"] = notes
        elif clip:
            p["source"] = clip["url"]
            if ev.get("timeFormat") == "0":  # audio event placed in seconds: start/length/offset in seconds
                p.update(time_format="seconds", start_sec=start, length_sec=length, offset_sec=offset)
                tempo = self.tempo_map()
                p["start_beats"] = round(notegrid.sec_to_beat(tempo, start), 4)
                p["length_beats"] = round(notegrid.sec_to_beat(tempo, start + length) - p["start_beats"], 4)
            else:
                p["offset_beats"] = offset
        return p

    def summary(self):
        tr = self.tracks()
        return {
            "path": self.path,
            "tempo": self.tempo_map(),
            "time_signatures": self.time_signatures(),
            "markers": self.markers(),
            "arranger": self.arranger_sections(),
            "track_count": len(tr),
            "tracks": [_track_brief(t) for t in tr],
        }


def _track_brief(t):
    b = {k: v for k, v in t.items() if k != "parts"}
    b["part_count"] = len(t["parts"])
    b["note_count"] = sum(p.get("note_count", 0) for p in t["parts"])
    return b
