"""Melody (monophonic) notes from a WAV file. Runs in the analysis pack's Python:
    python -m s1bridge.melody <file.wav> <start_sec> <length_sec|-> <engine>
prints {"engine", "notes": [{start, end, pitch, cents, confidence}], "unsure": [[start, end]]} with
times in seconds from the start of the file.

f0 engines give a pitch per 10 ms frame; notes() cuts that into notes:
  voiced     confidence >= VOICED and loud enough (within 45 dB of the loudest frame)
  smoothing  a 110 ms moving average of the pitch hides vibrato (5-7 Hz) from the note splitter
  splits     an unvoiced gap, the smoothed pitch settling over half a semitone away for 60 ms,
             or a jump in loudness on the same pitch (a re-sung note)
  pitch      median of the raw frames, rounded; cents = how far the singer was from it
"unsure" lists stretches that are loud but have no confident pitch (breath, noise, chords),
worth checking by ear or in Melodyne.
"""
import json
import os
import sys

import numpy as np

from . import engines
from .audio_analysis import read_wav

VOICED = 0.5
MIN_NOTE = 0.08  # s
SPLIT_SEMITONES = 0.5
SPLIT_HOLD = 6  # frames (60 ms) the pitch must stay away before a split
ONSET_DB = 6.0


def _moving(x, n):
    if len(x) < n:
        return np.full(len(x), np.median(x)) if len(x) else x
    k = np.ones(n) / n
    pad = n // 2
    return np.convolve(np.pad(x, (pad, n - 1 - pad), mode="edge"), k, mode="valid")


def _frame_db(samples, sr, times, win=0.032):
    h = int(win * sr / 2)
    c = (np.asarray(times) * sr).astype(int)
    sq = np.concatenate([[0.0], np.cumsum(samples.astype(np.float64) ** 2)])
    lo, hi = np.clip(c - h, 0, len(samples)), np.clip(c + h, 0, len(samples))
    ms = (sq[hi] - sq[lo]) / np.maximum(hi - lo, 1)
    return 10 * np.log10(ms + 1e-12)


def _runs(mask):
    """[(i, j)] index ranges where mask is True."""
    d = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def _split_pitch(midi, i, j):
    """Cut frames i..j where the smoothed pitch settles away from the current note. The cut goes
    where the raw pitch first gets closer to the new note than to the old one."""
    smooth = _moving(midi[i:j], 11)
    raw = midi[i:j]
    cuts, ref, away = [0], float(np.median(raw[:SPLIT_HOLD])), 0
    for k in range(len(smooth)):
        if abs(smooth[k] - ref) <= SPLIT_SEMITONES:
            away = 0
            continue
        away += 1
        if away < SPLIT_HOLD:
            continue
        first = k - SPLIT_HOLD + 1
        new = float(np.median(raw[first:k + 1]))
        lo = max(first - 8, cuts[-1] + 1)
        cut = next((m for m in range(lo, k + 1) if abs(raw[m] - new) < abs(raw[m] - ref)), first)
        # a cut that leaves the same note on both sides (vibrato, a slide's start) is no cut
        if cut - cuts[-1] >= SPLIT_HOLD and round(float(np.median(raw[cuts[-1]:cut]))) != round(new):
            cuts.append(cut)
        ref, away = new, 0
    return [(i + a, i + b) for a, b in zip(cuts, cuts[1:] + [j - i])]


def _absorb_fragments(found, shortest=0.12):
    """Fold a very short note that touches a neighbour (a slide or a scoop) into the closer-pitched one."""
    out = list(found)
    k = 0
    while k < len(out):
        n = out[k]
        prev = out[k - 1] if k and abs(out[k - 1]["end"] - n["start"]) < 0.015 else None
        nxt = out[k + 1] if k + 1 < len(out) and abs(out[k + 1]["start"] - n["end"]) < 0.015 else None
        if n["end"] - n["start"] >= shortest or not (prev or nxt):
            k += 1
            continue
        pick = min((m for m in (prev, nxt) if m), key=lambda m: abs(m["pitch"] - n["pitch"]))
        if pick is prev:
            prev["end"] = n["end"]
        else:
            nxt["start"] = n["start"]
        del out[k]
    return out


def _split_onsets(db, i, j, min_frames):
    """Cut a same-pitch segment where loudness jumps (a repeated note sung again)."""
    cuts = [i]
    for k in range(i + 3, j - 2):
        if db[k] - db[k - 3:k].min() >= ONSET_DB and db[k - 3:k].min() < db[i:j].max() - 6:
            if k - cuts[-1] >= min_frames and j - k >= min_frames:
                cuts.append(k)
    return [(a, b) for a, b in zip(cuts, cuts[1:] + [j])]


def notes(times, f0, conf, db):
    times, f0, conf, db = (np.asarray(v, dtype=np.float64) for v in (times, f0, conf, db))
    if not len(times):
        return [], []
    hop = times[1] - times[0] if len(times) > 1 else 0.01
    loud = db > max(db.max() - 45, -70)
    voiced = (conf >= VOICED) & loud & (f0 > 0)
    # bridge single-frame dropouts
    for k in range(1, len(voiced) - 1):
        if not voiced[k] and voiced[k - 1] and voiced[k + 1]:
            voiced[k] = True
    midi = 69 + 12 * np.log2(np.maximum(f0, 1e-6) / 440.0)
    min_frames = max(int(round(MIN_NOTE / hop)), 1)
    out = []
    for i, j in _runs(voiced):
        for a, b in _split_pitch(midi, i, j):
            for s, e in _split_onsets(db, a, b, min_frames):
                if e - s < min_frames:
                    continue
                m = float(np.median(midi[s:e]))
                p = int(round(m))
                out.append({"start": round(float(times[s]), 3), "end": round(float(times[e - 1] + hop), 3),
                            "pitch": p, "cents": int(round((m - p) * 100)),
                            "confidence": round(float(conf[s:e].mean()), 2)})
    out = _absorb_fragments(out)
    unsure = [[round(float(times[i]), 2), round(float(times[j - 1] + hop), 2)]
              for i, j in _runs(loud & (conf < VOICED)) if (j - i) * hop >= 0.3]
    return out, unsure


def analyze(path, start_sec=0.0, length_sec=None, engine="crepe"):
    x, sr = read_wav(path, start_sec, length_sec)
    eng = engines.load().get(engine)
    if eng is None:
        raise SystemExit("unknown engine %r (have: %s)" % (engine, ", ".join(sorted(engines.load()))))
    if eng["task"] == "notes":
        found, unsure = eng["run"](x, sr), []
    else:
        r = eng["run"](x, sr)
        found, unsure = notes(r["times"], r["f0"], r["confidence"], _frame_db(x, sr, r["times"]))
    for n in found:
        n["start"] = round(n["start"] + start_sec, 3)
        n["end"] = round(n["end"] + start_sec, 3)
    unsure = [[a + start_sec, b + start_sec] for a, b in unsure]
    return {"engine": engine, "notes": found, "unsure": unsure}


if __name__ == "__main__":
    # Core ML writes diagnostics to fd 1: keep the real stdout for the JSON alone
    out = os.dup(1)
    os.dup2(2, 1)
    a = sys.argv[1:]
    length = None if len(a) < 3 or a[2] == "-" else float(a[2])
    res = analyze(a[0], float(a[1]) if len(a) > 1 else 0.0, length, a[3] if len(a) > 3 else "crepe")
    os.write(out, json.dumps(res).encode() + b"\n")
