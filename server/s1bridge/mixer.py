"""Live mixer, transport and plug-in tools that need more than one bridge job: loop range, tempo,
sends, buses, output routing, channel lookup and VST3 parameter names. Most run steps through the
bridge's generic "call" op (bridge/src/bridge.js Ops.call).
"""
from . import common, library, live, vst3params
from .common import ToolError


def vst3_param_name(plugin, title):
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


def transport_at(bar, sets, wait):
    """Apply transport parameters with the playhead at bar, then put the playhead back."""
    before = live.bridge("transport", {"set": [["primaryTime", "%04d.01.01.00" % bar]] + sets}, wait)["before"]
    r = live.bridge("transport", {"set": [["primaryTime", before["primaryTime"]]]}, wait)
    return {"tempo": r["after"].get("tempo"),
            "bpm_at_start": round(60 / r["seconds_per_beat"], 3) if r.get("seconds_per_beat") else None}


# Mixer channel by daw_list_mixer index, for daw_call steps (MixerConsole channel list 1, see bridge.js).
def mixer_channel_steps(index, var):
    return [{"as": var + "_f", "on": {"context": True}, "get": "functions"},
            {"as": var + "_r", "on": {"var": var + "_f"}, "get": "root"},
            {"as": var + "_e", "on": {"var": var + "_r"}, "get": "environment"},
            {"as": var + "_m", "on": {"var": var + "_e"}, "call": "find", "args": ["MixerConsole"]},
            {"as": var + "_l", "on": {"var": var + "_m"}, "call": "getChannelList", "args": [1]},
            {"as": var, "on": {"var": var + "_l"}, "call": "getChannel", "args": [index]}]


def _transport_steps():
    return [{"as": "t_f", "on": {"context": True}, "get": "functions"},
            {"as": "t_r", "on": {"var": "t_f"}, "get": "root"},
            {"as": "t_e", "on": {"var": "t_r"}, "get": "environment"},
            {"as": "tp", "on": {"var": "t_e"}, "call": "find", "args": ["TransportPanel"]},
            {"as": "tc", "on": {"var": "t_r"}, "get": "timeContext"}]


def set_loop(a, wait):
    # Observed 2026-10-18: TransportPanel loopStart/loopEnd take setValue(seconds), not fromString, and
    # Transport/Set Loop Start ignores the playhead set by the bridge; timeContext.ppqToSeconds converts.
    start, end = a.get("start_beat"), a.get("end_beat")
    if a.get("start_bar") is not None or a.get("end_bar") is not None:
        info = live.bridge("transport", {}, wait)["before"]
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
    live.call_steps(steps, wait, "set loop")
    info = live.bridge("transport", {}, wait)["before"]
    return {"loop": info.get("loop") == "1", "loop_start": info.get("loopStart"), "loop_end": info.get("loopEnd")}


def effect_class(effect):
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


def audio_channel_index(a, wait):
    """daw_list_mixer index of the audio channel for a track (instrument tracks: their instrument channel) or
    for mixer_channel (index, name or label of a channel that carries audio)."""
    chans = live.bridge("list_mixer", {}, wait).get("channels", [])
    audio = [c for c in chans if c.get("channelType") != "MusicTrack"]
    ref = a.get("mixer_channel")
    if ref is not None:
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            hits = [c for c in chans if c["index"] == int(ref)]
        else:
            hits = [c for c in audio if c.get("name") == ref] or [c for c in audio if c.get("label") == ref]
    elif a.get("track") is not None:
        t = common.find_track(live.list_tracks(wait), a["track"])
        if t.get("channelType") == "MusicTrack":
            hint = live.instrument_channel_hint(t["index"], wait)
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
    steps = mixer_channel_steps(index, "ch") + [{"as": "sends", "on": {"var": "ch"}, "call": "find", "args": ["Sends"]}]
    for s in _SEND_SLOTS:
        steps.append({"as": s, "on": {"var": "sends"}, "call": "find", "args": [s], "names": ["name", "title"]})
    return steps


def _read_sends(index, wait):
    """{slot name: title} of a channel's sends (slots Send01.., found by name; observed 2026-10-18)."""
    res = live.call_steps(_sends_steps(index), wait, "read sends")
    return {s: (r.get("props") or {}).get("title") for s, r in zip(_SEND_SLOTS, res[-len(_SEND_SLOTS):])
            if r.get("value") is not None}


