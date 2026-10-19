"""Analysis engines (run inside the analysis pack's Python, see analysis_pack.py).

An engine is a dict {"name", "task", "run", "about"}:
  task "f0"     run(samples, sr) -> {"times": [s], "f0": [Hz, 0 = unvoiced], "confidence": [0..1]}
                (monophonic pitch track; melody.py turns it into notes)
  task "notes"  run(samples, sr) -> [{"start": s, "end": s, "pitch": midi, "confidence": 0..1}]
                (an engine that already finds notes)
  task "chords" run(samples, sr) -> [{"start": s, "end": s, "root": 0..11 | None, "quality", "bass"}]
                (for audio_chords; quality as chords.QUALITIES)
samples is mono float32 at sr Hz (any rate; resample inside the engine if it needs one).

Built-in engines use permissively licensed models only. More engines are loaded from *.py files in
the engines folder (S1BRIDGE_ENGINES_DIR, default <data dir>/analysis/engines); each file defines
ENGINES = [ {...}, ... ]. Engines there are private to this Mac and never part of the repository,
so they may use models whose licenses forbid redistribution.
"""
import glob
import importlib.util
import os

import numpy as np

from . import config

CREPE_SR = 16000
CREPE_HOP = 160  # 10 ms
CREPE_BINS = 360


