"""Talking to the bridge inside Studio One: queue a job in the mailbox, press the virtual Run Jobs
button, wait for the result. Also the helpers built on it: the open song's path, saving, and editing a
saved song while Studio One has it open (offline_edit).
"""
import os
import subprocess
import time
import urllib.parse

from . import config, midi, setup, songwrite
from .common import ToolError
from .mailbox import Mailbox
from .song import Song


# Trigger the in-DAW "Run Jobs" command through the AI Bridge virtual MIDI surface.
# Set S1BRIDGE_MIDI=0 to disable (then run the command by hand in Studio One).
USE_MIDI = os.environ.get("S1BRIDGE_MIDI", "1") != "0"


def trigger():
    """Press the virtual 'Run Jobs' button. Returns an error string or None."""
    if not USE_MIDI:
        return "midi trigger disabled"
    try:
        midi.press(midi.CC_RUN_JOBS)
        return None
    except OSError as e:
        return str(e)


def bridge(op, args, wait):
    mb = Mailbox()
    mb.cleanup()
    job_id = mb.enqueue(op, args)
    trigger_error = trigger()
    wait = 10 if wait is None else wait
    r = mb.wait(job_id, min(wait, 2))
    if r is None and wait > 2 and not trigger_error:
        # Studio One re-binds a newly created MIDI port only after a moment: press again.
        trigger_error = trigger()
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


def call_steps(steps, wait, what):
    """Run steps through the bridge's call op; raise on the first failing step."""
    r = bridge("call", {"steps": steps}, wait)
    if not isinstance(r, dict) or "results" not in r:
        raise ToolError("%s: bridge did not answer (%s)" % (what, r))
    errors = [x["error"] for x in r["results"] if "error" in x]
    if errors:
        raise ToolError("%s failed: %s" % (what, errors[0]))
    return r["results"]


def list_tracks(wait):
    r = bridge("list_tracks", {}, wait)
    if isinstance(r, dict) and r.get("status") == "not_run":
        raise ToolError("the bridge did not answer; is Studio One running with a song open?")
    return r["tracks"]


def list_events(track, wait):
    r = bridge("list_events", {"track": track}, wait)
    if isinstance(r, dict) and r.get("status") == "not_run":
        raise ToolError("the bridge did not answer; is Studio One running with a song open?")
    return r["events"]


def wait_for_bridge(seconds):
    """True once the bridge answers a ping (a song is open again)."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            r = bridge("ping", {}, 3)
        except ToolError:
            r = None
        if isinstance(r, dict) and r.get("pong"):
            return True
        if isinstance(r, dict) and r.get("status") == "not_run":
            Mailbox().cancel(r["job_id"])
        time.sleep(1)
    return False


def active_song_path(wait=None):
    """File path of the song open in Studio One (DocumentManager.activeDocument.path.url), None if unknown
    (no song open, not saved yet, or the bridge did not answer)."""
    if not os.path.isfile(os.path.join(Mailbox().root, "status.json")):
        return None
    try:
        r = bridge("probe", {"root": "object", "url": "://studioapp/DocumentManager",
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


def open_in_daw(path):
    """True if Studio One's active document is this song, None if the bridge did not answer."""
    active = active_song_path()
    if active is None:
        return None
    return os.path.realpath(active) == os.path.realpath(path)


def save_open_song(path):
    """Press File/Save (surface CC 116) and wait up to 10 s for the file to change. True if it did."""
    before = os.path.getmtime(path)
    midi.press(midi.CC_SAVE)
    for _ in range(40):
        time.sleep(0.25)
        if os.path.getmtime(path) != before:
            return True
    return False


def offline_edit(path, write):
    """Run write(path) on a saved song. If Studio One has it open: save, close, write, reopen.
    Save and Close are pressed through the AI Bridge surface (CC 116/115): File/Close run from a job
    inside the Run Jobs edit task crashed Studio One (2026-10-17). `open -a "Studio One 5" <song>`
    reopens the song in the running app."""
    was_open = open_in_daw(path)
    if was_open:
        save_open_song(path)
        midi.press(midi.CC_CLOSE)
        for _ in range(20):
            time.sleep(0.5)
            if not open_in_daw(path):
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
        reopened = wait_for_bridge(60)
    if result is None:
        raise ToolError(error + (" (the song was reopened unchanged)" if was_open else ""))
    if was_open:
        result["reopened"] = reopened
    return result


def saved_instrument_channel(path, track):
    """(AudioSynth channel name or None, whether the saved file has the track at all)."""
    try:
        with Song(path) as s:
            ch = s.instrument_output_channel(track)
            known = ch is not None or any(t["name"] == track for t in s.tracks())
    except Exception:
        return None, False
    return (ch.get("name") if ch else None), known


def instrument_channel_hint(track, wait=None):
    """AudioSynth channel name ("Channel03") for an instrument track of the open song. The live API has no
    track -> instrument channel link (0.7.2: no such property or child on the track or its channel), so try
    in order: the saved song's instrumentOut connection, the one AudioSynth channel labelled like the track
    (Studio One names both after the instrument; daw_rename_track keeps them equal), and for a track the
    saved file does not have yet, File > Save and read again. None for audio tracks or if nothing matched."""
    res = bridge("list_tracks", {}, wait)
    tracks = res.get("tracks", []) if isinstance(res, dict) else []
    info = next((t for t in tracks if t["index"] == track or t["name"] == track), None)
    if info is None or info.get("channelType") != "MusicTrack":
        return None
    name = info["name"]
    path = active_song_path(wait)
    known = False
    if path:
        ch, known = saved_instrument_channel(path, name)
        if ch:
            return ch
    mixer = bridge("list_mixer", {}, wait)
    synths = [c for c in (mixer.get("channels", []) if isinstance(mixer, dict) else [])
              if c.get("channelType") == "AudioSynth" and c.get("label") == name]
    if len(synths) == 1:
        return synths[0]["name"]
    if path and not known and USE_MIDI and save_open_song(path):
        return saved_instrument_channel(path, name)[0]
    return None


def version_check(status):
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
