import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from s1bridge import devices  # noqa: E402

# MusicDevices.settings as Studio One 5.5.2 wrote it after adding a keyboard and AI Bridge by hand.
REAL = ('﻿<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Settings xmlns:x="https://www.presonus.software/xml/ccl" name="MusicDevices" version="1">\n'
        '\t<Section path="MusicDeviceList">\n'
        '\t\t<Attributes>\n'
        '\t\t\t<MusicDeviceDescription name="MPKmini" receivePortID="Capture/6E3942E1::MPK mini 3" midiReceiveMask="65535">\n'
        '\t\t\t\t<UID x:id="modelID" uid="{83B2BD06-5212-4378-AC7A-1D7C390256B4}"/>\n'
        '\t\t\t\t<UID x:id="instanceID" uid="{60C177F1-7220-8D43-908F-B30DB95B21C5}"/>\n'
        '\t\t\t</MusicDeviceDescription>\n'
        '\t\t\t<MusicDeviceDescription name="AI Bridge" receivePortID="Capture/FFFFFFFFF726E7E0::AI Bridge">\n'
        '\t\t\t\t<UID x:id="modelID" uid="{8C927C40-0146-4160-8F9C-2535B49B6F6F}"/>\n'
        '\t\t\t\t<UID x:id="instanceID" uid="{A1475657-C92B-324E-AE94-1AD4A57A7208}"/>\n'
        '\t\t\t</MusicDeviceDescription>\n'
        '\t\t</Attributes>\n'
        '\t</Section>\n'
        '</Settings>\n')


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "MusicDevices.settings")

    def tearDown(self):
        self.dir.cleanup()

    def put(self, text):
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def get(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_port_id_matches_studio_one(self):
        self.assertEqual(devices.PORT_ID, "Capture/FFFFFFFFF726E7E0::AI Bridge")

    def test_present_left_alone(self):
        self.put(REAL)
        self.assertEqual(devices.register(self.path), "present")
        self.assertEqual(self.get(), REAL)

    def test_unregister_keeps_other_devices(self):
        self.put(REAL)
        self.assertEqual(devices.unregister(self.path), "removed")
        text = self.get()
        self.assertNotIn("AI Bridge", text)
        self.assertIn('name="MPKmini"', text)
        self.assertIn("\t\t</Attributes>\n", text)
        self.assertEqual(devices.unregister(self.path), "absent")

    def test_register_after_unregister_restores_shape(self):
        self.put(REAL)
        devices.unregister(self.path)
        self.assertEqual(devices.register(self.path), "added")
        text = self.get()
        old_instance = "{A1475657-C92B-324E-AE94-1AD4A57A7208}"
        self.assertNotIn(old_instance, text)
        self.assertEqual(re.sub(r'(instanceID" uid=")[^"]+', r"\1X", text),
                         re.sub(r'(instanceID" uid=")[^"]+', r"\1X", REAL))

    def test_register_without_file(self):
        self.assertEqual(devices.register(self.path), "added")
        text = self.get()
        self.assertTrue(text.startswith("﻿<?xml"))
        self.assertIn('<Section path="MusicDeviceList">', text)
        self.assertIn(devices.PORT_ID, text)
        self.assertTrue(devices.is_registered(self.path))


if __name__ == "__main__":
    unittest.main()
