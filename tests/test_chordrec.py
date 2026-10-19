"""audio_chords: beat grid, merging and spelling (stdlib, mocked analysis); decoding (numpy);
a synthetic progression end to end (analysis pack)."""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from s1bridge import tools  # noqa: E402

try:
    import numpy as np
except ImportError:
    np = None

SONG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Song><Attributes x:id="Root">
<TempoMap x:id="tempoMap"><TempoMapSegment curveType="0" start="0" end="1e200" tempo="0.5"/></TempoMap>
<TimeSignatureMap x:id="timeSignatureMap"><TimeSignatureMapSegment numerator="4" denominator="4"/></TimeSignatureMap>
<List x:id="Tracks"/></Attributes></Song>"""


def chord(root, quality="", bass=None, score=0.8):
    return {"root": root, "quality": quality, "bass": root if bass is None else bass, "score": score}


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.song = os.path.join(self.dir, "t.song")
        with zipfile.ZipFile(self.song, "w") as z:
            z.writestr("Song/song.xml", SONG_XML)
            z.writestr("Song/mediapool.xml", "<MediaPool/>")
        self.wav = os.path.join(self.dir, "a.wav")
        import struct
        with open(self.wav, "wb") as f:  # 10 s of silence, 16-bit mono 1 kHz
            data = b"\0\0" * 10000
            f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " +
                    struct.pack("<IHHIIHH", 16, 1, 1, 1000, 2000, 2, 16) + b"data" + struct.pack("<I", len(data)) + data)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_tool(self, beats, chroma, **args):
        calls = []

        def fake(module, a, timeout=900):
            calls.append(a)
            n = len(a[3].split(",")) - 1
            return {"beats": (beats * n)[:n], "chroma": chroma}

        with mock.patch.object(tools, "_run_analysis", fake):
            out = tools._audio_chords(dict({"song": self.song, "path": self.wav, "start_beat": 4}, **args))
        return out, calls

    def test_beat_grid_and_strong_beats(self):
        _, calls = self.run_tool([chord(0)], [1] + [0] * 11, from_bar=2, to_bar=3)
        wav, start, length, bounds, strong = calls[0]
        self.assertEqual((start, length), (0.0, 4.0))  # bar 2 = beat 4 = file 0 s; two bars at 120 bpm
        self.assertEqual(bounds.split(",")[:3], ["0.0000", "0.5000", "1.0000"])
        self.assertEqual(strong, "0,2,4,6")

    def test_merging_and_flat_spelling(self):
        eb_major = [0, 0, 0, 5, 0, 0, 0, 3, 3, 0, 4, 0]  # Eb, G, Ab, Bb: an Eb major profile
        out, _ = self.run_tool([chord(3), chord(3), chord(8, "maj7"), chord(10, "7", 2)], eb_major,
                               from_bar=2, to_bar=2)
        self.assertEqual([(c["pos"], c["len"], c["chord"]) for c in out["chords"]],
                         [(1.0, 2, "Eb"), (3.0, 1, "Abmaj7"), (4.0, 1, "Bb7/D")])
        self.assertEqual(out["key"], "Eb major")

    def test_silence_is_left_out(self):
        out, _ = self.run_tool([chord(0), None], [1] + [0] * 11, from_bar=2, to_bar=2)
        self.assertEqual([c["chord"] for c in out["chords"]], ["C", "C"])

    def test_needs_a_source(self):
        with self.assertRaises(tools.ToolError):
            tools._audio_chords({"song": self.song})


@unittest.skipIf(np is None, "needs numpy")
class DecodeTests(unittest.TestCase):
    def profile(self, pcs, bass):
        c, b = np.zeros(12), np.zeros(12)
        c[list(pcs)] = 1.0
        b[bass] = 1.0
        return c, b

    def test_passing_note_does_not_flip_the_chord(self):
        from s1bridge import chordrec
        passing = self.profile({0, 4, 7}, 0)
        passing[0][5] = 0.8  # an F over the C chord on a weak beat
        rows = [self.profile({0, 4, 7}, 0)] * 3 + [passing] + [self.profile({0, 4, 7}, 0)] * 3
        out = chordrec.decode(np.array([r[0] for r in rows]), np.array([r[1] for r in rows]), [0, 2, 4, 6])
        self.assertEqual({(c["root"], c["quality"]) for c in out}, {(0, "")})

    def test_seventh_and_slash(self):
        from s1bridge import chordrec
        rows = [self.profile({7, 11, 2, 5}, 7)] * 4 + [self.profile({4, 7, 11}, 11)] * 4
        out = chordrec.decode(np.array([r[0] for r in rows]), np.array([r[1] for r in rows]), [0, 4])
        self.assertEqual((out[0]["root"], out[0]["quality"]), (7, "7"))
        self.assertEqual((out[-1]["root"], out[-1]["quality"], out[-1]["bass"]), (4, "m", 11))

    def test_silent_beats(self):
        from s1bridge import chordrec
        chroma = np.zeros((4, 12))
        chroma[:2, [0, 4, 7]] = 1
        out = chordrec.decode(chroma, np.zeros((4, 12)), [0, 2])
        self.assertIsNone(out[3])


@unittest.skipIf(np is None or not os.path.isfile(tools.ANALYSIS_PYTHON), "needs the analysis pack")
class EndToEndTests(unittest.TestCase):
    def test_synthetic_progression(self):
        from test_melody import write_wav
        sr, beat = 44100, 0.5
        prog = [([48, 60, 64, 67], "C"), ([45, 57, 60, 64], "Am"), ([41, 57, 60, 65], "F"),
                ([43, 59, 62, 65, 67], "G7"), ([47, 55, 59, 64], "Em/B"), ([50, 57, 62, 66], "D")]
        parts = []
        for notes, _ in prog:
            t = np.arange(int(4 * beat * sr)) / sr
            y = sum(a * np.sin(2 * np.pi * 440 * 2 ** ((p - 69) / 12) * h * t) * np.exp(-1.5 * t)
                    for p in notes for h, a in ((1, 1), (2, .5), (3, .3), (4, .2)))
            parts.append(0.08 * y)
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "p.wav")
            write_wav(path, np.concatenate(parts), sr)
            bounds = ",".join("%.4f" % (i * beat) for i in range(25))
            r = tools._run_analysis("chordrec", [path, 0, "-", bounds, "0,2,4,6,8,10,12,14,16,18,20,22"])
        finally:
            shutil.rmtree(d)
        names = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
        got = [tools._chord_label(c, names) for c in r["beats"]]
        self.assertEqual(got, [name for _, name in prog for _ in range(4)])


if __name__ == "__main__":
    unittest.main()
