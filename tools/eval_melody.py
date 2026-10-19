#!/usr/bin/env python3
"""Score audio_melody engines against reference notes (e.g. a Melodyne-to-MIDI track) in a saved song.

    <analysis venv>/bin/python tools/eval_melody.py SONG --audio TRACK --ref TRACK
        [--from-bar N] [--to-bar M] [--engine crepe ...] [--grid 0] [--show]

Needs mir_eval in the analysis venv (pip install mir_eval; MIT). Reference notes outside the range
are ignored. Scores (mir_eval.transcription, standard MIREX settings):
  onset      note F1: same pitch (+-50 cents) and onset within +-50 ms (MIREX), and within +-150 ms
             (for a hand-made reference whose timing is only roughly right)
  pitch only share of reference notes overlapping an estimate of the same pitch (frame-free check of
             wrong notes vs wrong timing)
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

import mir_eval  # noqa: E402

from s1bridge import notegrid, tools  # noqa: E402
from s1bridge.song import Song  # noqa: E402


def ref_notes(song, track, lo, hi, tempo):
    with Song(song) as s:
        t = tools._find_track(s.tracks(include_notes=True), track)
    out = []
    for p in t["parts"]:
        if p.get("muted"):
            continue
        for n in p.get("notes", []):
            a = notegrid.beat_to_sec(tempo, n["start_beats"])
            b = notegrid.beat_to_sec(tempo, n["start_beats"] + n["length_beats"])
            if a >= lo and (hi is None or a < hi):
                out.append((a, b, n["pitch"]))
    return sorted(out)


def arrays(notes):
    if not notes:
        return np.zeros((0, 2)), np.zeros(0)
    iv = np.array([[a, max(b, a + 0.01)] for a, b, _ in notes])
    return iv, np.array([440.0 * 2 ** ((p - 69) / 12.0) for _, _, p in notes])


def pitch_only(ref, est):
    hit = sum(1 for a, b, p in ref if any(q == p and x < b and y > a for x, y, q in est))
    return hit / len(ref) if ref else 0.0


def score(ref, est, label):
    ri, rp = arrays(ref)
    ei, ep = arrays(est)
    f = {}
    for tol in (0.05, 0.15):
        f[tol] = mir_eval.transcription.precision_recall_f1_overlap(ri, rp, ei, ep, onset_tolerance=tol,
                                                                    offset_ratio=None)
    print("  %-11s ref %3d est %3d | onset 50ms P %.2f R %.2f F1 %.2f | onset 150ms F1 %.2f | pitch only %.2f"
          % (label, len(ref), len(est), f[0.05][0], f[0.05][1], f[0.05][2], f[0.15][2], pitch_only(ref, est)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--from-bar", type=int, default=1)
    ap.add_argument("--to-bar", type=int)
    ap.add_argument("--engine", nargs="+", default=["crepe"])
    ap.add_argument("--grid", type=float, default=0.0)
    ap.add_argument("--by-bars", type=int, help="also score every N bars")
    ap.add_argument("--show", action="store_true", help="list reference vs estimate notes")
    a = ap.parse_args()
    with Song(a.song) as s:
        tempo, sigs = s.tempo_map(), s.time_signatures()
    lo = notegrid.beat_to_sec(tempo, notegrid.bar_start(sigs, a.from_bar))
    hi = notegrid.beat_to_sec(tempo, notegrid.bar_start(sigs, a.to_bar + 1)) if a.to_bar else None
    ref = ref_notes(a.song, a.ref, lo, hi, tempo)
    print("reference: %d notes" % len(ref))
    for eng in a.engine:
        r = tools._audio_melody({"song": a.song, "track": a.audio, "from_bar": a.from_bar, "to_bar": a.to_bar,
                                 "grid": a.grid, "engine": eng}, 10)
        est = [(notegrid.beat_to_sec(tempo, n["beat"]), notegrid.beat_to_sec(tempo, n["beat"] + n["len"]),
                n["pitch"]) for n in r["notes"]]
        print("%-12s notes %3d (reference %d)" % (eng, len(est), len(ref)))
        score(ref, est, "all")
        if a.by_bars:
            bar = a.from_bar
            last = a.to_bar or notegrid.bar_pos(sigs, notegrid.sec_to_beat(tempo, ref[-1][1]))[0]
            while bar <= last:
                b0 = notegrid.beat_to_sec(tempo, notegrid.bar_start(sigs, bar))
                b1 = notegrid.beat_to_sec(tempo, notegrid.bar_start(sigs, bar + a.by_bars))
                inside = lambda ns: [n for n in ns if b0 <= n[0] < b1]
                if inside(ref):
                    score(inside(ref), inside(est), "bars %d-%d" % (bar, bar + a.by_bars - 1))
                bar += a.by_bars
        if a.show:
            for x, y, p in ref:
                m = [q for u, v, q in est if u < y and v > x]
                bar, pos = notegrid.bar_pos(sigs, notegrid.sec_to_beat(tempo, x))
                print("  %3d %5.2f  ref %-4s est %s" % (bar, pos, notegrid.note_name(p),
                                                       " ".join(notegrid.note_name(q) for q in m) or "-"))


if __name__ == "__main__":
    main()
