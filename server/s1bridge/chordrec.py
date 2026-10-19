"""Chords from audio, one decision per beat. Runs in the analysis pack's Python:
    python -m s1bridge.chordrec <file.wav> <start_sec> <length_sec|-> <bounds> <strong> [engine]
bounds: comma-separated beat boundaries in seconds from start_sec (n beats = n + 1 values);
strong: comma-separated indexes of beats where a chord change is natural (bar starts, mid-bar).
Prints {"beats": [{"root", "quality", "bass", "score"} | null], "chroma": [12 summed weights]}.

Pitch content comes from Basic Pitch's polyphonic note activations (engines.basic_pitch), folded to
12 pitch classes per beat; the bass profile follows the lowest active note. Each beat is scored
against chord templates (cosine similarity, root/third weighted, bass agreement as a bonus) and a
Viterbi pass picks the sequence, charging for every change and more for changes off strong beats,
so chords do not flicker with passing notes.

Another engine (engines.py, task "chords": run(samples, sr) -> [{start, end, root, quality, bass}],
root None = no chord) can replace the built-in decision; beats then take the segment covering most
of them, and the pitch content still drives the key estimate.
"""
import json
import os
import sys

import numpy as np

from . import engines
from .audio_analysis import read_wav

# quality -> semitones above the root (names as chords.QUALITIES, so song_write_chords accepts them)
VOCAB = {"": (0, 4, 7), "m": (0, 3, 7), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10),
         "dim": (0, 3, 6), "m7b5": (0, 3, 6, 10), "sus4": (0, 5, 7), "sus2": (0, 2, 7), "aug": (0, 4, 8)}
# simple chords win ties: a 7th has to be really there, sus/dim/aug are rarer
PRIOR = {"": 0.0, "m": 0.0, "7": -0.03, "maj7": -0.03, "m7": -0.03, "dim": -0.05, "m7b5": -0.05,
         "sus4": -0.05, "sus2": -0.05, "aug": -0.07}
DEGREE_WEIGHT = [1.0, 1.0, 0.8, 0.9]  # root, third, fifth, seventh
BASS_BONUS = 0.12
CHANGE = 0.10        # cost of a chord change (in similarity units)
WEAK_CHANGE = 0.10   # extra cost when the change falls on a weak beat
SILENT = 0.05        # beats whose summed activation is below this fraction of the median: no chord
ACTIVE = 0.3


def _templates():
    labels, rows = [], []
    for root in range(12):
        for q, ivs in VOCAB.items():
            t = np.zeros(12)
            for k, iv in enumerate(ivs):
                t[(root + iv) % 12] = DEGREE_WEIGHT[min(k, 3)]
            labels.append((root, q))
            rows.append(t / np.linalg.norm(t))
    return labels, np.array(rows)


def beat_profiles(act, times, bounds):
    """Per beat: chroma (12, from mean activations) and bass (12, lowest active note per frame)."""
    lowest = engines.BP_LOWEST
    chroma = np.zeros((len(bounds) - 1, 12))
    bass = np.zeros((len(bounds) - 1, 12))
    pcs = (np.arange(act.shape[1]) + lowest) % 12
    for i in range(len(bounds) - 1):
        rows = act[(times >= bounds[i]) & (times < bounds[i + 1])]
        if not len(rows):
            continue
        m = rows.mean(axis=0)
        np.add.at(chroma[i], pcs, m)
        for r in rows:
            on = np.flatnonzero(r >= ACTIVE)
            if len(on) and on[0] + lowest < 64:
                bass[i, pcs[on[0]]] += r[on[0]]
    return chroma, bass


def decode(chroma, bass, strong):
    """Viterbi over the chord vocabulary; None for silent beats."""
    labels, tmpl = _templates()
    n, k = len(chroma), len(labels)
    energy = chroma.sum(axis=1)
    silent = energy < SILENT * max(np.median(energy[energy > 0]) if (energy > 0).any() else 1.0, 1e-9)
    norm = chroma / np.maximum(np.linalg.norm(chroma, axis=1, keepdims=True), 1e-9)
    bnorm = bass / np.maximum(bass.max(axis=1, keepdims=True), 1e-9)
    roots = np.array([r for r, _ in labels])
    prior = np.array([PRIOR[q] for _, q in labels])
    score = norm @ tmpl.T + prior[None, :] + BASS_BONUS * bnorm[:, roots]
    score[silent] = 0.0
    strong = set(strong)
    best = score[0].copy()
    back = np.zeros((n, k), dtype=np.int32)
    for i in range(1, n):
        cost = CHANGE + (0 if i in strong else WEAK_CHANGE)
        j = int(np.argmax(best))
        stay = best
        move = best[j] - cost
        back[i] = np.where(stay >= move, np.arange(k), j)
        best = np.maximum(stay, move) + score[i]
    path = np.zeros(n, dtype=np.int64)
    path[-1] = int(np.argmax(best))
    for i in range(n - 1, 0, -1):
        path[i - 1] = back[i, path[i]]
    out = []
    for i, s in enumerate(path):
        if silent[i]:
            out.append(None)
            continue
        root, q = labels[s]
        chord_pcs = {(root + iv) % 12 for iv in VOCAB[q]}
        b = int(np.argmax(bass[i])) if bass[i].max() > 0 else root
        # a slash chord only when the bass clearly sits on another chord tone
        if b == root or b not in chord_pcs or bnorm[i, root] > 0.5:
            b = root
        out.append({"root": int(root), "quality": q, "bass": int(b), "score": round(float(score[i, s]), 3)})
    return out


def from_segments(segments, bounds):
    """Beats from an engine's chord segments [{start, end, root, quality, bass} | {start, end, root: None}]:
    each beat takes the segment covering most of it."""
    out = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        best, cover = None, 0.0
        for s in segments:
            c = min(b, s["end"]) - max(a, s["start"])
            if c > cover:
                best, cover = s, c
        if best is None or best.get("root") is None:
            out.append(None)
        else:
            out.append({"root": int(best["root"]), "quality": best["quality"],
                        "bass": int(best.get("bass", best["root"])), "score": best.get("score", 1.0)})
    return out


def analyze(path, start_sec, length_sec, bounds, strong, engine="basic-pitch"):
    x, sr = read_wav(path, start_sec, length_sec)
    bp = engines.basic_pitch(x, sr)
    chroma, bass = beat_profiles(np.asarray(bp["notes"]), np.asarray(bp["times"]), np.asarray(bounds))
    if engine == "basic-pitch":
        beats = decode(chroma, bass, strong)
    else:
        eng = engines.load().get(engine)
        if eng is None or eng["task"] != "chords":
            raise SystemExit("unknown chord engine %r" % engine)
        beats = from_segments(eng["run"](x, sr), list(bounds))
    return {"beats": beats, "chroma": chroma.sum(axis=0).round(3).tolist()}


if __name__ == "__main__":
    out = os.dup(1)
    os.dup2(2, 1)
    a = sys.argv[1:]
    bounds = [float(v) for v in a[3].split(",")]
    strong = [int(v) for v in a[4].split(",") if v] if len(a) > 4 else []
    res = analyze(a[0], float(a[1]), None if a[2] == "-" else float(a[2]), bounds, strong,
                  a[5] if len(a) > 5 else "basic-pitch")
    os.write(out, json.dumps(res).encode() + b"\n")
