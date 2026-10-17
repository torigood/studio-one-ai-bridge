"""Insert removal on a synthetic .song shaped like Studio One 5's Devices/audiomixer.xml."""
import os
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

SONG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Song><Attributes x:id="Root"><List x:id="Tracks">
 <MediaTrack mediaType="Audio" trackID="{T}" name="Drums"><UID x:id="channelID" uid="{CH}"/></MediaTrack>
</List></Attributes></Song>"""


def slot(name, plugin, n):
    return """
					<Attributes name="%s">
						<Attributes x:id="deviceData" name="%s">
							<UID x:id="uniqueID" uid="{D%d}"/>
						</Attributes>
						<Attributes x:id="Presets" pname="default" dirty="0">
							<Attributes x:id="url" type="1" url="file:///x.preset"/>
						</Attributes>
						<String x:id="presetPath" text="Presets/Channels/Drums/%d - %s.fxpreset"/>
					</Attributes>""" % (name, plugin, n, n, plugin)


MIXER_XML = """﻿<AudioMixer>
			<AudioTrackChannel gain="1" pan="0.5" name="Channel01" label="Drums">
				<UID x:id="uniqueID" uid="{CH}"/>
				<Attributes x:id="Inserts">%s%s
					<Attributes x:id="Presets" pname="default" dirty="0"/>
				</Attributes>
			</AudioTrackChannel>
			<AudioSynthChannel gain="1" pan="0.5" name="Channel01" label="Main Output">
				<UID x:id="uniqueID" uid="{S}"/>
				<Attributes x:id="Inserts">%s
				</Attributes>
			</AudioSynthChannel>
</AudioMixer>""" % (slot("FX01", "Compressor", 1), slot("FX02", "Room Reverb", 2), slot("FX01", "Compressor", 3))


class EffectTests(unittest.TestCase):
    def setUp(self):
        from s1bridge import effects, songwrite
        self.effects, self.err = effects, songwrite.SongWriteError
        songwrite.BACKUP_DIR = tempfile.mkdtemp()
        self.path = os.path.join(tempfile.mkdtemp(), "t.song")
        with zipfile.ZipFile(self.path, "w") as z:
            z.writestr("Song/song.xml", SONG_XML)
            z.writestr("Devices/audiomixer.xml", MIXER_XML)
            z.writestr("Presets/Channels/Drums/1 - Compressor.fxpreset", "x")
            z.writestr("Presets/Channels/Drums/2 - Room Reverb.fxpreset", "y")

    def test_list(self):
        r = self.effects.list_effects(self.path, track="Drums")
        self.assertEqual(r["effects"], [{"slot": "FX01", "plugin": "Compressor"}, {"slot": "FX02", "plugin": "Room Reverb"}])
        with self.assertRaises(self.err):  # Channel01 is both an audio track and a synth channel
            self.effects.list_effects(self.path, mixer_channel="Channel01")
        self.assertEqual(len(self.effects.list_effects(self.path, mixer_channel="Main Output")["effects"]), 1)

    def test_remove_by_plugin(self):
        r = self.effects.remove_effect(self.path, track="Drums", plugin="compressor")
        self.assertEqual(r["removed"], {"slot": "FX01", "plugin": "Compressor"})
        self.assertEqual(r["remaining"], [{"slot": "FX02", "plugin": "Room Reverb"}])
        with zipfile.ZipFile(self.path) as z:
            names = z.namelist()
            xml = z.read("Devices/audiomixer.xml").decode("utf-8")
        self.assertNotIn("Presets/Channels/Drums/1 - Compressor.fxpreset", names)
        self.assertIn("Presets/Channels/Drums/2 - Room Reverb.fxpreset", names)
        self.assertTrue(xml.startswith("﻿"))
        self.assertEqual(xml.count('name="Compressor"'), 1)  # the synth channel's one stays
        self.assertTrue(os.path.isfile(r["backup"]))

    def test_errors(self):
        with self.assertRaises(self.err):
            self.effects.remove_effect(self.path, track="Drums", slot="FX09")
        with self.assertRaises(self.err):
            self.effects.remove_effect(self.path, track="Nope", slot="FX01")


if __name__ == "__main__":
    unittest.main()
