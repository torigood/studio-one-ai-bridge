"""Composing tools (pattern generator, note transforms, bar copy), snapshots and the mix check.

The live parts run against a fake bridge (no Studio One). The mix check needs numpy.
Run: python3 -m unittest discover -s tests   (from the repo root)
"""
import os
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge import compose, live, snapshots, tools  # noqa: E402
from s1bridge.common import ToolError  # noqa: E402

try:
    import numpy as np
    from s1bridge import mix_analysis
except ImportError:
    np = None


class Chords(unittest.TestCase):
    def test_chord_tones(self):
        self.assertEqual(compose.chord_tones("Am7"), (9, [9, 0, 4, 7], 9))
        self.assertEqual(compose.chord_tones("C/E"), (0, [0, 4, 7], 4))
        self.assertEqual(compose.chord_tones("Bb")[0], 10)
        with self.assertRaises(ToolError):
            compose.chord_tones("H7")

    def test_progression_holds_with_percent(self):
        p = compose.progression("Am % F G", 0, 4)
        self.assertEqual([(c["beat"], c["length"], c["chord"]) for c in p],
                         [(0, 8.0, "Am"), (8.0, 4.0, "F"), (12.0, 4.0, "G")])


class Voicing(unittest.TestCase):
    def test_voice_leading_moves_little(self):
        chords = compose.progression("C Am F G", 0, 4)
        vs = compose.voice_lead(chords, 60, 4)
        for prev, cur in zip(vs, vs[1:]):
            self.assertLessEqual(sum(abs(a - b) for a, b in zip(prev, cur)), 12)
        for v, c in zip(vs, chords):
            self.assertEqual({p % 12 for p in v}, set(compose.chord_tones(c["chord"])[1]))
            self.assertTrue(48 <= min(v) and max(v) <= 76, v)

    def test_five_note_chord_drops_fifth(self):
        v = compose.voice_lead([{"beat": 0, "length": 4, "chord": "Cmaj9"}], 60, 4)[0]
        self.assertNotIn(7, {p % 12 for p in v})


class Generate(unittest.TestCase):
    chords = compose.progression("Am F", 0, 4)

    def test_bass_root_one_note_per_chord(self):
        n = compose.generate("bass", self.chords)
        self.assertEqual([(x["pitch"], x["beat"]) for x in n], [(33, 0), (41, 4.0)])

    def test_bass_root_fifth_and_walk(self):
        n = compose.generate("bass", self.chords, pattern="root_fifth")
        self.assertEqual([x["pitch"] for x in n[:4]], [33, 40, 33, 40])
        w = compose.generate("bass", self.chords, pattern="walk")
        self.assertEqual(len(w), 8)
        self.assertIn(w[3]["pitch"], (40, 42))  # step into F (41)

    def test_pad_and_stabs(self):
        pad = compose.generate("pad", self.chords)
        self.assertEqual(len(pad), 8)
        self.assertTrue(all(x["length"] > 3.9 for x in pad))
        stabs = compose.generate("stabs", self.chords)
        self.assertEqual(len(stabs), 32)

    def test_arp_directions_and_seed(self):
        up = compose.generate("arp", self.chords[:1], rate=0.5)
        self.assertEqual(len(up), 8)
        self.assertEqual([x["pitch"] for x in up[:4]], sorted(x["pitch"] for x in up[:4]))
        r1 = compose.generate("arp", self.chords, direction="random", seed=3)
        r2 = compose.generate("arp", self.chords, direction="random", seed=3)
        self.assertEqual(r1, r2)

    def test_unknown_style(self):
        with self.assertRaises(ToolError):
            compose.generate("drums", self.chords)


class Transform(unittest.TestCase):
    notes = [{"index": 0, "pitch": 60, "velocity": 0.5, "start": 0.07, "length": 0.5},
             {"index": 1, "pitch": 62, "velocity": 0.5, "start": 0.5, "length": 0.5},
             {"index": 2, "pitch": 64, "velocity": 0.5, "start": 1.0, "length": 0.5}]

    def test_quantize_with_strength(self):
        e = compose.transform(self.notes, 0, quantize=0.25)
        self.assertEqual(e, [{"index": 0, "start": 0.0}])
        e = compose.transform(self.notes, 0, quantize=0.25, strength=0.5)
        self.assertAlmostEqual(e[0]["start"], 0.035)

    def test_quantize_uses_song_grid(self):
        e = compose.transform([dict(self.notes[1], start=0.4)], 0.1, quantize=0.25)
        self.assertEqual(e, [])  # part at 0.1 + 0.4 = 0.5 is on the grid

    def test_swing_moves_offbeats_only(self):
        e = compose.transform(self.notes, 0, swing=66)
        self.assertEqual([x["index"] for x in e], [1])
        self.assertAlmostEqual(e[0]["start"], 0.66)

    def test_transpose_velocity_and_range(self):
        e = compose.transform(self.notes, 0, transpose=12, velocity_scale=1.2)
        self.assertEqual(e[0]["pitch"], 72)
        self.assertAlmostEqual(e[0]["velocity"], 0.6)
        with self.assertRaises(ToolError):
            compose.transform([dict(self.notes[0], pitch=120)], 0, transpose=12)

    def test_humanize_is_seeded(self):
        a = compose.transform(self.notes, 0, humanize=1, seed=1)
        self.assertEqual(a, compose.transform(self.notes, 0, humanize=1, seed=1))
        self.assertEqual(len(a), 3)


