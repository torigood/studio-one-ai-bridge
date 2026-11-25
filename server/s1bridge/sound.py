"""Sounds and processing in plain words: find a sound across every installed instrument, add an instrument
track with that sound in one step, and put a ready effect chain (inserts plus reverb/delay sends) on a track.

Chains use Studio One's own effects, so they work on every install; parameters are set by display value
(checked live 2026-11-25: Compressor threshold '-18', ratio '4' -> '4.0 : 1', attack '10' -> '10.00 ms';
Pro EQ '250 Hz', '-3'; from 1 kHz on Pro EQ wants kHz: '3 k' (it reads '3000' or '3000 Hz' as 3000 k)).
"""
import os

from . import effects, library, live, mixer, presets
from .common import ToolError

# role: (inserts [(effect, {param: display})], sends [(fx kind, level dB)])
CHAINS = {
    "vocal": ([("Pro EQ", {"lcactive": "1", "lcfreq": "100 Hz", "hmfactive": "1", "hmffreq": "3 k",
                           "hmfgain": "2", "hmfq": "1"}),
               ("Compressor", {"threshold": "-18", "ratio": "3", "attack": "10", "release": "100", "gain": "3"})],
              [("reverb", -12), ("delay", -18)]),
    "bass": ([("Pro EQ", {"lcactive": "1", "lcfreq": "35 Hz"}),
              ("Compressor", {"threshold": "-20", "ratio": "4", "attack": "20", "release": "120", "gain": "3"})], []),
    "pad": ([("Pro EQ", {"lcactive": "1", "lcfreq": "150 Hz", "hfactive": "1", "hffreq": "10 k",
                         "hfgain": "-2"})], [("reverb", -8)]),
    "keys": ([("Pro EQ", {"lcactive": "1", "lcfreq": "80 Hz"}),
              ("Compressor", {"threshold": "-16", "ratio": "2", "attack": "15", "release": "150"})], [("reverb", -14)]),
    "lead": ([("Pro EQ", {"lcactive": "1", "lcfreq": "120 Hz"}),
              ("Compressor", {"threshold": "-16", "ratio": "3", "attack": "10", "release": "100", "gain": "2"})],
             [("delay", -14), ("reverb", -16)]),
    "pluck": ([("Pro EQ", {"lcactive": "1", "lcfreq": "150 Hz"})], [("delay", -16), ("reverb", -14)]),
    "drums": ([("Pro EQ", {"lcactive": "1", "lcfreq": "30 Hz"}),
               ("Compressor", {"threshold": "-15", "ratio": "4", "attack": "30", "release": "100", "gain": "2"})],
              [("reverb", -20)]),
    "guitar": ([("Pro EQ", {"lcactive": "1", "lcfreq": "90 Hz", "hmfactive": "1", "hmffreq": "2.5 k",
                            "hmfgain": "1.5", "hmfq": "1"}),
                ("Compressor", {"threshold": "-16", "ratio": "2.5", "attack": "15", "release": "120"})],
               [("reverb", -16)]),
    "strings": ([("Pro EQ", {"lcactive": "1", "lcfreq": "60 Hz"})], [("reverb", -6)]),
    "loud": ([("Limiter", {"ceiling": "-1", "thresholdrel": "-3"})], []),
}

# FX channels the sends go to: (effect, settings for a 100 % wet return)
SEND_FX = {"reverb": ("Room Reverb", {"mix": "100"}), "delay": ("Analog Delay", {"mix": "100", "sync": "1"})}


def hz(freq):
    """Display value for a frequency (Pro EQ reads numbers from 1000 on as kHz)."""
    return "%g k" % (freq / 1000.0) if freq >= 1000 else "%g Hz" % freq


def chains():
    return {role: {"inserts": [e for e, _p in ins], "sends": ["%s %+g dB" % (k, db) for k, db in sends]}
            for role, (ins, sends) in CHAINS.items()}


def _class_id(effect):
    for e in library.effects():
        if e["name"] == effect:
            return e["class_id"]
    raise ToolError("%s is not installed" % effect)


def _fx_args(track, wait):
    args = {"track": track}
    hint = live.instrument_channel_hint(track, wait)
    if hint:
        args["instrument_channel"] = hint
    return args


