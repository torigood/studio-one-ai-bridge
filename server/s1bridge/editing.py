"""Live track and event edits that combine several bridge jobs: rename, duplicate, repeat, bounce."""
import glob
import os
import time

from . import analysis_tools, common, config, live, mixer
from .common import ToolError


def bounce(track, part_start, keep, analyze, wait):
    # Observed 0.6.0: Event/Bounce To New Track on selected instrument parts adds an audio track named like
    # the source track, writes <song folder>/Bounces/<track>.wav and mutes the source parts.
    events = [e for e in live.list_events(track, wait) if e.get("mediaType") == "Music"]
    if part_start is not None:
        events = [e for e in events if abs(e["start"] - part_start) < 1e-6]
    if not events:
        raise ToolError("no instrument parts to bounce on %s" % track)
    tracks_before = live.bridge("list_tracks", {}, wait)["tracks"]
    r = live.bridge("call", {"steps": [
        {"as": "ed", "on": {"context": True}, "get": "editor"},
        {"as": "sel", "on": {"var": "ed"}, "get": "selection"},
        {"on": {"var": "sel"}, "call": "unselectAll"},
        {"as": "sf", "on": {"var": "ed"}, "call": "createSelectFunctions", "args": [{"functions": ""}]},
        {"on": {"var": "sf"}, "call": "selectMultiple",
         "args": [[{"event": {"track": track, "index": e["index"]}} for e in events]]}]}, wait)
    if (r.get("results") or [{}])[-1].get("value") != 1:
        raise ToolError("could not select the parts: %s" % r)
    t0 = time.time() - 1
    live.bridge("command", {"category": "Event", "name": "Bounce To New Track"}, wait or 60)
    wav = None
    for _ in range(60):
        hits = [p for p in glob.glob(os.path.join(config.SONGS_ROOT, "**", "Bounces", "*.wav"), recursive=True)
                if os.path.getmtime(p) >= t0]
        if hits:
            wav = max(hits, key=os.path.getmtime)
            size = os.path.getsize(wav)
            time.sleep(0.5)
            if os.path.getsize(wav) == size:
                break
        time.sleep(0.5)
    tracks_after = live.bridge("list_tracks", {}, wait)["tracks"]
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
        out["analysis"] = analysis_tools.audio_analyze(wav)
    if not keep and new_index is not None:
        steps = [{"on": {"functions": ""}, "set": "executeImmediately", "value": True},
                 {"on": {"functions": ""}, "call": "removeTrack", "args": [{"track": new_index}]}]
        steps += [{"on": {"functions": ""}, "call": "muteEvent",
                   "args": [{"event": {"track": track, "index": e["index"]}}, False]} for e in events]
        live.bridge("call", {"steps": steps}, wait)
        out["bounced_track_removed"] = True
    elif new_index is not None:
        out["bounced_track_index"] = new_index
    if not wav:
        out["warning"] = "no new WAV found in a Bounces folder"
    return out


def rename_track(track, name, wait):
    # Observed 0.7.2: track.name = .. is ignored, track.channel.label = .. renames the track (arrange and
    # mixer); an AudioSynth channel's label is writable the same way, and renaming it also renames the
    # instrument tracks it plays (Studio One names them after the instrument). Not an undo step.
    if not name:
        raise ToolError("name is empty")
    before = live.list_tracks(wait)
    t = common.find_track(before, track)
    steps = []
    synth = None
    if t.get("channelType") == "MusicTrack":
        chans = live.bridge("list_mixer", {}, wait).get("channels", [])
        hits = [c for c in chans if c.get("channelType") == "AudioSynth" and c.get("label") == t["name"]]
        if len(hits) == 1:
            synth = hits[0]
            steps += mixer.mixer_channel_steps(synth["index"], "s") + [{"on": {"var": "s"}, "set": "label", "value": name}]
    steps += [{"as": "c", "on": {"track": t["index"]}, "get": "channel"},
              {"on": {"var": "c"}, "set": "label", "value": name}]
    r = live.bridge("call", {"steps": steps}, wait)
    errors = [x["error"] for x in r.get("results", []) if "error" in x]
    if errors:
        raise ToolError("rename failed: %s" % errors[0])
    after = live.list_tracks(wait)
    # other tracks on the same instrument were renamed with it: put their names back
    restore = [b for b, a in zip(before, after) if b["index"] != t["index"] and a["name"] != b["name"]]
    if restore:
        steps = []
        for i, b in enumerate(restore):
            steps += [{"as": "c%d" % i, "on": {"track": b["index"]}, "get": "channel"},
                      {"on": {"var": "c%d" % i}, "set": "label", "value": b["name"]}]
        live.bridge("call", {"steps": steps}, wait)
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


