"""MCP tools: call(name, args) runs one. Definitions are in schemas.py; the work is done by

  song_*, library_*  read saved .song files and the installed content directly (song.py, library.py ...);
                     song_write_* and friends edit the saved file through live.offline_edit
  daw_*              send a job to the in-DAW bridge through the mailbox and wait for its result (live.py,
                     mixer.py, editing.py)
  audio_*            analysis in the analysis pack's Python (analysis_tools.py)
  bridge_*           install, check and share the Studio One side (setup.py, clients.py, share.py)
"""
import contextlib
import glob
import json
import os
import sys

from . import (analysis_tools, automation, chords, clients, config, editing, effects, library, live, loops, midi,
               mixer, presets, setup, songwrite, vst3params)
from .common import ToolError, find_track, song_path
from .mailbox import Mailbox
from .schemas import DEV_TOOLS, REMOTE_HIDDEN, TOOLS, listed_tools
from .song import Song

# What __main__, httpserver, setup and packaging use.
__all__ = ["call", "HANDLERS", "ToolError", "TOOLS", "DEV_TOOLS", "REMOTE_HIDDEN", "listed_tools"]

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
        try:
            with open(config.CATALOG, encoding="utf-8") as f:
                _catalog = json.load(f)
        except (OSError, ValueError):
            # Generated on the user's machine by setup (s1bridge/setup.py); not shipped.
            _catalog = {"commands": [], "editTasks": [], "missing": config.CATALOG}
    return _catalog


def _pick(a, *keys):
    """The arguments among keys that the caller passed."""
    return {k: a[k] for k in keys if k in a}


def _with_instrument_channel(args, wait):
    """For a track (no mixer_channel), add the instrument channel an instrument track plays through."""
    if "track" in args and "mixer_channel" not in args:
        hint = live.instrument_channel_hint(args["track"], wait)
        if hint:
            args["instrument_channel"] = hint
    return args


def _offline(write):
    """Handler for a tool that edits the saved song a["path"]: write(path, a)."""
    return lambda a: live.offline_edit(song_path(a["path"]), lambda p: write(p, a))


def _live(op, *keys):
    """Handler for a tool that is one bridge op with some of its arguments."""
    return lambda a: live.bridge(op, _pick(a, *keys), a.get("wait"))


# ---- offline ------------------------------------------------------------------------------------

def _song_list(a):
    files = [p for p in glob.glob(os.path.join(config.SONGS_ROOT, "**", "*.song"), recursive=True)
             if os.sep + "History" + os.sep not in p]
    files.sort(key=os.path.getmtime, reverse=True)
    return [{"path": p, "name": os.path.basename(p)[:-5]} for p in files[: a.get("limit", 20)]]


def _library_loops(a):
    res = library.search_loops(a.get("query", ""), a.get("kind"), a.get("bpm"), a.get("limit", 50))
    index, ids = loops.load_index(), loops.package_ids()
    for item in res["loops"]:
        if item["kind"] == "audioloop" and item["sound_set"] in ids:
            info = index.get(loops.loop_url(item, ids))
            if info:
                item["loop_beats"] = info["beats"]
                item["bpm"] = round(60 / info["tempo"], 2)
    return res


def _library_plugin_params(a):
    found = vst3params.find(a["plugin"], a.get("query", ""), a.get("limit", 60))
    if found is not None:
        found["plugin"] = a["plugin"]
        found["format"] = "VST3"
        return found
    return {"params": library.native_params(a["plugin"]),
            "presets": [p["folder"] + "/" + p["name"] for p in library.presets(a["plugin"])][:100]}


def _song_summary(a):
    with Song(song_path(a["path"])) as s:
        return s.summary()


def _song_track_notes(a):
    with Song(song_path(a["path"])) as s:
        t = find_track(s.tracks(include_notes=True), a["track"])
    notes = sorted((n for p in t["parts"] for n in p.get("notes", [])), key=lambda n: n["start_beats"])
    return {"track": t["name"], "parts": [{k: v for k, v in p.items() if k != "notes"} for p in t["parts"]],
            "notes": notes}


