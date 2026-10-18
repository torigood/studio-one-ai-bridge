"""song_write_markers: named markers -> Marker Track events in song.xml (offline)."""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge import songwrite  # noqa: E402
from s1bridge.song import Song  # noqa: E402

SONG_XML = """﻿<?xml version="1.0" encoding="UTF-8"?>
<Song xmlns:x="https://www.presonus.software/xml/ccl"><Attributes x:id="Root">
<TimeSignatureMap x:id="timeSignatureMap">
<TimeSignatureMapSegment numerator="4" denominator="4"/>
<TimeSignatureMapSegment start="8" numerator="3" denominator="4"/>
</TimeSignatureMap>
<List x:id="Tracks">
<MarkerTrack version="1" trackID="{M}" timeFormat="2">
<MarkerEvent markerType="2" timeFormat="2" name="Start"/>
<MarkerEvent markerType="3" start="600" timeFormat="2" name="End"/>
<MarkerEvent markerType="0" start="32" timeFormat="2" name="Old"/>
</MarkerTrack>
</List></Attributes></Song>"""


class Markers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_backup = songwrite.BACKUP_DIR
        songwrite.backup.__globals__["BACKUP_DIR"] = os.path.join(self.tmp, "backups")
        self.path = os.path.join(self.tmp, "t.song")
        with zipfile.ZipFile(self.path, "w") as z:
            z.writestr("Song/song.xml", SONG_XML.encode("utf-8"))
            z.writestr("Song/mediapool.xml", b"<MediaPool/>")

    def tearDown(self):
        songwrite.backup.__globals__["BACKUP_DIR"] = self.orig_backup
        shutil.rmtree(self.tmp)

    def markers(self):
        return [(m["name"], m["start_beats"], m["type"]) for m in Song(self.path).markers()]

    def test_bar_to_beats_follows_signature_changes(self):
        sigs = [{"start": 0, "numerator": 4, "denominator": 4}, {"start": 8, "numerator": 3, "denominator": 4}]
        self.assertEqual(songwrite.bar_to_beats(sigs, 1), 0)
        self.assertEqual(songwrite.bar_to_beats(sigs, 3), 8)
        self.assertEqual(songwrite.bar_to_beats(sigs, 5), 14)
        self.assertEqual(songwrite.bar_to_beats([{"start": 0, "numerator": 6, "denominator": 8}], 3), 6)

    def test_replace_keeps_start_and_end(self):
        r = songwrite.write_markers(self.path, [{"name": "Chorus", "bar": 5}, {"name": "Intro", "beat": 0}])
        self.assertEqual(r["kept"], 2)
        self.assertEqual(self.markers(), [("Start", 0.0, "2"), ("End", 600.0, "3"),
                                          ("Intro", 0.0, "0"), ("Chorus", 14.0, "0")])

    def test_add_without_replace_and_escaping(self):
        songwrite.write_markers(self.path, [{"name": 'Verse "A" & B', "bar": 2}], replace=False)
        self.assertIn(('Verse "A" & B', 4.0, "0"), self.markers())
        self.assertIn(("Old", 32.0, "0"), self.markers())
        with zipfile.ZipFile(self.path) as z:
            xml = z.read("Song/song.xml").decode("utf-8-sig")
            self.assertEqual(z.read("Song/mediapool.xml"), b"<MediaPool/>")
        self.assertIn('name="Verse &quot;A&quot; &amp; B"', xml)

    def test_bad_input(self):
        for bad in ([{"name": ""}], [{"name": "X"}], [{"name": "X", "bar": 0}]):
            with self.assertRaises(songwrite.SongWriteError):
                songwrite.write_markers(self.path, bad)


if __name__ == "__main__":
    unittest.main()
