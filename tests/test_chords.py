"""song_write_chords: chord names -> Chord Track events in song.xml (offline)."""
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

SONG_XML = """﻿<?xml version="1.0" encoding="UTF-8"?>
<Song><Attributes x:id="Root"><List x:id="Tracks">
<ChordTrack version="1" trackID="{T}" timeFormat="2">
<ChordEvent start="2" timeFormat="2" length="4"><Attributes x:id="chord" root="0" intervals="FF 0 0 0 FF 0 0 FF 0 0 0 0"/></ChordEvent>
<ChordEvent start="10" timeFormat="2" length="2"><Attributes x:id="chord" root="7" intervals="FF 0 0 0 FF 0 0 FF 0 0 0 0"/></ChordEvent>
<UID x:id="channelID" uid="{U}"/>
</ChordTrack></List></Attributes></Song>"""


class Chords(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["S1BRIDGE_DATA"] = os.path.join(self.tmp, "data")
        from s1bridge import chords
        chords.backup.__globals__["BACKUP_DIR"] = os.path.join(self.tmp, "backups")
        self.chords = chords
        self.path = os.path.join(self.tmp, "t.song")
        with zipfile.ZipFile(self.path, "w") as z:
            z.writestr("Song/song.xml", SONG_XML.encode("utf-8"))
            z.writestr("Song/mediapool.xml", b"<MediaPool/>")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_parse(self):
        self.assertEqual(self.chords.parse_chord("F#m7b5"), (6, (0, 3, 6, 10), None))
        self.assertEqual(self.chords.parse_chord("Bbmaj7"), (-2, (0, 4, 7, 11), None))
        self.assertEqual(self.chords.parse_chord("D#m7"), (9, (0, 3, 7, 10), None))  # observed in Studio One
        self.assertEqual(self.chords.parse_chord("C/E"), (0, (0, 4, 7), 4))
        # names as daw_read_chords prints them
        self.assertEqual(self.chords.parse_chord("F♯ø7/A"), (6, (0, 3, 6, 10), 3))
        self.assertEqual(self.chords.parse_chord("B♭maj7"), (-2, (0, 4, 7, 11), None))
        self.assertEqual(self.chords.parse_chord("C°7"), (0, (0, 3, 6, 9), None))
        with self.assertRaises(ValueError):
            self.chords.parse_chord("H7")

    def test_write_replaces_overlap(self):
        r = self.chords.write_chords(self.path, [{"beat": 0, "length": 4, "chord": "Am"},
                                                 {"beat": 4, "length": 4, "chord": "G7/B"}])
        self.assertEqual(r["replaced"], 1)  # the C at 2..6 overlaps, the G at 10 does not
        with zipfile.ZipFile(self.path) as z:
            xml = z.read("Song/song.xml").decode("utf-8")
            self.assertEqual(z.read("Song/mediapool.xml"), b"<MediaPool/>")
        self.assertTrue(xml.startswith("﻿"))
        events = re.findall(r'<ChordEvent( start="([^"]*)")? timeFormat="2" length="([^"]*)">\s*'
                            r'<Attributes x:id="chord" root="(-?\d+)"(?: bass="-?\d+")? intervals="([^"]*)"', xml)
        self.assertEqual([(e[1], e[2], e[3]) for e in events], [("10", "2", "7"), ("", "4", "3"), ("4", "4", "1")])
        self.assertIn('root="1" bass="5"', xml)
        self.assertEqual(events[2][4], "FF 0 0 0 FF 0 0 FF 0 0 FF 0")
        self.assertLess(xml.index('start="4"'), xml.index('<UID x:id="channelID"'))


if __name__ == "__main__":
    unittest.main()
