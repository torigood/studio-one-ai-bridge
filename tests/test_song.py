"""Song parser + UBJSON tests on a synthetic .song built to match observed Studio One 5 files."""
import os
import struct
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge import ubjson  # noqa: E402
from s1bridge.song import Song  # noqa: E402


def ub_key(s):
    b = s.encode()
    return b"i" + struct.pack(">b", len(b)) + b


def ub_note(start, pitch, length, vel):
    return (b"{" + ub_key("start") + b"D" + struct.pack(">d", start)
            + ub_key("pitch") + b"i" + struct.pack(">b", pitch)
            + ub_key("length") + b"D" + struct.pack(">d", length)
            + ub_key("velocity") + b"D" + struct.pack(">d", vel) + b"}")


MUSICX = (b"{" + ub_key("timeFormat") + b"i\x02" + ub_key("events") + b"["
          + ub_note(7.0, 50, 1.0, 0.5)    # before part offset -> hidden
          + ub_note(8.5, 60, 1.0, 0.8)
          + ub_note(9.0, 64, 0.5, 0.6)
          + b"]}")

SONG_XML = """﻿<?xml version="1.0" encoding="UTF-8"?>
<Song>
 <Attributes x:id="Root">
  <Attributes x:id="timeContext">
   <TempoMap x:id="tempoMap"><TempoMapSegment start="0" tempo="0.5"/></TempoMap>
   <TimeSignatureMap x:id="timeSignatureMap"><TimeSignatureMapSegment start="0" numerator="3" denominator="4"/></TimeSignatureMap>
  </Attributes>
  <List x:id="Tracks">
   <MarkerTrack><MarkerEvent markerType="2" name="Start"/></MarkerTrack>
   <FolderTrack trackID="{F}" name="Keys"/>
   <MediaTrack mediaType="Music" trackID="{T}" parentFolder="{F}" name="Piano">
    <UID x:id="channelID" uid="{C}"/>
    <List x:id="Events">
     <MusicPart clipID="{CLIP}" start="16" length="4" offset="8" name="P1"/>
    </List>
   </MediaTrack>
  </List>
 </Attributes>
</Song>"""

POOL_XML = """<MediaPool><MusicClip mediaID="{CLIP}"><Url x:id="dataPath" url="media:///Performances/Piano/P1.musicx"/></MusicClip></MediaPool>"""

MIXER_XML = """<AudioMixer><AudioSynthChannel gain="0.5" pan="0.25" label="Piano" mute="0" solo="1">
<UID x:id="uniqueID" uid="{OTHER}"/></AudioSynthChannel></AudioMixer>"""


class SongTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path = os.path.join(cls.tmp, "t.song")
        with zipfile.ZipFile(cls.path, "w") as z:
            z.writestr("Song/song.xml", SONG_XML)
            z.writestr("Song/mediapool.xml", POOL_XML)
            z.writestr("Devices/audiomixer.xml", MIXER_XML)
            z.writestr("Performances/Piano/P1.musicx", MUSICX)

    def test_ubjson(self):
        d = ubjson.loads(MUSICX)
        self.assertEqual(d["timeFormat"], 2)
        self.assertEqual(d["events"][1]["pitch"], 60)
        self.assertEqual(ubjson.loads(b"[$i#i\x03\x01\x02\x03"), [1, 2, 3])

    def test_summary(self):
        with Song(self.path) as s:
            sm = s.summary()
        self.assertEqual(sm["tempo"][0]["bpm"], 120.0)
        self.assertEqual(sm["time_signatures"][0]["numerator"], 3)
        names = [(t["name"], t["folder"]) for t in sm["tracks"]]
        self.assertEqual(names, [("Keys", None), ("Piano", "Keys")])
        piano = sm["tracks"][1]
        self.assertEqual(piano["note_count"], 2)
        self.assertAlmostEqual(piano["mixer"]["volume_db"], -6.02, places=2)
        self.assertEqual(piano["mixer"]["pan"], -0.5)

    def test_notes_absolute_positions(self):
        with Song(self.path) as s:
            t = [t for t in s.tracks(include_notes=True) if t["name"] == "Piano"][0]
        notes = t["parts"][0]["notes"]
        # part starts at 16, clip offset 8: clip 8.5 -> 16.5, clip 9.0 -> 17.0
        self.assertEqual([(n["start_beats"], n["pitch"]) for n in notes], [(16.5, 60), (17.0, 64)])


if __name__ == "__main__":
    unittest.main()