def _set(args, slot, params, wait):
    done = {}
    for p, v in params.items():
        r = live.bridge("plugin_param", dict(args, slot=slot, param=p, display=v), wait)
        done[p] = r.get("display") if isinstance(r, dict) else r
    return done


def _fx_channels(wait):
    return [c for c in live.bridge("list_mixer", {}, wait).get("channels", []) if c.get("channelType") == "AudioEffect"]


def apply_chain(a, wait):
    """daw_apply_chain: [{track, role}] (or track + role)."""
    items = a.get("chains") or [{"track": a.get("track"), "role": a.get("role")}]
    out, offline_sends = [], []
    for it in items:
        track, role = it.get("track"), it.get("role")
        if track is None or role not in CHAINS:
            raise ToolError("each chain needs track and role (one of %s)" % ", ".join(CHAINS))
        inserts, sends = CHAINS[role]
        if it.get("sends") is False:
            sends = []
        args = _fx_args(track, wait)
        res = {"track": track, "role": role, "inserts": [], "sends": []}
        for effect, params in inserts:
            before = {e["slot"] for e in live.bridge("list_effects", args, wait).get("effects", [])}
            live.bridge("insert_effect", dict(args, class_id=_class_id(effect)), wait)
            new = [e for e in live.bridge("list_effects", args, wait).get("effects", []) if e["slot"] not in before]
            if not new:
                raise ToolError("Studio One did not insert %s on %s" % (effect, track))
            res["inserts"].append({"effect": effect, "slot": new[0]["slot"],
                                   "set": _set(args, new[0]["slot"], params, wait)})
        for kind, db in sends:
            effect, fx_params = SEND_FX[kind]
            existing = [c for c in _fx_channels(wait) if c["label"].startswith(effect)]
            if existing:  # a send to an FX channel that exists can only be written into the song file
                offline_sends.append((track, existing[0]["label"], db))
                res["sends"].append({"to": existing[0]["label"], "level_db": db, "how": "song file (reopen)"})
                continue
            r = mixer.add_send({"effect": effect, "track": track, "level_db": db}, wait)
            fx = r.get("fx_channel") or {}
            if fx.get("label"):
                fx_args = {"mixer_channel": fx["label"]}
                slots = live.bridge("list_effects", fx_args, wait).get("effects", [])
                if slots:
                    res.setdefault("fx_settings", {})[fx["label"]] = _set(fx_args, slots[0]["slot"], fx_params, wait)
            res["sends"].append({"to": fx.get("label"), "level_db": db, "how": "new FX channel"})
        out.append(res)
    result = {"chains": out}
    if offline_sends:
        path = live.active_song_path()
        if not path:
            result["warning"] = "sends to existing FX channels need a saved song; save it and call again"
        else:
            def write(p):
                for track, dest, db in offline_sends:
                    effects.add_send(p, dest, track=track, level_db=db)
                return {"sends_written": len(offline_sends)}
            result.update(live.offline_edit(path, write))
    return result


# ---- finding and loading sounds ---------------------------------------------------------------------------

SYNONYMS = {"warm": ["analog", "soft", "warm", "mellow"], "bright": ["bright", "shiny", "digital", "glass"],
            "dark": ["dark", "deep", "low"], "sub": ["sub", "808", "bass"], "keys": ["keys", "piano", "ep", "organ"],
            "piano": ["piano", "keys", "ep"], "lead": ["lead", "solo"], "pad": ["pad", "strings", "atmo", "ambient"],
            "pluck": ["pluck", "pizz", "plucked"], "arp": ["arp", "sequence", "seq"], "bass": ["bass", "sub", "808"],
            "strings": ["strings", "string", "violin", "cello"], "fx": ["fx", "sfx", "riser", "impact"]}


