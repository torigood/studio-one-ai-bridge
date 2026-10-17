"""Audio analysis for WAV files (needs numpy; run with the project .venv).

Used as a subprocess by the MCP server, which stays stdlib-only:
    .venv/bin/python -m s1bridge.audio_analysis <file.wav> [start_sec] [length_sec]
prints one JSON object.

Estimates (heuristics, not ground truth):
  level      peak / RMS in dBFS, crest factor
  spectrum   energy share per band (sub, bass, low-mid, high-mid, air) and centroid
  tempo      onset-strength autocorrelation, 60..180 BPM
  key        chroma vs Krumhansl-Kessler profiles (top 3)
  sections   RMS per 8 s window, to see where the track is loud or quiet
"""
import json
import struct
import sys

import numpy as np

from .keys import estimate_key

BANDS = [("sub", 20, 60), ("bass", 60, 250), ("low_mid", 250, 2000), ("high_mid", 2000, 6000), ("air", 6000, 20000)]


def read_wav(path, start_sec=0.0, length_sec=None):
    """Mono float32 samples and sample rate. PCM 16/24/32-bit and IEEE float 32/64."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    p, fmt, raw = 12, None, None
    while p + 8 <= len(data):
        cid, size = data[p:p + 4], struct.unpack("<I", data[p + 4:p + 8])[0]
        body = data[p + 8:p + 8 + size]
        if cid == b"fmt ":
            tag, ch, rate, _br, _al, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and len(body) >= 26:  # WAVE_FORMAT_EXTENSIBLE: real tag in sub-format GUID
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            raw = body
        p += 8 + size + (size & 1)
    if not fmt or raw is None:
        raise ValueError("missing fmt or data chunk")
    tag, ch, rate, bits = fmt
    width = bits // 8
    frame = width * ch
    first = int(start_sec * rate) * frame
    last = len(raw) if length_sec is None else min(len(raw), first + int(length_sec * rate) * frame)
    raw = raw[first:last - (last - first) % frame]
    if tag == 3:
        x = np.frombuffer(raw, dtype="<f4" if bits == 32 else "<f8").astype(np.float32)
    elif bits == 16:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif bits == 24:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = (np.where(v & 0x800000, v - 0x1000000, v)).astype(np.float32) / 8388608.0
    elif bits == 32:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError("unsupported WAV format: tag %d, %d bits" % (tag, bits))
    return x.reshape(-1, ch).mean(axis=1), rate


def _db(v):
    return round(float(20 * np.log10(max(v, 1e-12))), 1)


def analyze(path, start_sec=0.0, length_sec=None):
    x, rate = read_wav(path, start_sec, length_sec)
    if len(x) < rate:
        raise ValueError("less than one second of audio")
    out = {"file": path, "sample_rate": rate, "seconds": round(len(x) / rate, 2)}
    peak, rms = float(np.max(np.abs(x))), float(np.sqrt(np.mean(x * x)))
    out["level"] = {"peak_dbfs": _db(peak), "rms_dbfs": _db(rms), "crest_db": round(_db(peak) - _db(rms), 1)}

    n, hop = 4096, 2048
    frames = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(frames)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(n), axis=1))
    freqs = np.fft.rfftfreq(n, 1.0 / rate)
    power = spec ** 2
    total = power.sum() + 1e-12
    out["spectrum"] = {name: round(float(power[:, (freqs >= lo) & (freqs < hi)].sum() / total), 3)
                       for name, lo, hi in BANDS}
    out["spectrum"]["centroid_hz"] = round(float((power.sum(axis=0) * freqs).sum() / total), 0)

    # Tempo: spectral flux onset envelope on a finer grid (hop 512), autocorrelation over 60..180 BPM,
    # parabolic interpolation around the best lag.
    on_n, on_hop = 1024, 512
    on_frames = 1 + (len(x) - on_n) // on_hop
    on_idx = np.arange(on_n)[None, :] + on_hop * np.arange(on_frames)[:, None]
    on_spec = np.log1p(np.abs(np.fft.rfft(x[on_idx] * np.hanning(on_n), axis=1)))
    flux = np.maximum(np.diff(on_spec, axis=0), 0).sum(axis=1)
    flux = flux - flux.mean()
    fps = rate / on_hop
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    lo, hi = int(60 * fps / 180), int(60 * fps / 60) + 1
    if len(x) < 6 * rate:
        out["tempo_note"] = "too short for a tempo estimate (< 6 s)"  # a 2 s bounce gave 188.7 BPM, confidence < 0
    elif ac[0] > 0 and hi < len(ac) - 1:
        best = lo + int(np.argmax(ac[lo:hi]))
        a, b, c = ac[best - 1], ac[best], ac[best + 1]
        shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        out["tempo_bpm"] = round(float(60.0 * fps / (best + shift)), 1)
        out["tempo_confidence"] = round(float(b / ac[0]), 2)
        out["tempo_alternatives"] = [round(out["tempo_bpm"] * f, 1) for f in (0.5, 2.0)
                                     if 50 <= out["tempo_bpm"] * f <= 200]  # half/double-time ambiguity

    # Key: chroma from 55 Hz .. 4 kHz.
    band = (freqs >= 55) & (freqs <= 4000)
    pcs = (np.round(12 * np.log2(freqs[band] / 440.0)).astype(int) + 9) % 12  # 0 = C
    chroma = np.bincount(pcs, weights=power[:, band].sum(axis=0), minlength=12)
    out["key"] = estimate_key([float(c) for c in chroma])
    out["chroma"] = [round(float(c / (chroma.max() + 1e-12)), 2) for c in chroma]

    win = int(8 * rate)
    out["sections_rms_dbfs"] = [_db(float(np.sqrt(np.mean(x[i:i + win] ** 2)))) for i in range(0, len(x), win)]
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        r = analyze(a[0], float(a[1]) if len(a) > 1 else 0.0, float(a[2]) if len(a) > 2 else None)
    except (OSError, ValueError) as e:
        r = {"error": str(e)}
    print(json.dumps(r, ensure_ascii=False))
