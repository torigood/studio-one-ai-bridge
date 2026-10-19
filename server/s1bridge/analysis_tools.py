"""Audio analysis tools. The server stays standard-library only: numpy work runs in the analysis
pack's Python as a subprocess (audio_analysis.py, melody.py), at low priority and cached per file.
"""
import glob
import json
import os
import re
import subprocess

from . import clashes, common, config, live, notegrid
from .common import ToolError
from .keys import key_from_notes
from .song import Song


# Directory that contains the s1bridge package (server/ in a checkout, site-packages when installed)
PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ANALYSIS_PYTHON = config.analysis_python()


def audio_analyze(path, start=0, length=None):
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise ToolError("file not found: %s" % path)
    if not os.path.exists(ANALYSIS_PYTHON):
        raise ToolError("S1BRIDGE_ANALYSIS_PYTHON not found: %s" % ANALYSIS_PYTHON)
    cmd = [ANALYSIS_PYTHON, "-m", "s1bridge.audio_analysis", path, str(start)] + ([str(length)] if length else [])
    r = subprocess.run(cmd, cwd=PACKAGE_PARENT, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        if "numpy" in r.stderr:
            raise ToolError("audio analysis needs numpy: install the 'analysis' extra, or create .venv with numpy "
                            "in the checkout, or set S1BRIDGE_ANALYSIS_PYTHON")
        raise ToolError("analysis failed: %s" % r.stderr.strip()[-500:])
    return json.loads(r.stdout)


def _analysis_cache_key(module, args):
    """Same file (path, size, mtime), same arguments, same analysis code -> same result."""
    import hashlib
    h = hashlib.sha1(json.dumps([module, [str(x) for x in args]]).encode())
    if args and os.path.isfile(str(args[0])):
        st = os.stat(str(args[0]))
        h.update(("%d %d" % (st.st_size, st.st_mtime_ns)).encode())
    here = os.path.dirname(os.path.abspath(__file__))
    for f in sorted(glob.glob(os.path.join(here, "*.py")) + glob.glob(os.path.join(config.ENGINES_DIR, "*.py"))):
        h.update(("%s %d" % (f, os.stat(f).st_mtime_ns)).encode())
    return os.path.join(config.ANALYSIS_DIR, "cache", h.hexdigest() + ".json")


def run_analysis(module, args, timeout=900):
    """Run an analysis module in the analysis pack's Python at low priority (Studio One keeps the CPU);
    its JSON result, cached per file and arguments."""
    if not os.path.exists(ANALYSIS_PYTHON):
        raise ToolError("analysis Python not found: %s (run: studio-one-ai-bridge analysis-setup)" % ANALYSIS_PYTHON)
    cache = _analysis_cache_key(module, args)
    if os.path.isfile(cache):
        with open(cache) as f:
            return json.load(f)
    r = subprocess.run([ANALYSIS_PYTHON, "-m", "s1bridge." + module] + [str(x) for x in args], cwd=PACKAGE_PARENT,
                       capture_output=True, text=True, timeout=timeout,
                       preexec_fn=(lambda: os.nice(10)) if hasattr(os, "nice") else None)
    if r.returncode != 0:
        err = r.stderr.strip()
        if "ModuleNotFoundError" in err or "model missing" in err:
            raise ToolError("the analysis pack is not installed: run `studio-one-ai-bridge analysis-setup` "
                            "(%s)" % err.splitlines()[-1])
        raise ToolError("analysis failed: %s" % err[-500:])
    out = json.loads(r.stdout.strip().splitlines()[-1])
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache + ".part", "w") as f:
            json.dump(out, f)
        os.replace(cache + ".part", cache)
    except OSError:
        pass
    return out


def _audio_sources(song, tracks, tempo, ref):
    """[(wav, song second where file second `file_at` plays, file_at, length_sec)] for an audio track's events."""
    t = common.find_track(tracks, ref)
    out = []
    for p in t["parts"]:
        if not p.get("source") or p["muted"]:
            continue
        wav = common.media_path(song, p["source"])
        if not wav or not wav.lower().endswith(".wav"):
            raise ToolError("event %r: only WAV files can be analysed (%s)" % (p["name"], p["source"]))
        if p.get("time_format") == "seconds":
            out.append((wav, p["start_sec"], p["offset_sec"], p["length_sec"]))
        else:
            start = notegrid.beat_to_sec(tempo, p["start_beats"])
            off = notegrid.beat_to_sec(tempo, p["start_beats"] + p.get("offset_beats", 0)) - start
            out.append((wav, start, off, notegrid.beat_to_sec(tempo, p["start_beats"] + p["length_beats"]) - start))
    if not out:
        raise ToolError("no unmuted audio events on track %r" % (ref,))
    return out


MELODY_ENGINE = "pyin"         # best match to a hand-corrected reference in our test
CROSS_CHECK_ENGINE = "crepe-tiny"  # a neural second opinion; notes where the two differ are mostly wrong


