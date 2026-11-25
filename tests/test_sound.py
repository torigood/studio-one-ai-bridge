"""Sound tools: Vital presets into the song's Vital state, sound search, chain recipes (no Studio One)."""
import json
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge import library, presets, sound  # noqa: E402
from s1bridge.songwrite import SongWriteError  # noqa: E402

TRAILER = b"\x00" * 17 + b"JUCEPrivateData"
META = b'<?xml version="1.0" encoding="UTF-8"?>\n<MetaInfo/>\n'


def fake_vital(sound_json):
    """A Vital .vstpreset in the layout Vital 1.5 writes (see presets.vital_vstpreset)."""
    chunk = sound_json + TRAILER
    fbch = b"FBCh" + struct.pack(">I", 2) + b"Vita" + struct.pack(">I", 0x10505) + struct.pack(">I", 0) + \
        b"\x00" * 128 + struct.pack(">I", len(chunk)) + chunk
    comp = b"VstW" + struct.pack(">III", 8, 1, 0) + b"CcnK" + struct.pack(">I", len(fbch)) + fbch
    head = b"VST3" + struct.pack("<i", 1) + b"56535456697461766974616C00000000"
    body_start = len(head) + 8
    list_off = body_start + len(comp) + len(META)
    lst = b"List" + struct.pack("<i", 3) + b"Comp" + struct.pack("<qq", body_start, len(comp)) + \
        b"Cont" + struct.pack("<qq", body_start + len(comp), 0) + \
        b"Info" + struct.pack("<qq", body_start + len(comp), len(META))
    return head + struct.pack("<q", list_off) + comp + META + lst


class VitalPreset(unittest.TestCase):
    def test_layout_matches_and_roundtrips(self):
        old = fake_vital(b'{"preset_style":"Pad","settings":{"a":1}}')
        self.assertEqual(old[0x40:0x44], b"CcnK")
        self.assertEqual(old[0xe0:0xe1], b"{")
        new_json = b'{"preset_style":"Bass","settings":{"a":2,"b":' + b"3" * 5000 + b"}}"
        new = presets.vital_vstpreset(old, new_json)
        self.assertEqual(new, fake_vital(new_json))
        self.assertEqual(presets.vital_vstpreset(new, b'{"preset_style":"Pad","settings":{"a":1}}'), old)

    def test_unknown_layout(self):
        with self.assertRaises(SongWriteError):
            presets.vital_vstpreset(b"VST3" + b"\x00" * 300, b"{}")

    def test_style_search(self):
        tmp = tempfile.mkdtemp()
        try:
            for name, style in (("Glass", "Keys"), ("Warmth", "Pad"), ("Wobble", "Bass")):
                with open(os.path.join(tmp, name + ".vital"), "w") as f:
                    json.dump({"author": "", "preset_style": style, "settings": {}}, f)
            with mock.patch.dict(presets.OWN_DIRS, {"Vital": [tmp]}), \
                    mock.patch.object(presets.config, "APP_PRESETS_DIR", tmp + "/none"), \
                    mock.patch.object(presets.config, "USER_PRESETS_DIR", tmp + "/none"):
                r = presets.find("Vital", "pad")
                self.assertEqual([(p["name"], p["style"]) for p in r["presets"]], [("Warmth", "Pad")])
                with mock.patch.object(library, "instruments", lambda: [{"name": "Vital", "class_id": "{V}"}]):
                    s = sound.find_sounds("warm pad", None, 5)
                self.assertEqual(s["sounds"][0]["preset"], "Warmth")
        finally:
            import shutil
            shutil.rmtree(tmp)


class Chains(unittest.TestCase):
    def test_hz(self):
        self.assertEqual(sound.hz(250), "250 Hz")
        self.assertEqual(sound.hz(3000), "3 k")
        self.assertEqual(sound.hz(2500), "2.5 k")

    def test_recipes_use_known_effects_and_khz(self):
        for role, (inserts, sends) in sound.CHAINS.items():
            for effect, params in inserts:
                self.assertIn(effect, ("Pro EQ", "Compressor", "Limiter"), role)
                for p, v in params.items():
                    if p.endswith("freq"):
                        num = float(v.split()[0])
                        self.assertTrue(v.endswith(" k") or num < 1000, (role, p, v))
            for kind, db in sends:
                self.assertIn(kind, sound.SEND_FX)
                self.assertLess(db, 0)


if __name__ == "__main__":
    unittest.main()
