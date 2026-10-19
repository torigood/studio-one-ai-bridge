"""Melody analysis: song timing (stdlib), note segmentation (numpy), CREPE end to end (analysis pack)."""
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge import notegrid, tools  # noqa: E402
from s1bridge.song import Song  # noqa: E402

try:
    import numpy as np
except ImportError:
    np = None

SONG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Song xmlns:x="https://www.presonus.software/xml/ccl"><Attributes x:id="Root">
<TempoMap x:id="tempoMap"><TempoMapSegment curveType="0" start="0" end="1e200" tempo="0.5"/></TempoMap>
<TimeSignatureMap x:id="timeSignatureMap"><TimeSignatureMapSegment numerator="4" denominator="4"/></TimeSignatureMap>
<List x:id="Tracks">
<MediaTrack mediaType="Audio" version="1" trackID="{T1}" timeFormat="2" name="Vox">
<List x:id="Events">
<AudioEvent clipID="{C1}" start="10" timeFormat="0" length="20" offset="4" name="Vox"/>
</List>
</MediaTrack>
</List></Attributes></Song>"""

POOL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<MediaPool xmlns:x="https://www.presonus.software/xml/ccl"><Attributes x:id="rootFolder">
<AudioClip mediaID="{C1}"><Url x:id="url" url="file://%s"/></AudioClip>
</Attributes></MediaPool>"""


def write_wav(path, x, sr):
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
                struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)


def sung(seq, sr=44100):
    """Harmonic tones with +-40 cent vibrato; seq [(midi or None, seconds)]. Returns samples, [(start, midi)]."""
    out, truth, t0 = [], [], 0.0
    for p, d in seq:
        t = np.arange(int(d * sr)) / sr
        if p is not None:
            f = 440 * 2 ** ((p - 69) / 12) * 2 ** (0.4 * np.sin(2 * np.pi * 5.5 * t) / 12)
            ph = 2 * np.pi * np.cumsum(f) / sr
            env = np.minimum(1, np.minimum(t / 0.02, (d - t) / 0.03))
            out.append(0.3 * env * (np.sin(ph) + 0.5 * np.sin(2 * ph) + 0.25 * np.sin(3 * ph)))
            truth.append((t0, p))
        else:
            out.append(np.zeros(len(t)))
        t0 += d
    return np.concatenate(out), truth


class TimingTests(unittest.TestCase):
    def test_tempo_changes_and_ramps(self):
        tempo = [{"start_beats": 0, "bpm": 120}, {"start_beats": 8, "bpm": 60}]
        self.assertAlmostEqual(notegrid.beat_to_sec(tempo, 10), 6.0)
        self.assertAlmostEqual(notegrid.sec_to_beat(tempo, 6.0), 10.0, places=6)
        ramp = [{"start_beats": 0, "bpm": 60, "ramp": True}, {"start_beats": 4, "bpm": 120}]
        self.assertAlmostEqual(notegrid.beat_to_sec(ramp, 4), 4 * 0.6931472, places=3)  # 4 ln 2

    def test_bars_with_time_signature_change(self):
        sigs = [{"start": 0, "numerator": 4, "denominator": 4}, {"start": 8, "numerator": 6, "denominator": 8}]
        self.assertEqual(notegrid.bar_start(sigs, 4), 11.0)
        self.assertEqual(notegrid.bar_pos(sigs, 9.5), (3, 4.0))

    def test_quantize_snaps_and_removes_overlap(self):
        q = notegrid.quantize([{"beat": 0.05, "len": 0.4}, {"beat": 0.3, "len": 0.5}, {"beat": 1.1, "len": 0.02}],
                              0.25)
        self.assertEqual([(n["beat"], n["len"]) for n in q], [(0.0, 0.25), (0.25, 0.5), (1.0, 0.25)])

    def test_note_name(self):
        self.assertEqual(notegrid.note_name(60), "C4")
        self.assertEqual(notegrid.note_name(70), "A#4")


class SongPlacementTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.wav = os.path.join(self.dir, "vox.wav")
        open(self.wav, "wb").close()
        self.song = os.path.join(self.dir, "t.song")
        with zipfile.ZipFile(self.song, "w") as z:
            z.writestr("Song/song.xml", SONG_XML)
            z.writestr("Song/mediapool.xml", POOL_XML % self.wav)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_audio_event_in_seconds(self):
        with Song(self.song) as s:
            p = s.tracks()[0]["parts"][0]
        self.assertEqual((p["start_sec"], p["offset_sec"], p["length_sec"]), (10.0, 4.0, 20.0))
        self.assertEqual((p["start_beats"], p["length_beats"]), (20.0, 40.0))  # 120 bpm

    def test_notes_land_on_song_bars(self):
        calls = []

        def fake(module, args, timeout=900):
            calls.append(args)
            # a note 1.0 s into the analysed window = file second args[1] + 1
            return {"notes": [{"start": args[1] + 1.0, "end": args[1] + 1.5, "pitch": 64, "cents": 3,
                               "confidence": 0.9}], "unsure": []}

        with mock.patch.object(tools, "_run_analysis", fake):
            r = tools._audio_melody({"song": self.song, "track": "Vox", "from_bar": 7, "to_bar": 7}, 1)
        # bar 7 = beats 24..28 = song 12..14 s = file 6..8 s (event: song 10 s plays file 4 s)
        self.assertEqual(calls[0][1:3], [6.0, 2.0])
        n = r["notes"][0]
        self.assertEqual((n["bar"], n["pos"], n["len"], n["note"]), (7, 3.0, 1.0, "E4"))

    def test_quality_picks_the_engine(self):
        used = []

        def fake(module, args, timeout=900):
            used.append(args[3])
            return {"notes": [], "unsure": []}

        with mock.patch.object(tools, "_run_analysis", fake):
            for q in (None, "best", "fast"):
                tools._audio_melody({"song": self.song, "track": "Vox", "quality": q}, 1)
            with self.assertRaises(tools.ToolError):
                tools._audio_melody({"song": self.song, "track": "Vox", "quality": "max"}, 1)
        self.assertEqual(used, ["crepe", "crepe", "crepe-tiny"])


@unittest.skipIf(np is None, "needs numpy")
class SegmentationTests(unittest.TestCase):
    def frames(self, seq, hop=0.01):
        """Fake f0 track: seq [(midi or None, seconds, db)] with +-40 cent vibrato."""
        f0, conf, db = [], [], []
        for p, d, level in seq:
            n = int(round(d / hop))
            t = np.arange(n) * hop
            if p is None:
                f0 += [0.0] * n
                conf += [0.05] * n
                db += [-90.0] * n
            else:
                f0 += list(440 * 2 ** ((p - 69 + 0.4 * np.sin(2 * np.pi * 5.5 * t)) / 12))
                conf += [0.9] * n
                db += [level] * n
        return np.arange(len(f0)) * hop, f0, conf, db

    def test_legato_vibrato_gap_and_repeated_note(self):
        from s1bridge import melody
        times, f0, conf, db = self.frames([(60, 0.5, -12), (62, 0.5, -12), (64, 0.25, -12), (64, 0.05, -30),
                                           (64, 0.25, -12), (None, 0.2, 0), (67, 1.0, -12)])
        found, _ = melody.notes(times, f0, conf, db)
        self.assertEqual([n["pitch"] for n in found], [60, 62, 64, 64, 67])
        for n, start in zip(found, (0.0, 0.5, 1.0, 1.3, 1.75)):
            self.assertAlmostEqual(n["start"], start, delta=0.04)

    def test_loud_without_pitch_is_unsure(self):
        from s1bridge import melody
        times = np.arange(100) * 0.01
        _, unsure = melody.notes(times, [0.0] * 100, [0.1] * 100, [-12.0] * 100)
        self.assertEqual(unsure, [[0.0, 1.0]])

    def test_viterbi_follows_one_hot_bins(self):
        from s1bridge import engines
        probs = np.full((5, engines.CREPE_BINS), 1e-4)
        probs[np.arange(5), [100, 101, 102, 102, 103]] = 1.0
        path = engines._viterbi(probs)
        self.assertEqual(path.tolist(), [100, 101, 102, 102, 103])
        hz = engines._bins_to_hz(probs, path)
        self.assertAlmostEqual(hz[0], 10 * 2 ** ((1997.3794084376191 + 2000) / 1200), delta=0.5)