class FakeSong:
    """Instrument parts on tracks, enough for read_notes / insert_notes / delete_event / list_events."""

    def __init__(self):
        self.tracks = {"Keys": [{"start": 0.0, "length": 8.0, "notes": [
            {"index": 0, "pitch": 60, "velocity": 0.8, "start": 0.0, "length": 1.0},
            {"index": 1, "pitch": 64, "velocity": 0.8, "start": 4.0, "length": 6.0}]}],
            "Vox": None}
        self.calls = []

    def __call__(self, op, args, wait):
        self.calls.append((op, args))
        if op == "transport":
            return {"before": {"timeSignatureNum": 4, "timeSignatureDenom": 4}}
        if op == "list_tracks":
            return {"tracks": [{"index": i, "name": n, "mediaType": "Music" if p is not None else "Audio"}
                               for i, (n, p) in enumerate(self.tracks.items())]}
        if op == "list_events":
            return {"events": [{"index": i, "start": p["start"], "length": p["length"], "mediaType": "Music"}
                               for i, p in enumerate(self.tracks[args["track"]] or [])]}
        if op == "read_notes":
            return {"track": args["track"], "parts": [
                p for p in self.tracks[args["track"]] or []
                if "part_start" not in args or p["start"] == args["part_start"]]}
        if op == "delete_event":
            parts = self.tracks[args["track"]]
            parts[:] = [p for p in parts if p["start"] != args["start_beats"]]
            return {"deleted": 1}
        if op == "insert_notes":
            self.tracks.setdefault(args["track"], []).append(
                {"start": args["start_beats"], "length": args["length_beats"],
                 "notes": [{"index": i, "pitch": n["pitch"], "velocity": n["velocity"], "start": n["start_beats"],
                            "length": n["length_beats"]} for i, n in enumerate(args["notes"])]})
            return {"notes": len(args["notes"])}
        if op == "edit_notes":
            return {"edited": len(args["edits"])}
        if op == "read_chords":
            return {"chords": [{"start": 0, "length": 4, "name": "C"}, {"start": 4, "length": 4, "name": "G"}]}
        raise AssertionError("unexpected op " + op)


