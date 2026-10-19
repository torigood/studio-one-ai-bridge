"""song_check_clashes rules and reading the Chord Track (stdlib only)."""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge import clashes  # noqa: E402
from s1bridge.song import Song  # noqa: E402

SONG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Song><Attributes x:id="Root"><List x:id="Tracks">
<ChordTrack version="1" trackID="{T}" timeFormat="2">
<ChordEvent timeFormat="2" length="4"><Attributes x:id="chord" root="0" intervals="FF 0 0 0 FF 0 0 FF 0 0 0 0"/></ChordEvent>
<ChordEvent start="4" timeFormat="2" length="4"><Attributes x:id="chord" root="3" bass="7" intervals="FF 0 0 FF 0 0 0 FF 0 0 FF 0"/></ChordEvent>
<ChordEvent start="8" timeFormat="2" length="4"><Attributes x:id="chord" root="-2" intervals="FF 0 0 0 FF 0 0 FF 0 6 0 0"/></ChordEvent>
</ChordTrack></List></Attributes></Song>"""

C_MAJOR = [{"start_beats": 0, "length_beats": 8, "name": "C", "root": 0, "pcs": [0, 4, 7]}]


def n(beat, length, pitch, **kw):
    return dict(beat=beat, len=length, pitch=pitch, **kw)


def kinds(issues):
    return [(x["kind"], x["detail"].split(";")[0]) for x in issues]


class ChordTrackTests(unittest.TestCase):
    def test_read_chords(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "t.song")
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("Song/song.xml", SONG_XML)
                z.writestr("Song/mediapool.xml", "<MediaPool/>")
            with Song(path) as s:
                got = [(c["start_beats"], c["name"], c["pcs"]) for c in s.chords()]
        finally:
            shutil.rmtree(d)
        self.assertEqual(got, [(0.0, "C", [0, 4, 7]), (4.0, "Am7/C#", [0, 4, 7, 9]),
                               (8.0, "Bb6", [2, 5, 7, 10])])


class RuleTests(unittest.TestCase):
    def test_minor_second_against_vocal(self):
        out = clashes.check({"Strings": [n(0, 2, 65)]}, [n(0, 2, 64)], C_MAJOR)
        self.assertIn(("vocal_clash", "minor 2nd vs vocal"), kinds(out))
        self.assertIn("off C: Strings", [x["detail"].split("; ")[1] for x in out if x["kind"] == "vocal_clash"])

    def test_major_seventh_over_chord_tone_is_colour(self):
        # vocal B4 over a C held in the strings: the vocal's 7th, not a clash
        out = clashes.check({"Strings": [n(0, 2, 60)]}, [n(0, 2, 71)], C_MAJOR)
        self.assertNotIn("vocal_clash", [x["kind"] for x in out])

    def test_short_overlap_is_ignored(self):
        out = clashes.check({"Flute": [n(0, 1, 65)]}, [n(0.9, 1, 64)], C_MAJOR)
        self.assertNotIn("vocal_clash", [x["kind"] for x in out])

    def test_parts_a_minor_ninth_apart(self):
        out = clashes.check({"Cello": [n(0, 2, 48)], "Horn": [n(0, 2, 61)]}, [], C_MAJOR)
        self.assertIn(("part_clash", "minor 9th"), kinds(out))

    def test_octaves_and_unisons_are_fine(self):
        out = clashes.check({"Cello": [n(0, 2, 48)], "Horn": [n(0, 2, 60)], "Viola": [n(0, 2, 60)]}, [], C_MAJOR)
        self.assertEqual(out, [])

    def test_off_chord_and_avoid_note(self):
        out = clashes.check({"Oboe": [n(0, 2, 65)], "Clarinet": [n(2, 2, 66)], "Flute": [n(0, 2, 62)]}, [], C_MAJOR)
        details = sorted(x["detail"] for x in out if x["kind"] == "off_chord")
        self.assertEqual(details, ["avoid note (4th over C)", "not in C"])  # the 9th (D) passes

    def test_crowding_and_lockstep(self):
        parts = {"P%d" % i: [n(0, 1, 50 + i), n(2, 1, 50 + i)] for i in range(4)}
        parts["Viola"] = [n(0, 4, 62)]
        out = clashes.check(parts, [n(0, 4, 64)], [])
        got = [x["kind"] for x in out]
        self.assertIn("crowding", got)
        lock = [x for x in out if x["kind"] == "lockstep"][0]
        self.assertEqual(lock["tracks"], ["P0", "P1", "P2", "P3"])

    def test_range_and_summary(self):
        out = clashes.check({"Strings": [n(0, 2, 65), n(16, 2, 65)]}, [n(0, 2, 64), n(16, 2, 64)], [],
                            lo=8, hi=20)
        self.assertEqual([(x["bar"], x["kind"]) for x in out], [(5, "vocal_clash"), (5, "crowding")])
        self.assertEqual(clashes.summary(out), {"by_kind": {"crowding": 1, "vocal_clash": 1},
                                                "by_track": {"Strings": 2}})


if __name__ == "__main__":
    unittest.main()
