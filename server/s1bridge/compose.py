"""Composing helpers built on the note tools: generate parts from chords (bass, pad, arpeggio, stabs),
transform the notes of a part (quantize, swing, humanize, transpose, velocity) and copy bars between
places. The music is worked out here; the bridge only gets insert_notes / edit_notes / delete_event jobs,
so none of this needs a bridge.js change.

Times: beat = quarter notes from song start (absolute), as everywhere else. Velocity 0..1.
"""
import itertools
import random

from . import live
from .chords import parse_chord
from .common import ToolError
from .songwrite import SongWriteError

STYLES = ("bass", "pad", "arp", "stabs")


# ---- chords ---------------------------------------------------------------------------------------

def _pc(fifths):
    """Circle-of-fifths position (chords.parse_chord) -> pitch class 0..11."""
    return (fifths * 7) % 12


def chord_tones(name):
    """'Am7' -> (root pc, [pitch classes in chord order], bass pc)."""
    try:
        root, intervals, bass = parse_chord(name)
    except SongWriteError as e:
        raise ToolError(str(e))
    r = _pc(root)
    return r, [(r + i) % 12 for i in intervals], _pc(bass) if bass is not None else r


def progression(text, start_beat, beats_per_chord):
    """'Am F C G' (or a list of names) -> [{beat, length, chord}]. '%' or '-' repeats the last chord."""
    names = text.split() if isinstance(text, str) else list(text)
    out = []
    for name in names:
        if name in ("%", "-") and out:
            out[-1]["length"] += beats_per_chord
            continue
        out.append({"beat": start_beat + sum(c["length"] for c in out), "length": float(beats_per_chord),
                    "chord": name})
    if not out:
        raise ToolError("empty progression")
    return out


# ---- voicing --------------------------------------------------------------------------------------

def _nearest(pc, center):
    """MIDI pitch of pitch class pc closest to center."""
    p = center - ((center - pc) % 12)
    return p + 12 if center - p > 6 else p


def _voicings(pcs, center, voices):
    """Close-position voicings of the chord (every inversion) placed near center."""
    tones = list(dict.fromkeys(pcs))
    if len(tones) > voices:  # drop the fifth first, then the root (a pad keeps the colour tones)
        fifth = (tones[0] + 7) % 12
        if fifth in tones[1:]:
            tones.remove(fifth)
        while len(tones) > voices:
            tones.pop(0)
    pool = [p for p in range(center - 10, center + 13) if p % 12 in tones]
    out = []
    for combo in itertools.combinations(pool, voices):
        # every chord tone present, spread at most an octave and a fifth, no seconds below C3 (mud)
        if set(p % 12 for p in combo) != set(tones) or combo[-1] - combo[0] > 19:
            continue
        if any(b - a < 3 and a < 48 for a, b in zip(combo, combo[1:])):
            continue
        out.append(list(combo))
    return out or [sorted(_nearest(pc, center) for pc in tones)]


def voice_lead(chords, center=60, voices=4):
    """One voicing per chord: the first nearest center, then each the one with the least total movement."""
    result, prev = [], None
    for c in chords:
        cands = _voicings(chord_tones(c["chord"])[1], center, voices)
        if prev is None:
            best = min(cands, key=lambda v: abs(sum(v) / len(v) - center) + 0.1 * (v[-1] - v[0]))
        else:
            best = min(cands, key=lambda v: sum(abs(a - b) for a, b in zip(v, prev)) +
                       0.3 * abs(sum(v) / len(v) - center) + 0.1 * (v[-1] - v[0]))
        result.append(best)
        prev = best
    return result


# ---- generating -----------------------------------------------------------------------------------

def _steps(c, rate):
    """Start beats inside chord c every rate quarter notes."""
    n = max(int(round(c["length"] / rate)), 1)
    return [c["beat"] + i * rate for i in range(n)]