@unittest.skipIf(np is None, "needs numpy")
class LayerTests(unittest.TestCase):
    """layers() on made-up Basic Pitch activations (frames every 10 ms, row = MIDI - 21)."""

    def bp(self, rows, seconds=1.0):
        t = np.arange(int(seconds * 100)) * 0.01
        act = np.zeros((len(t), 88))
        for pitch, level in rows.items():
            act[:, pitch - 21] = level
        return {"times": t, "notes": act}

    def test_note_moves_to_louder_octave_and_lists_the_double(self):
        from s1bridge import melody
        found, spans = melody.layers([{"start": 0.1, "end": 0.9, "pitch": 64}], self.bp({64: 0.3, 76: 0.6}))
        self.assertEqual((found[0]["pitch"], found[0]["octave_from"], found[0]["also"]), (76, 64, [64]))
        self.assertEqual(spans, [[0.0, 1.0]])

    def test_single_voice_is_left_alone(self):
        from s1bridge import melody
        found, spans = melody.layers([{"start": 0.1, "end": 0.9, "pitch": 64}], self.bp({64: 0.7, 65: 0.3}))
        self.assertEqual(found[0]["pitch"], 64)
        self.assertNotIn("also", found[0])  # a neighbouring semitone is smear, not a voice
        self.assertEqual(spans, [])

    def test_harmony_is_reported(self):
        from s1bridge import melody
        found, _ = melody.layers([{"start": 0.1, "end": 0.9, "pitch": 64}], self.bp({64: 0.6, 67: 0.4}))
        self.assertEqual((found[0]["pitch"], found[0]["also"]), (64, [67]))


@unittest.skipIf(np is None or not os.path.isfile(tools.ANALYSIS_PYTHON) or
                 not os.path.isfile(os.path.join(tools.config.ANALYSIS_DIR, "models", "crepe-full.onnx")),
                 "needs the analysis pack (studio-one-ai-bridge analysis-setup)")
class CrepeEndToEndTests(unittest.TestCase):
    def test_synthetic_melody(self):
        x, truth = sung([(60, 0.5), (62, 0.5), (64, 0.25), (64, 0.25), (None, 0.2), (67, 1.0), (65, 0.4),
                         (64, 0.6)])
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "m.wav")
            write_wav(path, x, 44100)
            r = tools._run_analysis("melody", [path, 0, "-", "crepe"])
        finally:
            shutil.rmtree(d)
        self.assertEqual([n["pitch"] for n in r["notes"]], [p for _, p in truth])
        for n, (start, _) in zip(r["notes"], truth):
            self.assertAlmostEqual(n["start"], start, delta=0.03)
            self.assertLess(abs(n["cents"]), 30)
        self.assertEqual(r["layered"], [])

    def test_basic_pitch_finds_a_second_voice_in_time(self):
        a, _ = sung([(None, 3.0), (64, 2.0)])  # the second voice enters 3 s in, past the first window
        b, _ = sung([(None, 3.0), (71, 2.0)])
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "two.wav")
            write_wav(path, a + b, 44100)
            r = tools._run_analysis("melody", [path, 0, "-", "crepe"])
        finally:
            shutil.rmtree(d)
        self.assertTrue(r["layered"], r)
        self.assertAlmostEqual(r["layered"][0][0], 3.0, delta=0.15)
        self.assertTrue(all(n.get("also") for n in r["notes"]), r["notes"])


if __name__ == "__main__":
    unittest.main()