def _resample(x, sr, target):
    if sr == target:
        return x.astype(np.float32)
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(sr), int(target))
    return resample_poly(x, target // g, int(sr) // g).astype(np.float32)


def _crepe_frames(x):
    """1024-sample frames every 10 ms, centred (as torchcrepe), each normalised to zero mean, unit std;
    and each frame's level in dBFS before normalising."""
    x = np.pad(x, 512)
    n = 1 + (len(x) - 1024) // CREPE_HOP
    idx = np.arange(1024)[None, :] + CREPE_HOP * np.arange(n)[:, None]
    f = x[idx]
    f = f - f.mean(axis=1, keepdims=True)
    std = f.std(axis=1, keepdims=True)
    return (f / np.maximum(std, 1e-10)).astype(np.float32), 20 * np.log10(std[:, 0] + 1e-9)


def _viterbi(probs, width=12):
    """Most likely bin path; transitions allowed within +-width-1 bins, weight falling linearly (torchcrepe)."""
    n, k = probs.shape
    d = np.arange(-width + 1, width)
    w = (width - np.abs(d)).astype(np.float64)
    src = np.arange(k)[None, :] - d[:, None]  # src[j, b]: bin b is reached from bin b - d[j]
    trans = np.where((src >= 0) & (src < k), np.log(w / w.sum())[:, None], -np.inf)
    src = np.clip(src, 0, k - 1)
    obs = np.log(probs / np.maximum(probs.sum(axis=1, keepdims=True), 1e-12) + 1e-12)
    score = obs[0].copy()
    back = np.zeros((n, k), dtype=np.int16)
    cols = np.arange(k)
    for t in range(1, n):
        cand = score[src] + trans
        j = cand.argmax(axis=0)
        back[t] = src[j, cols]
        score = cand[j, cols] + obs[t]
    path = np.zeros(n, dtype=np.int64)
    path[-1] = int(np.argmax(score))
    for t in range(n - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    return path


def _bins_to_hz(probs, path):
    """Weighted mean of the cents of the bins within +-4 of the path (torchcrepe's local average)."""
    n = len(path)
    offs = np.arange(-4, 5)
    idx = np.clip(path[:, None] + offs[None, :], 0, CREPE_BINS - 1)
    p = probs[np.arange(n)[:, None], idx]
    cents = 1997.3794084376191 + 20.0 * idx
    c = (p * cents).sum(axis=1) / np.maximum(p.sum(axis=1), 1e-12)
    return 10.0 * 2.0 ** (c / 1200.0)


_sessions = {}


def _session(path):
    """ONNX session on Core ML (Apple GPU/Neural Engine, ~3x faster, same output) when available, else CPU."""
    if path not in _sessions:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        sess = None
        if "CoreMLExecutionProvider" in ort.get_available_providers():
            try:
                sess = ort.InferenceSession(path, opts, providers=[
                    ("CoreMLExecutionProvider", {"ModelFormat": "MLProgram"}), "CPUExecutionProvider"])
            except Exception:  # some graphs (Basic Pitch's CQT) do not convert to Core ML
                sess = None
        _sessions[path] = sess or ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
    return _sessions[path]


def crepe_model_path(capacity="full"):
    return os.path.join(config.ANALYSIS_DIR, "models", "crepe-%s.onnx" % capacity)


def _crepe(capacity):
    def run(samples, sr):
        path = crepe_model_path(capacity)
        if not os.path.isfile(path):
            raise RuntimeError("CREPE model missing: %s (run: studio-one-ai-bridge analysis-setup)" % path)
        frames, db = _crepe_frames(_resample(samples, sr, CREPE_SR))
        if not len(frames):
            return {"times": [], "f0": [], "confidence": []}
        # silent frames (60 dB under the loudest) are not worth the model's time: about 3 ms a frame
        live = np.flatnonzero(db > max(db.max() - 60, -90))
        probs = np.zeros((len(frames), CREPE_BINS), dtype=np.float32)
        for i in range(0, len(live), 1024):
            sel = live[i:i + 1024]
            probs[sel] = _session(path).run(None, {"frames": frames[sel]})[0]
        path_bins = _viterbi(probs)
        hz = _bins_to_hz(probs, path_bins)
        conf = probs[np.arange(len(path_bins)), path_bins]
        return {"times": (np.arange(len(hz)) * CREPE_HOP / CREPE_SR).tolist(), "f0": hz.tolist(),
                "confidence": conf.tolist()}
    return run


def _pyin(samples, sr):
    """Probabilistic YIN (Mauch & Dixon 2014) from librosa (ISC). Its voicing probabilities sit far
    below the note splitter's threshold, so its own voiced/unvoiced decision is the confidence."""
    import librosa
    x = _resample(samples, sr, CREPE_SR)
    f0, voiced, _ = librosa.pyin(x, fmin=65, fmax=1100, sr=CREPE_SR, frame_length=2048, hop_length=CREPE_HOP)
    f0 = np.nan_to_num(f0)
    return {"times": (np.arange(len(f0)) * CREPE_HOP / CREPE_SR).tolist(),
            "f0": np.where(voiced, f0, 0.0).tolist(), "confidence": np.where(voiced, 0.9, 0.0).tolist()}


# Basic Pitch (Spotify, Apache 2.0; models/NOTICE): a small polyphonic note model. Here it answers
# "which notes sound at once", to find layered vocals (doubles, harmonies) the f0 trackers cannot see.
BP_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "basic-pitch.onnx")
BP_SR = 22050
BP_HOP = 256
BP_WINDOW = BP_SR * 2 - BP_HOP  # 43844 samples in, 172 frames out
BP_OVERLAP = 30  # frames shared by neighbouring windows, half trimmed from each side
BP_LOWEST = 21  # MIDI note of row 0 (A0), 88 rows


def basic_pitch(samples, sr):
    """{"times": frame times (s), "notes": (frames, 88) note activations 0..1, "onsets": same shape}."""
    x = _resample(samples, sr, BP_SR)
    overlap = BP_OVERLAP * BP_HOP
    hop = BP_WINDOW - overlap
    x = np.concatenate([np.zeros(overlap // 2, dtype=np.float32), x])
    starts = list(range(0, len(x), hop)) or [0]
    wins = np.zeros((len(starts), BP_WINDOW, 1), dtype=np.float32)
    for i, a in enumerate(starts):
        chunk = x[a:a + BP_WINDOW]
        wins[i, :len(chunk), 0] = chunk
    sess = _session(BP_MODEL)
    outs = [sess.run(["StatefulPartitionedCall:1", "StatefulPartitionedCall:2"],
                     {"serving_default_input_2:0": wins[i:i + 16]}) for i in range(0, len(wins), 16)]
    trim = BP_OVERLAP // 2

    def unwrap(k):
        out = np.concatenate([o[k] for o in outs])[:, trim:-trim, :]
        return out.reshape(-1, out.shape[2])
    notes, onsets = unwrap(0), unwrap(1)
    # windows advance `hop` samples but keep 142 frames (142 * 256 > hop): time each frame from its
    # own window, or the frames drift ~8 ms per window. The padding equals the trimmed frames.
    kept = notes.shape[0] // len(starts)
    times = (np.repeat(np.arange(len(starts)) * hop, kept) + np.tile(np.arange(kept) * BP_HOP, len(starts))) / BP_SR
    keep = times < len(samples) / sr
    return {"times": times[keep], "notes": notes[keep], "onsets": onsets[keep]}


BUILTIN = [
    {"name": "crepe", "task": "f0", "run": _crepe("full"),
     "about": "CREPE full (MIT), neural monophonic pitch tracker, Viterbi-smoothed"},
    {"name": "crepe-tiny", "task": "f0", "run": _crepe("tiny"),
     "about": "CREPE tiny (MIT): ~40x faster, less accurate"},
    {"name": "pyin", "task": "f0", "run": _pyin,
     "about": "pYIN (librosa, ISC): signal processing, no model; ~3x faster than CREPE full"},
]


def load(engines_dir=None):
    """name -> engine: built-ins, then the engines folder (a private engine may replace a built-in)."""
    out = {e["name"]: e for e in BUILTIN}
    d = engines_dir or config.ENGINES_DIR
    for f in sorted(glob.glob(os.path.join(d, "*.py"))):
        spec = importlib.util.spec_from_file_location("s1bridge_engine_" + os.path.basename(f)[:-3], f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for e in getattr(mod, "ENGINES", []):
            out[e["name"]] = dict(e, private=True)
    return out