def duplicate_track(track, name, copy_events, wait):
    # Observed 0.7.2: functions.duplicateTrack (track) adds an empty copy (same instrument, name + " 2") at the
    # end of the track list; Track/Duplicate acts on the focused track, not the script selection, so it is not used.
    # functions.transferEvent (event, track, startBeats) moves an event to another track. Each event is
    # copied by moving it to the new track, Edit/Duplicate there (the copy lands at start + length, where
    # nothing else on the new track starts because events go in ascending order), and moving the copy back.
    before = live.list_tracks(wait)
    src = common.find_track(before, track)
    events = sorted(live.list_events(src["index"], wait), key=lambda e: e["start"]) if copy_events else []
    r = live.bridge("call", {"steps": [
        {"on": {"functions": ""}, "set": "executeImmediately", "value": True},
        {"on": {"functions": ""}, "call": "duplicateTrack", "args": [{"track": src["index"]}], "names": ["name"]},
        {"on": {"functions": ""}, "set": "executeImmediately", "value": False}]}, wait)
    res = r.get("results", [])
    if len(res) < 2 or "error" in res[1] or res[1].get("value") != "object":
        raise ToolError("duplicateTrack failed: %s" % r)
    after = live.list_tracks(wait)
    if len(after) != len(before) + 1:
        raise ToolError("expected one new track, found %d" % (len(after) - len(before)))
    old = [(t["name"], t["mediaType"]) for t in before]
    new = next(t for i, t in enumerate(after) if i >= len(old) or (t["name"], t["mediaType"]) != old[i])
    src_i = src["index"] + (1 if new["index"] <= src["index"] else 0)
    warnings = []
    for e in events:
        key = _event_key(e)
        cur = next((x for x in live.list_events(src_i, wait) if _event_key(x) == key), None)
        if cur is None:
            warnings.append("event at beat %s not found any more" % e["start"])
            continue
        live.bridge("call", {"steps": [
            {"as": "e", "on": {"event": {"track": src_i, "index": cur["index"]}}},
            {"on": {"functions": ""}, "set": "executeImmediately", "value": True},
            {"on": {"functions": ""}, "call": "transferEvent", "args": [{"var": "e"}, {"track": new["index"]}, e["start"]]},
            {"on": {"functions": ""}, "set": "executeImmediately", "value": False},
            {"as": "ed", "on": {"context": True}, "get": "editor"},
            {"as": "sel", "on": {"var": "ed"}, "get": "selection"},
            {"on": {"var": "sel"}, "call": "unselectAll"},
            {"as": "sf", "on": {"var": "ed"}, "call": "createSelectFunctions", "args": [{"functions": ""}]},
            {"on": {"var": "sf"}, "call": "selectMultiple", "args": [[{"var": "e"}]]}]}, wait)
        live.bridge("command", {"category": "Edit", "name": "Duplicate"}, wait)
        copy_key = (round(e["start"] + e["length"], 6),) + key[1:]
        copy = next((x for x in live.list_events(new["index"], wait) if _event_key(x) == copy_key), None)
        if copy is None:
            warnings.append("no copy made of the event at beat %s; it was moved to the new track" % e["start"])
            continue
        live.bridge("call", {"steps": [
            {"on": {"functions": ""}, "set": "executeImmediately", "value": True},
            {"on": {"functions": ""}, "call": "transferEvent",
             "args": [{"event": {"track": new["index"], "index": copy["index"]}}, {"track": src_i}, e["start"]]},
            {"on": {"functions": ""}, "set": "executeImmediately", "value": False}]}, wait)
    out = {"source": src["name"], "index": new["index"], "name": new["name"]}
    if copy_events:
        got = sorted(_event_key(x) for x in live.list_events(new["index"], wait))
        if got != sorted(_event_key(e) for e in events):
            warnings.append("new track has %d events, source had %d" % (len(got), len(events)))
        out["events"] = len(got)
    if name:
        out["name"] = rename_track(new["index"], name, wait)["name"]
    if warnings:
        out["warnings"] = warnings
    return out


def repeat_event(track, start_beats, times, shared, wait):
    # Observed 0.6.0: editor.createSelectFunctions(functions).selectMultiple([event]) then Edit/Duplicate
    # puts a copy right after the event and selects the copy, so N presses give N back-to-back copies.
    if not 1 <= times <= 64:
        raise ToolError("times must be 1..64")
    before = live.list_events(track, wait)
    hits = [e for e in before if abs(e["start"] - start_beats) < 1e-6]
    if not hits:
        raise ToolError("no event starts at beat %s on %s; events start at %s"
                        % (start_beats, track, sorted(e["start"] for e in before)))
    r = live.bridge("call", {"steps": [
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
        live.bridge("command", {"category": "Edit", "name": "Duplicate Shared" if shared else "Duplicate"}, wait)
    old = {(e["start"], e["length"], e["offset"]) for e in before}
    added = sorted((e for e in live.list_events(track, wait) if (e["start"], e["length"], e["offset"]) not in old),
                   key=lambda e: e["start"])
    out = {"track": track, "event": hits[0]["name"], "copies": [{"start": e["start"], "length": e["length"]}
                                                                for e in added]}
    if len(added) != times:
        out["warning"] = "expected %d new events, found %d" % (times, len(added))
    return out
