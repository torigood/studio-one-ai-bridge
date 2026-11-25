"""Drum parts by genre: grooves written as step strings, fills, crashes, humanize and swing.

A groove is one or two bars of 16th steps per voice:
    x hit   X accent   g ghost   . rest
    r roll (two 32nds in the step)   t triplet roll (three in the step)
Pitches come from a kit map (General MIDI by default: MT-PowerDrumKit, Impact XT GM kits and most drum
plug-ins follow it); map overrides single voices, e.g. {"clap": 40}.
"""
import random

from . import compose
from .common import ToolError

GM = {"kick": 36, "side": 37, "snare": 38, "clap": 39, "hat": 42, "pedal": 44, "open": 46, "tom_low": 45,
      "tom_mid": 47, "tom_high": 50, "tom_floor": 41, "crash": 49, "ride": 51, "rim": 37, "shaker": 70,
      "cowbell": 56, "tamb": 54}

VEL = {"x": 0.8, "X": 1.0, "g": 0.35, "r": 0.6, "t": 0.55}

# name: (voices, default swing, description)
GROOVES = {
    "pop": ({"kick": "x.......x.x.....", "snare": "....x.......x...", "hat": "x.x.x.x.x.x.x.x."}, 50,
            "straight 8th hats, backbeat"),
    "rock": ({"kick": "x.......xx......", "snare": "....X.......X...", "hat": "X.x.X.x.X.x.X.x."}, 50,
             "driving 8ths, accented backbeat"),
    "ballad": ({"kick": "x.......x.....x.", "side": "....x.......x...", "hat": "x...x...x...x..."}, 50,
               "quarter hats, side stick"),
    "funk": ({"kick": "x..x..x...x..x..", "snare": "....X..g.g..X..g", "hat": "xxxxxxxxxxxxxxxx"}, 54,
             "16th hats, ghost notes"),
    "house": ({"kick": "x...x...x...x...", "clap": "....x.......x...", "hat": "x.xxx.xxx.xxx.xx",
               "open": "..x...x...x...x."}, 52, "four on the floor, offbeat open hat"),
    "trap": ({"kick": "x......x..x.....x.....x...x.....", "clap": "........x...............x.......",
              "hat": "x.x.x.x.xrx.x.x.x.x.x.x.x.t.xrxr"}, 50, "half-time clap, hat rolls (two bars)"),
    "lofi": ({"kick": "x.....x...x.....", "snare": "....x.......x...", "hat": "x.x.x.xgx.x.x.xg"}, 58,
             "boom bap, swung"),
    "reggaeton": ({"kick": "x...x...x...x...", "snare": "...x..x....x..x.", "hat": "x.x.x.x.x.x.x.x."}, 50,
                  "dembow"),
    "dnb": ({"kick": "x.........x.....", "snare": "....x.......x...", "hat": "x.x.x.x.x.x.x.x."}, 50,
            "two-step (use at 160-175 BPM)"),
    "rnb": ({"kick": "x......x..x.....", "snare": "....x.......x...", "hat": "x.xxx.x.x.xxx.x.",
             "rim": "..........g....."}, 56, "laid back, swung 16ths"),
}

# Fills over the last two beats of a bar (8 steps): (voice, step, char)
FILLS = [
    [("snare", 0, "x"), ("snare", 1, "x"), ("snare", 2, "x"), ("snare", 3, "X"),
     ("tom_high", 4, "x"), ("tom_high", 5, "x"), ("tom_low", 6, "x"), ("tom_floor", 7, "X"), ("kick", 0, "x")],
    [("tom_high", 0, "x"), ("tom_high", 1, "x"), ("tom_mid", 2, "x"), ("tom_mid", 3, "x"),
     ("tom_low", 4, "x"), ("tom_low", 5, "x"), ("tom_floor", 6, "X"), ("tom_floor", 7, "X"), ("kick", 4, "x")],
    [("snare", 0, "g"), ("snare", 1, "x"), ("snare", 2, "g"), ("snare", 3, "x"),
     ("snare", 4, "x"), ("snare", 5, "x"), ("snare", 6, "X"), ("snare", 7, "X"), ("kick", 0, "x"), ("kick", 6, "x")],
]


def describe():
    return {g: v[2] for g, v in GROOVES.items()}


