"""MCP tool list: name, description and input schema of every tool, and which ones a client sees.

Shared argument conventions (song, track, wait, beat/bar, replace, offline writes) are explained once in
the server instructions (__main__.INSTRUCTIONS) instead of in every schema.
"""
import os

from . import clients


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
    {"name": "library_sounds",
     "description": "Find sounds across every installed instrument by words (e.g. 'warm pad', 'sub bass', 'pluck'): "
                    "preset name, folder and style (Vital presets carry one), with synonyms; best first. Load one "
                    "with daw_load_sound. plugin limits it to one instrument.",
     "inputSchema": _schema({"query": _S, "plugin": _S, "limit": _I}, ["query"])},
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
                    "Studio One instruments (Mai Tai, Mojito, Impact, SampleOne), Vital .vital presets and "
                    ".vstpreset/.aupreset files. Presets inside other plug-ins' own browsers (Surge XT, BBC SO) "
                    "cannot be loaded this way.",
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
    {"name": "audio_mix_check",
     "description": "Mix check of WAV files [{path, name?}]: loudness (LUFS integrated, peak, PLR), stereo "
                    "(correlation, side vs mid dB), octave band levels (dB from the loudest band) and tilt; with "
                    "several stems, masking = octave bands where two stems are both strong and within 6 dB. "
                    "Needs the analysis pack.",
     "inputSchema": _schema({"files": {"type": "array", "items": _schema({"path": _S, "name": _S}, ["path"])}},
                            ["files"])},
    {"name": "daw_mix_check",
     "description": "audio_mix_check on instrument tracks of the open song: each is bounced (the bounced track is "
                    "removed again, the WAV stays in Bounces) and checked together with any files (e.g. vocal WAVs). "
                    "Bounces include the fader but not the channel's insert effects.",
     "inputSchema": _schema({"tracks": {"type": "array", "items": _TRACK},
                             "files": {"type": "array", "items": _schema({"path": _S, "name": _S}, ["path"])},
                             "wait": _WAIT})},
    {"name": "daw_mix_fix",
     "description": "Check the mix of instrument tracks (daw_mix_check), fix it and check again: if a stem peaks above "
                    "target_peak_db (default -6) all are turned down equally (balance kept); for the strongest overlaps (max_cuts, default 3) the "
                    "less important stem gets a Pro EQ cut (cut_db, default 3) at that octave. priority = track names, "
                    "most important first (else bass/kick keep the low end, else the louder stem keeps the band). "
                    "Takes a snapshot first; apply=false returns the plan only. In after, gain is measured and EQ cuts "
                    "are estimated (bounces leave insert effects out).",
     "inputSchema": _schema({"tracks": {"type": "array", "items": _TRACK}, "priority": {"type": "array", "items": _S},
                             "max_cuts": _I, "cut_db": _N, "target_peak_db": _N, "apply": {"type": "boolean"},
                             "wait": _WAIT}, ["tracks"])},
    {"name": "audio_melody",
     "description": "Melody notes of a single voice or instrument in audio (neural pitch tracker): bar, pos (beat in "
                    "bar), beat, len, note, conf; cents = how far off pitch it was sung; 'unsure' = loud spans with "
                    "no clear pitch (check by ear); 'layered' = spans where two or more voices sound (doubles, "
                    "harmonies), 'also' = other pitches under a note, 'octave_from' = moved to the louder octave. "
                    "Give song + track (an audio track: positions follow the song) or "
                    "path (a WAV; with song + start_beat it is placed there, else positions are seconds). from_bar/"
                    "to_bar limit the range (faster). grid: quarter notes, default 0.25, 0 = unquantized. to_track: "
                    "write the notes as a new part on that instrument track (live). quality: best (default: pYIN "
                    "checked against the CREPE neural tracker; 'check' on a note = the second engine heard "
                    "another pitch, wrong far more often, so verify those) or fast (pYIN alone). Needs the "
                    "analysis pack.",
     "inputSchema": _schema({"song": _SONG, "track": _TRACK, "path": _S, "start_beat": _N, "from_bar": _I,
                             "to_bar": _I, "grid": _N, "quality": _QUALITY, "engine": _S, "to_track": _TRACK,
                             "wait": _WAIT})},
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
    {"name": "daw_write_pattern",
     "description": "Write a part generated from chords onto an instrument track. style: bass (pattern root/"
                    "root_fifth/octave/walk), pad (voice-led held chords), arp (direction up/down/updown/random), "
                    "stabs (short chords). Chords from chords [{beat, length, chord}], or progression 'Am F C G' "
                    "('%' holds the last chord) from start_bar with bars_per_chord and repeat, or else the Chord "
                    "Track (start_bar/end_bar limit it). rate = note spacing in quarter notes (0.5 = 8ths); octave "
                    "= register (bass 2, others 4); velocity 0..1; gate = note length share; humanize 0..1; "
                    "seed makes random choices repeatable. replace deletes parts inside the range first.",
     "inputSchema": _schema({
         "track": _TRACK, "style": {"type": "string", "enum": ["bass", "pad", "arp", "stabs"]},
         "chords": {"type": "array", "items": _schema({"beat": _N, "length": _N, "chord": _S},
                                                      ["beat", "length", "chord"])},
         "progression": _S, "start_bar": _I, "end_bar": _I, "bars_per_chord": _N, "repeat": _I,
         "pattern": {"type": "string", "enum": ["root", "root_fifth", "octave", "walk"]},
         "direction": {"type": "string", "enum": ["up", "down", "updown", "random"]},
         "rate": _N, "octave": _I, "voices": _I, "velocity": _N, "gate": _N, "accent": _N, "humanize": _N,
         "seed": _I, "replace": {"type": "boolean"}, "wait": _WAIT}, ["track", "style"])},
    {"name": "daw_write_drums",
     "description": "Write a drum part on a drum instrument track: genre pop, rock, ballad, funk, house, trap, lofi, "
                    "reggaeton, dnb, rnb (or groove {voice: 16 or 32 steps of x X g . r t}), from start_bar for bars "
                    "(default 8), a fill every fill_every bars (0 = none), crash on bar 1 and after fills. voices "
                    "limits it (kick, snare, clap, side, rim, hat, open, pedal, ride, crash, tom_high/mid/low/floor). "
                    "General MIDI pitches; map {voice: pitch} for other kits. swing (genre default), humanize "
                    "(default 0.3), seed, velocity scale. replace deletes parts inside the range first.",
     "inputSchema": _schema({
         "track": _TRACK, "genre": {"type": "string", "enum": ["pop", "rock", "ballad", "funk", "house", "trap",
                                                              "lofi", "reggaeton", "dnb", "rnb"]},
         "groove": {"type": "object", "additionalProperties": _S}, "start_bar": _I, "bars": _I, "fill_every": _I,
         "crash": {"type": "boolean"}, "voices": {"type": "array", "items": _S},
         "map": {"type": "object", "additionalProperties": _I}, "swing": _N, "humanize": _N, "seed": _I,
         "velocity": _N, "replace": {"type": "boolean"}, "wait": _WAIT}, ["track"])},
    {"name": "daw_transform_notes",
     "description": "Change the notes of a track's parts (all, or the part at part_start): quantize (grid in quarter "
                    "notes, strength 0..1), swing 50..75 (66 = triplet feel) on swing_grid (0.5 = 8ths), humanize "
                    "0..1 (timing and velocity), transpose semitones, velocity_scale / velocity_add. pitch_min/"
                    "pitch_max limit it to a range (e.g. only the hi-hat).",
     "inputSchema": _schema({"track": _TRACK, "part_start": _N, "quantize": _N, "strength": _N, "swing": _N,
                             "swing_grid": _N, "humanize": _N, "transpose": _I, "velocity_scale": _N,
                             "velocity_add": _N, "pitch_min": _I, "pitch_max": _I, "seed": _I, "wait": _WAIT},
                            ["track"])},
    {"name": "daw_copy_bars",
     "description": "Copy the notes of instrument tracks in bars from_bar..to_bar (end exclusive) to dest_bar, as new "
                    "parts (e.g. verse to the second verse). tracks limits it (default: every instrument track). "
                    "Audio tracks are not copied. replace deletes parts inside the destination first.",
     "inputSchema": _schema({"from_bar": _I, "to_bar": _I, "dest_bar": _I,
                             "tracks": {"type": "array", "items": _TRACK}, "replace": {"type": "boolean"},
                             "wait": _WAIT}, ["from_bar", "to_bar", "dest_bar"])},
    {"name": "daw_insert_bars",
     "description": "Make room in the arrangement: every event (parts, audio clips) starting at at_bar or later moves "
                    "right by bars bars, on all tracks or tracks. Instrument parts crossing at_bar are split there (notes re-written; "
                    "split=false keeps them); audio clips crossing it are reported, not moved. "
                    "Markers, chords, tempo and automation are not shifted.",
     "inputSchema": _schema({"at_bar": _I, "bars": _I, "tracks": {"type": "array", "items": _TRACK},
                             "split": {"type": "boolean"}, "wait": _WAIT}, ["at_bar", "bars"])},
    {"name": "daw_apply_chain",
     "description": "Put a ready effect chain on a track (Studio One effects, parameters set): role vocal, bass, pad, "
                    "keys, lead, pluck, drums, guitar, strings, loud (limiter). Inserts go on the track's channel; "
                    "reverb/delay sends go to a shared Room Reverb / Analog Delay FX channel (created the first time, "
                    "later sends are written into the song file with one reopen). chains [{track, role, sends?}] does "
                    "several tracks in one call. Tune afterwards with daw_plugin_param.",
     "inputSchema": _schema({"track": _TRACK, "role": _S,
                             "chains": {"type": "array", "items": _schema(
                                 {"track": _TRACK, "role": _S, "sends": {"type": "boolean"}}, ["track", "role"])},
                             "wait": _WAIT})},
    {"name": "daw_load_sound",
     "description": "Add an instrument track playing plugin with preset (from library_sounds) in one step, or load the "
                    "preset into track. Vital .vital presets, Studio One presets and .vstpreset/.aupreset files load; "
                    "the song is saved, closed and reopened for the preset (~8 s).",
     "inputSchema": _schema({"plugin": _S, "preset": _S, "track": _TRACK, "name": _S, "wait": _WAIT},
                            ["plugin", "preset"])},
    {"name": "daw_duck",
     "description": "Sidechain-style ducking: track's volume dips by depth_db (default 6) on every trigger note of "
                    "trigger_track (default pitches 36/35 = kick), back after release_beats (default 0.3). Written as "
                    "volume automation (song reopens once); start_bar/end_bar limit it.",
     "inputSchema": _schema({"track": _TRACK, "trigger_track": _TRACK, "pitches": {"type": "array", "items": _I},
                             "depth_db": _N, "attack_beats": _N, "release_beats": _N, "start_bar": _I, "end_bar": _I,
                             "wait": _WAIT}, ["track", "trigger_track"])},
    {"name": "daw_snapshot",
     "description": "Snapshots of the open song (or path) to undo a run of edits in one step. action take (label "
                    "optional; take one before larger changes), list, restore (snapshot id or label, default the "
                    "latest; the song is saved, closed, replaced and reopened, the current state is backed up). "
                    "Audio files are shared, not copied.",
     "inputSchema": _schema({"action": {"type": "string", "enum": ["take", "list", "restore"]}, "label": _S,
                             "snapshot": _S, "path": _SONG}, ["action"])},
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