class LiveTools(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSong()
        self.p = mock.patch.object(live, "bridge", self.fake)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_write_pattern_from_progression_with_repeat(self):
        self.fake.tracks["Bass"] = []
        r = tools.call("daw_write_pattern", {"track": "Bass", "style": "bass", "progression": "Am F",
                                             "start_bar": 3, "repeat": 2})
        self.assertEqual((r["part_start"], r["part_length"], r["notes"]), (8.0, 16.0, 4))
        self.assertEqual(r["chords"], ["Am", "F", "Am", "F"])
        notes = self.fake.tracks["Bass"][0]["notes"]
        self.assertEqual([n["start"] for n in notes], [0.0, 4.0, 8.0, 12.0])

    def test_write_pattern_from_chord_track_replaces_parts_inside(self):
        r = tools.call("daw_write_pattern", {"track": "Keys", "style": "pad"})
        self.assertEqual(r["replaced_parts"], [0.0])
        self.assertEqual(r["chords"], ["C", "G"])
        self.assertEqual(len(self.fake.tracks["Keys"]), 1)

    def test_transform_sends_edit_notes(self):
        r = tools.call("daw_transform_notes", {"track": "Keys", "transpose": 2})
        self.assertEqual(r["parts"], [{"part_start": 0.0, "notes": 2, "changed": 2}])
        edits = [a for op, a in self.fake.calls if op == "edit_notes"][0]["edits"]
        self.assertEqual([e["pitch"] for e in edits], [62, 66])
        with self.assertRaises(ToolError):
            tools.call("daw_transform_notes", {"track": "Keys", "swing": 90})

    def test_copy_bars_clips_and_skips_audio(self):
        r = tools.call("daw_copy_bars", {"from_bar": 1, "to_bar": 3, "dest_bar": 5})
        self.assertEqual(r["skipped"], ["Vox"])
        new = self.fake.tracks["Keys"][-1]
        self.assertEqual((new["start"], new["length"]), (16.0, 8.0))
        self.assertEqual([(n["start"], n["length"]) for n in new["notes"]], [(0.0, 1.0), (4.0, 4.0)])
        with self.assertRaises(ToolError):
            tools.call("daw_copy_bars", {"from_bar": 1, "to_bar": 3, "dest_bar": 2})


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.song = os.path.join(self.tmp, "Tune.song")
        with open(self.song, "w") as f:
            f.write("v1")
        self.patches = [mock.patch.object(snapshots, "ROOT", os.path.join(self.tmp, "snaps")),
                        mock.patch.object(snapshots.songwrite, "BACKUP_DIR", os.path.join(self.tmp, "bak")),
                        mock.patch.object(live, "open_in_daw", lambda p: False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp)

    def test_take_list_restore(self):
        a = tools.call("daw_snapshot", {"action": "take", "label": "before bass/drums", "path": self.song})
        self.assertTrue(a["snapshot"].endswith("before bass_drums"))
        with open(self.song, "w") as f:
            f.write("v2")
        tools.call("daw_snapshot", {"action": "take", "path": self.song})
        listed = tools.call("daw_snapshot", {"action": "list", "path": self.song})["snapshots"]
        self.assertEqual(len(listed), 2)
        r = tools.call("daw_snapshot", {"action": "restore", "snapshot": "before bass_drums", "path": self.song})
        with open(self.song) as f:
            self.assertEqual(f.read(), "v1")
        with open(r["backup"]) as f:
            self.assertEqual(f.read(), "v2")
        with self.assertRaises(ToolError):
            tools.call("daw_snapshot", {"action": "restore", "snapshot": "nope", "path": self.song})

    def test_never_saved_song_is_saved_first(self):
        new = os.path.join(self.tmp, "New.song")

        def press(cc):
            with open(new, "w") as f:
                f.write("saved")
        with mock.patch.object(live, "active_song_path", lambda wait=None, must_exist=True: new), \
                mock.patch.object(snapshots.midi, "press", press):
            r = tools.call("daw_snapshot", {"action": "take"})
        self.assertEqual(r["song"], new)
        self.assertTrue(os.path.isfile(new))


def _wav(path, x, rate=48000):
    """Write stereo float frames as 16-bit PCM."""
    data = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " +
                struct.pack("<IHHIIHH", 16, 1, x.shape[1], rate, rate * 2 * x.shape[1], 2 * x.shape[1], 16) +
                b"data" + struct.pack("<I", len(data)) + data)


@unittest.skipIf(np is None, "needs numpy")
class MixCheck(unittest.TestCase):
    def test_loudness_of_1k_sine(self):
        # BS.1770: a 0 dBFS 1 kHz sine in one channel reads -3.01 LUFS; -20 dB in two channels: -20 LUFS
        t = np.arange(48000 * 5) / 48000.0
        s = 0.1 * np.sin(2 * np.pi * 1000 * t)
        lufs = mix_analysis.loudness(np.stack([s, s], axis=1), 48000)
        self.assertAlmostEqual(lufs, -20.0, delta=0.3)

    def test_masking_between_overlapping_stems(self):
        tmp = tempfile.mkdtemp()
        try:
            t = np.arange(48000 * 3) / 48000.0
            bass = 0.3 * np.sin(2 * np.pi * 120 * t)
            kick = 0.3 * np.sin(2 * np.pi * 125 * t)
            hat = 0.05 * np.random.RandomState(0).randn(len(t))
            hat = np.diff(np.concatenate([[0], hat]))  # high-passed noise
            for name, x in (("bass", bass), ("kick", kick), ("hat", hat)):
                _wav(os.path.join(tmp, name + ".wav"), np.stack([x, x], axis=1))
            r = mix_analysis.analyze([(n, os.path.join(tmp, n + ".wav")) for n in ("bass", "kick", "hat")])
            top = r["masking"][0]
            self.assertEqual(sorted(top["stems"]), ["bass", "kick"])
            self.assertEqual(top["band_hz"], 125)
            self.assertFalse(any("hat" in h["stems"] and h["band_hz"] <= 250 for h in r["masking"]))
            self.assertEqual(r["files"]["bass"]["stereo"]["correlation"], 1.0)
            self.assertEqual(r["files"]["bass"]["bands_db"]["125"], 0.0)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