def find_sounds(query, plugin=None, limit=20):
    """library_sounds: presets of every installed instrument ranked by how many query words (and synonyms)
    match name, folder or style."""
    words = query.lower().split()
    names = [plugin] if plugin else sorted({i["name"] for i in library.instruments()} | {"Mai Tai", "Mojito"})
    installed = {i["name"]: i for i in library.instruments()}
    hits = []
    for name in names:
        if name not in installed:
            continue
        for p in presets.find(name, "", 100000)["presets"]:
            text = ("%s %s %s" % (p["folder"], p["name"], p.get("style", ""))).lower()
            score = 0
            for w in words:
                if w in text:
                    score += 3
                elif any(s in text for s in SYNONYMS.get(w, [])):
                    score += 1
            if score:
                hits.append({"plugin": name, "preset": (p["folder"] + "/" + p["name"]).lstrip("/"),
                             "style": p.get("style") or None, "score": score, "path": p["path"]})
    hits.sort(key=lambda h: (-h["score"], h["plugin"], h["preset"]))
    for h in hits:
        if h["style"] is None:
            del h["style"]
    return {"query": query, "total": len(hits), "sounds": hits[:limit],
            "instruments_with_presets": sorted({h["plugin"] for h in hits})}


def load_sound(a, wait):
    """daw_load_sound: an instrument track (new, or track) playing plugin with preset."""
    inst = next((i for i in library.instruments() if i["name"] == a["plugin"]), None)
    if inst is None:
        raise ToolError("instrument not installed: %s (library_instruments)" % a["plugin"])
    track = a.get("track")
    out = {}
    if track is None:
        name = a.get("name") or os.path.splitext(os.path.basename(a["preset"]))[0]
        r = live.bridge("add_instrument", {"class_id": inst["class_id"], "name": name}, wait or 30)
        if not isinstance(r, dict) or "track" not in r:
            raise ToolError("could not add %s: %s" % (a["plugin"], r))
        track = r["track"]["name"]
        out["new_track"] = track
    path = live.active_song_path()
    if not path:
        raise ToolError("the song is not saved yet: the preset is written into the song file; save it once "
                        "(daw_snapshot action take saves a new song) and call again with track=%r" % track)
    res = live.offline_edit(path, lambda p: presets.load_preset(p, track, a["preset"]))
    out.update(res)
    return out


# ---- ducking ------------------------------------------------------------------------------------------------

def duck_points(hits, base_db, depth_db, attack, release):
    """Volume points: down by depth_db at each hit (reached after attack beats), back to base after release."""
    pts = []
    for h in sorted(set(round(x, 6) for x in hits)):
        pts += [(h - 0.005, base_db), (h + attack, base_db - depth_db), (h + attack + release, base_db)]
    out = []
    for b, v in sorted(pts):
        if out and b <= out[-1][0] + 1e-4:
            out[-1] = (out[-1][0], min(out[-1][1], v))  # overlapping dips: keep the deeper point
            continue
        out.append((b, v))
    return [{"beat": round(max(b, 0.0), 4), "value": round(v, 2)} for b, v in out]


def duck(a, wait):
    """daw_duck: the target's volume dips on every trigger note (kick), like a sidechain compressor."""
    from . import automation, mixfix
    r = live.bridge("read_notes", {"track": a["trigger_track"]}, wait)
    pitches = a.get("pitches") or [36, 35]
    hits = sorted(p["start"] + n["start"] for p in r.get("parts", []) if not p.get("muted")
                  for n in p["notes"] if n["pitch"] in pitches and not n.get("muted"))
    if a.get("start_bar") or a.get("end_bar"):
        from .compose import beats_per_bar
        per_bar = beats_per_bar(wait)
        lo = (a.get("start_bar", 1) - 1) * per_bar
        hi = (a["end_bar"] - 1) * per_bar if a.get("end_bar") else float("inf")
        hits = [h for h in hits if lo <= h < hi]
    if not hits:
        raise ToolError("no trigger notes (pitches %s) on %s" % (pitches, a["trigger_track"]))
    vol, _hint = mixfix._channel_volume(a["track"], wait)
    base = 20 * __import__("math").log10(vol) if vol else 0.0
    pts = duck_points(hits, base, a.get("depth_db", 6.0), a.get("attack_beats", 0.02), a.get("release_beats", 0.3))
    path = live.active_song_path()
    if not path:
        raise ToolError("save the song once first: automation is written into the song file")
    res = live.offline_edit(path, lambda p: automation.write_automation(p, a["track"], "volume", pts, True))
    res.update(hits=len(hits), base_db=round(base, 1), depth_db=a.get("depth_db", 6.0), points=len(pts))
    return res