def _send_param_steps(index, slot, level_db=None, pan=None):
    steps = mixer_channel_steps(index, "ch") + [
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


def add_send(a, wait):
    # Observed 2026-10-18: DeviceEditFunctions.insertDevice(channel.find("Sends"), effectClassID) creates an
    # FX channel (channelType AudioEffect, labelled after the effect) and a send Send0N to it.
    # connectChannel(sends, fxChannel[.input]) and insertDevice(sends, fxChannel) do nothing, so a send to an
    # existing FX channel is not possible from a script.
    class_id, effect = effect_class(a["effect"])
    ch = audio_channel_index(a, wait)
    before = _read_sends(ch["index"], wait)
    steps = mixer_channel_steps(ch["index"], "ch") + [
        {"as": "sends", "on": {"var": "ch"}, "call": "find", "args": ["Sends"]},
        {"as": "fx", "on": {"functions": "DeviceEditFunctions"}},
        {"as": "slot", "on": {"var": "fx"}, "call": "insertDevice", "args": [{"var": "sends"}, class_id],
         "names": ["name"]}]
    res = live.call_steps(steps, wait, "add send")
    if res[-1].get("value") is None:
        raise ToolError("Studio One did not add the send (effect %s)" % effect)
    after = _read_sends(ch["index"], wait)
    new = [s for s in after if s not in before]
    out = {"channel": ch["label"], "mixer_index": ch["index"], "effect": effect, "send": new[0] if new else None}
    fx = [c for c in live.bridge("list_mixer", {}, wait).get("channels", []) if c.get("channelType") == "AudioEffect"]
    if fx:
        out["fx_channel"] = {"label": fx[-1]["label"], "mixer_index": fx[-1]["index"]}
    if new and a.get("level_db") is not None:
        out.update(_send_state(live.call_steps(_send_param_steps(ch["index"], new[0], a["level_db"]), wait, "set send level")))
    return out


def _mixer_env_steps():
    return [{"as": "b_f", "on": {"context": True}, "get": "functions"},
            {"as": "b_r", "on": {"var": "b_f"}, "get": "root"},
            {"as": "b_e", "on": {"var": "b_r"}, "get": "environment"},
            {"as": "b_con", "on": {"var": "b_e"}, "call": "find", "args": ["MixerConsole"]},
            {"as": "b_cl", "on": {"var": "b_con"}, "call": "getChannelList", "args": [1]},
            {"as": "b_fx", "on": {"functions": "DeviceEditFunctions"}}]


def _channel_output(index, wait):
    steps = mixer_channel_steps(index, "o") + [
        {"as": "od", "on": {"var": "o"}, "call": "getDestinationChannel", "names": ["label", "channelType", "name"]}]
    return (live.call_steps(steps, wait, "read output")[-1].get("props") or {})


def add_bus(a, wait):
    # Create Tracks.js (AddBusForSelectedChannelsTask): AudioMixer.setup.addChannel("AudioGroup"),
    # DeviceEditFunctions.connectChannel(channel, bus.input), bus.connectTo(old destination). Verified 2026-10-18.
    refs = [{"track": t} for t in a.get("tracks") or []] + [{"mixer_channel": m} for m in a.get("mixer_channels") or []]
    if not refs:
        raise ToolError("pass tracks and/or mixer_channels")
    chans = [audio_channel_index(r, wait) for r in refs]
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
    live.call_steps(steps, wait, "add bus")
    mixer = live.bridge("list_mixer", {}, wait).get("channels", [])
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


def set_output(a, wait):
    ch = audio_channel_index(a, wait)
    if a.get("destination") is None:
        return {"channel": ch["label"], "output": _channel_output(ch["index"], wait)}
    # The main output is not in channel list 1 (daw_list_mixer) but alone in list 3 (observed 2026-10-18).
    main = str(a["destination"]).lower() in ("main", "main out", "main output", "master")
    if main:
        dest = {"label": "Main"}
        get_dest = [{"as": "l3", "on": {"var": "b_con"}, "call": "getChannelList", "args": [3]},
                    {"as": "d", "on": {"var": "l3"}, "call": "getChannel", "args": [0], "names": ["label"]}]
    else:
        dest = audio_channel_index({"mixer_channel": a["destination"]}, wait)
        if dest["index"] == ch["index"]:
            raise ToolError("a channel cannot be routed to itself")
        get_dest = [{"as": "d", "on": {"var": "b_cl"}, "call": "getChannel", "args": [dest["index"]]}]
    steps = _mixer_env_steps() + [
        {"as": "c", "on": {"var": "b_cl"}, "call": "getChannel", "args": [ch["index"]]}] + get_dest + [
        {"as": "din", "on": {"var": "d"}, "get": "input"},
        {"as": "ok", "on": {"var": "b_fx"}, "call": "connectChannel", "args": [{"var": "c"}, {"var": "din"}]}]
    live.call_steps(steps, wait, "set output")
    now = _channel_output(ch["index"], wait)
    if not main and now.get("label") != dest["label"] or main and now.get("channelType") != "AudioOutput":
        raise ToolError("Studio One kept the output %r" % now.get("label"))
    return {"channel": ch["label"], "output": now.get("label")}


def set_send(a, wait):
    ch = audio_channel_index(a, wait)
    sends = _read_sends(ch["index"], wait)
    if a.get("level_db") is None and a.get("pan") is None and a.get("send") is None:
        out = []
        for slot, title in sends.items():
            out.append(dict({"send": int(slot[4:]), "slot": slot, "title": title},
                            **_send_state(live.call_steps(_send_param_steps(ch["index"], slot), wait, "read send"))))
        return {"channel": ch["label"], "sends": out}
    slot = "Send%02d" % int(a.get("send") or 1)
    if slot not in sends:
        raise ToolError("%s has no send %s (sends: %s)" % (ch["label"], slot, ", ".join(sends) or "none"))
    res = live.call_steps(_send_param_steps(ch["index"], slot, a.get("level_db"), a.get("pan")), wait, "set send")
    return dict({"channel": ch["label"], "send": int(slot[4:]), "title": sends[slot]}, **_send_state(res))