def generate(style, chords, rate=None, octave=None, voices=4, velocity=0.8, direction="up", pattern=None,
             gate=0.9, accent=0.1, seed=0):
    """Notes [{pitch, beat (absolute), length, velocity}] for a style over chords [{beat, length, chord}].

    bass:  pattern 'root' (one note per rate, default a note per chord), 'root_fifth', 'octave', 'walk'.
    pad:   voice-led chords held for each chord (rate re-strikes them).
    arp:   chord tones over two octaves, direction up/down/updown/random, rate default 0.25 (16ths).
    stabs: short voice-led chords every rate (default 1 beat).
    """
    if style not in STYLES:
        raise ToolError("style must be one of %s" % ", ".join(STYLES))
    rng = random.Random(seed)
    notes = []

    def add(pitch, beat, length, vel):
        notes.append({"pitch": int(max(0, min(127, pitch))), "beat": beat, "length": length,
                      "velocity": round(max(0.05, min(1.0, vel)), 3)})

    if style == "bass":
        base = 12 * ((2 if octave is None else octave) + 1)  # octave 2: C2 = 36; notes stay near G1..G2
        pattern = pattern or "root"
        carry = None  # walk: the next root as the approach note aimed at it
        for i, c in enumerate(chords):
            root, pcs, bass = chord_tones(c["chord"])
            low = base + (bass - base) % 12
            if low > base + 7:
                low -= 12
            if carry is not None and carry % 12 == bass:
                low = carry
            carry = None
            fifth = low + 7
            nxt = chords[i + 1] if i + 1 < len(chords) else None
            step = rate or (c["length"] if pattern == "root" else 0.5 if pattern == "octave" else 1.0)
            starts = _steps(c, step)
            prev = low
            for k, b in enumerate(starts):
                if pattern == "root_fifth":
                    p = low if k % 2 == 0 else fifth
                elif pattern == "octave":
                    p = low if k % 2 == 0 else low + 12
                elif pattern == "walk":
                    if k == len(starts) - 1 and nxt and len(starts) > 1:
                        # approach the next root by a half step, from the side of the note before
                        pc = chord_tones(nxt["chord"])[2]
                        target = min((prev + d for d in range(-11, 12) if (prev + d) % 12 == pc),
                                     key=lambda t: (abs(t - prev), t))
                        while target < base - 8:
                            target += 12
                        while target > base + 12:
                            target -= 12
                        p = target - 1 if prev < target else target + 1
                        if p == prev:
                            p = target + 1 if p < target else target - 1
                        carry = target
                    else:
                        third = low + ((pcs[1] - bass) % 12 if len(pcs) > 1 else 4)
                        if fifth > base + 12:  # high root: walk down instead (fifth and third below)
                            p = [low, fifth - 12, third - 12, fifth - 12][k % 4]
                        else:
                            p = [low, third, fifth, third][k % 4]
                else:
                    p = low
                prev = p
                on_beat = abs(b - round(b)) < 1e-6
                add(p, b, step * gate, velocity + (accent if on_beat and k % 2 == 0 else 0))
    elif style in ("pad", "stabs"):
        center = 12 * ((4 if octave is None else octave) + 1)  # octave 4: around C4 = 60
        voicings = voice_lead(chords, center, voices)
        for c, v in zip(chords, voicings):
            step = rate or (c["length"] if style == "pad" else 1.0)
            for k, b in enumerate(_steps(c, step)):
                length = step * (0.98 if style == "pad" else min(gate, 0.5))
                for p in v:
                    add(p, b, length, velocity + (accent if k == 0 and step < c["length"] else 0))
    else:  # arp
        center = 12 * ((4 if octave is None else octave) + 1)
        step = rate or 0.25
        voicings = voice_lead(chords, center, min(voices, 4))
        for c, v in zip(chords, voicings):
            tones = sorted(set(v)) + [p + 12 for p in sorted(set(v))]
            if direction == "down":
                seq = tones[::-1]
            elif direction == "updown":
                seq = tones + tones[-2:0:-1]
            else:
                seq = tones
            for k, b in enumerate(_steps(c, step)):
                p = rng.choice(tones) if direction == "random" else seq[k % len(seq)]
                add(p, b, step * gate, velocity + (accent if abs(b - round(b)) < 1e-6 else 0))
    return notes


# ---- transforming ---------------------------------------------------------------------------------

