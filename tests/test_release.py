"""packaging/release.py: CalVer numbers (Sunday-to-Saturday weeks, releases within a week)."""
import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packaging"))

import release  # noqa: E402

D = datetime.date


class CalVer(unittest.TestCase):
    def test_weeks_run_sunday_to_saturday(self):
        # 2026-01-01 is a Thursday: Jan 1-3 are week 1, Sunday Jan 4 starts week 2
        self.assertEqual([release.week(D(2026, 1, d)) for d in (1, 3, 4, 10, 11)], [1, 1, 2, 2, 3])
        self.assertEqual(release.week(D(2026, 11, 22)), 48)  # Sunday
        self.assertEqual(release.week(D(2026, 11, 25)), 48)
        self.assertEqual(release.week(D(2026, 11, 28)), 48)  # Saturday
        self.assertEqual(release.week(D(2026, 11, 29)), 49)  # next Sunday
        self.assertEqual(release.week(D(2023, 1, 1)), 1)  # a Sunday January 1
        self.assertEqual(release.week(D(2023, 1, 8)), 2)

    def test_number_within_week(self):
        self.assertEqual(release.calver(D(2026, 11, 25), ["v0.6.0"]), "26.48.0")
        self.assertEqual(release.calver(D(2026, 11, 25), ["v26.48.0"]), "26.48.1")
        self.assertEqual(release.calver(D(2026, 11, 25), ["v26.48.0", "v26.48.1", "v26.47.3"]), "26.48.2")
        self.assertEqual(release.calver(D(2027, 2, 3), ["v26.48.0"]), "27.6.0")  # Jan 1 2027 is a Friday


if __name__ == "__main__":
    unittest.main()
