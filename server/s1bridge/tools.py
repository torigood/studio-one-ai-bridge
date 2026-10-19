"""MCP tool definitions and handlers.

Two groups:
  song_*  read saved .song files directly (read-only, no Studio One needed)
  daw_*   send a job to the in-DAW bridge through the mailbox and wait for its result
"""
import contextlib
import glob
import json
import math
import re
import os
import sys

from . import config
from . import midi
from .mailbox import Mailbox
from .song import Song
from . import library
from . import vst3params
from . import songwrite
from . import loops
from . import chords
from . import effects
from . import setup
from . import clients
from . import automation
from . import presets
from . import notegrid
from . import clashes
import time
from .keys import estimate_key, key_from_notes
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
            # Generated on the user's machine by setup (s1bridge/setup.py); not shipped.
            _catalog = {"commands": [], "editTasks": [], "missing": path}
    return _catalog


def _schema(props, required=()):
    s = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        s["required"] = list(required)
    return s


_S = {"type": "string"}
_N = {"type": "number"}
_I = {"type": "integer"}
# Shared argument conventions are explained once in the server instructions (__main__.INSTRUCTIONS)
# instead of in every schema: song, track, wait, beat/bar, replace, offline writes.
_SONG = {"type": "string"}
_TRACK = {"type": ["string", "integer"]}
_WAIT = {"type": "number"}
_QUALITY = {"type": "string", "enum": ["best", "fast"]}

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
    {"name": "library_presets",
     "description": "Preset files of an instrument by plug-in name (e.g. 'Mai Tai', 'Mojito'), filtered by words in "
                    "name or folder (e.g. 'warm pad'); for song_load_preset.",
     "inputSchema": _schema({"plugin": _S, "query": _S, "limit": _I}, ["plugin"])},
    {"name": "library_plugin_params",
     "description": "Parameters of a plug-in for daw_plugin_param. Native (Pro EQ, Room Reverb, Mojito...): IDs and "
                    "ranges from factory presets, plus preset names. VST3 (Vital, Surge XT...): 'vstp<id>' with titles, "
                    "filtered by query words (e.g. 'filter 1 cutoff'); needs python3 -m s1bridge.vst3params once.",
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
     "description": "Write MIDI CC points (1 modulation, 11 expression, 64 sustain...) into an instrument part: [{beat, "
                    "value 0..127}], joined by ramps unless step=true. The part is the one containing the first point "
                    "unless part (0-based) is given.",
     "inputSchema": _schema({
         "path": _SONG, "track": _TRACK, "cc": _I, "part": _I,
         "points": {"type": "array", "items": _schema({"beat": _N, "value": _N}, ["beat", "value"])},
         "replace": {"type": "boolean"}, "step": {"type": "boolean"}},
         ["path", "track", "cc", "points"])},
    {"name": "song_add_loop",
     "description": "Place a Sound Set .audioloop (path or name from library_loops; needs loop_beats there) on an "
                    "existing AUDIO track from start_beats, repeated to fill length_beats (default one loop; the last "
                    "copy is cut).",
     "inputSchema": _schema({"path": _SONG, "track": _TRACK, "loop": _S, "start_beats": _N, "length_beats": _N},
                            ["path", "track", "loop", "start_beats"])},
    {"name": "song_set_time_signatures",
     "description": "Replace the time signature map: [{bar (first must be 1), numerator, denominator 2|4|8|16}].",
     "inputSchema": _schema({"path": _SONG, "signatures": {"type": "array", "items": _schema(
         {"bar": _I, "numerator": _I, "denominator": _I}, ["bar", "numerator", "denominator"])}},
         ["path", "signatures"])},
    {"name": "song_write_chords",
     "description": "Write chords into the Chord Track (the song needs one): [{beat, length, chord}], chord like C, Am7, "
                    "F#m7b5, Gsus4, Bbmaj7, Dadd9, C/E; daw_read_chords spellings (F♯ø7, B♭maj7, C°7) work too.",
     "inputSchema": _schema({"path": _SONG, "replace": {"type": "boolean"},
                             "chords": {"type": "array", "items": _schema({"beat": _N, "length": _N, "chord": _S},
                                                                          ["beat", "length", "chord"])}},
                            ["path", "chords"])},
    {"name": "song_write_automation",
     "description": "Write mixer automation for a track: param volume (dB, '-inf' = silence, max +10), pan (-1..1) or "
                    "mute (0/1). points [{bar or beat, value}] joined by straight lines (a jump = two close points, e.g. "
                    "beat 16 and 16.01; a fade = two points). Sets the channel to automation Read (plays only while the "
                    "song runs).",
     "inputSchema": _schema({"path": _SONG, "track": _TRACK,
                             "param": {"type": "string", "enum": ["volume", "pan", "mute"]},
                             "points": {"type": "array", "items": {
                                 "type": "object", "properties": {"bar": _I, "beat": _N, "value": {"type": ["number", "string"]}},
                                 "required": ["value"], "additionalProperties": False}},
                             "replace": {"type": "boolean"}}, ["path", "track", "param", "points"])},
    {"name": "song_write_plugin_automation",
     "description": "Automate a plug-in parameter (filter sweep, reverb rising...): an insert effect on the track's "
                    "channel (slot FX01.. or plugin name; daw_list_effects) or, with instrument=true, the instrument "
                    "the track plays. param: ID from library_plugin_params ('lmffreq', 'vstp54'). points [{bar or "
                    "beat, value 0..1}] (the knob's travel, as daw_plugin_param normalized), straight lines between. "
                    "title names the lane. Sets automation Read.",
     "inputSchema": _schema({"path": _SONG, "track": _TRACK, "param": _S, "instrument": {"type": "boolean"},
                             "slot": _S, "plugin": _S, "title": _S,
                             "points": {"type": "array", "items": _schema({"bar": _I, "beat": _N, "value": _N},
                                                                          ["value"])},
                             "replace": {"type": "boolean"}}, ["path", "track", "param", "points"])},
    {"name": "song_set_tempo_map",
     "description": "Replace the tempo map with tempo changes: [{bar or beat, bpm, ramp?}] (the first applies from "
                    "the song start; ramp=true glides linearly to the next change, e.g. a ritardando).",
     "inputSchema": _schema({"path": _SONG, "changes": {"type": "array", "items": _schema(
         {"bar": _I, "beat": _N, "bpm": _N, "ramp": {"type": "boolean"}}, ["bpm"])}}, ["path", "changes"])},
    {"name": "song_add_audio",
     "description": "Place a WAV or AIFF file from disk (a sample, vocal, bounce) on an existing AUDIO track at "
                    "start_beats, at its own speed. It is copied into the song's Media folder unless copy=false.",
     "inputSchema": _schema({"path": _SONG, "track": _TRACK, "file": _S, "start_beats": _N,
                             "copy": {"type": "boolean"}}, ["path", "track", "file", "start_beats"])},
    {"name": "song_load_preset",
     "description": "Load a preset into the instrument a track plays (name or file path from library_presets): "
                    "Studio One instruments (Mai Tai, Mojito, Impact, SampleOne) and .vstpreset/.aupreset files. "
                    "Presets inside a plug-in's own browser (Vital, BBC SO) cannot be loaded this way.",
     "inputSchema": _schema({"path": _SONG, "track": _TRACK, "preset": _S}, ["path", "track", "preset"])},
    {"name": "song_add_send",
     "description": "Add a send from a track or mixer channel to an EXISTING FX channel or bus (destination = its label, "
                    "e.g. 'Room Reverb'), so tracks share one reverb. level_db (default -6), pan -1..1, prefader.",
     "inputSchema": _schema({"path": _SONG, "destination": _S, "track": _TRACK, "mixer_channel": _S,
                             "level_db": _N, "pan": _N, "prefader": {"type": "boolean"}}, ["path", "destination"])},
    {"name": "song_write_sections",
     "description": "Write arranger sections (Intro, Verse, Chorus...) into the Arranger Track: [{name, bar or beat, and "
                    "bars, beats or end_bar}], no overlaps. A section can then be moved or copied with all its parts.",
     "inputSchema": _schema({"path": _SONG, "replace": {"type": "boolean"},
                             "sections": {"type": "array", "items": {
                                 "type": "object", "properties": {"name": _S, "bar": _I, "beat": _N, "bars": _I,
                                                                  "beats": _N, "end_bar": _I},
                                 "required": ["name"], "additionalProperties": False}}},
                            ["path", "sections"])},
    {"name": "song_write_markers",
     "description": "Write named markers (Intro, Verse, Chorus...) into the Marker Track: [{name, bar or beat}]. replace "
                    "removes old named markers; Start/End stay. Read back with song_summary.",
     "inputSchema": _schema({"path": _SONG, "replace": {"type": "boolean"},
                             "markers": {"type": "array", "items": {
                                 "type": "object", "properties": {"name": _S, "bar": _I, "beat": _N},
                                 "required": ["name"], "additionalProperties": False}}},
                            ["path", "markers"])},
    {"name": "audio_analyze",
     "description": "Analyze a WAV file: level (peak/RMS dBFS), spectrum balance (sub/bass/low_mid/high_mid/air), "
                    "tempo estimate, key estimate (top 3), loudness per 8 s. Heuristics. Needs numpy (see README).",
     "inputSchema": _schema({"path": _S, "start_sec": _N, "length_sec": _N}, ["path"])},
    {"name": "audio_melody",
     "description": "Melody notes of a single voice or instrument in audio (neural pitch tracker): bar, pos (beat in "
                    "bar), beat, len, note, conf; cents = how far off pitch it was sung; 'unsure' = loud spans with "
                    "no clear pitch (check by ear); 'layered' = spans where two or more voices sound (doubles, "
                    "harmonies), 'also' = other pitches under a note, 'octave_from' = moved to the louder octave. "
                    "Give song + track (an audio track: positions follow the song) or "
                    "path (a WAV; with song + start_beat it is placed there, else positions are seconds). from_bar/"
                    "to_bar limit the range (faster). grid: quarter notes, default 0.25, 0 = unquantized. to_track: "
                    "write the notes as a new part on that instrument track (live). quality: best (default, "
                    "CREPE full) or fast (CREPE tiny: ~10x faster, slightly less accurate). Needs the analysis "
                    "pack.",
     "inputSchema": _schema({"song": _SONG, "track": _TRACK, "path": _S, "start_beat": _N, "from_bar": _I,
                             "to_bar": _I, "grid": _N, "quality": _QUALITY, "engine": _S, "to_track": _TRACK,
                             "wait": _WAIT})},
    {"name": "audio_chords",
     "description": "Chords of audio (an instrumental, a band or orchestra bounce), one decision per beat, merged into "
                    "chord changes: bar, pos, beat, len, chord (spelled for the estimated key; slash chords when "
                    "the bass is on another chord tone), conf. Basic Pitch note activations + chord templates + "
                    "Viterbi smoothing. Give song + track (an audio track) or song + path + start_beat. from_bar/"
                    "to_bar limit the range. to_chord_track=true writes the result into the Chord Track (replacing "
                    "chords in that range; the song is saved, closed and reopened if open; backed up first). "
                    "Needs the analysis pack.",
     "inputSchema": _schema({"song": _SONG, "track": _TRACK, "path": _S, "start_beat": _N, "from_bar": _I,
                             "to_bar": _I, "engine": _S, "to_chord_track": {"type": "boolean"}}, ["song"])},
    {"name": "song_check_clashes",
     "description": "Arrangement check of a saved song against the vocal and the Chord Track: vocal_clash (part a "
                    "minor 2nd/9th or major 7th from the vocal), part_clash (two parts a minor 2nd/9th apart), "
                    "off_chord (held note outside the chord; avoid notes), crowding (part within 3 semitones of "
                    "the vocal), lockstep (4+ parts with the same rhythm). vocal: an audio track (analysed like "
                    "audio_melody, layered voices included) or an instrument track holding the melody. tracks/"
                    "exclude: names to check or skip (default: all instrument tracks except unpitched percussion). "
                    "quality: as audio_melody. Reads the saved file: save first.",
     "inputSchema": _schema({"path": _SONG, "vocal": _TRACK, "from_bar": _I, "to_bar": _I,
                             "tracks": {"type": "array", "items": _S}, "exclude": {"type": "array", "items": _S},
                             "quality": _QUALITY, "limit": _I}, ["path"])},
    {"name": "song_analyze",
     "description": "Musical overview for arranging: tempo, time signature, key from all notes, per-track range and key, "
                    "audio analysis of audio tracks (first 120 s of up to 6 files).",
     "inputSchema": _schema({"path": _SONG, "audio": {"type": "boolean"}}, ["path"])},
    {"name": "commands_search",
     "description": "Search Studio One commands (category/name/args) known from shipped files. "
                    "Use before daw_command.",
     "inputSchema": _schema({"query": _S, "category": _S, "limit": _I})},
    # ---- install -----------------------------------------------------------
    {"name": "bridge_doctor",
     "description": "Check every piece of the Studio One side (app, extension version, surface, device, MIDI port, "
                    "mailbox, ping, catalog, numpy), each failure with a fix. Run it when daw_* tools fail.",
     "inputSchema": _schema({})},
    {"name": "bridge_setup",
     "description": "Install or update the Studio One side (extension, control surface, device entry, command catalog, "
                    "MIDI port; backed up first). Studio One must be quit: ask the user to save and quit it first, then "
                    "to start it and open a song, and call daw_ping. catalog=false skips the catalog.",
     "inputSchema": _schema({"catalog": {"type": "boolean"}})},
    {"name": "bridge_connect_client",
     "description": "Register this server with another AI app on this Mac (claude-code, codex, gemini, cursor, lmstudio, "
                    "vscode, claude-desktop only without this extension); the app must be restarted. Without client: list"
                    " the apps found. remove=true unregisters. The ChatGPT chat app needs bridge_share instead.",
     "inputSchema": _schema({"client": {"type": "string", "enum": sorted(clients.CLIENTS)},
                             "remove": {"type": "boolean"}})},
    {"name": "bridge_share",
     "description": "Share these tools with the ChatGPT chat app over a public HTTPS address, in the background: on (now "
                    "and at every login), off, status. The connector URL holds a secret that controls Studio One; it is "
                    "returned only with show_url=true, so ask the user first. ChatGPT: Settings > Apps & Connectors > "
                    "Developer mode > Create, URL = that URL, authentication none.",
     "inputSchema": _schema({"action": {"type": "string", "enum": ["on", "off", "status"]},
                             "show_url": {"type": "boolean"}}, ["action"])},
    # ---- live (bridge) -----------------------------------------------------
    {"name": "daw_status",
     "description": "Bridge status: whether the Studio One bridge has loaded, jobs still waiting, and whether the "
                    "installed bridge matches this server (problem says what to do if not).",
     "inputSchema": _schema({})},
    {"name": "daw_set_loop",
     "description": "Set the loop range and turn looping on or off. start_bar/end_bar 1-based, end exclusive (5 to 9 "
                    "loops bars 5-8); with time signature changes pass start_beat/end_beat. No range: only switch "
                    "enabled.",
     "inputSchema": _schema({"start_bar": _I, "end_bar": _I, "start_beat": _N, "end_beat": _N,
                             "enabled": {"type": "boolean"}, "wait": _WAIT})},
    {"name": "daw_add_send",
     "description": "Create a new FX channel with an effect (name or class_id from library_effects, e.g. Room Reverb) and"
                    " a send to it from a track or mixer channel; level_db (Studio One default -6). For more sends to an "
                    "FX channel that exists, use song_add_send.",
     "inputSchema": _schema({"effect": _S, "track": _TRACK, "mixer_channel": {"type": ["string", "integer"]},
                             "level_db": _N, "wait": _WAIT}, ["effect"])},
    {"name": "daw_add_bus",
     "description": "Group channels into a new bus (e.g. drums): routes the given tracks and/or mixer channels into it "
                    "and the bus to where the first one went (usually Main). name labels the bus.",
     "inputSchema": _schema({"tracks": {"type": "array", "items": _TRACK},
                             "mixer_channels": {"type": "array", "items": {"type": ["string", "integer"]}},
                             "name": _S, "wait": _WAIT})},
    {"name": "daw_set_output",
     "description": "Route a track or mixer channel's output to a bus (daw_add_bus), an FX channel or 'Main'. destination"
                    " = 'Main' or index/name/label from daw_list_mixer; omit to read the output.",
     "inputSchema": _schema({"track": _TRACK, "mixer_channel": {"type": ["string", "integer"]},
                             "destination": {"type": ["string", "integer"]}, "wait": _WAIT})},
    {"name": "daw_set_send",
     "description": "Read or set send number send (1-based, default 1) of a track or mixer channel: level_db and/or pan "
                    "(0..1, below 0.5 = RIGHT). Omit both to read the sends.",
     "inputSchema": _schema({"track": _TRACK, "mixer_channel": {"type": ["string", "integer"]}, "send": _I,
                             "level_db": _N, "pan": _N, "wait": _WAIT})},
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
     "description": "Events (parts, audio clips) on a track sorted by start: index, name, start, length, offset (audio: "
                    "speed, tune). index is what daw_delete_event and daw_call refer to.",
     "inputSchema": _schema({"track": _TRACK, "wait": _WAIT}, ["track"])},
    {"name": "daw_edit_event",
     "description": "Move (move_to), resize (length) or mute/unmute an event picked by start_beats or index "
                    "(daw_list_events). Notes move with their part.",
     "inputSchema": _schema({"track": _TRACK, "start_beats": _N, "index": _I, "move_to": _N, "length": _N,
                             "mute": {"type": "boolean"}, "wait": _WAIT}, ["track"])},
    {"name": "daw_delete_track",
     "description": "Delete a track of the open song (undoable with Edit/Undo). An instrument it used stays loaded.",
     "inputSchema": _schema({"track": _TRACK, "wait": _WAIT}, ["track"])},
    {"name": "daw_rename_track",
     "description": "Rename a track of the open song. For an instrument track whose instrument output channel is "
                    "labelled like the track, that mixer channel is renamed too. Not undoable with Edit/Undo.",
     "inputSchema": _schema({"track": _TRACK, "name": _S, "wait": _WAIT}, ["track", "name"])},
    {"name": "daw_duplicate_track",
     "description": "Duplicate a track: new track at the end, same (shared) instrument, copies of all parts/clips unless "
                    "events=false (~2 s per event). name renames the copy (default: Studio One appends ' 2').",
     "inputSchema": _schema({"track": _TRACK, "name": _S, "events": {"type": "boolean"}, "wait": _WAIT}, ["track"])},
    {"name": "daw_delete_event",
     "description": "Delete the event starting at start_beats on a track, or the one at index (daw_list_events); all=true"
                    " deletes every event starting there. Undoable.",
     "inputSchema": _schema({"track": _TRACK, "start_beats": _N, "index": _I, "all": {"type": "boolean"},
                             "wait": _WAIT}, ["track"])},
    {"name": "daw_read_notes",
     "description": "Notes of the instrument parts on a track (live): per part start/length, notes with index, pitch, "
                    "velocity 0..1, start/length relative to the part. part_start limits to one part.",
     "inputSchema": _schema({"track": _TRACK, "part_start": _N, "wait": _WAIT}, ["track"])},
    {"name": "daw_edit_notes",
     "description": "Edit the part starting at part_start: edits [{index (daw_read_notes), pitch?, velocity? 0..1, "
                    "start?, length?, delete?}], add [{pitch, start, length, velocity?}]; starts relative to the part. "
                    "Indexes refer to the order before this call.",
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
     "description": "Set the song tempo in BPM (the tempo at bar 1, the whole song if it has one tempo). For tempo "
                    "changes later in the song use song_set_tempo_map.",
     "inputSchema": _schema({"bpm": _N, "wait": _WAIT}, ["bpm"])},
    {"name": "daw_set_position",
     "description": "Move the playhead to a bar (1-based) and optional beat (1-based).",
     "inputSchema": _schema({"bar": _I, "beat": _I, "wait": _WAIT}, ["bar"])},
    {"name": "daw_bounce",
     "description": "Render a track's instrument parts (or the part at part_start) to audio and analyze it like "
                    "audio_analyze. The bounced track is removed and the originals unmuted unless keep=true. The WAV "
                    "stays in the song's Bounces folder.",
     "inputSchema": _schema({"track": _TRACK, "part_start": _N, "keep": {"type": "boolean"},
                             "analyze": {"type": "boolean"}, "wait": _WAIT}, ["track"])},
    {"name": "daw_repeat_event",
     "description": "Repeat the event (loop or part) starting at start_beats times times back to back (Edit > Duplicate; "
                    "shared=true: Duplicate Shared). Cannot bring in a new loop file (song_add_loop does).",
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
     "description": "Set a mixer parameter: volume (linear gain, 1.0 = 0 dB), pan (0..1, 0.5 centre, below 0.5 = RIGHT), "
                    "mute, solo, recordArmed, monitor (0/1), automationMode (0 Off, 1 Read, 2 Touch, 3 Latch, 4 Write). "
                    "If an instrument track's channel cannot be found (song never saved), pass mixer_channel from "
                    "daw_list_mixer.",
     "inputSchema": _schema({"track": _TRACK, "mixer_channel": {"type": ["string", "integer"]},
                             "param": _S, "value": _N, "wait": _WAIT}, ["param", "value"])},
    {"name": "daw_add_instrument",
     "description": "Load an instrument plug-in (class_id from library_instruments) and connect it to a track. "
                    "Without track, a new instrument track (optional name) is created.",
     "inputSchema": _schema({"class_id": _S, "track": _TRACK, "name": _S, "wait": _WAIT}, ["class_id"])},
    {"name": "daw_insert_effect",
     "description": "Insert an effect (class_id from library_effects) on a track or mixer channel. Returns the slot for "
                    "daw_plugin_param. If an instrument track's channel is not found, pass mixer_channel.",
     "inputSchema": _schema({"class_id": _S, "track": _TRACK, "mixer_channel": {"type": ["string", "integer"]},
                             "wait": _WAIT}, ["class_id"])},
    {"name": "daw_plugin_param",
     "description": "Read or set a plug-in parameter. Instrument: instrument_slot ('Inst04', from daw_list_instruments). "
                    "Effect: track or mixer_channel plus slot (daw_list_effects). param: ID from library_plugin_params "
                    "('vstp123' for VST3) or a VST3 title with plugin (plugin 'Vital', param 'Filter 1 Cutoff'). Set with"
                    " display (plug-in units: '6', '80 Hz', '25 %'; preferred) or normalized (0..1); omit both to read.",
     "inputSchema": _schema({"instrument_slot": _S, "track": _TRACK, "mixer_channel": {"type": ["string", "integer"]},
                             "slot": _S, "param": _S, "plugin": _S,
                             "display": {"type": ["string", "number"]}, "normalized": _N,
                             "wait": _WAIT}, ["param"])},
    {"name": "daw_scan_plugin_params",
     "description": "List which parameters of a third-party plug-in Studio One exposes, by scanning names "
                    "prefix+N (default vstp0..vstp1000). Same slot arguments as daw_plugin_param. EXPERIMENTAL.",
     "inputSchema": _schema({"instrument_slot": _S, "track": _TRACK, "mixer_channel": {"type": ["string", "integer"]},
                             "slot": _S, "prefix": _S, "from": _I, "to": _I, "limit": _I, "wait": _WAIT})},
    {"name": "daw_list_effects",
     "description": "Insert effects on a track's channel (instrument tracks: their instrument output channel) or a "
                    "mixer channel of the open song: slot, plug-in, bypass.",
     "inputSchema": _schema({"track": _TRACK, "mixer_channel": {"type": ["string", "integer"]}, "wait": _WAIT})},
    {"name": "daw_bypass_effect",
     "description": "Bypass (bypass=true) or re-enable an insert effect live (slot from daw_list_effects). The "
                    "effect stays in the chain; song_remove_effect takes it out.",
     "inputSchema": _schema({"track": _TRACK, "mixer_channel": {"type": ["string", "integer"]}, "slot": _S,
                             "bypass": {"type": "boolean"}, "wait": _WAIT}, ["slot"])},
    {"name": "song_remove_effect",
     "description": "Remove an insert effect from a track's or mixer channel by slot (FX02, from daw_list_effects) or "
                    "plug-in name (Studio One's script API cannot remove devices live).",
     "inputSchema": _schema({"path": _SONG, "track": _TRACK, "mixer_channel": _S, "slot": _S, "plugin": _S},
                            ["path"])},
    {"name": "daw_list_instruments",
     "description": "Instrument slots in the open song (e.g. Inst01) and the plug-in in each, for daw_plugin_param.",
     "inputSchema": _schema({"wait": _WAIT})},
    {"name": "daw_insert_notes",
     "description": "Create a new instrument part on a track and fill it with notes. "
                    "start_beats/length_beats in quarter notes; note start is relative to the part.",
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


# API discovery tools for development: callable, but listed only with S1BRIDGE_DEV_TOOLS=1 (saves tokens on
# every request; composing never needs them).
DEV_TOOLS = {"daw_inspect_track", "daw_inspect_mixer", "daw_probe", "daw_dump_host", "daw_call"}
# Not offered over a shared URL (ChatGPT): they change files on this Mac outside the song.
REMOTE_HIDDEN = {"bridge_setup", "bridge_connect_client"}


def listed_tools(remote=False, dev=None):
    """Tools for tools/list. remote: the HTTP server behind a public tunnel."""
    if dev is None:
        dev = os.environ.get("S1BRIDGE_DEV_TOOLS", "0") == "1"
    out = []
    for t in TOOLS:
        if (t["name"] in DEV_TOOLS and not dev) or (remote and t["name"] in REMOTE_HIDDEN):
            continue
        if remote and t["name"] == "bridge_share":  # turning it on from ChatGPT makes no sense
            t = dict(t, description="Stop sharing Studio One with this chat app (off; it cannot be turned on "
                                    "from here) or show whether it is on (status).",
                     inputSchema=_schema({"action": {"type": "string", "enum": ["off", "status"]}}, ["action"]))
        out.append(t)
    return out


class ToolError(Exception):
    pass


def _song_path(path):
    path = os.path.expanduser(path)
    if not path.endswith(".song"):
        raise ToolError("not a .song file: %s" % path)
    if not os.path.isfile(path):
        raise ToolError("song not found: %s (see song_list)" % path)
    return path


def _find_track(tracks, ref):
    for t in tracks:
        if (isinstance(ref, int) and t["index"] == ref) or t["name"] == ref:
            return t
    raise ToolError("track not found: %r" % (ref,))


def _saved_instrument_channel(path, track):
    """(AudioSynth channel name or None, whether the saved file has the track at all)."""
    try:
        with Song(path) as s:
            ch = s.instrument_output_channel(track)
            known = ch is not None or any(t["name"] == track for t in s.tracks())
    except Exception:
        return None, False
    return (ch.get("name") if ch else None), known


def _instrument_channel_hint(track, wait=None):
    """AudioSynth channel name ("Channel03") for an instrument track of the open song. The live API has no
    track -> instrument channel link (0.7.2: no such property or child on the track or its channel), so try
    in order: the saved song's instrumentOut connection, the one AudioSynth channel labelled like the track
    (Studio One names both after the instrument; daw_rename_track keeps them equal), and for a track the
    saved file does not have yet, File > Save and read again. None for audio tracks or if nothing matched."""
    res = _bridge("list_tracks", {}, wait)
    tracks = res.get("tracks", []) if isinstance(res, dict) else []
    info = next((t for t in tracks if t["index"] == track or t["name"] == track), None)
    if info is None or info.get("channelType") != "MusicTrack":
        return None
    name = info["name"]
    path = _active_song_path(wait)
    known = False
    if path:
        ch, known = _saved_instrument_channel(path, name)
        if ch:
            return ch
    mixer = _bridge("list_mixer", {}, wait)
    synths = [c for c in (mixer.get("channels", []) if isinstance(mixer, dict) else [])
              if c.get("channelType") == "AudioSynth" and c.get("label") == name]
    if len(synths) == 1:
        return synths[0]["name"]
    if path and not known and USE_MIDI and _save_open_song(path):
        return _saved_instrument_channel(path, name)[0]
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


def _run_analysis(module, args, timeout=900):
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
    t = _find_track(tracks, ref)
    out = []
    for p in t["parts"]:
        if not p.get("source") or p["muted"]:
            continue
        wav = _media_path(song, p["source"])
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


QUALITY_ENGINES = {"best": "crepe", "fast": "crepe-tiny"}


def _audio_melody(a, wait):
    song = _song_path(a["song"]) if a.get("song") else None
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
    engine = a.get("engine") or QUALITY_ENGINES.get(a.get("quality") or "best")
    if engine is None:
        raise ToolError("quality must be best or fast")
    found, unsure, layered = [], [], []
    for wav, at, file_at, length in sources:
        s0 = at if lo is None else max(at, lo)
        s1 = (at + length if length is not None else None)
        if hi is not None:
            s1 = hi if s1 is None else min(s1, hi)
        if s1 is not None and s1 <= s0:
            continue
        r = _run_analysis("melody", [wav, file_at + (s0 - at), "-" if s1 is None else s1 - s0, engine])
        for n in r["notes"]:
            found.append(dict(n, start=at + n["start"] - file_at, end=at + n["end"] - file_at))
        unsure.extend([at + x - file_at, at + y - file_at] for x, y in r["unsure"])
        layered.extend([at + x - file_at, at + y - file_at] for x, y in r.get("layered", []))
    if not timed:
        return {"engine": engine, "unit": "seconds", "notes": found, "unsure": unsure, "layered": layered}
    beat = lambda sec: notegrid.sec_to_beat(tempo, sec)
    notes = [{"beat": beat(n["start"]), "len": beat(n["end"]) - beat(n["start"]), "pitch": n["pitch"],
              "cents": n["cents"], "conf": n["confidence"], "also": n.get("also"),
              "octave_from": n.get("octave_from")} for n in found]
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
        return d
    out = {"engine": engine, "grid": grid, "count": len(notes), "notes": [brief(n) for n in notes],
           "unsure": bar_spans(unsure), "layered": bar_spans(layered)}
    if a.get("to_track") is not None and notes:
        first = notegrid.bar_start(sigs, notes[0]["bar"])
        end = notegrid.bar_start(sigs, notegrid.bar_pos(sigs, notes[-1]["beat"] + notes[-1]["len"] - 1e-6)[0] + 1)
        out["written"] = _bridge("insert_notes", {
            "track": a["to_track"], "start_beats": first, "length_beats": end - first,
            "notes": [{"pitch": n["pitch"], "start_beats": n["beat"] - first, "length_beats": n["len"],
                       "velocity": 0.8} for n in notes]}, wait)
    return out


_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_FLAT_KEYS = {"F major", "Bb major", "Eb major", "Ab major", "C# major", "D minor", "G minor", "C minor",
              "F minor", "Bb minor", "Eb minor"}


def _chord_label(c, names):
    return names[c["root"]] + c["quality"] + ("" if c["bass"] == c["root"] else "/" + names[c["bass"]])


def _audio_chords(a):
    song = _song_path(a["song"])
    with Song(song) as s:
        tempo, sigs, tracks = s.tempo_map(), s.time_signatures(), s.tracks()
    if a.get("track") is not None:
        sources = _audio_sources(song, tracks, tempo, a["track"])
    elif a.get("path") and a.get("start_beat") is not None:
        wav = os.path.expanduser(a["path"])
        if not os.path.isfile(wav):
            raise ToolError("file not found: %s" % wav)
        sources = [(wav, notegrid.beat_to_sec(tempo, a["start_beat"]), 0.0, None)]
    else:
        raise ToolError("give track, or path + start_beat")
    lo = notegrid.bar_start(sigs, a["from_bar"]) if a.get("from_bar") else None
    hi = notegrid.bar_start(sigs, a["to_bar"] + 1) if a.get("to_bar") else None
    beats, chroma = [], [0.0] * 12
    for wav, at, file_at, length in sources:
        b0 = max(math.ceil(notegrid.sec_to_beat(tempo, at) - 1e-6), lo if lo is not None else -1e9)
        end_sec = at + length if length is not None else at + _wav_seconds(wav) - file_at
        b1 = math.floor(notegrid.sec_to_beat(tempo, end_sec) + 1e-6)
        if hi is not None:
            b1 = min(b1, hi)
        if b1 <= b0:
            continue
        grid = [b0 + i for i in range(int(b1 - b0) + 1)]
        secs = [notegrid.beat_to_sec(tempo, b) for b in grid]
        start = file_at + secs[0] - at
        strong = []
        for i, b in enumerate(grid[:-1]):
            bar, pos = notegrid.bar_pos(sigs, b)
            num = notegrid._bar_length(notegrid._sigs(sigs), notegrid.bar_start(sigs, bar))[0]
            if abs(pos - 1) < 1e-6 or (num % 2 == 0 and abs(pos - 1 - num / 2) < 1e-6):
                strong.append(i)
        r = _run_analysis("chordrec", [wav, round(start, 4), round(secs[-1] - secs[0], 4),
                                       ",".join("%.4f" % (t - secs[0]) for t in secs), ",".join(map(str, strong)),
                                       a.get("engine") or "basic-pitch"])
        beats.extend((b, c) for b, c in zip(grid, r["beats"]))
        chroma = [x + y for x, y in zip(chroma, r["chroma"])]
    key = estimate_key(chroma)[:1]
    names = _FLAT if key and key[0]["key"] in _FLAT_KEYS else _SHARP
    out_chords = []
    for b, c in beats:
        label = _chord_label(c, names) if c else None
        if out_chords and out_chords[-1]["chord"] == label and abs(out_chords[-1]["beat"] + out_chords[-1]["len"] - b) < 1e-6:
            out_chords[-1]["len"] += 1
            out_chords[-1]["_scores"].append(c["score"] if c else 0)
            continue
        out_chords.append({"beat": b, "len": 1, "chord": label, "_scores": [c["score"] if c else 0]})
    for c in out_chords:
        bar, pos = notegrid.bar_pos(sigs, c["beat"])
        c.update(bar=bar, pos=round(pos, 2), conf=round(sum(c["_scores"]) / len(c["_scores"]), 2))
        del c["_scores"]
    chords_out = [{k: c[k] for k in ("bar", "pos", "beat", "len", "chord", "conf")} for c in out_chords if c["chord"]]
    out = {"key": key[0]["key"] if key else None, "count": len(chords_out), "chords": chords_out}
    if a.get("to_chord_track") and chords_out:
        items = [{"beat": c["beat"], "length": c["len"], "chord": c["chord"]} for c in chords_out]
        out["written"] = _offline_edit(song, lambda p: chords.write_chords(p, items, True))
    return out


def _wav_seconds(path):
    """Duration of a WAV from its fmt and data chunk headers (any sample format)."""
    import struct
    with open(path, "rb") as f:
        if f.read(12)[8:12] != b"WAVE":
            raise ToolError("not a WAV file: %s" % path)
        rate = frame = None
        while True:
            head = f.read(8)
            if len(head) < 8:
                break
            cid, size = head[:4], struct.unpack("<I", head[4:])[0]
            if cid == b"fmt ":
                _tag, ch, rate, _br, frame = struct.unpack("<HHIIH", f.read(14))
                f.seek(size - 14 + (size & 1), 1)
            elif cid == b"data":
                if rate and frame:
                    return size / float(frame) / rate
                break
            else:
                f.seek(size + (size & 1), 1)
    raise ToolError("could not read the WAV header: %s" % path)


# unpitched or effect parts whose notes are sample triggers, not pitches (skipped unless listed in tracks)
UNPITCHED = re.compile(r"drum|perc|kit|snare|kick|hat|cymbal|sleigh|bell tree|shaker|tambourine|clap|gliss|fx|sfx",
                       re.I)


def _check_clashes(song, a):
    with Song(song) as s:
        tracks, sigs, chord_list = s.tracks(include_notes=True), s.time_signatures(), s.chords()
    lo = notegrid.bar_start(sigs, a["from_bar"]) if a.get("from_bar") else None
    hi = notegrid.bar_start(sigs, a["to_bar"] + 1) if a.get("to_bar") else None
    vocal, vocal_name = [], a.get("vocal")
    if vocal_name is not None:
        t = _find_track(tracks, vocal_name)
        if t["mediaType"] == "Audio":
            r = _audio_melody({"song": song, "track": vocal_name, "from_bar": a.get("from_bar"),
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


def _version_check(status):
    """Compare the bridge this server ships, the one installed and the one Studio One last loaded."""
    paths = setup.Paths()
    want = setup.source_version()
    installed = (setup.installed_version(os.path.join(paths.ext_dir, "scripts", "aibridge.package"))
                 or setup.installed_version(paths.app_script))
    loaded = status.get("version") if isinstance(status, dict) else None
    out = {"bridge_expected": want, "bridge_installed": installed}
    if installed is None:
        out["problem"] = ("The bridge is not installed in Studio One. Ask the user to save their song and quit "
                          "Studio One, then call bridge_setup.")
    elif installed != want:
        out["problem"] = ("Installed bridge %s is older than this server (%s). Ask the user to save their song and "
                          "quit Studio One, then call bridge_setup." % (installed, want))
    elif loaded and loaded != installed and isinstance(status, dict) and status.get("loaded"):
        out["problem"] = ("Studio One is still running bridge %s; %s is installed. Ask the user to restart "
                          "Studio One." % (loaded, installed))
    return out


def _trigger():
    """Press the virtual 'Run Jobs' button. Returns an error string or None."""
    if not USE_MIDI:
        return "midi trigger disabled"
    try:
        midi.press(midi.CC_RUN_JOBS)
        return None
    except OSError as e:
        return str(e)


def _active_song_path(wait=None):
    """File path of the song open in Studio One (DocumentManager.activeDocument.path.url), None if unknown
    (no song open, not saved yet, or the bridge did not answer)."""
    if not os.path.isfile(os.path.join(Mailbox().root, "status.json")):
        return None
    try:
        r = _bridge("probe", {"root": "object", "url": "://studioapp/DocumentManager",
                              "path": ["activeDocument", "path"], "names": ["url"]}, min(wait or 4, 4))
    except ToolError:
        return None
    if isinstance(r, dict) and r.get("status") == "not_run":
        Mailbox().cancel(r["job_id"])  # no song open (Run Jobs needs one)
        return None
    url = (r.get("names") or {}).get("url") if isinstance(r, dict) else None
    if not url or not url.startswith("file://"):
        return None
    p = urllib.parse.unquote(url[len("file://"):])
    return p if os.path.isfile(p) else None


def _open_in_daw(path):
    """True if Studio One's active document is this song, None if the bridge did not answer."""
    active = _active_song_path()
    if active is None:
        return None
    return os.path.realpath(active) == os.path.realpath(path)


def _bridge(op, args, wait):
    mb = Mailbox()
    mb.cleanup()
    job_id = mb.enqueue(op, args)
    trigger_error = _trigger()
    wait = 10 if wait is None else wait
    r = mb.wait(job_id, min(wait, 2))
    if r is None and wait > 2 and not trigger_error:
        # Studio One re-binds a newly created MIDI port only after a moment: press again.
        trigger_error = _trigger()
        r = mb.wait(job_id, wait - 2)
    if r is None:
        # Take the job back: left in the inbox it would run whenever Studio One next runs jobs, long after
        # the caller gave up (a surprise edit). If the bridge already took it, it may still finish.
        cancelled = mb.cancel(job_id)
        return {"status": "not_run", "job_id": job_id, "cancelled": cancelled, "midi_trigger_error": trigger_error,
                "note": ("The bridge did not answer in %s s, so the job was withdrawn and nothing changed. "
                         % wait if cancelled else
                         "The bridge took the job but did not answer in %s s; it may still finish. " % wait) +
                        "Is Studio One running with a song open (and no dialog open)? Run bridge_doctor to find the "
                        "cause; it says how to fix each missing piece."}
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
        if isinstance(r, dict) and r.get("status") == "not_run":
            Mailbox().cancel(r["job_id"])
        time.sleep(1)
    return False


def _save_open_song(path):
    """Press File/Save (surface CC 116) and wait up to 10 s for the file to change. True if it did."""
    before = os.path.getmtime(path)
    midi.press(midi.CC_SAVE)
    for _ in range(40):
        time.sleep(0.25)
        if os.path.getmtime(path) != before:
            return True
    return False


def _offline_edit(path, write):
    """Run write(path) on a saved song. If Studio One has it open: save, close, write, reopen.
    Save and Close are pressed through the AI Bridge surface (CC 116/115): File/Close run from a job
    inside the Run Jobs edit task crashed Studio One (2026-10-17). `open -a "Studio One 5" <song>`
    reopens the song in the running app."""
    was_open = _open_in_daw(path)
    if was_open:
        _save_open_song(path)
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
            subprocess.run(["/usr/bin/open", "-a", config.APP, path], check=False)
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
    if isinstance(r, dict) and r.get("status") == "not_run":
        raise ToolError("the bridge did not answer; is Studio One running with a song open?")
    return r["events"]


def _tracks(wait):
    r = _bridge("list_tracks", {}, wait)
    if isinstance(r, dict) and r.get("status") == "not_run":
        raise ToolError("the bridge did not answer; is Studio One running with a song open?")
    return r["tracks"]


def _find_track(tracks, ref):
    for t in tracks:
        if (isinstance(ref, int) and t["index"] == ref) or t["name"] == ref:
            return t
    raise ToolError("track not found: %r" % (ref,))


# Mixer channel by daw_list_mixer index, for daw_call steps (MixerConsole channel list 1, see bridge.js).
def _mixer_channel_steps(index, var):
    return [{"as": var + "_f", "on": {"context": True}, "get": "functions"},
            {"as": var + "_r", "on": {"var": var + "_f"}, "get": "root"},
            {"as": var + "_e", "on": {"var": var + "_r"}, "get": "environment"},
            {"as": var + "_m", "on": {"var": var + "_e"}, "call": "find", "args": ["MixerConsole"]},
            {"as": var + "_l", "on": {"var": var + "_m"}, "call": "getChannelList", "args": [1]},
            {"as": var, "on": {"var": var + "_l"}, "call": "getChannel", "args": [index]}]


def _call(steps, wait, what):
    """Run steps through the bridge's call op; raise on the first failing step."""
    r = _bridge("call", {"steps": steps}, wait)
    if not isinstance(r, dict) or "results" not in r:
        raise ToolError("%s: bridge did not answer (%s)" % (what, r))
    errors = [x["error"] for x in r["results"] if "error" in x]
    if errors:
        raise ToolError("%s failed: %s" % (what, errors[0]))
    return r["results"]


def _transport_steps():
    return [{"as": "t_f", "on": {"context": True}, "get": "functions"},
            {"as": "t_r", "on": {"var": "t_f"}, "get": "root"},
            {"as": "t_e", "on": {"var": "t_r"}, "get": "environment"},
            {"as": "tp", "on": {"var": "t_e"}, "call": "find", "args": ["TransportPanel"]},
            {"as": "tc", "on": {"var": "t_r"}, "get": "timeContext"}]


def _set_loop(a, wait):
    # Observed 2026-10-18: TransportPanel loopStart/loopEnd take setValue(seconds), not fromString, and
    # Transport/Set Loop Start ignores the playhead set by the bridge; timeContext.ppqToSeconds converts.
    start, end = a.get("start_beat"), a.get("end_beat")
    if a.get("start_bar") is not None or a.get("end_bar") is not None:
        info = _bridge("transport", {}, wait)["before"]
        per_bar = int(info.get("timeSignatureNum", 4)) * 4.0 / int(info.get("timeSignatureDenom", 4))
        if a.get("start_bar") is not None:
            start = (int(a["start_bar"]) - 1) * per_bar
        if a.get("end_bar") is not None:
            end = (int(a["end_bar"]) - 1) * per_bar
    if (start is None) != (end is None):
        raise ToolError("pass both the start and the end of the loop")
    if start is not None and (start < 0 or end <= start):
        raise ToolError("the loop must end after it starts (got %s..%s quarter notes)" % (start, end))
    steps = _transport_steps()
    if start is not None:
        steps += [{"as": "s0", "on": {"var": "tc"}, "call": "ppqToSeconds", "args": [start]},
                  {"as": "s1", "on": {"var": "tc"}, "call": "ppqToSeconds", "args": [end]},
                  # end first: a start after the old end may be refused
                  {"as": "pe", "on": {"var": "tp"}, "call": "findParameter", "args": ["loopEnd"]},
                  {"on": {"var": "pe"}, "call": "setValue", "args": [{"var": "s1"}, True]},
                  {"as": "ps", "on": {"var": "tp"}, "call": "findParameter", "args": ["loopStart"]},
                  {"on": {"var": "ps"}, "call": "setValue", "args": [{"var": "s0"}, True]},
                  {"on": {"var": "pe"}, "call": "setValue", "args": [{"var": "s1"}, True]}]
    if "enabled" in a or start is not None:
        steps += [{"as": "pl", "on": {"var": "tp"}, "call": "findParameter", "args": ["loop"]},
                  {"on": {"var": "pl"}, "call": "setValue", "args": [1 if a.get("enabled", True) else 0, True]}]
    _call(steps, wait, "set loop")
    info = _bridge("transport", {}, wait)["before"]
    return {"loop": info.get("loop") == "1", "loop_start": info.get("loopStart"), "loop_end": info.get("loopEnd")}


def _effect_class(effect):
    """class_id for an effect given by class_id or (part of) its name in library_effects."""
    if effect.startswith("{"):
        return effect, effect
    effs = library.effects()
    exact = [e for e in effs if e["name"].lower() == effect.lower()]
    hits = exact or [e for e in effs if effect.lower() in e["name"].lower()]
    if len(hits) != 1:
        raise ToolError("effect %r matches %s; use library_effects and pass its class_id"
                        % (effect, ", ".join(e["name"] for e in hits[:8]) or "nothing"))
    return hits[0]["class_id"], hits[0]["name"]


def _audio_channel_index(a, wait):
    """daw_list_mixer index of the audio channel for a track (instrument tracks: their instrument channel) or
    for mixer_channel (index, name or label of a channel that carries audio)."""
    chans = _bridge("list_mixer", {}, wait).get("channels", [])
    audio = [c for c in chans if c.get("channelType") != "MusicTrack"]
    ref = a.get("mixer_channel")
    if ref is not None:
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            hits = [c for c in chans if c["index"] == int(ref)]
        else:
            hits = [c for c in audio if c.get("name") == ref] or [c for c in audio if c.get("label") == ref]
    elif a.get("track") is not None:
        t = _find_track(_tracks(wait), a["track"])
        if t.get("channelType") == "MusicTrack":
            hint = _instrument_channel_hint(t["index"], wait)
            hits = [c for c in audio if c.get("channelType") == "AudioSynth" and c.get("name") == hint] if hint else []
            if not hits:
                raise ToolError("instrument channel of %r not found; pass mixer_channel from daw_list_mixer" % t["name"])
        else:
            hits = [c for c in audio if c.get("channelType") == "AudioTrack" and c.get("label") == t["name"]]
    else:
        raise ToolError("pass track or mixer_channel")
    if len(hits) != 1:
        raise ToolError("mixer channel %r matches %d channels; pass the index from daw_list_mixer"
                        % (ref if ref is not None else a.get("track"), len(hits)))
    return hits[0]


_SEND_SLOTS = ["Send%02d" % i for i in range(1, 17)]


def _sends_steps(index):
    steps = _mixer_channel_steps(index, "ch") + [{"as": "sends", "on": {"var": "ch"}, "call": "find", "args": ["Sends"]}]
    for s in _SEND_SLOTS:
        steps.append({"as": s, "on": {"var": "sends"}, "call": "find", "args": [s], "names": ["name", "title"]})
    return steps


def _read_sends(index, wait):
    """{slot name: title} of a channel's sends (slots Send01.., found by name; observed 2026-10-18)."""
    res = _call(_sends_steps(index), wait, "read sends")
    return {s: (r.get("props") or {}).get("title") for s, r in zip(_SEND_SLOTS, res[-len(_SEND_SLOTS):])
            if r.get("value") is not None}


def _send_param_steps(index, slot, level_db=None, pan=None):
    steps = _mixer_channel_steps(index, "ch") + [
        {"as": "sends", "on": {"var": "ch"}, "call": "find", "args": ["Sends"]},
        {"as": "snd", "on": {"var": "sends"}, "call": "find", "args": [slot]},
        {"as": "lv", "on": {"var": "snd"}, "call": "findParameter", "args": ["level"]},
        {"as": "pn", "on": {"var": "snd"}, "call": "findParameter", "args": ["pan"]}]
    if level_db is not None:  # level is linear gain 0..3.16 (+10 dB), shown in dB
        steps.append({"on": {"var": "lv"}, "call": "setValue", "args": [10 ** (float(level_db) / 20.0), True]})
    if pan is not None:
        steps.append({"on": {"var": "pn"}, "call": "setValue", "args": [float(pan), True]})
    steps += [{"as": "lv2", "on": {"var": "snd"}, "call": "findParameter", "args": ["level"], "names": ["string"]},
              {"as": "pn2", "on": {"var": "snd"}, "call": "findParameter", "args": ["pan"], "names": ["string"]}]
    return steps


def _send_state(results):
    return {"level_db": (results[-2].get("props") or {}).get("string"), "pan": (results[-1].get("props") or {}).get("string")}


def _add_send(a, wait):
    # Observed 2026-10-18: DeviceEditFunctions.insertDevice(channel.find("Sends"), effectClassID) creates an
    # FX channel (channelType AudioEffect, labelled after the effect) and a send Send0N to it.
    # connectChannel(sends, fxChannel[.input]) and insertDevice(sends, fxChannel) do nothing, so a send to an
    # existing FX channel is not possible from a script.
    class_id, effect = _effect_class(a["effect"])
    ch = _audio_channel_index(a, wait)
    before = _read_sends(ch["index"], wait)
    steps = _mixer_channel_steps(ch["index"], "ch") + [
        {"as": "sends", "on": {"var": "ch"}, "call": "find", "args": ["Sends"]},
        {"as": "fx", "on": {"functions": "DeviceEditFunctions"}},
        {"as": "slot", "on": {"var": "fx"}, "call": "insertDevice", "args": [{"var": "sends"}, class_id],
         "names": ["name"]}]
    res = _call(steps, wait, "add send")
    if res[-1].get("value") is None:
        raise ToolError("Studio One did not add the send (effect %s)" % effect)
    after = _read_sends(ch["index"], wait)
    new = [s for s in after if s not in before]
    out = {"channel": ch["label"], "mixer_index": ch["index"], "effect": effect, "send": new[0] if new else None}
    fx = [c for c in _bridge("list_mixer", {}, wait).get("channels", []) if c.get("channelType") == "AudioEffect"]
    if fx:
        out["fx_channel"] = {"label": fx[-1]["label"], "mixer_index": fx[-1]["index"]}
    if new and a.get("level_db") is not None:
        out.update(_send_state(_call(_send_param_steps(ch["index"], new[0], a["level_db"]), wait, "set send level")))
    return out


def _mixer_env_steps():
    return [{"as": "b_f", "on": {"context": True}, "get": "functions"},
            {"as": "b_r", "on": {"var": "b_f"}, "get": "root"},
            {"as": "b_e", "on": {"var": "b_r"}, "get": "environment"},
            {"as": "b_con", "on": {"var": "b_e"}, "call": "find", "args": ["MixerConsole"]},
            {"as": "b_cl", "on": {"var": "b_con"}, "call": "getChannelList", "args": [1]},
            {"as": "b_fx", "on": {"functions": "DeviceEditFunctions"}}]


def _channel_output(index, wait):
    steps = _mixer_channel_steps(index, "o") + [
        {"as": "od", "on": {"var": "o"}, "call": "getDestinationChannel", "names": ["label", "channelType", "name"]}]
    return (_call(steps, wait, "read output")[-1].get("props") or {})


def _add_bus(a, wait):
    # Create Tracks.js (AddBusForSelectedChannelsTask): AudioMixer.setup.addChannel("AudioGroup"),
    # DeviceEditFunctions.connectChannel(channel, bus.input), bus.connectTo(old destination). Verified 2026-10-18.
    refs = [{"track": t} for t in a.get("tracks") or []] + [{"mixer_channel": m} for m in a.get("mixer_channels") or []]
    if not refs:
        raise ToolError("pass tracks and/or mixer_channels")
    chans = [_audio_channel_index(r, wait) for r in refs]
    if len({c["index"] for c in chans}) != len(chans):
        raise ToolError("the same channel was given twice")
    steps = _mixer_env_steps() + [
        {"as": "b_am", "on": {"var": "b_e"}, "call": "find", "args": ["AudioMixer"]},
        {"as": "b_su", "on": {"var": "b_am"}, "get": "setup"}]
    for i, c in enumerate(chans):
        steps.append({"as": "c%d" % i, "on": {"var": "b_cl"}, "call": "getChannel", "args": [c["index"]]})
    steps += [{"as": "dest", "on": {"var": "c0"}, "call": "getDestinationChannel"},
              {"as": "bus", "on": {"var": "b_su"}, "call": "addChannel", "args": ["AudioGroup"], "names": ["label", "name"]},
              {"as": "bin", "on": {"var": "bus"}, "get": "input"}]
    for i in range(len(chans)):
        steps.append({"on": {"var": "b_fx"}, "call": "connectChannel", "args": [{"var": "c%d" % i}, {"var": "bin"}]})
    steps.append({"on": {"var": "bus"}, "call": "connectTo", "args": [{"var": "dest"}]})
    if a.get("name"):
        steps.append({"on": {"var": "bus"}, "set": "label", "value": a["name"]})
    _call(steps, wait, "add bus")
    mixer = _bridge("list_mixer", {}, wait).get("channels", [])
    buses = [c for c in mixer if c.get("channelType") == "AudioGroup"]
    bus = buses[-1] if buses else None
    routed = [{"channel": c["label"], "output": _channel_output(c["index"], wait).get("label")} for c in chans]
    out = {"bus": {"label": bus["label"], "mixer_index": bus["index"]} if bus else None, "channels": routed}
    if bus:
        out["bus"]["output"] = _channel_output(bus["index"], wait).get("label")
        wrong = [r["channel"] for r in routed if r["output"] != bus["label"]]
        if wrong:
            out["warning"] = "not routed to the bus: %s" % ", ".join(wrong)
    return out


def _set_output(a, wait):
    ch = _audio_channel_index(a, wait)
    if a.get("destination") is None:
        return {"channel": ch["label"], "output": _channel_output(ch["index"], wait)}
    # The main output is not in channel list 1 (daw_list_mixer) but alone in list 3 (observed 2026-10-18).
    main = str(a["destination"]).lower() in ("main", "main out", "main output", "master")
    if main:
        dest = {"label": "Main"}
        get_dest = [{"as": "l3", "on": {"var": "b_con"}, "call": "getChannelList", "args": [3]},
                    {"as": "d", "on": {"var": "l3"}, "call": "getChannel", "args": [0], "names": ["label"]}]
    else:
        dest = _audio_channel_index({"mixer_channel": a["destination"]}, wait)
        if dest["index"] == ch["index"]:
            raise ToolError("a channel cannot be routed to itself")
        get_dest = [{"as": "d", "on": {"var": "b_cl"}, "call": "getChannel", "args": [dest["index"]]}]
    steps = _mixer_env_steps() + [
        {"as": "c", "on": {"var": "b_cl"}, "call": "getChannel", "args": [ch["index"]]}] + get_dest + [
        {"as": "din", "on": {"var": "d"}, "get": "input"},
        {"as": "ok", "on": {"var": "b_fx"}, "call": "connectChannel", "args": [{"var": "c"}, {"var": "din"}]}]
    _call(steps, wait, "set output")
    now = _channel_output(ch["index"], wait)
    if not main and now.get("label") != dest["label"] or main and now.get("channelType") != "AudioOutput":
        raise ToolError("Studio One kept the output %r" % now.get("label"))
    return {"channel": ch["label"], "output": now.get("label")}


def _set_send(a, wait):
    ch = _audio_channel_index(a, wait)
    sends = _read_sends(ch["index"], wait)
    if a.get("level_db") is None and a.get("pan") is None and a.get("send") is None:
        out = []
        for slot, title in sends.items():
            out.append(dict({"send": int(slot[4:]), "slot": slot, "title": title},
                            **_send_state(_call(_send_param_steps(ch["index"], slot), wait, "read send"))))
        return {"channel": ch["label"], "sends": out}
    slot = "Send%02d" % int(a.get("send") or 1)
    if slot not in sends:
        raise ToolError("%s has no send %s (sends: %s)" % (ch["label"], slot, ", ".join(sends) or "none"))
    res = _call(_send_param_steps(ch["index"], slot, a.get("level_db"), a.get("pan")), wait, "set send")
    return dict({"channel": ch["label"], "send": int(slot[4:]), "title": sends[slot]}, **_send_state(res))


def _rename_track(track, name, wait):
    # Observed 0.7.2: track.name = .. is ignored, track.channel.label = .. renames the track (arrange and
    # mixer); an AudioSynth channel's label is writable the same way, and renaming it also renames the
    # instrument tracks it plays (Studio One names them after the instrument). Not an undo step.
    if not name:
        raise ToolError("name is empty")
    before = _tracks(wait)
    t = _find_track(before, track)
    steps = []
    synth = None
    if t.get("channelType") == "MusicTrack":
        chans = _bridge("list_mixer", {}, wait).get("channels", [])
        hits = [c for c in chans if c.get("channelType") == "AudioSynth" and c.get("label") == t["name"]]
        if len(hits) == 1:
            synth = hits[0]
            steps += _mixer_channel_steps(synth["index"], "s") + [{"on": {"var": "s"}, "set": "label", "value": name}]
    steps += [{"as": "c", "on": {"track": t["index"]}, "get": "channel"},
              {"on": {"var": "c"}, "set": "label", "value": name}]
    r = _bridge("call", {"steps": steps}, wait)
    errors = [x["error"] for x in r.get("results", []) if "error" in x]
    if errors:
        raise ToolError("rename failed: %s" % errors[0])
    after = _tracks(wait)
    # other tracks on the same instrument were renamed with it: put their names back
    restore = [b for b, a in zip(before, after) if b["index"] != t["index"] and a["name"] != b["name"]]
    if restore:
        steps = []
        for i, b in enumerate(restore):
            steps += [{"as": "c%d" % i, "on": {"track": b["index"]}, "get": "channel"},
                      {"on": {"var": "c%d" % i}, "set": "label", "value": b["name"]}]
        _bridge("call", {"steps": steps}, wait)
    if after[t["index"]]["name"] != name:
        raise ToolError("Studio One kept the name %r" % after[t["index"]]["name"])
    out = {"index": t["index"], "old": t["name"], "name": name}
    if synth:
        out["instrument_channel"] = synth["name"]
    if restore:
        out["kept_names"] = [b["name"] for b in restore]
    return out


def _event_key(e):
    return (round(e["start"], 6), round(e["length"], 6), round(e.get("offset", 0), 6), e.get("name"))


def _duplicate_track(track, name, copy_events, wait):
    # Observed 0.7.2: functions.duplicateTrack (track) adds an empty copy (same instrument, name + " 2") at the
    # end of the track list; Track/Duplicate acts on the focused track, not the script selection, so it is not used.
    # functions.transferEvent (event, track, startBeats) moves an event to another track. Each event is
    # copied by moving it to the new track, Edit/Duplicate there (the copy lands at start + length, where
    # nothing else on the new track starts because events go in ascending order), and moving the copy back.
    before = _tracks(wait)
    src = _find_track(before, track)
    events = sorted(_events(src["index"], wait), key=lambda e: e["start"]) if copy_events else []
    r = _bridge("call", {"steps": [
        {"on": {"functions": ""}, "set": "executeImmediately", "value": True},
        {"on": {"functions": ""}, "call": "duplicateTrack", "args": [{"track": src["index"]}], "names": ["name"]},
        {"on": {"functions": ""}, "set": "executeImmediately", "value": False}]}, wait)
    res = r.get("results", [])
    if len(res) < 2 or "error" in res[1] or res[1].get("value") != "object":
        raise ToolError("duplicateTrack failed: %s" % r)
    after = _tracks(wait)
    if len(after) != len(before) + 1:
        raise ToolError("expected one new track, found %d" % (len(after) - len(before)))
    old = [(t["name"], t["mediaType"]) for t in before]
    new = next(t for i, t in enumerate(after) if i >= len(old) or (t["name"], t["mediaType"]) != old[i])
    src_i = src["index"] + (1 if new["index"] <= src["index"] else 0)
    warnings = []
    for e in events:
        key = _event_key(e)
        cur = next((x for x in _events(src_i, wait) if _event_key(x) == key), None)
        if cur is None:
            warnings.append("event at beat %s not found any more" % e["start"])
            continue
        _bridge("call", {"steps": [
            {"as": "e", "on": {"event": {"track": src_i, "index": cur["index"]}}},
            {"on": {"functions": ""}, "set": "executeImmediately", "value": True},
            {"on": {"functions": ""}, "call": "transferEvent", "args": [{"var": "e"}, {"track": new["index"]}, e["start"]]},
            {"on": {"functions": ""}, "set": "executeImmediately", "value": False},
            {"as": "ed", "on": {"context": True}, "get": "editor"},
            {"as": "sel", "on": {"var": "ed"}, "get": "selection"},
            {"on": {"var": "sel"}, "call": "unselectAll"},
            {"as": "sf", "on": {"var": "ed"}, "call": "createSelectFunctions", "args": [{"functions": ""}]},
            {"on": {"var": "sf"}, "call": "selectMultiple", "args": [[{"var": "e"}]]}]}, wait)
        _bridge("command", {"category": "Edit", "name": "Duplicate"}, wait)
        copy_key = (round(e["start"] + e["length"], 6),) + key[1:]
        copy = next((x for x in _events(new["index"], wait) if _event_key(x) == copy_key), None)
        if copy is None:
            warnings.append("no copy made of the event at beat %s; it was moved to the new track" % e["start"])
            continue
        _bridge("call", {"steps": [
            {"on": {"functions": ""}, "set": "executeImmediately", "value": True},
            {"on": {"functions": ""}, "call": "transferEvent",
             "args": [{"event": {"track": new["index"], "index": copy["index"]}}, {"track": src_i}, e["start"]]},
            {"on": {"functions": ""}, "set": "executeImmediately", "value": False}]}, wait)
    out = {"source": src["name"], "index": new["index"], "name": new["name"]}
    if copy_events:
        got = sorted(_event_key(x) for x in _events(new["index"], wait))
        if got != sorted(_event_key(e) for e in events):
            warnings.append("new track has %d events, source had %d" % (len(got), len(events)))
        out["events"] = len(got)
    if name:
        out["name"] = _rename_track(new["index"], name, wait)["name"]
    if warnings:
        out["warnings"] = warnings
    return out


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
    if name == "song_write_automation":
        return _offline_edit(_song_path(a["path"]), lambda p: automation.write_automation(
            p, a["track"], a["param"], a["points"], a.get("replace", True)))
    if name == "song_write_plugin_automation":
        return _offline_edit(_song_path(a["path"]), lambda p: automation.write_plugin_automation(
            p, a["track"], a["param"], a["points"], a.get("instrument", False), a.get("slot"), a.get("plugin"),
            a.get("title"), a.get("replace", True)))
    if name == "song_set_tempo_map":
        return _offline_edit(_song_path(a["path"]), lambda p: songwrite.write_tempo_map(p, a["changes"]))
    if name == "song_add_audio":
        return _offline_edit(_song_path(a["path"]), lambda p: loops.add_audio_file(
            p, a["track"], a["file"], a["start_beats"], a.get("copy", True)))
    if name == "song_load_preset":
        return _offline_edit(_song_path(a["path"]), lambda p: presets.load_preset(p, a["track"], a["preset"]))
    if name == "library_presets":
        return presets.find(a["plugin"], a.get("query", ""), a.get("limit", 60))
    if name == "song_add_send":
        if a.get("track") is None and a.get("mixer_channel") is None:
            raise ToolError("pass track or mixer_channel")
        return _offline_edit(_song_path(a["path"]), lambda p: effects.add_send(
            p, a["destination"], track=a.get("track"), mixer_channel=a.get("mixer_channel"),
            level_db=a.get("level_db", -6.0), pan=a.get("pan", 0.0), prefader=a.get("prefader", False)))
    if name == "song_write_sections":
        return _offline_edit(_song_path(a["path"]),
                             lambda p: songwrite.write_sections(p, a["sections"], a.get("replace", True)))
    if name == "song_write_markers":
        return _offline_edit(_song_path(a["path"]),
                             lambda p: songwrite.write_markers(p, a["markers"], a.get("replace", True)))
    if name == "song_set_time_signatures":
        return _offline_edit(_song_path(a["path"]), lambda p: songwrite.write_time_signatures(p, a["signatures"]))
    if name == "song_remove_effect":
        if ("track" in a) == ("mixer_channel" in a):
            raise ToolError("pass track or mixer_channel")
        return _offline_edit(_song_path(a["path"]), lambda p: effects.remove_effect(
            p, a.get("track"), a.get("mixer_channel"), a.get("slot"), a.get("plugin")))
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

    if name == "audio_melody":
        return _audio_melody(a, wait)

    if name == "audio_chords":
        return _audio_chords(a)

    if name == "song_check_clashes":
        return _check_clashes(_song_path(a["path"]), a)

    if name == "song_analyze":
        return _song_analyze(_song_path(a["path"]), a.get("audio", True))

    if name == "commands_search":
        q = (a.get("query") or "").lower()
        cat = a.get("category")
        catalog = _load_catalog()
        if "missing" in catalog:
            raise ToolError("command catalog not found at %s. Generate it on this machine: "
                            "python3 server/run.py setup --apply" % catalog["missing"])
        hits = [c for c in catalog["commands"]
                if (not cat or c["category"] == cat)
                and (q in c["name"].lower() or q in c["category"].lower())]
        return hits[: a.get("limit", 40)]

    if name == "bridge_doctor":
        checks = setup.doctor()
        return {"ok": all(c["ok"] is not False for c in checks), "checks": checks}
    if name == "bridge_setup":
        if setup.studio_one_running():
            raise ToolError("Studio One is running. Ask the user to save their song and quit Studio One, "
                            "then call bridge_setup again.")
        paths = setup.Paths()
        log = []
        # stdout is the MCP channel; the catalog extractor prints progress
        with contextlib.redirect_stdout(sys.stderr):
            code = setup.run(setup.plan_install(paths, catalog=a.get("catalog", True)), paths, True, log=log.append)
        if code:
            raise ToolError("\n".join(log))
        return {"installed": setup.source_version(), "log": log,
                "next": "Ask the user to start Studio One and open a song, then call daw_ping."}

    if name == "bridge_share":
        from . import share
        if a["action"] == "on":
            s = share.load_settings()
            if s["provider"] == "ngrok" and not share.keychain_get("ngrok"):
                raise ToolError("ngrok is selected but its authtoken is not in the Keychain; the user runs "
                                "'python3 server/run.py share --set-ngrok-token' in Terminal")
            try:
                share.enable()
            except RuntimeError as e:
                raise ToolError(str(e))
            st = share.wait_for_url()
        elif a["action"] == "off":
            share.disable()
            st = share.status()
        else:
            st = share.status()
        url = st.pop("url")
        st["address"] = url.split("/")[2] if url else None
        if url and a.get("show_url"):
            st["url"] = url
        elif url:
            st["url"] = "(hidden: contains the secret; call again with show_url=true)"
        if st["provider"] == "cloudflare" and url:
            st["note"] = "Cloudflare quick tunnel: the address changes when sharing restarts; update the ChatGPT connector then."
        return st
    if name == "bridge_connect_client":
        if not a.get("client"):
            found = clients.detect()
            return {"clients": [{"client": n, "title": c["title"], "found": found[n]} for n, c in clients.CLIENTS.items()],
                    "server_command": clients.server_command()}
        log = []
        with contextlib.redirect_stdout(sys.stderr):
            code = clients.connect(a["client"], a.get("remove", False), True, log=log.append)
        if code:
            raise ToolError("\n".join(log))
        return {"log": log}

    if name == "daw_status":
        mb = Mailbox()
        status = mb.read_json("status.json")
        # poller.json reports the bridge's idle poller, which is off on purpose (addIdleTask hangs Studio One
        # 5.5); AIs read "running: false" as "live editing is off", so it is not reported. Jobs run through
        # the AI Bridge MIDI button; status.loaded and daw_ping tell whether that works.
        out = {"mailbox": mb.root, "status": status, "pending_jobs": mb.pending(),
               "job_trigger": "virtual MIDI button 'AI Bridge' (normal; live editing works when daw_ping answers)"}
        out.update(_version_check(status))
        return out

    wait = a.get("wait")
    if name == "daw_ping":
        r = _bridge("ping", {}, wait)
        want = setup.source_version()
        if isinstance(r, dict) and r.get("version") and r["version"] != want:
            r["warning"] = ("Studio One runs bridge %s, this server expects %s. Ask the user to quit Studio One, "
                            "call bridge_setup, then start Studio One again." % (r["version"], want))
        return r
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
    if name == "daw_edit_event":
        if "start_beats" not in a and "index" not in a:
            raise ToolError("pass start_beats or index")
        args = {k: a[k] for k in ("track", "start_beats", "index", "move_to", "length", "mute") if k in a}
        return _bridge("edit_event", args, wait)
    if name == "daw_delete_track":
        return _bridge("delete_track", {"track": a["track"]}, wait)
    if name == "daw_rename_track":
        return _rename_track(a["track"], a["name"], wait)
    if name == "daw_duplicate_track":
        return _duplicate_track(a["track"], a.get("name"), a.get("events", True), wait)
    if name == "daw_delete_event":
        if "start_beats" not in a and "index" not in a:
            raise ToolError("pass start_beats or index")
        return _bridge("delete_event", {k: a[k] for k in ("track", "start_beats", "index", "all") if k in a}, wait)
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
    if name == "daw_set_loop":
        return _set_loop(a, wait)
    if name == "daw_add_send":
        return _add_send(a, wait)
    if name == "daw_set_send":
        return _set_send(a, wait)
    if name == "daw_add_bus":
        return _add_bus(a, wait)
    if name == "daw_set_output":
        return _set_output(a, wait)
    if name == "daw_set_channel_param":
        args = {k: a[k] for k in ("track", "mixer_channel", "param", "value") if k in a}
        if "mixer_channel" not in args:
            if "track" not in args:
                raise ToolError("pass track or mixer_channel")
            hint = _instrument_channel_hint(args["track"], wait)
            if hint:
                args["instrument_channel"] = hint
        return _bridge("set_channel_param", args, wait)
    if name == "daw_add_instrument":
        return _bridge("add_instrument", {k: a[k] for k in ("class_id", "track", "name") if k in a}, wait)
    if name == "daw_scan_plugin_params":
        args = {k: a[k] for k in ("instrument_slot", "track", "mixer_channel", "slot", "prefix", "from", "to", "limit")
                if k in a}
        if "track" in args and "mixer_channel" not in args:
            hint = _instrument_channel_hint(args["track"], wait)
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
            hint = _instrument_channel_hint(args["track"], wait)
            if hint:
                args["instrument_channel"] = hint
        return _bridge("insert_effect" if name == "daw_insert_effect" else "plugin_param", args, wait)
    if name == "daw_list_instruments":
        return _bridge("list_instruments", {}, wait)
    if name in ("daw_list_effects", "daw_bypass_effect"):
        args = {k: a[k] for k in ("track", "mixer_channel") if k in a}
        if not args:
            raise ToolError("pass track or mixer_channel")
        if "track" in args:
            hint = _instrument_channel_hint(args["track"], wait)
            if hint:
                args["instrument_channel"] = hint
        if name == "daw_list_effects":
            return _bridge("list_effects", args, wait)
        args.update(slot=a["slot"], param="bypass", value=1 if a.get("bypass", True) else 0)
        r = _bridge("plugin_param", args, wait)
        return {"slot": a["slot"], "bypass": r.get("raw")} if isinstance(r, dict) and "raw" in r else r
    if name == "daw_insert_notes":
        return _bridge("insert_notes", {k: a[k] for k in ("track", "start_beats", "length_beats", "notes")}, wait)
    if name == "daw_transport":
        midi.press(midi.CC_PLAY if a["action"] == "play" else midi.CC_STOP)
        return {"sent": a["action"]}
    if name == "daw_dump_host":
        return _bridge("dump_host", {}, wait)
    raise ToolError("unknown tool: %s" % name)
