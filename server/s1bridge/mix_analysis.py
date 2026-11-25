"""Mix check for WAV stems and mixes (needs numpy; runs in the analysis pack's Python):
    python -m s1bridge.mix_analysis <name=file.wav> [<name=file.wav> ...]
prints one JSON object.

Per file:
  loudness   integrated loudness (ITU-R BS.1770-4: K-weighting, 400 ms blocks, -70 LUFS absolute and
             -10 LU relative gate), sample peak dBFS, PLR (peak to loudness ratio)
  stereo     left/right correlation (1 = mono, 0 = wide, < 0 = phase trouble) and side/mid level in dB
  bands      octave band levels (31 Hz .. 16 kHz) in dB relative to the file's loudest band, and the
             tilt (dB per octave, least squares over 63 Hz .. 8 kHz)
Across files (stems of one song): masking = octave bands where two stems are both strong and within
6 dB of each other, strongest first. Stems are compared time-averaged, so their start offsets do not
matter; two parts that never play together can still be listed.

Stems bounced in Studio One (Bounce To New Track) include the channel fader but NOT its insert effects
(observed 2026-11-25: -18 dB on a Pro EQ output changed nothing). eq:name=freq,gain,q simulates peaking cuts.
"""
import json
import os
import sys

import numpy as np

from .audio_analysis import read_wav

CENTERS = [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]


def _biquad_response(b, a, w):
    z = np.exp(-1j * w)
    return (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)


def k_weighting(rate, n):
    """|H|^2 of the BS.1770 K-weighting (high shelf + high pass) on the rfft bins of n samples."""
    w = 2 * np.pi * np.fft.rfftfreq(n, 1.0 / rate) / rate
    # stage 1: high shelf, +4 dB at 1500 Hz, Q 1/sqrt(2) (coefficients as in pyloudnorm)
    A, w0 = 10 ** (4.0 / 40), 2 * np.pi * 1500.0 / rate
    al = np.sin(w0) / (2 / np.sqrt(2))
    c, sa = np.cos(w0), 2 * np.sqrt(A) * al
    b1 = [A * ((A + 1) + (A - 1) * c + sa), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - sa)]
    a1 = [(A + 1) - (A - 1) * c + sa, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - sa]
    # stage 2: high pass at 38 Hz, Q 0.5
    w0 = 2 * np.pi * 38.0 / rate
    al, c = np.sin(w0) / (2 * 0.5), np.cos(w0)
    b2 = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
    a2 = [1 + al, -2 * c, 1 - al]
    return np.abs(_biquad_response(b1, a1, w) * _biquad_response(b2, a2, w)) ** 2


def loudness(x, rate):
    """Integrated loudness in LUFS of frames x channels (None if everything is below the gate)."""
    n = len(x)
    gain = np.sqrt(k_weighting(rate, n))
    y = np.fft.irfft(np.fft.rfft(x, axis=0) * gain[:, None], n=n, axis=0)  # zero-phase is fine for power
    block, hop = int(0.4 * rate), int(0.1 * rate)
    if n < block:
        return None
    sq = np.cumsum(np.concatenate([np.zeros((1, x.shape[1])), y * y]), axis=0)
    starts = np.arange(0, n - block + 1, hop)
    z = ((sq[starts + block] - sq[starts]) / block).sum(axis=1)  # channel weights 1 (L, R)
    lk = -0.691 + 10 * np.log10(np.maximum(z, 1e-20))
    z = z[lk > -70]
    if not len(z):
        return None
    rel = -0.691 + 10 * np.log10(z.mean()) - 10
    z = z[-0.691 + 10 * np.log10(z) > rel]
    return round(float(-0.691 + 10 * np.log10(z.mean())), 1)