def _song_add_send(a):
    if a.get("track") is None and a.get("mixer_channel") is None:
        raise ToolError("pass track or mixer_channel")
    return _offline(lambda p, a: effects.add_send(
        p, a["destination"], track=a.get("track"), mixer_channel=a.get("mixer_channel"),
        level_db=a.get("level_db", -6.0), pan=a.get("pan", 0.0), prefader=a.get("prefader", False)))(a)


def _song_remove_effect(a):
    if ("track" in a) == ("mixer_channel" in a):
        raise ToolError("pass track or mixer_channel")
    return _offline(lambda p, a: effects.remove_effect(
        p, a.get("track"), a.get("mixer_channel"), a.get("slot"), a.get("plugin")))(a)


def _commands_search(a):
    q = (a.get("query") or "").lower()
    cat = a.get("category")
    catalog = _load_catalog()
    if "missing" in catalog:
        raise ToolError("command catalog not found at %s. Generate it on this machine: %s"
                        % (catalog["missing"], clients.command("setup", "--apply")))
    hits = [c for c in catalog["commands"]
            if (not cat or c["category"] == cat) and (q in c["name"].lower() or q in c["category"].lower())]
    return hits[: a.get("limit", 40)]


# ---- install and sharing ------------------------------------------------------------------------

def _bridge_doctor(a):
    checks = setup.doctor()
    return {"ok": all(c["ok"] is not False for c in checks), "checks": checks}


def _bridge_setup(a):
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


def _bridge_share(a):
    from . import share
    if a["action"] == "on":
        s = share.load_settings()
        if s["provider"] == "ngrok" and not share.keychain_get("ngrok"):
            raise ToolError("ngrok is selected but its authtoken is not in the Keychain; the user runs this in "
                            "Terminal: %s" % clients.command("share", "--set-ngrok-token"))
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


def _bridge_connect_client(a):
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


# ---- live ---------------------------------------------------------------------------------------

def _daw_status(a):
    mb = Mailbox()
    status = mb.read_json("status.json")
    # Jobs run when the AI Bridge MIDI button presses Run Jobs; status.loaded and daw_ping tell whether
    # that works.
    out = {"mailbox": mb.root, "status": status, "pending_jobs": mb.pending(),
           "job_trigger": "virtual MIDI button 'AI Bridge' (normal; live editing works when daw_ping answers)"}
    out.update(live.version_check(status))
    return out


def _daw_ping(a):
    r = live.bridge("ping", {}, a.get("wait"))
    want = setup.source_version()
    if isinstance(r, dict) and r.get("version") and r["version"] != want:
        fix = live.version_check({}).get("problem") or "Ask the user to restart Studio One."
        r["warning"] = "Studio One runs bridge %s, this server expects %s. %s" % (r["version"], want, fix)
    return r


def _daw_command(a):
    if (a["category"], a["name"]) in BLOCKED_COMMANDS:
        raise ToolError("command blocked for safety: %s/%s" % (a["category"], a["name"]))
    return live.bridge("command", {"category": a["category"], "name": a["name"], "args": a.get("args")},
                       a.get("wait"))


def _needs_event(op, *keys):
    def run(a):
        if "start_beats" not in a and "index" not in a:
            raise ToolError("pass start_beats or index")
        return live.bridge(op, _pick(a, *keys), a.get("wait"))
    return run


def _daw_set_tempo(a):
    if not 10 <= a["bpm"] <= 400:
        raise ToolError("bpm must be 10..400")
    return mixer.transport_at(1, [["tempo", a["bpm"]]], a.get("wait"))


def _daw_set_position(a):
    r = live.bridge("transport", {"set": [["primaryTime", "%04d.%02d.01.00" % (a["bar"], a.get("beat", 1))]]},
                    a.get("wait"))
    return {"position": r["after"]["primaryTime"]}


def _daw_set_channel_param(a):
    args = _pick(a, "track", "mixer_channel", "param", "value")
    if "mixer_channel" not in args and "track" not in args:
        raise ToolError("pass track or mixer_channel")
    return live.bridge("set_channel_param", _with_instrument_channel(args, a.get("wait")), a.get("wait"))


