"""song_add_send: a send to an existing FX/bus channel in Devices/audiomixer.xml (offline)."""
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge import effects, songwrite  # noqa: E402

SONG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Song xmlns:x="https://www.presonus.software/xml/ccl"><Attributes x:id="Root"><List x:id="Tracks">
 <MediaTrack mediaType="Audio" trackID="{T1}" name="Drums"><UID x:id="channelID" uid="{DR}"/></MediaTrack>
 <MediaTrack mediaType="Audio" trackID="{T2}" name="Vox"><UID x:id="channelID" uid="{VX}"/></MediaTrack>
</List></Attributes></Song>"""

MIXER_XML = """﻿<AudioMixer xmlns:x="https://www.presonus.software/xml/ccl">
			<AudioTrackChannel gain="1" pan="0.5" name="Channel01" label="Drums">
				<UID x:id="uniqueID" uid="{DR}"/>
				<Attributes x:id="Sends">
					<Attributes name="Send01" bypass="0" prefader="0" level="0.5" pan="0.5">
						<Connection x:id="destination" objectID="{RV}/Input" friendlyName="Room Reverb"/>
					</Attributes>
				</Attributes>
			</AudioTrackChannel>
			<AudioTrackChannel gain="1" pan="0.5" name="Channel02" label="Vox">
				<UID x:id="uniqueID" uid="{VX}"/>
				<Attributes x:id="Sends"/>
			</AudioTrackChannel>
			<AudioEffectChannel gain="1" pan="0.5" name="Channel01" label="Room Reverb">
				<UID x:id="uniqueID" uid="{RV}"/>
			</AudioEffectChannel>
			<AudioGroupChannel gain="1" pan="0.5" name="Channel01" label="Drum Bus">
				<UID x:id="uniqueID" uid="{BUS}"/>
			</AudioGroupChannel>
</AudioMixer>"""


class Sends(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_backup = songwrite.BACKUP_DIR
        songwrite.backup.__globals__["BACKUP_DIR"] = os.path.join(self.tmp, "backups")
        self.path = os.path.join(self.tmp, "t.song")
        with zipfile.ZipFile(self.path, "w") as z:
            z.writestr("Song/song.xml", SONG_XML)
            z.writestr("Devices/audiomixer.xml", MIXER_XML.encode("utf-8"))

    def tearDown(self):
        songwrite.backup.__globals__["BACKUP_DIR"] = self.orig_backup
        shutil.rmtree(self.tmp)

    def channel(self, label):
        with zipfile.ZipFile(self.path) as z:
            xml = z.read("Devices/audiomixer.xml").decode("utf-8-sig")
        i = xml.find('label="%s"' % label)
        return xml[i:xml.find("</AudioTrackChannel>", i)]

    def test_next_slot_on_channel_with_sends(self):
        r = effects.add_send(self.path, "Drum Bus", track="Drums", level_db=-10, pan=-1)
        self.assertEqual(r["slot"], "Send02")
        ch = self.channel("Drums")
        self.assertEqual(re.findall(r'name="(Send\d+)"', ch), ["Send01", "Send02"])
        self.assertIn('objectID="{BUS}/Input" friendlyName="Drum Bus"', ch)
        level = float(re.search(r'name="Send02"[^>]*level="([^"]+)"', ch).group(1))
        self.assertAlmostEqual(level, 10 ** (-10 / 20.0), places=6)
        self.assertIn('pan="1.0"', ch)  # left is above 0.5 in Studio One

    def test_empty_sends_folder(self):
        effects.add_send(self.path, "Room Reverb", track="Vox")
        ch = self.channel("Vox")
        self.assertIn('<Attributes name="Send01"', ch)
        self.assertIn("{RV}/Input", ch)

    def test_errors(self):
        with self.assertRaises(songwrite.SongWriteError):  # already sends there
            effects.add_send(self.path, "Room Reverb", track="Drums")
        with self.assertRaises(songwrite.SongWriteError):  # not an FX/bus channel
            effects.add_send(self.path, "Vox", track="Drums")
        with self.assertRaises(songwrite.SongWriteError):
            effects.add_send(self.path, "Room Reverb", track="Vox", level_db=12)


if __name__ == "__main__":
    unittest.main()
