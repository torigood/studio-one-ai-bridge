import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from s1bridge.keys import key_from_notes  # noqa: E402


class KeyTests(unittest.TestCase):
    def test_c_major_scale(self):
        notes = [{"pitch": p, "length": 1} for p in (60, 62, 64, 65, 67, 69, 71)] + [{"pitch": 60, "length": 4}]
        self.assertEqual(key_from_notes(notes)[0]["key"], "C major")

    def test_a_minor_triad_weighted(self):
        notes = [{"pitch": 57, "length": 8}, {"pitch": 60, "length": 4}, {"pitch": 64, "length": 4},
                 {"pitch": 62, "length": 1}, {"pitch": 71, "length": 1}, {"pitch": 65, "length": 1}]
        self.assertEqual(key_from_notes(notes)[0]["key"], "A minor")

    def test_empty(self):
        self.assertEqual(key_from_notes([]), [])


if __name__ == "__main__":
    unittest.main()
