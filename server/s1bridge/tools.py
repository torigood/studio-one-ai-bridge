"""MCP tool definitions and handlers.

Two groups:
  song_*  read saved .song files directly (read-only, no Studio One needed)
  daw_*   send a job to the in-DAW bridge through the mailbox and wait for its result
"""
import glob
import json
import os

from . import config
from . import midi
from .mailbox import Mailbox
from .song import Song
from . import library
from . import vst3params
from . import songwrite
from . import loops
from . import chords
import time
from .keys import key_from_notes
import subprocess
import urllib.parse

# Trigger the in-DAW "Run Jobs" command through the AI Bridge virtual MIDI surface.
# Set S1BRIDGE_MIDI=0 to disable (then run the command by hand in Studio One).
USE_MIDI = os.environ.get("S1BRIDGE_MIDI", "1") != "0"

SONGS_ROOT = config.SONGS_ROOT

# Commands the AI may not run through daw_command (closing, quitting, discarding work).
BLOCKED_COMMANDS = {
    ("File", "Close"), ("File", "Quit"), ("File", "Exit"), ("File", "Revert"),
    ("File", "New Song"), ("File", "New Project"), ("File", "New Show"), ("File", "Open"),
    ("Application", "Quit"),
}

_catalog = None


def _load_catalog():
    global _catalog
    if _catalog is None:
        path = config.catalog_path()
        try:
            with open(path, encoding="utf-8") as f:
                _catalog = json.load(f)
        except (OSError, ValueError):
            # Generated on the user's machine by install/install.py; not shipped.
            _catalog = {"commands": [], "editTasks": [], "missing": path}
    return _catalog


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


_S = {"type": "string"}
_N = {"type": "number"}
_I = {"type": "integer"}
_SONG = {"type": "string", "description": "Absolute path to a .song file (see song_list)."}
_TRACK = {"type": ["string", "integer"], "description": "Track name (exact) or 0-based index."}
_WAIT = {"type": "number", "description": "Seconds to wait for the bridge (default 10)."}