def transform(notes, part_start=0.0, quantize=None, strength=1.0, swing=None, swing_grid=0.5, humanize=0.0,
              transpose=0, velocity_scale=1.0, velocity_add=0.0, seed=0):
    """New values for notes [{index, pitch, velocity, start, length}] (start relative to a part at
    part_start). Returns [{index, start?, length?, pitch?, velocity?}] with only what changed.

    quantize: grid in quarter notes (0.25 = 16ths), strength 0..1 moves part of the way.
    swing: 50 (straight) .. 75; delays notes on the off-beat of each swing_grid pair (66 = triplet feel).
    humanize: 0..1, random timing (up to 0.03 beat at 1) and velocity (up to 0.1) changes; seeded.
    """
    rng = random.Random(seed)
    edits = []
    for n in notes:
        start = n["start"]
        absolute = part_start + start
        if quantize:
            target = round(absolute / quantize) * quantize
            absolute += (target - absolute) * strength
        if swing is not None and swing != 50:
            pair = 2 * swing_grid
            pos = absolute % pair
            if abs(pos - swing_grid) < swing_grid * 0.25:  # on (or near) the off-beat
                absolute += swing / 100.0 * pair - swing_grid
        if humanize:
            absolute += rng.uniform(-0.03, 0.03) * humanize
        new_start = max(absolute - part_start, 0.0)
        vel = n["velocity"] * velocity_scale + velocity_add
        if humanize:
            vel += rng.uniform(-0.1, 0.1) * humanize
        vel = round(max(0.01, min(1.0, vel)), 3)
        pitch = n["pitch"] + transpose
        if not 0 <= pitch <= 127:
            raise ToolError("transpose puts note %d out of range" % n["pitch"])
        e = {"index": n["index"]}
        if abs(new_start - start) > 1e-6:
            e["start"] = round(new_start, 6)
        if pitch != n["pitch"]:
            e["pitch"] = pitch
        if abs(vel - n["velocity"]) > 1e-4:
            e["velocity"] = vel
        if len(e) > 1:
            edits.append(e)
    return edits


# ---- live -----------------------------------------------------------------------------------------

def beats_per_bar(wait):
    info = live.bridge("transport", {}, wait)["before"]
    return int(info.get("timeSignatureNum", 4)) * 4.0 / int(info.get("timeSignatureDenom", 4))


def _clear_range(track, lo, hi, wait):
    """Delete the instrument parts that lie inside [lo, hi). Returns (deleted starts, warnings)."""
    deleted, warnings = [], []
    for e in live.list_events(track, wait):
        if e.get("mediaType") != "Music":
            continue
        s, end = e["start"], e["start"] + e["length"]
        if s >= lo - 1e-6 and end <= hi + 1e-6:
            live.bridge("delete_event", {"track": track, "start_beats": s}, wait)
            deleted.append(s)
        elif s < hi and end > lo:
            warnings.append("part at beat %s overlaps the range and was kept" % s)
    return deleted, warnings


def _insert(track, lo, hi, notes, replace, wait):
    deleted, warnings = _clear_range(track, lo, hi, wait) if replace else ([], [])
    r = live.bridge("insert_notes", {"track": track, "start_beats": lo, "length_beats": hi - lo, "notes": [
        {"pitch": n["pitch"], "start_beats": round(n["beat"] - lo, 6), "length_beats": round(n["length"], 6),
         "velocity": n["velocity"]} for n in notes]}, wait)
    if isinstance(r, dict) and r.get("status") == "not_run":
        return r
    out = {"track": track, "part_start": lo, "part_length": hi - lo, "notes": len(notes)}
    if deleted:
        out["replaced_parts"] = deleted
    if warnings:
        out["warnings"] = warnings
    return out


def write_pattern(a, wait):
    """daw_write_pattern."""
    if a.get("chords"):
        chords = [{"beat": float(c["beat"]), "length": float(c["length"]), "chord": c["chord"]}
                  for c in sorted(a["chords"], key=lambda c: c["beat"])]
    elif a.get("progression"):
        per_bar = beats_per_bar(wait)
        once = progression(a["progression"], (a.get("start_bar", 1) - 1) * per_bar,
                           a.get("bars_per_chord", 1) * per_bar)
        span = sum(c["length"] for c in once)
        chords = [dict(c, beat=c["beat"] + r * span) for r in range(max(int(a.get("repeat", 1)), 1)) for c in once]
    else:  # the Chord Track
        r = live.bridge("read_chords", {}, wait)
        per_bar = beats_per_bar(wait) if a.get("start_bar") or a.get("end_bar") else None
        lo = (a["start_bar"] - 1) * per_bar if a.get("start_bar") else float("-inf")
        hi = (a["end_bar"] - 1) * per_bar if a.get("end_bar") else float("inf")
        chords = [{"beat": max(c["start"], lo), "length": min(c["start"] + c["length"], hi) - max(c["start"], lo),
                   "chord": c["name"]} for c in r.get("chords", [])
                  if c.get("name") and c["start"] < hi and c["start"] + c["length"] > lo]
        if not chords:
            raise ToolError("no chords: pass chords or progression, or write a Chord Track (song_write_chords)")
    notes = generate(a["style"], chords, a.get("rate"), a.get("octave"), a.get("voices", 4), a.get("velocity", 0.8),
                     a.get("direction", "up"), a.get("pattern"), a.get("gate", 0.9), a.get("accent", 0.1),
                     a.get("seed", 0))
    if a.get("humanize"):
        rel = [dict(n, index=i, start=n["beat"]) for i, n in enumerate(notes)]
        for e in transform(rel, 0.0, humanize=a["humanize"], seed=a.get("seed", 0)):
            n = notes[e["index"]]
            n["beat"] = e.get("start", n["beat"])
            n["velocity"] = e.get("velocity", n["velocity"])
    lo = chords[0]["beat"]
    hi = max(c["beat"] + c["length"] for c in chords)
    out = _insert(a["track"], lo, hi, notes, a.get("replace", True), wait)
    if isinstance(out, dict) and "notes" in out:
        out["chords"] = [c["chord"] for c in chords]
    return out