def audio_melody(a, wait):
    song = common.song_path(a["song"]) if a.get("song") else None
    tempo, sigs, tracks = [], [], []
    if song:
        with Song(song) as s:
            tempo, sigs, tracks = s.tempo_map(), s.time_signatures(), s.tracks()
    if a.get("track") is not None:
        if not song:
            raise ToolError("track needs song")
        sources = _audio_sources(song, tracks, tempo, a["track"])
    elif a.get("path"):
        wav = os.path.expanduser(a["path"])
        if not os.path.isfile(wav):
            raise ToolError("file not found: %s" % wav)
        placed = song and a.get("start_beat") is not None
        sources = [(wav, notegrid.beat_to_sec(tempo, a["start_beat"]) if placed else 0.0, 0.0, None)]
    else:
        raise ToolError("give song + track, or path")
    timed = bool(song) and (a.get("track") is not None or a.get("start_beat") is not None)
    lo, hi = None, None
    if a.get("from_bar") or a.get("to_bar"):
        if not timed:
            raise ToolError("from_bar/to_bar need the audio placed in a song (song + track, or song + start_beat)")
        lo = notegrid.beat_to_sec(tempo, notegrid.bar_start(sigs, a.get("from_bar") or 1))
        if a.get("to_bar"):
            hi = notegrid.beat_to_sec(tempo, notegrid.bar_start(sigs, a["to_bar"] + 1))
    quality = a.get("quality") or "best"
    if quality not in ("best", "fast"):
        raise ToolError("quality must be best or fast")
    engine = a.get("engine") or MELODY_ENGINE
    cross = CROSS_CHECK_ENGINE if quality == "best" and not a.get("engine") else None

    def run(eng):
        found, unsure, layered = [], [], []
        for wav, at, file_at, length in sources:
            s0 = at if lo is None else max(at, lo)
            s1 = (at + length if length is not None else None)
            if hi is not None:
                s1 = hi if s1 is None else min(s1, hi)
            if s1 is not None and s1 <= s0:
                continue
            r = run_analysis("melody", [wav, file_at + (s0 - at), "-" if s1 is None else s1 - s0, eng])
            for n in r["notes"]:
                found.append(dict(n, start=at + n["start"] - file_at, end=at + n["end"] - file_at))
            unsure.extend([at + x - file_at, at + y - file_at] for x, y in r["unsure"])
            layered.extend([at + x - file_at, at + y - file_at] for x, y in r.get("layered", []))
        return found, unsure, layered
    found, unsure, layered = run(engine)
    agree = None
    if cross:
        other = run(cross)[0]
        same = 0
        for n in found:
            ov = [(min(n["end"], m["end"]) - max(n["start"], m["start"]), m["pitch"]) for m in other]
            ov = [o for o in ov if o[0] > 0]
            alt = max(ov)[1] if ov else None
            if alt == n["pitch"]:
                same += 1
            else:
                n["alt"] = alt  # None: the second engine heard no note here
                n["check"] = True
        agree = round(same / len(found), 2) if found else None
    if not timed:
        return {"engine": engine, "cross_check": cross, "agree": agree, "unit": "seconds", "notes": found,
                "unsure": unsure, "layered": layered}
    beat = lambda sec: notegrid.sec_to_beat(tempo, sec)
    notes = [{"beat": beat(n["start"]), "len": beat(n["end"]) - beat(n["start"]), "pitch": n["pitch"],
              "cents": n["cents"], "conf": n["confidence"], "also": n.get("also"),
              "octave_from": n.get("octave_from"), "check": n.get("check"), "alt": n.get("alt")} for n in found]
    grid = a.get("grid", 0.25)
    notes = notegrid.quantize(notes, grid)
    for n in notes:
        bar, pos = notegrid.bar_pos(sigs, n["beat"])
        n.update(bar=bar, pos=round(pos, 3), beat=round(n["beat"], 3), len=round(n["len"], 3),
                 note=notegrid.note_name(n["pitch"]))
    def bar_spans(spans):
        out = []
        for x, y in spans:
            bar, pos = notegrid.bar_pos(sigs, beat(x))
            out.append({"bar": bar, "pos": round(pos, 2), "len": round(beat(y) - beat(x), 2)})
        return out

    def brief(n):
        d = {k: n[k] for k in ("bar", "pos", "beat", "len", "note", "pitch", "cents", "conf")}
        if n["also"]:
            d["also"] = [notegrid.note_name(p) for p in n["also"]]
        if n["octave_from"] is not None:
            d["octave_from"] = notegrid.note_name(n["octave_from"])
        if n["check"]:
            d["check"] = notegrid.note_name(n["alt"]) if n["alt"] is not None else "no note"
        return d
    out = {"engine": engine, "grid": grid, "count": len(notes), "notes": [brief(n) for n in notes],
           "unsure": bar_spans(unsure), "layered": bar_spans(layered)}
    if cross:
        out.update(cross_check=cross, agree=agree, to_check=sum(1 for n in notes if n["check"]))
    if a.get("to_track") is not None and notes:
        first = notegrid.bar_start(sigs, notes[0]["bar"])
        end = notegrid.bar_start(sigs, notegrid.bar_pos(sigs, notes[-1]["beat"] + notes[-1]["len"] - 1e-6)[0] + 1)
        out["written"] = live.bridge("insert_notes", {
            "track": a["to_track"], "start_beats": first, "length_beats": end - first,
            "notes": [{"pitch": n["pitch"], "start_beats": n["beat"] - first, "length_beats": n["len"],
                       "velocity": 0.8} for n in notes]}, wait)
    return out