TOOLS = [
    # ---- offline -----------------------------------------------------------
    {"name": "song_list",
     "description": "List saved Studio One songs (newest first). Autosave history is skipped.",
     "inputSchema": _schema({"limit": _I})},
    {"name": "library_instruments",
     "description": "Instrument plug-ins installed for Studio One (name, class_id, format, vendor). "
                    "Use class_id with daw_add_instrument.",
     "inputSchema": _schema({})},
    {"name": "library_loops",
     "description": "Search loops in installed Studio One Sound Sets by words in the name/folder, "
                    "kind (audioloop, musicloop, audio, midi) and bpm (+-3). audioloops whose length is indexed "
                    "show loop_beats and can be placed with song_add_loop.",
     "inputSchema": _schema({"query": _S, "kind": {"type": "string", "enum": ["audioloop", "musicloop", "audio", "midi"]},
                             "bpm": _N, "limit": _I})},
    {"name": "library_effects",
     "description": "Audio effect plug-ins installed for Studio One (name, class_id, format, vendor). "
                    "Use class_id with daw_insert_effect.",
     "inputSchema": _schema({})},
    {"name": "library_plugin_params",
     "description": "Parameters of a plug-in for daw_plugin_param. Studio One native plug-ins (Pro EQ, Room Reverb, "
                    "Mojito, ...): IDs and value ranges from factory presets, plus preset names. Third-party VST3 "
                    "(Vital, Surge XT, BBC Symphony Orchestra, ...): 'vstp<id>' names with titles, filtered by query "
                    "words (e.g. 'filter 1 cutoff'); needs the dump from python3 -m s1bridge.vst3params.",
     "inputSchema": _schema({"plugin": _S, "query": _S, "limit": _I}, ["plugin"])},
    {"name": "song_summary",
     "description": "Read a saved .song: tempo, time signature, markers, tracks with folder, "
                    "part/note counts and mixer state (volume dB, pan -1..1, mute, inserts). Read-only.",
     "inputSchema": _schema({"path": _SONG}, ["path"])},
    {"name": "song_track_notes",
     "description": "Notes of one track in a saved .song. Positions are quarter notes from song start; "
                    "pitch is MIDI number; velocity 0..1.",
     "inputSchema": _schema({"path": _SONG, "track": _TRACK}, ["path", "track"])},
    {"name": "song_write_cc",
     "description": "Write MIDI CC points (e.g. 1 modulation, 11 expression, 64 sustain) into an instrument part of a "
                    "saved .song. If the song is open in Studio One it is saved, closed, written and reopened "
                    "automatically (a few seconds; undo history is lost). beat = quarter notes from song start, value 0..127; points are joined by ramps unless "
                    "step is true. The part is the one containing the first point unless part (0-based) is given. "
                    "replace (default true) drops the lane's old points between the first and last new point. "
                    "The original file is backed up first.",
     "inputSchema": _schema({
         "path": _SONG, "track": _TRACK, "cc": _I, "part": _I,
         "points": {"type": "array", "items": _schema({"beat": _N, "value": _N}, ["beat", "value"])},
         "replace": {"type": "boolean"}, "step": {"type": "boolean"}},
         ["path", "track", "cc", "points"])},
    {"name": "song_add_loop",
     "description": "Place a Sound Set .audioloop (path or file name from library_loops) on an existing AUDIO track "
                    "of a saved .song from start_beats (quarter notes from song start), repeated to fill "
                    "length_beats (default one loop; the last copy is cut). If the song is open in Studio One it is "
                    "saved, closed, written and reopened automatically. Needs the loop length index (loop_beats in library_loops). "
                    "The original file is backed up first.",
     "inputSchema": _schema({"path": _SONG, "track": _TRACK, "loop": _S, "start_beats": _N, "length_beats": _N},
                            ["path", "track", "loop", "start_beats"])},
    {"name": "song_set_time_signatures",
     "description": "Replace the time signature map of a saved .song: [{bar (1-based, first must be 1), numerator, "
                    "denominator 2|4|8|16}]. If the song is open it is saved, closed, written and reopened automatically.",
     "inputSchema": _schema({"path": _SONG, "signatures": {"type": "array", "items": _schema(
         {"bar": _I, "numerator": _I, "denominator": _I}, ["bar", "numerator", "denominator"])}},
         ["path", "signatures"])},
    {"name": "song_write_chords",
     "description": "Write chords into the Chord Track of a saved .song: [{beat, length, chord}] with beat/length in "
                    "quarter notes and chord names like C, Am7, F#m7b5, Gsus4, Bbmaj7, Dadd9 (slash basses are ignored). "
                    "replace (default true) removes old chords overlapping the written range. If the song is open "
                    "it is saved, closed, written and reopened automatically. The song needs a Chord Track.",
     "inputSchema": _schema({"path": _SONG, "replace": {"type": "boolean"},
                             "chords": {"type": "array", "items": _schema({"beat": _N, "length": _N, "chord": _S},
                                                                          ["beat", "length", "chord"])}},
                            ["path", "chords"])},
    {"name": "audio_analyze",
     "description": "Analyze a WAV file: level (peak/RMS dBFS), spectrum balance (sub/bass/low_mid/high_mid/air), "
                    "tempo estimate, key estimate (top 3), loudness per 8 s. Heuristics. Needs numpy (see README).",
     "inputSchema": _schema({"path": _S, "start_sec": _N, "length_sec": _N}, ["path"])},
    {"name": "song_analyze",
     "description": "Musical overview of a saved .song for arranging: tempo/time signature, key estimated from all "
                    "notes, per-track note range and key, and audio analysis of the audio tracks' files "
                    "(first 120 s of up to 6 files). Read-only.",
     "inputSchema": _schema({"path": _SONG, "audio": {"type": "boolean"}}, ["path"])},
    {"name": "commands_search",
     "description": "Search Studio One commands (category/name/args) known from shipped files. "
                    "Use before daw_command.",
     "inputSchema": _schema({"query": _S, "category": _S, "limit": _I})},
    # ---- live (bridge) -----------------------------------------------------
    {"name": "daw_status",
     "description": "Bridge status: whether the Studio One bridge has loaded, and jobs still waiting.",
     "inputSchema": _schema({})},
    {"name": "daw_ping", "description": "Round-trip test through the bridge.",
     "inputSchema": _schema({"wait": _WAIT})},
    {"name": "daw_list_tracks", "description": "Tracks of the song open in Studio One right now.",
     "inputSchema": _schema({"wait": _WAIT})},
    {"name": "daw_command",
     "description": "Run a Studio One command by category and name, e.g. Transport/Start, Edit/Undo. "
                    "Optional args are passed like a macro's CommandArgument list.",
     "inputSchema": _schema({"category": _S, "name": _S,
                             "args": {"type": "object", "additionalProperties": {"type": ["string", "number"]}},
                             "wait": _WAIT}, ["category", "name"])},
    {"name": "daw_add_track",
     "description": "Add a track to the open song.",
     "inputSchema": _schema({"type": {"type": "string",
                                      "enum": ["audio_mono", "audio_stereo", "instrument", "folder", "automation"]},
                             "name": _S, "wait": _WAIT}, ["type"])},
    {"name": "daw_inspect_track",
     "description": "Read-only: which parameters/properties a track and its channel expose (discovery).",
     "inputSchema": _schema({"track": _TRACK, "wait": _WAIT}, ["track"])},
    {"name": "daw_inspect_mixer",
     "description": "Read-only: list mixer console channels and their parameters (discovery). "
                    "Optional track: also probe that track channel's destination channel.",
     "inputSchema": _schema({"track": _TRACK, "wait": _WAIT}, [])},
    {"name": "daw_probe",
     "description": "Read-only API discovery inside Studio One. root: track | trackChannel | mixerChannel | "
                    "environment. path: list of property names or {\"call\": name, \"args\": [...]}. "
                    "names: properties to report on the result.",
     "inputSchema": _schema({"root": {"type": "string", "enum": ["track", "trackChannel", "mixerChannel", "environment", "object", "functions"]},
                             "url": {"type": "string", "description": "object: Host.Objects URL; functions: name"},
                             "track": _TRACK, "list": _N, "index": _N,
                             "path": {"type": "array"}, "names": {"type": "array", "items": _S},
                             "wait": _WAIT}, ["root"])},
    {"name": "daw_list_events",
     "description": "Events (parts/audio clips) on a track of the open song, in order: index, name, start, "
                    "length, offset, speed. index is what daw_call's {event: {track, index}} refers to.",
     "inputSchema": _schema({"track": _TRACK, "wait": _WAIT}, ["track"])},
    {"name": "daw_read_notes",
     "description": "Notes of the instrument parts on a track of the open song (live, no save needed). Per part: "
                    "start/length (beats), notes with index, pitch, velocity 0..1, start/length relative to the part, "
                    "beat = song position. part_start limits to one part.",
     "inputSchema": _schema({"track": _TRACK, "part_start": _N, "wait": _WAIT}, ["track"])},
    {"name": "daw_edit_notes",
     "description": "Edit the part that starts at part_start on a track of the open song. edits: [{index (from "
                    "daw_read_notes), pitch?, velocity? 0..1, start? (beats from part start), length?, delete?}]. "
                    "add: [{pitch, start, length, velocity?}] new notes, start relative to the part. Indexes refer to "
                    "the order before this call.",
     "inputSchema": _schema({"track": _TRACK, "part_start": _N,
                             "edits": {"type": "array", "items": _schema(
                                 {"index": _I, "pitch": _I, "velocity": _N, "start": _N, "length": _N,
                                  "delete": {"type": "boolean"}}, ["index"])},
                             "add": {"type": "array", "items": _schema(
                                 {"pitch": _I, "start": _N, "length": _N, "velocity": _N}, ["pitch", "start", "length"])},
                             "wait": _WAIT}, ["track", "part_start"])},
    {"name": "daw_read_chords",
     "description": "Chords on the Chord Track of the open song (start, length in beats, name). Write chords with "
                    "song_write_chords.",
     "inputSchema": _schema({"wait": _WAIT})},
    {"name": "daw_transport_info",
     "description": "Tempo, time signature, playhead, loop range of the open song (transport panel).",
     "inputSchema": _schema({"wait": _WAIT})},
    {"name": "daw_set_tempo",
     "description": "Set the song tempo in BPM (changes the tempo segment at bar 1; for a song with one tempo "
                    "that is the whole song). Tempo changes later in the song cannot be added this way.",
     "inputSchema": _schema({"bpm": _N, "wait": _WAIT}, ["bpm"])},
    {"name": "daw_set_position",
     "description": "Move the playhead to a bar (1-based) and optional beat (1-based).",
     "inputSchema": _schema({"bar": _I, "beat": _I, "wait": _WAIT}, ["bar"])},
    {"name": "daw_bounce",
     "description": "Render a track's instrument parts (or the part starting at part_start) to audio and analyze it "
                    "(level, spectrum, key, tempo; same as audio_analyze) so the result can be checked. By default "
                    "the bounced track is removed and the original parts unmuted again; keep=true leaves the bounced "
                    "audio track (originals muted). The WAV stays in the song's Bounces folder.",
     "inputSchema": _schema({"track": _TRACK, "part_start": _N, "keep": {"type": "boolean"},
                             "analyze": {"type": "boolean"}, "wait": _WAIT}, ["track"])},
    {"name": "daw_repeat_event",
     "description": "Repeat an event (audio loop or instrument part) on a track of the open song: the event that "
                    "starts at start_beats is copied times times back to back after itself (Edit > Duplicate; "
                    "shared=true uses Duplicate Shared). Live counterpart of song_add_loop's repeat for loops "
                    "already in the song; it cannot bring in a new loop file. Changes the selection.",
     "inputSchema": _schema({"track": _TRACK, "start_beats": _N, "times": _I, "shared": {"type": "boolean"},
                             "wait": _WAIT}, ["track", "start_beats"])},
    {"name": "daw_call",
     "description": "API experiment: run steps that call Studio One script methods with objects resolved by "
                    "reference. Step: {as?, on: expr, call?: method, args?: [expr], get?: prop, set?: prop, "
                    "value?: expr, names?: [props to report]}. expr: {var: name} | {track: ref} | "
                    "{event: {track, index}} | {functions: 'AudioFunctions'} ('' = context.functions) | "
                    "{time: beats} | {context: true} | literal. Stops at the first error. CAN EDIT THE SONG.",
     "inputSchema": _schema({"steps": {"type": "array", "items": {"type": "object"}}, "wait": _WAIT}, ["steps"])},
    {"name": "daw_list_mixer",
     "description": "Mixer channels of the open song with their parameters (index, name, label, channelType). "
                    "Instrument tracks (MusicTrack) have no volume/pan; their sound goes through an AudioSynth channel.",
     "inputSchema": _schema({"wait": _WAIT})},
    {"name": "daw_set_channel_param",
     "description": "Set a mixer parameter. volume: linear gain, 1.0 = 0 dB. pan: 0..1, 0.5 = centre; observed 0.25 shows R50 in the mixer, so values below 0.5 pan RIGHT. "
                    "Also mute, solo, recordArmed, monitor (0/1). For an instrument track, volume/pan go to its "
                    "instrument output channel, found from the saved song; if the song is unsaved, pass "
                    "mixer_channel (index, name or label from daw_list_mixer) instead of track.",
     "inputSchema": _schema({"track": _TRACK, "mixer_channel": {"type": ["string", "integer"]},
                             "param": _S, "value": _N, "wait": _WAIT}, ["param", "value"])},
    {"name": "daw_add_instrument",
     "description": "Load an instrument plug-in (class_id from library_instruments) and connect it to a track. "
                    "Without track, a new instrument track (optional name) is created. EXPERIMENTAL.",
     "inputSchema": _schema({"class_id": _S, "track": _TRACK, "name": _S, "wait": _WAIT}, ["class_id"])},
    {"name": "daw_insert_effect",
     "description": "Insert an audio effect (class_id from library_effects) on a track's channel or a mixer channel. "
                    "For instrument tracks it goes on the instrument output channel (needs the song saved, or "
                    "mixer_channel). Returns the insert slot name for daw_plugin_param. EXPERIMENTAL.",
     "inputSchema": _schema({"class_id": _S, "track": _TRACK, "mixer_channel": {"type": ["string", "integer"]},
                             "wait": _WAIT}, ["class_id"])},
    {"name": "daw_plugin_param",
     "description": "Read or set a plug-in parameter. Instrument: instrument_slot (e.g. 'Inst04' from "
                    "daw_add_instrument). Effect: track or mixer_channel plus slot (from daw_insert_effect). "
                    "param: ID from library_plugin_params ('vstp123' for VST3), or a VST3 parameter title together with "
                    "plugin (e.g. plugin 'Vital', param 'Filter 1 Cutoff'). Set with display (text in the plug-in's own units, e.g. '6' "
                    "for 6 dB, '80 Hz', '25 %'; recommended) or normalized (0..1). Omit both to read.",
     "inputSchema": _schema({"instrument_slot": _S, "track": _TRACK, "mixer_channel": {"type": ["string", "integer"]},
                             "slot": _S, "param": _S, "plugin": _S,
                             "display": {"type": ["string", "number"]}, "normalized": _N,
                             "wait": _WAIT}, ["param"])},
    {"name": "daw_scan_plugin_params",
     "description": "List which parameters of a third-party plug-in Studio One exposes, by scanning names "
                    "prefix+N (default vstp0..vstp1000). Same slot arguments as daw_plugin_param. EXPERIMENTAL.",
     "inputSchema": _schema({"instrument_slot": _S, "track": _TRACK, "mixer_channel": {"type": ["string", "integer"]},
                             "slot": _S, "prefix": _S, "from": _I, "to": _I, "limit": _I, "wait": _WAIT})},
    {"name": "daw_list_instruments",
     "description": "Instrument slots in the open song (e.g. Inst01) and the plug-in in each, for daw_plugin_param.",
     "inputSchema": _schema({"wait": _WAIT})},
    {"name": "daw_insert_notes",
     "description": "Create a new instrument part on a track and fill it with notes. "
                    "start_beats/length_beats in quarter notes; note start is relative to the part. "
                    "EXPERIMENTAL until verified in Studio One.",
     "inputSchema": _schema({
         "track": _TRACK, "start_beats": _N, "length_beats": _N,
         "notes": {"type": "array", "items": _schema(
             {"pitch": _I, "start_beats": _N, "length_beats": _N, "velocity": _N},
             ["pitch", "start_beats", "length_beats"])},
         "wait": _WAIT}, ["track", "start_beats", "length_beats", "notes"])},
    {"name": "daw_transport",
     "description": "Start or stop playback immediately (virtual MIDI surface, no mailbox).",
     "inputSchema": _schema({"action": {"type": "string", "enum": ["play", "stop"]}}, ["action"])},
    {"name": "daw_dump_host",
     "description": "Ask the bridge to write host-api.json (Studio One scripting API discovery).",
     "inputSchema": _schema({"wait": _WAIT})},
]