def band_levels(mono, rate):
    """Octave band power in dB (absolute, from a Welch-averaged spectrum)."""
    n, hop = 8192, 4096
    if len(mono) < n:
        mono = np.pad(mono, (0, n - len(mono)))
    frames = 1 + (len(mono) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(frames)[:, None]
    power = (np.abs(np.fft.rfft(mono[idx] * np.hanning(n), axis=1)) ** 2).mean(axis=0)
    freqs = np.fft.rfftfreq(n, 1.0 / rate)
    out = []
    for c in CENTERS:
        sel = (freqs >= c / np.sqrt(2)) & (freqs < c * np.sqrt(2)) & (freqs < rate / 2)
        out.append(float(10 * np.log10(power[sel].sum() + 1e-20)) if sel.any() else None)
    return out


def _read(paths):
    """Frames x channels of one or more WAV files joined end to end (the parts of one stem)."""
    chunks, rate = [], None
    for p in paths:
        x, r = read_wav(p, mono=False)
        if rate is not None and r != rate:
            raise ValueError("%s: sample rate %d differs from %d" % (p, r, rate))
        rate = r
        chunks.append(x if not chunks or x.shape[1] == chunks[0].shape[1] else
                      np.repeat(x.mean(axis=1, keepdims=True), chunks[0].shape[1], axis=1))
    return np.concatenate(chunks), rate


def peaking_response(rate, n, freq, gain_db, q):
    """|H| of an RBJ peaking EQ on the rfft bins of n samples (Pro EQ's peaking bands are of this kind)."""
    w = 2 * np.pi * np.fft.rfftfreq(n, 1.0 / rate) / rate
    A, w0 = 10 ** (gain_db / 40.0), 2 * np.pi * freq / rate
    al = np.sin(w0) / (2 * q)
    b = [1 + al * A, -2 * np.cos(w0), 1 - al * A]
    a = [1 + al / A, -2 * np.cos(w0), 1 - al / A]
    return np.abs(_biquad_response(b, a, w))


def apply_eq(x, rate, eqs):
    """x (frames x channels) through peaking bands [(freq, gain_db, q)], zero phase (an estimate: Studio One's
    bounce leaves insert effects out, so a cut is simulated to judge it)."""
    if not eqs:
        return x
    n = len(x)
    h = np.ones(n // 2 + 1)
    for f, g, q in eqs:
        h = h * peaking_response(rate, n, f, g, q)
    return np.fft.irfft(np.fft.rfft(x, axis=0) * h[:, None], n=n, axis=0).astype(np.float32)


def analyze_file(path, eqs=None):
    x, rate = _read(path if isinstance(path, list) else [path])
    x = apply_eq(x, rate, eqs)
    if len(x) < rate // 2:
        raise ValueError("%s: less than half a second of audio" % path)
    mono = x.mean(axis=1)
    peak = float(np.max(np.abs(x)))
    if peak < 1e-5:  # below -100 dBFS: nothing to measure
        return {"seconds": round(len(x) / rate, 1), "silent": True,
                "note": "the file is silent: is the instrument loaded and making sound, the channel unmuted?",
                "_abs_bands": None}
    lufs = loudness(x, rate)
    out = {"seconds": round(len(x) / rate, 1),
           "loudness": {"lufs": lufs, "peak_dbfs": round(float(20 * np.log10(max(peak, 1e-12))), 1)}}
    if lufs is not None:
        out["loudness"]["plr_db"] = round(out["loudness"]["peak_dbfs"] - lufs, 1)
    if x.shape[1] >= 2:
        left, right = x[:, 0], x[:, 1]
        den = np.sqrt((left * left).sum() * (right * right).sum())
        mid, side = (left + right) / 2, (left - right) / 2
        ms, ss = float((mid * mid).sum()), float((side * side).sum())
        out["stereo"] = {"correlation": round(float((left * right).sum() / den), 2) if den > 0 else None}
        if ss < ms * 1e-6:
            out["stereo"]["mono"] = True  # left = right
        else:
            out["stereo"]["side_minus_mid_db"] = round(10 * float(np.log10((ss + 1e-20) / (ms + 1e-20))), 1)
    else:
        out["stereo"] = {"mono_file": True}
    levels = band_levels(mono, rate)
    top = max(v for v in levels if v is not None)
    out["bands_db"] = {("%gk" % (c / 1000) if c >= 1000 else "%g" % c): (round(v - top, 1) if v is not None else None)
                       for c, v in zip(CENTERS, levels)}
    pts = [(np.log2(c), v) for c, v in zip(CENTERS, levels) if v is not None and 63 <= c <= 8000]
    if len(pts) >= 3:
        xs, ys = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
        out["tilt_db_per_octave"] = round(float(np.polyfit(xs, ys, 1)[0]), 1)
    out["_abs_bands"] = levels
    return out


def masking(files, limit=8):
    """Bands where two stems are both near their own loudest band and within 6 dB of each other."""
    hits = []
    names = list(files)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            la, lb = files[a]["_abs_bands"], files[b]["_abs_bands"]
            if la is None or lb is None:  # silent
                continue
            ta, tb = max(v for v in la if v is not None), max(v for v in lb if v is not None)
            for c, va, vb in zip(CENTERS, la, lb):
                if va is None or vb is None:
                    continue
                if va > ta - 9 and vb > tb - 9 and abs(va - vb) <= 6:
                    hits.append({"band_hz": c, "stems": [a, b], "difference_db": round(abs(va - vb), 1),
                                 "level_db": round(min(va, vb), 1),
                                 "louder": a if va >= vb else b})
    hits.sort(key=lambda h: (-h["level_db"], h["difference_db"]))
    top = hits[0]["level_db"] if hits else 0
    for h in hits:
        h["level_db"] = round(h["level_db"] - top, 1)  # 0 = the strongest overlap
    return hits[:limit]


def analyze(named, eqs=None):
    eqs = eqs or {}
    groups = {}
    for name, path in named:
        groups.setdefault(name, []).append(path)
    files = {}
    for name, paths in groups.items():
        files[name] = analyze_file(paths, eqs.get(name))
        if eqs.get(name):
            files[name]["simulated_eq"] = [{"freq_hz": f, "gain_db": g, "q": q} for f, g, q in eqs[name]]
        files[name]["files" if len(paths) > 1 else "file"] = paths if len(paths) > 1 else paths[0]
    out = {"files": files}
    if len(files) > 1:
        out["masking"] = masking(files)
    for f in files.values():
        del f["_abs_bands"]
    return out


if __name__ == "__main__":
    # args: name=file.wav ... and eq:name=freq,gain_db,q to simulate a peaking cut on a stem
    pairs, eq_args = [], {}
    for arg in sys.argv[1:]:
        if arg.startswith("eq:"):
            name, _, spec = arg[3:].rpartition("=")
            eq_args.setdefault(name, []).append(tuple(float(v) for v in spec.split(",")))
            continue
        name, sep, path = arg.partition("=")
        if not sep:
            name, path = os.path.splitext(os.path.basename(arg))[0], arg
        pairs.append((name, path))
    try:
        r = analyze(pairs, eq_args)
    except (OSError, ValueError) as e:
        r = {"error": str(e)}
    print(json.dumps(r, ensure_ascii=False))
