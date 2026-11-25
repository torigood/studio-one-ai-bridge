"""daw_mix_fix: measure the stems (daw_mix_check), then fix the plain problems and measure again.

  gain     if a stem peaks above target_peak_db, every stem is turned down by the same amount (channel
           volume), so the loudest peaks at the target and the balance stays
  masking  for the strongest overlaps, the less important stem gets a Pro EQ cut (peaking band, Q 1.4) at
           that octave; importance = order in priority (first = most important), else below 250 Hz the stem
           named like bass/kick/808/sub keeps its low end, else the louder stem keeps the band
A snapshot is taken first (daw_snapshot restore undoes everything). apply=false only returns the plan.
"""
import math
import re

from . import analysis_tools, live, snapshots, sound
from .common import ToolError

PRO_EQ = "{073C4094-E062-4FB5-8328-74608DD1A3A4}"
LOW_OWNERS = re.compile(r"bass|kick|808|sub|bd\b", re.I)


def _band(freq):
    """Pro EQ peaking band for a frequency (band ranges from the plug-in: lmf 55-2449, mf 485-8296,
    hmf 1213-11667 Hz)."""
    if freq <= 400:
        return "lmf"
    if freq <= 2500:
        return "mf"
    return "hmf"


def plan(check, priority=None, max_cuts=3, cut_db=3.0, target_peak_db=-6.0):
    files = check["files"]
    rank = {str(n): i for i, n in enumerate(priority or [])}
    steps = []
    peaks = {n: f["loudness"]["peak_dbfs"] for n, f in files.items() if not f.get("silent")}
    if peaks and max(peaks.values()) > target_peak_db + 0.5:
        # every stem by the same amount: the balance stays (levelling each to its own peak changed it and
        # undid an EQ cut in a live test, 2026-11-25)
        top = max(peaks.values())
        for name in peaks:
            steps.append({"action": "gain", "track": name, "db": round(target_peak_db - top, 1),
                          "why": "loudest stem peaks at %.1f dBFS" % top})
    cuts = set()
    for h in check.get("masking", []):
        if len([s for s in steps if s["action"] == "eq_cut"]) >= max_cuts:
            break
        a, b = h["stems"]
        if a in rank or b in rank:
            keep = min((a, b), key=lambda n: rank.get(n, len(rank)))
        elif h["band_hz"] <= 250 and bool(LOW_OWNERS.search(a)) != bool(LOW_OWNERS.search(b)):
            keep = a if LOW_OWNERS.search(a) else b
        else:
            keep = h["louder"]
        cut = b if keep == a else a
        band = _band(h["band_hz"])
        if (cut, band) in cuts:
            continue
        cuts.add((cut, band))
        steps.append({"action": "eq_cut", "track": cut, "freq_hz": h["band_hz"], "band": band, "db": -cut_db,
                      "why": "overlaps %s at %s Hz (%.1f dB apart)" % (keep, h["band_hz"], h["difference_db"])})
    return steps


def _channel_volume(track, wait):
    hint = live.instrument_channel_hint(track, wait)
    for c in live.bridge("list_mixer", {}, wait).get("channels", []):
        if (hint and c["name"] == hint and c["channelType"] == "AudioSynth") or (not hint and c["label"] == track):
            return c["params"].get("volume"), hint
    return None, hint


def _apply(step, wait):
    t = step["track"]
    if step["action"] == "gain":
        vol, hint = _channel_volume(t, wait)
        if vol is None:
            raise ToolError("no mixer channel found for %s" % t)
        new = max(vol * 10 ** (step["db"] / 20.0), 1e-4)
        args = {"track": t, "param": "volume", "value": new}
        if hint:
            args["instrument_channel"] = hint
        live.bridge("set_channel_param", args, wait)
        return {"volume_db": round(20 * math.log10(new), 1)}
    fx = live.bridge("list_effects", _fx_args(t, wait), wait)
    eq = next((e for e in fx.get("effects", []) if "Pro EQ" in (e.get("plugin") or "")), None)
    if eq is None:
        live.bridge("insert_effect", dict(_fx_args(t, wait), class_id=PRO_EQ), wait)
        fx = live.bridge("list_effects", _fx_args(t, wait), wait)
        eq = next((e for e in fx.get("effects", []) if "Pro EQ" in (e.get("plugin") or "")), None)
        if eq is None:
            raise ToolError("could not insert Pro EQ on %s: %s" % (t, fx))
    slot, band = eq["slot"], step["band"]
    for param, display in ((band + "active", "1"), (band + "freq", sound.hz(step["freq_hz"])),
                           (band + "gain", "%g" % step["db"]), (band + "q", "1.4")):
        live.bridge("plugin_param", dict(_fx_args(t, wait), slot=slot, param=param, display=display), wait)
    return {"eq_slot": slot}


def _fx_args(track, wait):
    args = {"track": track}
    hint = live.instrument_channel_hint(track, wait)
    if hint:
        args["instrument_channel"] = hint
    return args


def mix_fix(a, wait):
    tracks = a.get("tracks")
    if not tracks:
        raise ToolError("pass tracks (instrument tracks to check and fix)")
    before = analysis_tools.daw_mix_check(tracks, None, wait)
    steps = plan(before, a.get("priority"), a.get("max_cuts", 3), a.get("cut_db", 3.0), a.get("target_peak_db", -6.0))
    out = {"plan": steps, "before": _summary(before)}
    if not a.get("apply", True) or not steps:
        if not steps:
            out["note"] = "nothing to fix: no stem peaks above target and no strong overlaps"
        return out
    try:
        out["snapshot"] = snapshots.take("before mix fix")["snapshot"]
    except ToolError as e:
        out["snapshot_warning"] = "no snapshot taken (%s)" % e
    for s in steps:
        s.update(_apply(s, wait))
    # The bounce leaves insert effects out, so the cuts are simulated on the re-bounced (re-levelled) stems.
    eqs = {}
    for s in steps:
        if s["action"] == "eq_cut":
            eqs.setdefault(str(s["track"]), []).append((s["freq_hz"], s["db"], 1.4))
    after = analysis_tools.daw_mix_check(tracks, None, wait, eqs)
    out["after"] = _summary(after)
    if eqs:
        out["after"]["note"] = "EQ cuts are estimated (Studio One's bounce leaves insert effects out); gain is measured"
    return out


def _summary(check):
    return {"stems": {n: ({"silent": True} if f.get("silent") else
                          {"lufs": f["loudness"]["lufs"], "peak_dbfs": f["loudness"]["peak_dbfs"]})
                      for n, f in check["files"].items()},
            "masking": [{k: h[k] for k in ("band_hz", "stems", "difference_db", "louder")}
                        for h in check.get("masking", [])[:5]]}