def transform_notes(a, wait):
    """daw_transform_notes: read the parts, work out edits, send one edit_notes job per part."""
    r = live.bridge("read_notes", {k: a[k] for k in ("track", "part_start") if k in a}, wait)
    if isinstance(r, dict) and r.get("status") == "not_run":
        return r
    parts = r.get("parts", [])
    if not parts:
        raise ToolError("no instrument parts on %s" % a["track"])
    opts = {k: a[k] for k in ("quantize", "strength", "swing", "swing_grid", "humanize", "transpose",
                              "velocity_scale", "velocity_add", "seed") if k in a}
    if a.get("pitch_min") is not None or a.get("pitch_max") is not None:
        lo, hi = a.get("pitch_min", 0), a.get("pitch_max", 127)
    else:
        lo, hi = 0, 127
    done = []
    for p in parts:
        notes = [n for n in p["notes"] if lo <= n["pitch"] <= hi]
        edits = transform(notes, p["start"], **opts)
        if edits:
            live.bridge("edit_notes", {"track": a["track"], "part_start": p["start"], "edits": edits}, wait)
        done.append({"part_start": p["start"], "notes": len(notes), "changed": len(edits)})
    return {"track": r.get("track", a["track"]), "parts": done}


def copy_bars(a, wait):
    """daw_copy_bars: copy the notes of instrument tracks in bars [from_bar, to_bar) to dest_bar."""
    per_bar = beats_per_bar(wait)
    lo, hi = (a["from_bar"] - 1) * per_bar, (a["to_bar"] - 1) * per_bar
    if hi <= lo:
        raise ToolError("to_bar must be after from_bar (end exclusive: 1 to 5 copies bars 1-4)")
    dest = (a["dest_bar"] - 1) * per_bar
    if lo < dest + (hi - lo) and dest < hi:
        raise ToolError("the destination overlaps the source bars")
    shift = dest - lo
    tracks = live.list_tracks(wait)
    wanted = a.get("tracks")
    if wanted:
        tracks = [t for t in tracks if t["name"] in wanted or t["index"] in wanted]
        missing = [w for w in wanted if not any(w in (t["name"], t["index"]) for t in tracks)]
        if missing:
            raise ToolError("tracks not found: %s" % missing)
    out, skipped = [], []
    for t in tracks:
        if t.get("mediaType") != "Music":
            if wanted or t.get("mediaType") == "Audio":
                skipped.append(t["name"])
            continue
        r = live.bridge("read_notes", {"track": t["name"]}, wait)
        notes = []
        for p in r.get("parts", []):
            if p.get("muted"):
                continue
            for n in p["notes"]:
                b = p["start"] + n["start"]
                if lo - 1e-6 <= b < hi - 1e-6 and not n.get("muted"):
                    notes.append({"pitch": n["pitch"], "beat": b + shift, "velocity": n["velocity"],
                                  "length": min(n["length"], hi - b)})
        if not notes:
            continue
        out.append(_insert(t["name"], dest, dest + hi - lo, notes, a.get("replace", True), wait))
    res = {"from": [lo, hi], "dest": dest, "tracks": out}
    if skipped:
        res["skipped"] = skipped
        res["note"] = "audio and other non-instrument tracks are not copied (copy their events in Studio One)"
    return res
