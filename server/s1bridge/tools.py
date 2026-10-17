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
                    "kind (audioloop, musicloop, audio, midi) and bpm (+-3). Names only; "
                    "placing a Sound Set loop into a song is not supported yet.",
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


def _bridge(op, args, wait):
    mb = Mailbox()
    job_id = mb.enqueue(op, args)
    trigger_error = _trigger()
    r = mb.wait(job_id, 10 if wait is None else wait)
    if r is None:
        return {"status": "queued", "job_id": job_id, "midi_trigger_error": trigger_error,
                "note": "The bridge has not run this job yet. Is Studio One running with a song open? "
                        "If daw_status shows poller.running is not true, run 'AI Bridge - Run Jobs' "
                        "once in Studio One (Keyboard Shortcuts); after that jobs run automatically."}
    if not r.get("ok"):
        raise ToolError("bridge error: %s" % r.get("error"))
    return r.get("result")


def call(name, a):
    if name == "song_list":
        files = [p for p in glob.glob(os.path.join(SONGS_ROOT, "**", "*.song"), recursive=True)
                 if os.sep + "History" + os.sep not in p]
        files.sort(key=os.path.getmtime, reverse=True)
        return [{"path": p, "name": os.path.basename(p)[:-5]} for p in files[: a.get("limit", 20)]]

    if name == "library_instruments":
        return library.instruments()
    if name == "library_loops":
        return library.search_loops(a.get("query", ""), a.get("kind"), a.get("bpm"), a.get("limit", 50))

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
