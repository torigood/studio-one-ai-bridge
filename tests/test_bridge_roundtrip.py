"""Round trip: Python Mailbox -> bridge JS (under tests/mock_host.js) -> Python result.

Checks our own protocol and dispatch, not real Studio One behaviour.
Run: python3 -m unittest discover -s tests   (from the repo root; needs node)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge.mailbox import Mailbox  # noqa: E402

NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node not installed")
class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mb = Mailbox(os.path.join(self.tmp, "AIBridge"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_bridge(self, mode="run"):
        out = subprocess.run([NODE, os.path.join(ROOT, "tests", "mock_host.js"), self.tmp, mode],
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_jobs(self):
        ids = {
            "ping": self.mb.enqueue("ping"),
            "list": self.mb.enqueue("list_tracks"),
            "cmd": self.mb.enqueue("command", {"category": "Transport", "name": "Start", "args": {"Pause": 1}}),
            "add": self.mb.enqueue("add_track", {"type": "instrument", "name": "Strings"}),
            "vol": self.mb.enqueue("set_channel_param", {"track": "Piano", "param": "volume", "value": 0.5}),
            "notes": self.mb.enqueue("insert_notes", {"track": "Piano", "start_beats": 4, "length_beats": 4,
                                                      "notes": [{"pitch": 60, "start_beats": 0, "length_beats": 1}]}),
            "bad": self.mb.enqueue("nope"),
            "missing": self.mb.enqueue("set_channel_param", {"track": "Nope", "param": "volume", "value": 1}),
        }
        calls = self.run_bridge()
        r = {k: self.mb.result(v) for k, v in ids.items()}

        self.assertEqual(r["ping"]["result"]["pong"], True)
        self.assertEqual([t["name"] for t in r["list"]["result"]["tracks"]], ["Piano", "Vox"])
        self.assertIn(["interpretCommand", "Transport", "Start", ["Pause", "1"]], calls)
        self.assertEqual(r["add"]["result"]["created"]["name"], "Strings")
        self.assertIn(["setValue", "Piano", "volume", 0.5], calls)
        self.assertIn(["insertNewClip", "Music", 4, 8, "Piano"], calls)
        self.assertIn(["note", 60, 0, 1, 0.8], calls)
        self.assertFalse(r["bad"]["ok"])
        self.assertIn("track not found", r["missing"]["error"])
        self.assertEqual(self.mb.pending(), [])
        # results consumed above -> inbox cleaned by Python
        self.assertEqual(os.listdir(os.path.join(self.mb.root, "inbox")), [])

    def test_call_and_list_events(self):
        call = self.mb.enqueue("call", {"steps": [
            {"on": {"functions": ""}, "set": "executeImmediately", "value": True},
            {"as": "copy", "on": {"functions": ""}, "call": "addEventOnTrack",
             "args": [{"track": "Vox"}, {"event": {"track": "Vox", "index": 0}}, {"time": 8}]},
            {"on": {"var": "copy"}, "get": "start"},
            {"on": {"functions": ""}, "call": "noSuchMethod"},
            {"on": {"var": "copy"}}]})
        events = self.mb.enqueue("list_events", {"track": "Vox"})
        calls = self.run_bridge()
        steps = self.mb.result(call)["result"]["results"]
        self.assertIn(["addEventOnTrack", "Vox", "Vox 1", 8], calls)
        self.assertEqual(steps[0]["value"], True)
        self.assertEqual(steps[1]["props"]["start"], 8)
        self.assertEqual(steps[2]["value"], 8)
        self.assertIn("not a function", steps[3]["error"])
        self.assertEqual(len(steps), 4)  # stops at the first error
        self.assertEqual([e["start"] for e in self.mb.result(events)["result"]["events"]], [0, 8])

    def test_notes_and_transport(self):
        read = self.mb.enqueue("read_notes", {"track": "Piano"})
        edit = self.mb.enqueue("edit_notes", {"track": "Piano", "part_start": 0,
                                              "edits": [{"index": 1, "pitch": 65, "start": 2}, {"index": 0, "delete": True}],
                                              "add": [{"pitch": 72, "start": 3, "length": 1}]})
        bad = self.mb.enqueue("edit_notes", {"track": "Piano", "part_start": 0, "edits": [{"index": 9, "pitch": 1}]})
        reread = self.mb.enqueue("read_notes", {"track": "Piano"})
        tr = self.mb.enqueue("transport", {"set": [["primaryTime", "0002.01.01.00"], ["tempo", 128]],
                                           "restore_position": True})
        calls = self.run_bridge()
        notes = self.mb.result(read)["result"]["parts"][0]["notes"]
        self.assertEqual([(n["index"], n["pitch"], n["start"]) for n in notes], [(0, 60, 0), (1, 64, 1)])
        self.assertEqual(self.mb.result(edit)["result"]["notes"], 2)
        self.assertIn("out of range", self.mb.result(bad)["error"])
        after = self.mb.result(reread)["result"]["parts"][0]["notes"]
        self.assertEqual([(n["pitch"], n["start"]) for n in after], [(65, 2), (72, 3)])
        t = self.mb.result(tr)["result"]
        self.assertEqual(t["after"]["tempo"], "128")
        self.assertEqual(t["after"]["primaryTime"], "0001.01.01.00")
        self.assertIn(["transport", "tempo", 128], calls)

    def test_guards_and_delete(self):
        audio = self.mb.enqueue("insert_notes", {"track": "Vox", "start_beats": 4, "length_beats": 4,
                                                 "notes": [{"pitch": 60, "start_beats": 0, "length_beats": 1}]})
        ambiguous = self.mb.enqueue("set_channel_param", {"mixer_channel": "St-1", "param": "volume", "value": 1})
        by_index = self.mb.enqueue("set_channel_param", {"mixer_channel": 1, "param": "volume", "value": 0.5})
        # Channel03 is both an AudioSynth and a MusicTrack channel; only the AudioSynth one has volume
        by_name = self.mb.enqueue("set_channel_param", {"mixer_channel": "Channel03", "param": "volume", "value": 0.7})
        events = self.mb.enqueue("list_events", {"track": "Piano"})
        delete = self.mb.enqueue("delete_event", {"track": "Piano", "start_beats": 0})
        missing = self.mb.enqueue("delete_event", {"track": "Piano", "start_beats": 99})
        calls = self.run_bridge()
        self.assertIn("not an instrument track", self.mb.result(audio)["error"])
        self.assertFalse(any(c[0] == "insertNewClip" for c in calls))
        self.assertIn("ambiguous", self.mb.result(ambiguous)["error"])
        self.assertEqual(self.mb.result(by_index)["result"]["channel"], "Channel03")
        self.assertEqual(self.mb.result(by_name)["result"]["channelType"], "AudioSynth")
        ev = self.mb.result(events)["result"]["events"][0]
        self.assertNotIn("speed", ev)  # music parts: no "undefined" strings
        self.assertEqual(self.mb.result(delete)["result"]["remaining"], 0)
        self.assertIn(["removeEvent", "Piano", 0], calls)
        self.assertIn("events start at", self.mb.result(missing)["error"])

    def test_jobs_not_rerun(self):
        job = self.mb.enqueue("add_track", {"type": "folder", "name": "Once"})
        self.run_bridge()
        # result not consumed yet: inbox file still there, bridge must skip it
        self.assertEqual(self.mb.pending(), [])
        second = self.run_bridge()
        self.assertEqual(second, [])
        self.assertEqual(self.mb.result(job)["result"]["created"]["name"], "Once")

    def test_service_and_dump(self):
        self.run_bridge("service")
        self.assertTrue(self.mb.read_json("status.json")["loaded"])
        self.run_bridge("dump")
        dump = self.mb.read_json("host-api.json")
        self.assertIn("IO", dump["members"])

    def test_service_has_no_poller(self):
        self.run_bridge("service")
        st = self.mb.read_json("status.json")
        self.assertTrue(st["loaded"])
        self.assertNotIn("polling", st)
        self.assertIsNone(self.mb.read_json("poller.json"))

    def test_poller_disabled(self):
        self.mb.ensure()
        self.run_bridge()
        poller = self.mb.read_json("poller.json")
        self.assertFalse(poller["running"])
        self.assertIn("disabled", poller["reason"])

    def test_invalid_json_job(self):
        self.mb.ensure()
        with open(os.path.join(self.mb.root, "inbox", "broken.json"), "w") as f:
            f.write("{not json")
        self.run_bridge()
        self.assertFalse(self.mb.result("broken")["ok"])


if __name__ == "__main__":
    unittest.main()
