"""Offline writers for composing: tempo map, audio files, plug-in automation, instrument presets."""
import os
import re
import shutil
import sys
import tempfile
import unittest
import wave
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge import automation, loops, presets, songwrite, ubjson  # noqa: E402
from s1bridge.song import Song  # noqa: E402

MAITAI = "{B625F134-4485-4A50-A3C8-C9CF0C5495E1}"

SONG_XML = """﻿<?xml version="1.0" encoding="UTF-8"?>
<Song xmlns:x="https://www.presonus.software/xml/ccl"><Attributes x:id="Root">
<TempoMap x:id="tempoMap">
				<Attributes x:id="envelope">
					<EnvelopePoint value="0.28205128205128205"/>
				</Attributes>
				<TempoMapSegment curveType="0" start="0" end="1.7976931348623156386368421785227527018228425601508e+200" tempo="0.5"/>
			</TempoMap>
<TimeSignatureMap x:id="timeSignatureMap"><TimeSignatureMapSegment numerator="4" denominator="4"/></TimeSignatureMap>
<List x:id="Tracks">
<MediaTrack mediaType="Audio" version="1" trackID="{T1}" timeFormat="2" name="Vox">
<UID x:id="channelID" uid="{AUD}"/>
</MediaTrack>
<MediaTrack mediaType="Music" version="1" trackID="{T2}" timeFormat="2" name="Synth">
<UID x:id="channelID" uid="{MUS}"/>
</MediaTrack>
</List></Attributes></Song>"""

POOL_XML = """﻿<?xml version="1.0" encoding="UTF-8"?>
<MediaPool xmlns:x="https://www.presonus.software/xml/ccl">
	<Attributes x:id="rootFolder"/>
</MediaPool>"""

MIXER_XML = """﻿<?xml version="1.0" encoding="UTF-8"?>
<AudioMixer xmlns:x="https://www.presonus.software/xml/ccl">
<AudioTrackChannel gain="0.5" pan="0.5" name="Channel01" label="Vox">
<UID x:id="uniqueID" uid="{AUD}"/>
<Attributes x:id="Automation" mode="0"/>
</AudioTrackChannel>
<AudioSynthChannel gain="1" pan="0.5" name="Channel02" label="Synth">
<UID x:id="uniqueID" uid="{SYN}"/>
<Attributes x:id="Automation" mode="0"/>
<Attributes x:id="Inserts">
	<Attributes name="FX01">
		<UID x:id="uniqueID" uid="{SLOT}"/>
		<UID x:id="deviceClassID" uid="{073C4094-E062-4FB5-8328-74608DD1A3A4}"/>
		<Attributes x:id="deviceData" name="Pro EQ">
			<UID x:id="uniqueID" uid="{EQDEV}"/>
		</Attributes>
		<String x:id="presetPath" text="Presets/Channels/Synth/1 - Pro EQ.fxpreset"/>
	</Attributes>
</Attributes>
</AudioSynthChannel>
</AudioMixer>"""

MUSICTRACKS_XML = """﻿<?xml version="1.0" encoding="UTF-8"?>
<MusicTrackDevice><MusicTrackChannel name="Channel01" label="Synth">
<Connection x:id="destination" objectID="{SYNDEV}/MusicInput00" friendlyName="1 - Mai Tai/MIDI Input 1"/>
<UID x:id="uniqueID" uid="{MUS}"/>
</MusicTrackChannel></MusicTrackDevice>"""

SYNTHS_XML = """﻿<?xml version="1.0" encoding="UTF-8"?>
<AudioSynthFolder>
	<Attributes name="Inst01">
		<UID x:id="uniqueID" uid="{INSTSLOT}"/>
		<UID x:id="deviceClassID" uid="%s"/>
		<Attributes x:id="deviceData" name="Mai Tai">
			<UID x:id="uniqueID" uid="{SYNDEV}"/>
		</Attributes>
		<Attributes x:id="Automation" mode="0"/>
		<Attributes x:id="Presets" pname="default" dirty="1"/>
		<String x:id="presetPath" text="Presets/Synths/1 - Mai Tai.fxpreset"/>
	</Attributes>
</AudioSynthFolder>""" % MAITAI


def fxpreset(cid, marker):
    return ('﻿<?xml version="1.0" encoding="UTF-8"?>\n<AudioEffectPreset cid="%s" version="2">'
            '<Attributes x:id="ComponentData" marker="%s"/></AudioEffectPreset>' % (cid, marker)).encode("utf-8")


