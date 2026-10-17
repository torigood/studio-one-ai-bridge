"""Key estimation from a 12-bin pitch-class weight vector (0 = C). Stdlib only.

Krumhansl-Kessler profiles, Pearson correlation against all 24 rotations.
"""
import math

NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def _corr(a, b):
    ma, mb = sum(a) / 12.0, sum(b) / 12.0
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else 0.0


def estimate_key(weights, top=3):
    """[{key, score}] best first; score is a correlation in -1..1."""
    if not any(weights):
        return []
    res = []
    for tonic in range(12):
        rotated = [weights[(tonic + i) % 12] for i in range(12)]
        res.append({"key": NAMES[tonic] + " major", "score": round(_corr(rotated, MAJOR), 3)})
        res.append({"key": NAMES[tonic] + " minor", "score": round(_corr(rotated, MINOR), 3)})
    res.sort(key=lambda r: -r["score"])
    return res[:top]


def key_from_notes(notes):
    """notes: [{pitch, length}] (length in any unit). Duration-weighted pitch classes."""
    w = [0.0] * 12
    for n in notes:
        w[int(n["pitch"]) % 12] += float(n.get("length", 1.0))
    return estimate_key(w)