class ToolError(Exception):
    pass


def _song_path(path):
    path = os.path.expanduser(path)
    if not path.endswith(".song") or not os.path.isfile(path):
        raise ToolError("not a .song file: %s" % path)
    return path


def _find_track(tracks, ref):
    for t in tracks:
        if (isinstance(ref, int) and t["index"] == ref) or t["name"] == ref:
            return t
    raise ToolError("track not found: %r" % (ref,))


def _instrument_channel_hint(track):
    """AudioSynth channel name ("Channel03") for an instrument track, from the newest saved song
    that has a track with this label. None if not found (unsaved song or not an instrument track)."""
    if not isinstance(track, str):
        return None
    songs = sorted(glob.glob(os.path.join(SONGS_ROOT, "**", "*.song"), recursive=True),
                   key=os.path.getmtime, reverse=True)
    songs = [p for p in songs if os.sep + "History" + os.sep not in p]
    for path in songs[:10]:
        try:
            with Song(path) as s:
                ch = s.instrument_output_channel(track)
        except Exception:
            continue
        if ch and ch.get("name"):
            return ch["name"]
    return None


# Directory that contains the s1bridge package (server/ in a checkout, site-packages when installed)
PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYSIS_PYTHON = config.analysis_python()


def _audio_analyze(path, start=0, length=None):
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