def _daw_scan_plugin_params(a):
    args = _pick(a, "instrument_slot", "track", "mixer_channel", "slot", "prefix", "from", "to", "limit")
    return live.bridge("scan_params", _with_instrument_channel(args, a.get("wait")), a.get("wait") or 30)


def _daw_insert_effect(a):
    args = _pick(a, "class_id", "track", "mixer_channel")
    return live.bridge("insert_effect", _with_instrument_channel(args, a.get("wait")), a.get("wait"))


def _daw_plugin_param(a):
    args = _pick(a, "instrument_slot", "track", "mixer_channel", "slot", "param", "display", "normalized")
    if a.get("plugin") and not args["param"].startswith("vstp"):
        args["param"] = mixer.vst3_param_name(a["plugin"], args["param"])
    return live.bridge("plugin_param", _with_instrument_channel(args, a.get("wait")), a.get("wait"))


def _effects_args(a):
    args = _pick(a, "track", "mixer_channel")
    if not args:
        raise ToolError("pass track or mixer_channel")
    return _with_instrument_channel(args, a.get("wait"))


def _daw_list_effects(a):
    return live.bridge("list_effects", _effects_args(a), a.get("wait"))


def _daw_bypass_effect(a):
    args = _effects_args(a)
    args.update(slot=a["slot"], param="bypass", value=1 if a.get("bypass", True) else 0)
    r = live.bridge("plugin_param", args, a.get("wait"))
    return {"slot": a["slot"], "bypass": r.get("raw")} if isinstance(r, dict) and "raw" in r else r


def _daw_transport(a):
    midi.press(midi.CC_PLAY if a["action"] == "play" else midi.CC_STOP)
    return {"sent": a["action"]}


