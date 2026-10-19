"""Song time for analysed notes (stdlib only): seconds <-> quarter notes through the tempo map,
bar/beat labels through the time signature map, and grid quantising.

tempo: Song.tempo_map() [{start_beats, bpm, ramp}]; a ramp segment changes bpm linearly over its
beats up to the next segment's bpm. signatures: Song.time_signatures().
"""
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_STEPS = 64  # integration steps per beat inside a ramp


def note_name(pitch):
    return "%s%d" % (NAMES[pitch % 12], pitch // 12 - 1)


def _segments(tempo):
    segs = sorted((t for t in tempo if t.get("bpm")), key=lambda t: t["start_beats"]) or [
        {"start_beats": 0.0, "bpm": 120.0}]
    out = []
    for i, s in enumerate(segs):
        end = segs[i + 1]["start_beats"] if i + 1 < len(segs) else None
        bpm_end = segs[i + 1]["bpm"] if s.get("ramp") and end is not None else s["bpm"]
        out.append((s["start_beats"], end, s["bpm"], bpm_end))
    return out


def _seg_seconds(b0, b1, bpm0, bpm1, upto):
    """Seconds from b0 to upto (<= b1) in a segment whose bpm goes linearly bpm0 -> bpm1."""
    if bpm0 == bpm1 or b1 is None:
        return (upto - b0) * 60.0 / bpm0
    n = max(int((upto - b0) * _STEPS), 1)
    h = (upto - b0) / n
    total = 0.0
    for k in range(n):
        mid = b0 + (k + 0.5) * h
        total += h * 60.0 / (bpm0 + (bpm1 - bpm0) * (mid - b0) / (b1 - b0))
    return total


def beat_to_sec(tempo, beat):
    segs = _segments(tempo)
    if beat <= segs[0][0]:
        return (beat - segs[0][0]) * 60.0 / segs[0][2]
    sec = 0.0
    for b0, b1, bpm0, bpm1 in segs:
        if b1 is None or beat <= b1:
            return sec + _seg_seconds(b0, b1, bpm0, bpm1, beat)
        sec += _seg_seconds(b0, b1, bpm0, bpm1, b1)
    return sec


def sec_to_beat(tempo, sec):
    """Inverse of beat_to_sec (bisection; tempo maps are monotonic)."""
    lo, hi = -1.0, 1.0
    while beat_to_sec(tempo, hi) < sec:
        hi *= 2
    while beat_to_sec(tempo, lo) > sec:
        lo *= 2
    for _ in range(60):
        mid = (lo + hi) / 2
        if beat_to_sec(tempo, mid) < sec:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _sigs(signatures):
    return sorted(signatures, key=lambda s: s["start"]) or [{"start": 0.0, "numerator": 4, "denominator": 4}]


def bar_start(signatures, bar):
    """Quarter notes at the start of 1-based bar."""
    sigs, beat = _sigs(signatures), 0.0
    for _ in range(1, bar):
        beat += _bar_length(sigs, beat)[0]
    return beat


def _bar_length(sigs, start):
    cur = sigs[0]
    for s in sigs:
        if s["start"] <= start + 1e-9:
            cur = s
    return cur["numerator"] * 4.0 / cur["denominator"], cur["denominator"]


def bar_pos(signatures, beat):
    """(1-based bar, 1-based beat in the bar, counted in the bar's own beat unit)."""
    sigs, bar, start = _sigs(signatures), 1, 0.0
    while True:
        length, den = _bar_length(sigs, start)
        if beat < start + length - 1e-9:
            return bar, 1 + (beat - start) * den / 4.0
        start += length
        bar += 1


def quantize(notes, grid):
    """Snap [{beat, len, ...}] starts and ends to the grid (quarter notes; 0 = leave), keep every note at
    least one grid step long and cut a note that would overlap the next one."""
    if not grid:
        return notes
    out = []
    for n in sorted(notes, key=lambda n: n["beat"]):
        s = round(n["beat"] / grid) * grid
        e = max(round((n["beat"] + n["len"]) / grid) * grid, s + grid)
        if out and out[-1]["beat"] + out[-1]["len"] > s:
            if out[-1]["beat"] >= s:  # same slot: keep the longer one
                if e - s > out[-1]["len"]:
                    out[-1] = dict(n, beat=s, len=e - s)
                continue
            out[-1]["len"] = s - out[-1]["beat"]
        out.append(dict(n, beat=s, len=e - s))
    return out