def _media_path(song_path, url):
    """Local file for a media pool url (file:///... or a name looked up in the song's Media folder)."""
    if not url:
        return None
    if url.startswith("file://"):
        p = urllib.parse.unquote(url[len("file://"):])
        if os.path.isfile(p):
            return p
    name = os.path.basename(urllib.parse.unquote(url))
    p = os.path.join(os.path.dirname(song_path), "Media", name)
    return p if os.path.isfile(p) else None


def _song_analyze(path, audio=True):
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
        srcs = sorted({_media_path(path, p.get("source")) for p in t["parts"] if p.get("source")} - {None})
        if srcs:
            info["audio_files"] = srcs
            files.extend(f for f in srcs if f.lower().endswith(".wav") and f not in files)
        out["tracks"].append(info)
    out["key_from_notes"] = key_from_notes(all_notes)
    if audio and files:
        out["audio"] = {}
        for f in files[:6]:
            try:
                out["audio"][os.path.basename(f)] = _audio_analyze(f, 0, 120)
            except ToolError as e:
                out["audio"][os.path.basename(f)] = {"error": str(e)}
    return out


def _vst3_param_name(plugin, title):
    """'vstp<id>' for a VST3 parameter title (exact, else the only title containing it)."""
    if vst3params.params(plugin) is None:
        return title  # not a dumped VST3 plug-in: pass through (native parameter IDs)
    exact = vst3params.resolve(plugin, title)
    if exact:
        return exact
    hits = vst3params.find(plugin, title, 5)
    if hits["total"] == 1:
        return hits["params"][0]["param"]
    raise ToolError("parameter %r of %s: %d matches %s; use library_plugin_params"
                    % (title, plugin, hits["total"], [p["title"] for p in hits["params"]]))