# unpitched or effect parts whose notes are sample triggers, not pitches (skipped unless listed in tracks)
UNPITCHED = re.compile(r"drum|perc|kit|snare|kick|hat|cymbal|sleigh|bell tree|shaker|tambourine|clap|gliss|fx|sfx",
                       re.I)


def check_clashes(song, a):
    with Song(song) as s:
        tracks, sigs, chord_list = s.tracks(include_notes=True), s.time_signatures(), s.chords()
    lo = notegrid.bar_start(sigs, a["from_bar"]) if a.get("from_bar") else None
    hi = notegrid.bar_start(sigs, a["to_bar"] + 1) if a.get("to_bar") else None
    vocal, vocal_name = [], a.get("vocal")
    if vocal_name is not None:
        t = common.find_track(tracks, vocal_name)
        if t["mediaType"] == "Audio":
            r = audio_melody({"song": song, "track": vocal_name, "from_bar": a.get("from_bar"),
                               "to_bar": a.get("to_bar"), "grid": 0, "quality": a.get("quality")}, 10)
            for n in r["notes"]:
                vocal.append({"beat": n["beat"], "len": n["len"], "pitch": n["pitch"]})
                vocal.extend({"beat": n["beat"], "len": n["len"], "pitch": notegrid.note_number(x), "layer": True}
                             for x in n.get("also", []))
        else:
            vocal = [{"beat": n["start_beats"], "len": n["length_beats"] or 0, "pitch": n["pitch"]}
                     for p in t["parts"] if not p["muted"] for n in p.get("notes", [])]
        vocal_name = t["name"]
    wanted, skip = a.get("tracks"), set(a.get("exclude") or [])
    parts = {}
    for t in tracks:
        if t["mediaType"] != "Music" or t["name"] == vocal_name or t["name"] in skip or t["muted_track"]:
            continue
        if wanted is not None and t["name"] not in wanted:
            continue
        if wanted is None and UNPITCHED.search(t["name"]):
            continue
        notes = [{"beat": n["start_beats"], "len": n["length_beats"] or 0, "pitch": n["pitch"]}
                 for p in t["parts"] if not p["muted"] for n in p.get("notes", [])
                 if n["pitch"] is not None and n["pitch"] >= 21]  # below A0: keyswitches
        if notes:
            parts[t["name"]] = notes
    issues = clashes.check(parts, vocal, chord_list, sigs, lo, hi)
    limit = a.get("limit", 60)
    out = {"checked": sorted(parts), "vocal": vocal_name, "chords": len(chord_list), "count": len(issues)}
    out.update(clashes.summary(issues))
    out["issues"] = issues[:limit]
    if len(issues) > limit:
        out["more"] = "%d more; narrow from_bar/to_bar or raise limit" % (len(issues) - limit)
    return out


def song_analyze(path, audio=True):
    with Song(path) as s:
        tracks = s.tracks(include_notes=True)
        out = {"path": path, "tempo": s.tempo_map(), "time_signatures": s.time_signatures(),
               "markers": s.markers(), "arranger": s.arranger_sections(), "tracks": []}
    all_notes, files = [], []
    for t in tracks:
        notes = [n for p in t["parts"] for n in p.get("notes", [])]
        info = {"name": t["name"], "kind": t["kind"], "mediaType": t["mediaType"], "folder": t["folder"]}
        if notes:
            pitches = [n["pitch"] for n in notes]
            info.update(note_count=len(notes), pitch_range=[min(pitches), max(pitches)],
                        key=key_from_notes([{"pitch": n["pitch"], "length": n["length_beats"] or 0} for n in notes])[:1])
            all_notes.extend({"pitch": n["pitch"], "length": n["length_beats"] or 0} for n in notes)
        srcs = sorted({common.media_path(path, p.get("source")) for p in t["parts"] if p.get("source")} - {None})
        if srcs:
            info["audio_files"] = srcs
            files.extend(f for f in srcs if f.lower().endswith(".wav") and f not in files)
        out["tracks"].append(info)
    out["key_from_notes"] = key_from_notes(all_notes)
    if audio and files:
        out["audio"] = {}
        for f in files[:6]:
            try:
                out["audio"][os.path.basename(f)] = audio_analyze(f, 0, 120)
            except ToolError as e:
                out["audio"][os.path.basename(f)] = {"error": str(e)}
    return out