HANDLERS = {
    # offline: read
    "song_list": _song_list,
    "song_summary": _song_summary,
    "song_track_notes": _song_track_notes,
    "song_analyze": lambda a: analysis_tools.song_analyze(song_path(a["path"]), a.get("audio", True)),
    "library_instruments": lambda a: library.instruments(),
    "library_effects": lambda a: library.effects(),
    "library_loops": _library_loops,
    "library_presets": lambda a: presets.find(a["plugin"], a.get("query", ""), a.get("limit", 60)),
    "library_plugin_params": _library_plugin_params,
    "commands_search": _commands_search,

    # offline: write the saved song (saved, closed and reopened when it is open)
    "song_write_cc": _offline(lambda p, a: songwrite.write_cc(
        p, a["track"], a["cc"], a["points"], a.get("part"), a.get("replace", True), a.get("step", False))),
    "song_add_loop": _offline(lambda p, a: loops.add_loop(
        p, a["track"], a["loop"], a["start_beats"], a.get("length_beats"))),
    "song_add_audio": _offline(lambda p, a: loops.add_audio_file(
        p, a["track"], a["file"], a["start_beats"], a.get("copy", True))),
    "song_write_automation": _offline(lambda p, a: automation.write_automation(
        p, a["track"], a["param"], a["points"], a.get("replace", True))),
    "song_write_plugin_automation": _offline(lambda p, a: automation.write_plugin_automation(
        p, a["track"], a["param"], a["points"], a.get("instrument", False), a.get("slot"), a.get("plugin"),
        a.get("title"), a.get("replace", True))),
    "song_set_tempo_map": _offline(lambda p, a: songwrite.write_tempo_map(p, a["changes"])),
    "song_set_time_signatures": _offline(lambda p, a: songwrite.write_time_signatures(p, a["signatures"])),
    "song_write_markers": _offline(lambda p, a: songwrite.write_markers(p, a["markers"], a.get("replace", True))),
    "song_write_sections": _offline(lambda p, a: songwrite.write_sections(p, a["sections"], a.get("replace", True))),
    "song_write_chords": _offline(lambda p, a: chords.write_chords(p, a["chords"], a.get("replace", True))),
    "song_load_preset": _offline(lambda p, a: presets.load_preset(p, a["track"], a["preset"])),
    "song_add_send": _song_add_send,
    "song_remove_effect": _song_remove_effect,

    # analysis
    "audio_analyze": lambda a: analysis_tools.audio_analyze(a["path"], a.get("start_sec", 0), a.get("length_sec")),
    "audio_melody": lambda a: analysis_tools.audio_melody(a, a.get("wait")),
    "song_check_clashes": lambda a: analysis_tools.check_clashes(song_path(a["path"]), a),

    # install and sharing
    "bridge_doctor": _bridge_doctor,
    "bridge_setup": _bridge_setup,
    "bridge_share": _bridge_share,
    "bridge_connect_client": _bridge_connect_client,

    # live: status and transport
    "daw_status": _daw_status,
    "daw_ping": _daw_ping,
    "daw_transport": _daw_transport,
    "daw_transport_info": lambda a: live.bridge("transport", {}, a.get("wait"))["before"],
    "daw_set_tempo": _daw_set_tempo,
    "daw_set_position": _daw_set_position,
    "daw_set_loop": lambda a: mixer.set_loop(a, a.get("wait")),
    "daw_command": _daw_command,

    # live: tracks and events
    "daw_list_tracks": _live("list_tracks"),
    "daw_add_track": lambda a: live.bridge("add_track", {"type": a["type"], "name": a.get("name", "")}, a.get("wait")),
    "daw_delete_track": _live("delete_track", "track"),
    "daw_rename_track": lambda a: editing.rename_track(a["track"], a["name"], a.get("wait")),
    "daw_duplicate_track": lambda a: editing.duplicate_track(a["track"], a.get("name"), a.get("events", True),
                                                             a.get("wait")),
    "daw_list_events": _live("list_events", "track"),
    "daw_edit_event": _needs_event("edit_event", "track", "start_beats", "index", "move_to", "length", "mute"),
    "daw_delete_event": _needs_event("delete_event", "track", "start_beats", "index", "all"),
    "daw_repeat_event": lambda a: editing.repeat_event(a["track"], a["start_beats"], a.get("times", 1),
                                                       a.get("shared", False), a.get("wait")),
    "daw_bounce": lambda a: editing.bounce(a["track"], a.get("part_start"), a.get("keep", False),
                                           a.get("analyze", True), a.get("wait")),

    # live: notes and chords
    "daw_insert_notes": lambda a: live.bridge(
        "insert_notes", {k: a[k] for k in ("track", "start_beats", "length_beats", "notes")}, a.get("wait")),
    "daw_read_notes": _live("read_notes", "track", "part_start"),
    "daw_edit_notes": _live("edit_notes", "track", "part_start", "edits", "add"),
    "daw_read_chords": _live("read_chords"),

    # live: mixer and plug-ins
    "daw_list_mixer": _live("list_mixer"),
    "daw_set_channel_param": _daw_set_channel_param,
    "daw_add_send": lambda a: mixer.add_send(a, a.get("wait")),
    "daw_set_send": lambda a: mixer.set_send(a, a.get("wait")),
    "daw_add_bus": lambda a: mixer.add_bus(a, a.get("wait")),
    "daw_set_output": lambda a: mixer.set_output(a, a.get("wait")),
    "daw_list_instruments": _live("list_instruments"),
    "daw_add_instrument": _live("add_instrument", "class_id", "track", "name"),
    "daw_insert_effect": _daw_insert_effect,
    "daw_list_effects": _daw_list_effects,
    "daw_bypass_effect": _daw_bypass_effect,
    "daw_plugin_param": _daw_plugin_param,
    "daw_scan_plugin_params": _daw_scan_plugin_params,

    # live: API discovery (DEV_TOOLS)
    "daw_inspect_track": _live("inspect_track", "track"),
    "daw_inspect_mixer": _live("inspect_mixer", "track"),
    "daw_probe": _live("probe", "root", "track", "list", "index", "path", "names", "url"),
    "daw_call": lambda a: live.bridge("call", {"steps": a["steps"]}, a.get("wait")),
    "daw_dump_host": _live("dump_host"),
}


def call(name, a):
    handler = HANDLERS.get(name)
    if handler is None:
        raise ToolError("unknown tool: %s" % name)
    return handler(a)