def _trigger():
    """Press the virtual 'Run Jobs' button. Returns an error string or None."""
    if not USE_MIDI:
        return "midi trigger disabled"
    try:
        midi.press(midi.CC_RUN_JOBS)
        return None
    except OSError as e:
        return str(e)


def _open_in_daw(path):
    """True if Studio One's active document is this song, None if the bridge did not answer."""
    if not os.path.isfile(os.path.join(Mailbox().root, "status.json")):
        return None
    try:
        r = _bridge("probe", {"root": "object", "url": "://studioapp/DocumentManager",
                              "path": ["activeDocument"], "names": ["title"]}, 4)
    except ToolError:
        return None
    if isinstance(r, dict) and r.get("status") == "queued":
        Mailbox().cancel(r["job_id"])  # no song open (Run Jobs needs one)
        return None
    title =(r.get("names") or {}).get("title") if isinstance(r, dict) else None
    return title == os.path.splitext(os.path.basename(path))[0] if title else None


def _bridge(op, args, wait):
    mb = Mailbox()
    job_id = mb.enqueue(op, args)
    trigger_error = _trigger()
    wait = 10 if wait is None else wait
    r = mb.wait(job_id, min(wait, 2))
    if r is None and wait > 2 and not trigger_error:
        # Studio One re-binds a newly created MIDI port only after a moment: press again.
        trigger_error = _trigger()
        r = mb.wait(job_id, wait - 2)
    if r is None:
        return {"status": "queued", "job_id": job_id, "midi_trigger_error": trigger_error,
                "note": "The bridge has not run this job yet. Is Studio One running with a song open? "
                        "If daw_status shows poller.running is not true, run 'AI Bridge - Run Jobs' "
                        "once in Studio One (Keyboard Shortcuts); after that jobs run automatically."}
    if not r.get("ok"):
        raise ToolError("bridge error: %s" % r.get("error"))
    return r.get("result")


