import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from s1bridge.mailbox import Mailbox  # noqa: E402


class MailboxCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mb = Mailbox(self.tmp.name)
        self.mb.ensure()

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, folder, name, age):
        path = os.path.join(self.tmp.name, folder, name + ".json")
        with open(path, "w") as f:
            f.write("{}")
        t = time.time() - age
        os.utime(path, (t, t))
        return path

    def test_cleanup(self):
        fresh_job = self.put("inbox", "a", 5)              # waiting: kept
        stale_job = self.put("inbox", "b", 3600)           # never answered: would run later, removed
        answered_job = self.put("inbox", "c", 5)           # answered: removed, its result kept
        result = self.put("outbox", "c", 5)
        old_result = self.put("outbox", "d", 7200)         # old result: removed
        self.assertEqual(self.mb.cleanup(), 3)
        self.assertTrue(os.path.exists(fresh_job))
        self.assertTrue(os.path.exists(result))
        for gone in (stale_job, answered_job, old_result):
            self.assertFalse(os.path.exists(gone))

    def test_cancel(self):
        job = self.mb.enqueue("ping")
        self.assertTrue(self.mb.cancel(job))
        self.assertFalse(self.mb.cancel(job))
        self.assertEqual(self.mb.pending(), [])


if __name__ == "__main__":
    unittest.main()