def _hits(voice, chars, bar_start, steps, step_len):
    out = []
    for i, ch in enumerate(chars[:steps]):
        b = bar_start + i * step_len
        if ch in "xXg":
            out.append((voice, b, step_len, VEL[ch]))
        elif ch == "r":
            out += [(voice, b + k * step_len / 2, step_len / 2, VEL[ch] + 0.1 * k) for k in range(2)]
        elif ch == "t":
            out += [(voice, b + k * step_len / 3, step_len / 3, VEL[ch] + 0.08 * k) for k in range(3)]
        elif ch != ".":
            raise ToolError("unknown step %r in %s pattern" % (ch, voice))
    return out


def generate(genre, bars, per_bar, start=0.0, fill_every=4, crash=True, voices=None, groove=None, seed=0,
             velocity=1.0):
    """Hits [(voice, beat, length, velocity)] for bars of a genre (or groove {voice: steps})."""
    if groove is None:
        if genre not in GROOVES:
            raise ToolError("genre must be one of %s (or pass groove)" % ", ".join(GROOVES))
        groove = GROOVES[genre][0]
    rng = random.Random(seed)
    step_len = 0.25
    steps = int(round(per_bar / step_len))
    length = max(len(p) for p in groove.values())
    groove_bars = max(1, length // 16)
    hits = []
    for bar in range(bars):
        b0 = start + bar * per_bar
        fill = fill_every and (bar + 1) % fill_every == 0 and steps >= 8
        fill_from = steps - 8 if fill else steps
        for voice, pat in groove.items():
            if voices and voice not in voices:
                continue
            seg = pat[(bar % groove_bars) * 16:(bar % groove_bars) * 16 + 16] if len(pat) > 16 else pat
            seg = (seg * (steps // len(seg) + 1))[:steps]  # other meters: repeat/cut the bar
            keep = seg[:fill_from] + "." * (steps - fill_from)  # the fill replaces the last two beats
            hits += _hits(voice, keep, b0, steps, step_len)
        if fill:
            for voice, i, ch in rng.choice(FILLS):
                if voices and voice not in voices:
                    continue
                v = VEL[ch] * (0.75 + 0.25 * i / 7)  # crescendo
                hits.append((voice, b0 + (fill_from + i) * step_len, step_len, v))
        if crash and (bar == 0 or (fill_every and bar % fill_every == 0)) and (not voices or "crash" in voices):
            hits.append(("crash", b0, per_bar / 2, 0.9))
    return [(v, b, l, max(0.05, min(1.0, vel * velocity))) for v, b, l, vel in hits]


def write_drums(a, wait):
    """daw_write_drums."""
    per_bar = compose.beats_per_bar(wait)
    if a.get("groove"):
        lens = {len(p) for p in a["groove"].values()}
        if not lens <= {16, 32}:
            raise ToolError("groove steps must be 16 (one bar) or 32 (two bars) long")
    start = (a.get("start_bar", 1) - 1) * per_bar
    bars = int(a.get("bars", 8))
    if bars < 1:
        raise ToolError("bars must be >= 1")
    kit = dict(GM, **(a.get("map") or {}))
    hits = generate(a.get("genre", "pop"), bars, per_bar, start, a.get("fill_every", 4), a.get("crash", True),
                    a.get("voices"), a.get("groove"), a.get("seed", 0), a.get("velocity", 1.0))
    missing = sorted({v for v, *_ in hits if v not in kit})
    if missing:
        raise ToolError("no pitch for %s: pass map {voice: pitch}" % ", ".join(missing))
    notes = [{"pitch": kit[v], "beat": b, "length": l, "velocity": round(vel, 3)} for v, b, l, vel in hits]
    swing = a.get("swing", GROOVES.get(a.get("genre", "pop"), (None, 50))[1] if not a.get("groove") else 50)
    humanize = a.get("humanize", 0.3)
    if swing != 50 or humanize:
        rel = [dict(n, index=i, start=n["beat"]) for i, n in enumerate(notes)]
        for e in compose.transform(rel, 0.0, swing=swing if swing != 50 else None, swing_grid=0.25,
                                   humanize=humanize, seed=a.get("seed", 0)):
            n = notes[e["index"]]
            n["beat"] = max(e.get("start", n["beat"]), start)
            n["velocity"] = e.get("velocity", n["velocity"])
    notes.sort(key=lambda n: n["beat"])
    out = compose._insert(a["track"], start, start + bars * per_bar, notes, a.get("replace", True), wait)
    if isinstance(out, dict) and "notes" in out:
        out.update(genre=a.get("genre", "pop") if not a.get("groove") else "custom", bars=bars, swing=swing,
                   fills=bars // a.get("fill_every", 4) if a.get("fill_every", 4) else 0)
        if per_bar != 4:
            out["note"] = "grooves are written for 4/4; in %g/4 each bar repeats or cuts the 16-step pattern" % per_bar
    return out