def _wait_for_bridge(seconds):
    """True once the bridge answers a ping (a song is open again)."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            r = _bridge("ping", {}, 3)
        except ToolError:
            r = None
        if isinstance(r, dict) and r.get("pong"):
            return True
        if isinstance(r, dict) and r.get("status") == "queued":
            Mailbox().cancel(r["job_id"])
        time.sleep(1)
    return False


def _offline_edit(path, write):
    """Run write(path) on a saved song. If Studio One has it open: save, close, write, reopen.
    Save and Close are pressed through the AI Bridge surface (CC 116/115): File/Close run from a job
    inside the Run Jobs edit task crashed Studio One (2026-10-17). `open -a "Studio One 5" <song>`
    reopens the song in the running app."""
    was_open = _open_in_daw(path)
    if was_open:
        before = os.path.getmtime(path)
        midi.press(midi.CC_SAVE)
        for _ in range(40):
            time.sleep(0.25)
            if os.path.getmtime(path) != before:
                break
        midi.press(midi.CC_CLOSE)
        for _ in range(20):
            time.sleep(0.5)
            if not _open_in_daw(path):
                break
        else:
            raise ToolError("Studio One did not close the song (a dialog may be open); nothing was written")
    try:
        result = write(path)
    except songwrite.SongWriteError as e:
        result = None
        error = str(e)
    if was_open:
        if config.IS_MAC:
            subprocess.run(["open", "-a", config.APP, path], check=False)
        else:
            os.startfile(path)  # UNVERIFIED on Windows
        reopened = _wait_for_bridge(60)
    if result is None:
        raise ToolError(error + (" (the song was reopened unchanged)" if was_open else ""))
    if was_open:
        result["reopened"] = reopened
    return result


def _transport_at(bar, sets, wait):
    """Apply transport parameters with the playhead at bar, then put the playhead back."""
    before = _bridge("transport", {"set": [["primaryTime", "%04d.01.01.00" % bar]] + sets}, wait)["before"]
    r = _bridge("transport", {"set": [["primaryTime", before["primaryTime"]]]}, wait)
    return {"tempo": r["after"].get("tempo"),
            "bpm_at_start": round(60 / r["seconds_per_beat"], 3) if r.get("seconds_per_beat") else None}


def _bounce(track, part_start, keep, analyze, wait):
    # Observed 0.6.0: Event/Bounce To New Track on selected instrument parts adds an audio track named like
    # the source track, writes <song folder>/Bounces/<track>.wav and mutes the source parts.
    events = [e for e in _events(track, wait) if e.get("mediaType") == "Music"]
    if part_start is not None:
        events = [e for e in events if abs(e["start"] - part_start) < 1e-6]
    if not events:
        raise ToolError("no instrument parts to bounce on %s" % track)
    tracks_before = _bridge("list_tracks", {}, wait)["tracks"]
    r = _bridge("call", {"steps": [
        {"as": "ed", "on": {"context": True}, "get": "editor"},
        {"as": "sel", "on": {"var": "ed"}, "get": "selection"},
        {"on": {"var": "sel"}, "call": "unselectAll"},
        {"as": "sf", "on": {"var": "ed"}, "call": "createSelectFunctions", "args": [{"functions": ""}]},
        {"on": {"var": "sf"}, "call": "selectMultiple",
         "args": [[{"event": {"track": track, "index": e["index"]}} for e in events]]}]}, wait)
    if (r.get("results") or [{}])[-1].get("value") != 1:
        raise ToolError("could not select the parts: %s" % r)
    t0 = time.time() - 1
    _bridge("command", {"category": "Event", "name": "Bounce To New Track"}, wait or 60)
    wav = None
    for _ in range(60):
        hits = [p for p in glob.glob(os.path.join(SONGS_ROOT, "**", "Bounces", "*.wav"), recursive=True)
                if os.path.getmtime(p) >= t0]
        if hits:
            wav = max(hits, key=os.path.getmtime)
            size = os.path.getsize(wav)
            time.sleep(0.5)
            if os.path.getsize(wav) == size:
                break
        time.sleep(0.5)
    tracks_after = _bridge("list_tracks", {}, wait)["tracks"]
    new_index = len(tracks_after) - 1 if len(tracks_after) > len(tracks_before) else None
    if len(tracks_after) > len(tracks_before):
        # the new track is the one whose (index, name) pair is not in the old list after shifting
        old = [t["name"] for t in tracks_before]
        for i, t in enumerate(tracks_after):
            if i >= len(old) or t["name"] != old[i] or t["mediaType"] != tracks_before[i]["mediaType"]:
                new_index = i
                break
    out = {"track": track, "parts": [e["start"] for e in events], "wav": wav}
    if analyze and wav:
        out["analysis"] = _audio_analyze(wav)
    if not keep and new_index is not None:
        steps = [{"on": {"functions": ""}, "set": "executeImmediately", "value": True},
                 {"on": {"functions": ""}, "call": "removeTrack", "args": [{"track": new_index}]}]
        steps += [{"on": {"functions": ""}, "call": "muteEvent",
                   "args": [{"event": {"track": track, "index": e["index"]}}, False]} for e in events]
        _bridge("call", {"steps": steps}, wait)
        out["bounced_track_removed"] = True
    elif new_index is not None:
        out["bounced_track_index"] = new_index
    if not wav:
        out["warning"] = "no new WAV found in a Bounces folder"
    return out


def _events(track, wait):
    r = _bridge("list_events", {"track": track}, wait)
    if isinstance(r, dict) and r.get("status") == "queued":
        raise ToolError("the bridge did not answer; is Studio One running with a song open?")
    return r["events"]


def _repeat_event(track, start_beats, times, shared, wait):
    # Observed 0.6.0: editor.createSelectFunctions(functions).selectMultiple([event]) then Edit/Duplicate
    # puts a copy right after the event and selects the copy, so N presses give N back-to-back copies.
    if not 1 <= times <= 64:
        raise ToolError("times must be 1..64")
    before = _events(track, wait)
    hits = [e for e in before if abs(e["start"] - start_beats) < 1e-6]
    if not hits:
        raise ToolError("no event starts at beat %s on %s; events start at %s"
                        % (start_beats, track, sorted(e["start"] for e in before)))
    r = _bridge("call", {"steps": [
        {"as": "ed", "on": {"context": True}, "get": "editor"},
        {"as": "sel", "on": {"var": "ed"}, "get": "selection"},
        {"on": {"var": "sel"}, "call": "unselectAll"},
        {"as": "sf", "on": {"var": "ed"}, "call": "createSelectFunctions", "args": [{"functions": ""}]},
        {"on": {"var": "sf"}, "call": "selectMultiple",
         "args": [[{"event": {"track": track, "index": hits[0]["index"]}}]]}]}, wait)
    last = (r.get("results") or [{}])[-1]
    if last.get("value") != 1:
        raise ToolError("could not select the event: %s" % r)
    for _ in range(times):
        _bridge("command", {"category": "Edit", "name": "Duplicate Shared" if shared else "Duplicate"}, wait)
    old = {(e["start"], e["length"], e["offset"]) for e in before}
    added = sorted((e for e in _events(track, wait) if (e["start"], e["length"], e["offset"]) not in old),
                   key=lambda e: e["start"])
    out = {"track": track, "event": hits[0]["name"], "copies": [{"start": e["start"], "length": e["length"]}
                                                                for e in added]}
    if len(added) != times:
        out["warning"] = "expected %d new events, found %d" % (times, len(added))
    return out


def call(name, a):
    if name == "song_list":
        files = [p for p in glob.glob(os.path.join(SONGS_ROOT, "**", "*.song"), recursive=True)
                 if os.sep + "History" + os.sep not in p]
        files.sort(key=os.path.getmtime, reverse=True)
        return [{"path": p, "name": os.path.basename(p)[:-5]} for p in files[: a.get("limit", 20)]]

    if name == "library_instruments":
        return library.instruments()
    if name == "library_loops":
        res = library.search_loops(a.get("query", ""), a.get("kind"), a.get("bpm"), a.get("limit", 50))
        index, ids = loops.load_index(), loops.package_ids()
        for item in res["loops"]:
            if item["kind"] == "audioloop" and item["sound_set"] in ids:
                info = index.get(loops.loop_url(item, ids))
                if info:
                    item["loop_beats"] = info["beats"]
                    item["bpm"] = round(60 / info["tempo"], 2)
        return res

    if name == "song_add_loop":
        return _offline_edit(_song_path(a["path"]), lambda p: loops.add_loop(
            p, a["track"], a["loop"], a["start_beats"], a.get("length_beats")))
    if name == "song_set_time_signatures":
        return _offline_edit(_song_path(a["path"]), lambda p: songwrite.write_time_signatures(p, a["signatures"]))
    if name == "song_write_chords":
        return _offline_edit(_song_path(a["path"]), lambda p: chords.write_chords(p, a["chords"], a.get("replace", True)))

    if name == "library_effects":
        return library.effects()
    if name == "library_plugin_params":
        found = vst3params.find(a["plugin"], a.get("query", ""), a.get("limit", 60))
        if found is not None:
            found["plugin"] = a["plugin"]
            found["format"] = "VST3"
            return found
        return {"params": library.native_params(a["plugin"]),
                "presets": [p["folder"] + "/" + p["name"] for p in library.presets(a["plugin"])][:100]}

    if name == "song_summary":
        with Song(_song_path(a["path"])) as s:
            return s.summary()

    if name == "song_track_notes":
        with Song(_song_path(a["path"])) as s:
            t = _find_track(s.tracks(include_notes=True), a["track"])
            notes = sorted((n for p in t["parts"] for n in p.get("notes", [])), key=lambda n: n["start_beats"])
            return {"track": t["name"], "parts": [{k: v for k, v in p.items() if k != "notes"} for p in t["parts"]],
                    "notes": notes}

    if name == "song_write_cc":
        return _offline_edit(_song_path(a["path"]), lambda p: songwrite.write_cc(
            p, a["track"], a["cc"], a["points"], a.get("part"), a.get("replace", True), a.get("step", False)))

    if name == "audio_analyze":
        return _audio_analyze(a["path"], a.get("start_sec", 0), a.get("length_sec"))

    if name == "song_analyze":
        return _song_analyze(_song_path(a["path"]), a.get("audio", True))

    if name == "commands_search":
        q = (a.get("query") or "").lower()
        cat = a.get("category")
        catalog = _load_catalog()
        if "missing" in catalog:
            raise ToolError("command catalog not found at %s. Generate it on this machine: "
                            "python3 install/install.py --catalog --apply" % catalog["missing"])
        hits = [c for c in catalog["commands"]
                if (not cat or c["category"] == cat)
                and (q in c["name"].lower() or q in c["category"].lower())]
        return hits[: a.get("limit", 40)]

    if name == "daw_status":
        mb = Mailbox()
        return {"mailbox": mb.root, "status": mb.read_json("status.json"),
                "poller": mb.read_json("poller.json"), "pending_jobs": mb.pending()}

    wait = a.get("wait")
    if name == "daw_ping":
        return _bridge("ping", {}, wait)
    if name == "daw_list_tracks":
        return _bridge("list_tracks", {}, wait)
    if name == "daw_command":
        if (a["category"], a["name"]) in BLOCKED_COMMANDS:
            raise ToolError("command blocked for safety: %s/%s" % (a["category"], a["name"]))
        return _bridge("command", {"category": a["category"], "name": a["name"], "args": a.get("args")}, wait)
    if name == "daw_add_track":
        return _bridge("add_track", {"type": a["type"], "name": a.get("name", "")}, wait)
    if name == "daw_inspect_track":
        return _bridge("inspect_track", {"track": a["track"]}, wait)
    if name == "daw_inspect_mixer":
        return _bridge("inspect_mixer", {k: a[k] for k in ("track",) if k in a}, wait)
    if name == "daw_probe":
        return _bridge("probe", {k: a[k] for k in ("root", "track", "list", "index", "path", "names", "url") if k in a}, wait)
    if name == "daw_list_events":
        return _bridge("list_events", {"track": a["track"]}, wait)
    if name == "daw_read_notes":
        return _bridge("read_notes", {k: a[k] for k in ("track", "part_start") if k in a}, wait)
    if name == "daw_edit_notes":
        return _bridge("edit_notes", {k: a[k] for k in ("track", "part_start", "edits", "add") if k in a}, wait)
    if name == "daw_read_chords":
        return _bridge("read_chords", {}, wait)
    if name == "daw_transport_info":
        return _bridge("transport", {}, wait)["before"]
    if name == "daw_set_tempo":
        if not 10 <= a["bpm"] <= 400:
            raise ToolError("bpm must be 10..400")
        return _transport_at(1, [["tempo", a["bpm"]]], wait)
    if name == "daw_set_position":
        r = _bridge("transport", {"set": [["primaryTime", "%04d.%02d.01.00" % (a["bar"], a.get("beat", 1))]]}, wait)
        return {"position": r["after"]["primaryTime"]}
    if name == "daw_bounce":
        return _bounce(a["track"], a.get("part_start"), a.get("keep", False), a.get("analyze", True), wait)
    if name == "daw_repeat_event":
        return _repeat_event(a["track"], a["start_beats"], a.get("times", 1), a.get("shared", False), wait)
    if name == "daw_call":
        return _bridge("call", {"steps": a["steps"]}, wait)
    if name == "daw_list_mixer":
        return _bridge("list_mixer", {}, wait)
    if name == "daw_set_channel_param":
        args = {k: a[k] for k in ("track", "mixer_channel", "param", "value") if k in a}
        if "mixer_channel" not in args:
            if "track" not in args:
                raise ToolError("pass track or mixer_channel")
            hint = _instrument_channel_hint(args["track"])
            if hint:
                args["instrument_channel"] = hint
        return _bridge("set_channel_param", args, wait)
    if name == "daw_add_instrument":
        return _bridge("add_instrument", {k: a[k] for k in ("class_id", "track", "name") if k in a}, wait)
    if name == "daw_scan_plugin_params":
        args = {k: a[k] for k in ("instrument_slot", "track", "mixer_channel", "slot", "prefix", "from", "to", "limit")
                if k in a}
        if "track" in args and "mixer_channel" not in args:
            hint = _instrument_channel_hint(args["track"])
            if hint:
                args["instrument_channel"] = hint
        return _bridge("scan_params", args, wait or 30)
    if name in ("daw_insert_effect", "daw_plugin_param"):
        keys = ("class_id", "track", "mixer_channel") if name == "daw_insert_effect" else \
            ("instrument_slot", "track", "mixer_channel", "slot", "param", "display", "normalized")
        args = {k: a[k] for k in keys if k in a}
        if name == "daw_plugin_param" and a.get("plugin") and not args["param"].startswith("vstp"):
            args["param"] = _vst3_param_name(a["plugin"], args["param"])
        if "track" in args and "mixer_channel" not in args:
            hint = _instrument_channel_hint(args["track"])
            if hint:
                args["instrument_channel"] = hint
        return _bridge("insert_effect" if name == "daw_insert_effect" else "plugin_param", args, wait)
    if name == "daw_list_instruments":
        return _bridge("list_instruments", {}, wait)
    if name == "daw_insert_notes":
        return _bridge("insert_notes", {k: a[k] for k in ("track", "start_beats", "length_beats", "notes")}, wait)
    if name == "daw_transport":
        midi.press(midi.CC_PLAY if a["action"] == "play" else midi.CC_STOP)
        return {"sent": a["action"]}
    if name == "daw_dump_host":
        return _bridge("dump_host", {}, wait)
    raise ToolError("unknown tool: %s" % name)