class Composing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_backup = songwrite.BACKUP_DIR
        songwrite.backup.__globals__["BACKUP_DIR"] = os.path.join(self.tmp, "backups")
        os.makedirs(os.path.join(self.tmp, "Song A"))
        self.path = os.path.join(self.tmp, "Song A", "a.song")
        with zipfile.ZipFile(self.path, "w") as z:
            z.writestr("Song/song.xml", SONG_XML.encode("utf-8"))
            z.writestr("Song/mediapool.xml", POOL_XML.encode("utf-8"))
            z.writestr("Devices/audiomixer.xml", MIXER_XML.encode("utf-8"))
            z.writestr("Devices/musictrackdevice.xml", MUSICTRACKS_XML.encode("utf-8"))
            z.writestr("Devices/audiosynthfolder.xml", SYNTHS_XML.encode("utf-8"))
            z.writestr("Presets/Synths/1 - Mai Tai.fxpreset", fxpreset(MAITAI, "old"))

    def tearDown(self):
        songwrite.backup.__globals__["BACKUP_DIR"] = self.orig_backup
        shutil.rmtree(self.tmp)

    def read(self, name):
        with zipfile.ZipFile(self.path) as z:
            return z.read(name)

    # ---- tempo -------------------------------------------------------------------------------
    def test_tempo_map(self):
        r = songwrite.write_tempo_map(self.path, [{"bar": 5, "bpm": 90, "ramp": True}, {"bar": 1, "bpm": 120},
                                                  {"beat": 32, "bpm": 140}])
        self.assertEqual([(p["beat"], p["bpm"], p["ramp_to_next"]) for p in r["tempo_map"]],
                         [(0.0, 120.0, False), (16.0, 90.0, True), (32.0, 140.0, False)])
        with Song(self.path) as s:
            self.assertEqual([(t["start_beats"], t["bpm"]) for t in s.tempo_map()], [(0.0, 120.0), (16.0, 90.0), (32.0, 140.0)])
        xml = self.read("Song/song.xml").decode("utf-8-sig")
        segs = re.findall(r'<TempoMapSegment curveType="(\d)" start="([^"]+)" end="([^"]+)"', xml)
        self.assertEqual([(c, s) for c, s, _e in segs], [("0", "0"), ("1", "16"), ("0", "32")])
        self.assertEqual(segs[0][2], "16")
        self.assertEqual(len(re.findall("<EnvelopePoint", xml)), 3)

    def test_tempo_errors(self):
        for bad in ([], [{"bar": 1, "bpm": 500}], [{"bpm": 100}], [{"bar": 1, "bpm": 90}, {"beat": 0, "bpm": 100}]):
            with self.assertRaises(songwrite.SongWriteError):
                songwrite.write_tempo_map(self.path, bad)

    # ---- audio files -------------------------------------------------------------------------
    def wav(self, seconds=2.0):
        p = os.path.join(self.tmp, "kick 1.wav")
        with wave.open(p, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\0\0" * int(8000 * seconds))
        return p

    def test_add_audio_copies_and_places(self):
        src = self.wav(2.0)
        r = loops.add_audio_file(self.path, "Vox", src, 8)
        self.assertTrue(r["copied"])
        self.assertEqual(r["file"], os.path.join(self.tmp, "Song A", "Media", "kick 1.wav"))
        self.assertAlmostEqual(r["length_beats"], 4.0)  # 2 s at 120 BPM
        pool = self.read("Song/mediapool.xml").decode("utf-8-sig")
        self.assertIn('url="file://%s"' % r["file"], pool)
        xml = self.read("Song/song.xml").decode("utf-8-sig")
        self.assertRegex(xml, r'<AudioEvent clipID="\{[0-9A-F-]+\}" start="8.0" timeFormat="2" length="4.0"')
        # the same file again reuses the copy and the clip
        r2 = loops.add_audio_file(self.path, "Vox", src, 16)
        self.assertEqual(r2["file"], r["file"])
        self.assertEqual(self.read("Song/mediapool.xml").decode("utf-8-sig").count("<AudioClip "), 1)

    def test_add_audio_errors(self):
        with self.assertRaises(songwrite.SongWriteError):
            loops.add_audio_file(self.path, "Synth", self.wav(), 0)  # not an audio track
        mp3 = os.path.join(self.tmp, "a.mp3")
        open(mp3, "wb").close()
        with self.assertRaises(songwrite.SongWriteError):
            loops.add_audio_file(self.path, "Vox", mp3, 0)

    def test_seconds_to_beats_across_tempo_changes(self):
        tm = [{"start_beats": 0.0, "bpm": 120.0}, {"start_beats": 4.0, "bpm": 60.0}]
        self.assertAlmostEqual(loops.seconds_to_beats(tm, 0, 3.0), 5.0)  # 2 s = 4 beats, then 1 s = 1 beat

    # ---- plug-in automation ----------------------------------------------------------------
    def test_effect_parameter_lane(self):
        r = automation.write_plugin_automation(self.path, "Synth", "lmffreq",
                                               [{"bar": 1, "value": 0.1}, {"bar": 3, "value": 0.9}], plugin="Pro EQ")
        self.assertEqual((r["slot"], r["device"], r["new_lane"]), ("FX01", "Pro EQ", True))
        xml = self.read("Song/song.xml").decode("utf-8-sig")
        self.assertIn('identity="param:///AudioMixer/{SYN}/Inserts/{EQDEV}/lmffreq"', xml)
        self.assertIn('metaIdentity="{073C4094-E062-4FB5-8328-74608DD1A3A4}/lmffreq"', xml)
        ev = ubjson.loads(self.read("Envelopes/Synth/lmffreq.envelopex"))["events"]
        self.assertEqual([(e["start"], e["value"]) for e in ev], [(0.0, 0.1), (8.0, 0.9)])

    def test_instrument_parameter_lane_sets_read(self):
        r = automation.write_plugin_automation(self.path, "Synth", "vstp7", [{"beat": 0, "value": 0}, {"beat": 4, "value": 1}],
                                               instrument=True, title="Cutoff")
        self.assertEqual((r["slot"], r["read_mode_set"]), ("Inst01", True))
        xml = self.read("Song/song.xml").decode("utf-8-sig")
        self.assertIn('title="Cutoff" identity="param:///Synths/{SYNDEV}/vstp7"', xml)
        self.assertIn('mode="1"', self.read("Devices/audiosynthfolder.xml").decode("utf-8-sig"))

    def test_plugin_automation_errors(self):
        with self.assertRaises(songwrite.SongWriteError):  # values are normalized
            automation.write_plugin_automation(self.path, "Synth", "vstp7", [{"beat": 0, "value": 5}], instrument=True)
        with self.assertRaises(songwrite.SongWriteError):  # no such insert
            automation.write_plugin_automation(self.path, "Synth", "lmffreq", [{"beat": 0, "value": 0}], plugin="Reverb")
        with self.assertRaises(songwrite.SongWriteError):  # audio track plays no instrument
            automation.write_plugin_automation(self.path, "Vox", "vstp1", [{"beat": 0, "value": 0}], instrument=True)

    # ---- presets -----------------------------------------------------------------------------
    def preset_file(self, cid, name="Warm Pad"):
        p = os.path.join(self.tmp, name + ".preset")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("data.fxpreset", fxpreset(cid, "new"))
            z.writestr("metainfo.xml", "<MetaInformation/>")
        return p

    def test_load_native_preset(self):
        r = presets.load_preset(self.path, "Synth", self.preset_file(MAITAI))
        self.assertEqual((r["instrument"], r["slot"], r["preset"]), ("Mai Tai", "Inst01", "Warm Pad"))
        self.assertIn(b'marker="new"', self.read("Presets/Synths/1 - Mai Tai.fxpreset"))
        self.assertIn('<Attributes x:id="Presets" pname="Warm Pad" dirty="0"/>',
                      self.read("Devices/audiosynthfolder.xml").decode("utf-8-sig"))

    def test_preset_for_another_plugin_is_refused(self):
        with self.assertRaises(songwrite.SongWriteError):
            presets.load_preset(self.path, "Synth", self.preset_file("{00000000-0000-0000-0000-000000000000}"))
        self.assertIn(b'marker="old"', self.read("Presets/Synths/1 - Mai Tai.fxpreset"))
        vst = os.path.join(self.tmp, "x.vstpreset")
        open(vst, "wb").close()
        with self.assertRaises(songwrite.SongWriteError):  # Mai Tai stores .fxpreset
            presets.load_preset(self.path, "Synth", vst)

    def test_find_presets(self):
        d = os.path.join(self.tmp, "presets")
        os.makedirs(os.path.join(d, "Mai Tai", "Pad"))
        for n in ("Pad - Warm Coat", "Pad - Cold"):
            open(os.path.join(d, "Mai Tai", "Pad", n + ".preset"), "wb").close()
        from unittest import mock
        with mock.patch.object(presets, "_dirs", lambda plugin: [os.path.join(d, plugin)]):
            r = presets.find("Mai Tai", "warm")
            self.assertEqual([(p["folder"], p["name"]) for p in r["presets"]], [("Pad", "Pad - Warm Coat")])
            self.assertTrue(presets._pick("Mai Tai", "warm coat").endswith("Pad - Warm Coat.preset"))
            with self.assertRaises(songwrite.SongWriteError):
                presets._pick("Mai Tai", "pad")  # two matches


if __name__ == "__main__":
    unittest.main()
