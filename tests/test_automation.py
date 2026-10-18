"""song_write_automation: mixer envelopes in song.xml + Envelopes/*.envelopex (offline)."""
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge import automation, songwrite, ubjson  # noqa: E402

SONG_XML = """﻿<?xml version="1.0" encoding="UTF-8"?>
<Song xmlns:x="https://www.presonus.software/xml/ccl"><Attributes x:id="Root">
<TimeSignatureMap x:id="timeSignatureMap"><TimeSignatureMapSegment numerator="4" denominator="4"/></TimeSignatureMap>
<List x:id="Tracks">
<MediaTrack mediaType="Audio" version="1" trackID="{T1}" timeFormat="2" name="Loop">
<UID x:id="channelID" uid="{AUD}"/>
<Attributes x:id="AutomationRegionList" visible="0">
<AutomationRegion title="Volume" identity="param:///AudioMixer/{AUD}/volume" metaIdentity="AudioTrackChannel/volume" color="FFFFD829" mute="0">
<Url x:id="points" type="1" url="media:///Envelopes/Loop/Volume.envelopex"/>
</AutomationRegion>
</Attributes>
</MediaTrack>
<MediaTrack mediaType="Music" version="1" trackID="{T2}" timeFormat="2" name="Synth">
<UID x:id="channelID" uid="{MUS}"/>
</MediaTrack>
</List></Attributes></Song>"""

MIXER_XML = """﻿<?xml version="1.0" encoding="UTF-8"?>
<AudioMixer xmlns:x="https://www.presonus.software/xml/ccl">
<AudioTrackChannel gain="0.5" pan="0.5" name="Channel01" label="Loop">
<UID x:id="uniqueID" uid="{AUD}"/>
<Attributes x:id="Automation" mode="0"/>
</AudioTrackChannel>
<AudioSynthChannel gain="1" pan="0.5" name="Channel02" label="Synth">
<UID x:id="uniqueID" uid="{SYN}"/>
<Attributes x:id="Automation" mode="0"/>
</AudioSynthChannel>
</AudioMixer>"""


class Automation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_backup = songwrite.BACKUP_DIR
        songwrite.backup.__globals__["BACKUP_DIR"] = os.path.join(self.tmp, "backups")
        self.path = os.path.join(self.tmp, "t.song")
        with zipfile.ZipFile(self.path, "w") as z:
            z.writestr("Song/song.xml", SONG_XML.encode("utf-8"))
            z.writestr("Devices/audiomixer.xml", MIXER_XML.encode("utf-8"))
            z.writestr("Envelopes/Loop/Volume.envelopex",
                       ubjson.dumps({"events": [{"start": 4.0, "value": 0.1}, {"start": 40.0, "value": 0.2}]}))

    def tearDown(self):
        songwrite.backup.__globals__["BACKUP_DIR"] = self.orig_backup
        shutil.rmtree(self.tmp)

    def read(self, name):
        with zipfile.ZipFile(self.path) as z:
            return z.read(name)

    def test_values(self):
        self.assertAlmostEqual(automation.normalize("volume", 0), 1 / automation.MAX_GAIN)
        self.assertEqual(automation.normalize("volume", 10), 1.0)
        self.assertEqual(automation.normalize("volume", "-inf"), 0.0)
        self.assertEqual(automation.normalize("pan", 1), 0.0)     # right is below 0.5 in Studio One
        self.assertEqual(automation.normalize("pan", -1), 1.0)
        self.assertEqual(automation.normalize("mute", 1), 1.0)
        with self.assertRaises(songwrite.SongWriteError):
            automation.normalize("volume", 12)

    def test_existing_lane_replaces_range_and_sets_read_mode(self):
        r = automation.write_automation(self.path, "Loop", "volume", [{"bar": 2, "value": 0}, {"bar": 5, "value": -6}])
        self.assertFalse(r["new_lane"])
        self.assertTrue(r["read_mode_set"])
        ev = ubjson.loads(self.read("Envelopes/Loop/Volume.envelopex"))["events"]
        self.assertEqual([e["start"] for e in ev], [4.0, 16.0, 40.0])  # old point at 4 replaced, 40 kept
        mixer = self.read("Devices/audiomixer.xml").decode("utf-8-sig")
        loop = mixer[mixer.find("<AudioTrackChannel"):mixer.find("</AudioTrackChannel>")]
        self.assertIn('mode="1"', loop)
        synth = mixer[mixer.find("<AudioSynthChannel"):]
        self.assertIn('mode="0"', synth)

    def test_new_lane_on_instrument_track(self):
        # instrument track -> its AudioSynthChannel (labelled like the track)
        r = automation.write_automation(self.path, "Synth", "pan", [{"beat": 0, "value": -1}, {"beat": 8, "value": 1}])
        self.assertTrue(r["new_lane"])
        xml = self.read("Song/song.xml").decode("utf-8-sig")
        synth = xml[xml.find('name="Synth"'):]
        self.assertIn('identity="param:///AudioMixer/{SYN}/pan"', synth)
        self.assertIn('metaIdentity="AudioSynthChannel/pan"', synth)
        self.assertIn('url="media:///Envelopes/Synth/Pan.envelopex"', synth)
        self.assertEqual(len(re.findall("<AutomationRegion ", xml)), 2)
        ev = ubjson.loads(self.read("Envelopes/Synth/Pan.envelopex"))["events"]
        self.assertEqual([(e["start"], e["value"]) for e in ev], [(0.0, 1.0), (8.0, 0.0)])

    def test_errors(self):
        with self.assertRaises(songwrite.SongWriteError):
            automation.write_automation(self.path, "Nope", "volume", [{"bar": 1, "value": 0}])
        with self.assertRaises(songwrite.SongWriteError):
            automation.write_automation(self.path, "Loop", "cutoff", [{"bar": 1, "value": 0}])
        with self.assertRaises(songwrite.SongWriteError):
            automation.write_automation(self.path, "Loop", "volume", [{"value": 0}])


if __name__ == "__main__":
    unittest.main()
