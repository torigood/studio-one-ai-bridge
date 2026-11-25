"""daw_rename_track, the instrument channel fallback and folder tracks in bounce/duplicate, against a fake bridge (no Studio One).

Run: python3 -m unittest discover -s tests   (from the repo root)
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge import editing, live, tools  # noqa: E402


class FakeBridge:
    """Tracks and AudioSynth channels; renaming a synth renames the tracks it plays, like Studio One."""

    def __init__(self, tracks, synths):
        self.tracks = tracks      # [{"name", "channelType", "synth": synth index or None}]
        self.synths = synths      # [{"name": "Channel04", "label": "Vital"}]
        self.calls = []

    def __call__(self, op, args, wait):
        self.calls.append(op)
        if op == "list_tracks":
            return {"tracks": [{"index": i, "name": t["name"], "mediaType": "Music", "channelType": t["channelType"]}
                               for i, t in enumerate(self.tracks)]}
        if op == "list_mixer":
            return {"channels": [{"index": i, "name": s["name"], "label": s["label"], "channelType": "AudioSynth"}
                                 for i, s in enumerate(self.synths)]}
        if op == "call":
            var = {}
            for st in args["steps"]:
                on = st["on"]
                if "track" in on:
                    var[st.get("as")] = ("track", on["track"])
                elif "call" in st and st["call"] == "getChannel":
                    var[st["as"]] = ("synth", st["args"][0])
                elif "set" in st and "var" in on:
                    kind, i = var[on["var"]]
                    if kind == "track":
                        self.tracks[i]["name"] = st["value"]
                    else:
                        self.synths[i]["label"] = st["value"]
                        for t in self.tracks:
                            if t["synth"] == i:
                                t["name"] = st["value"]
            return {"results": [{"value": "object"} for _ in args["steps"]]}
        raise AssertionError("unexpected op " + op)


class Rename(unittest.TestCase):
    def test_renames_track_and_its_instrument_channel(self):
        fb = FakeBridge([{"name": "Vital", "channelType": "MusicTrack", "synth": 0}],
                        [{"name": "Channel04", "label": "Vital"}])
        with mock.patch.object(live, "bridge", fb):
            r = editing.rename_track("Vital", "Lead", None)
        self.assertEqual(r["name"], "Lead")
        self.assertEqual(r["instrument_channel"], "Channel04")
        self.assertEqual(fb.synths[0]["label"], "Lead")

    def test_keeps_names_of_other_tracks_on_the_same_instrument(self):
        fb = FakeBridge([{"name": "Vital", "channelType": "MusicTrack", "synth": 0},
                         {"name": "Vital 2", "channelType": "MusicTrack", "synth": 0}],
                        [{"name": "Channel04", "label": "Vital"}])
        with mock.patch.object(live, "bridge", fb):
            r = editing.rename_track(0, "Lead", None)
        self.assertEqual([t["name"] for t in fb.tracks], ["Lead", "Vital 2"])
        self.assertEqual(r["kept_names"], ["Vital 2"])

    def test_leaves_unrelated_instrument_channel(self):
        fb = FakeBridge([{"name": "Piano", "channelType": "MusicTrack", "synth": 0}],
                        [{"name": "Channel02", "label": "St-1"}])
        with mock.patch.object(live, "bridge", fb):
            r = editing.rename_track("Piano", "Keys", None)
        self.assertNotIn("instrument_channel", r)
        self.assertEqual(fb.synths[0]["label"], "St-1")

    def test_empty_name(self):
        with self.assertRaises(tools.ToolError):
            editing.rename_track("Piano", "", None)


class InstrumentChannelHint(unittest.TestCase):
    def hint(self, fb, track, saved=(None, False), path="/x.song", save_ok=False):
        with mock.patch.object(live, "bridge", fb), \
                mock.patch.object(live, "active_song_path", return_value=path), \
                mock.patch.object(live, "saved_instrument_channel", return_value=saved) as sv, \
                mock.patch.object(live, "save_open_song", return_value=save_ok) as save:
            return live.instrument_channel_hint(track), sv, save

    def test_saved_song_wins(self):
        fb = FakeBridge([{"name": "Lead", "channelType": "MusicTrack", "synth": 0}], [])
        ch, _, save = self.hint(fb, "Lead", saved=("Channel07", True))
        self.assertEqual(ch, "Channel07")
        save.assert_not_called()

    def test_label_match_without_save(self):
        fb = FakeBridge([{"name": "Lead", "channelType": "MusicTrack", "synth": 0}],
                        [{"name": "Channel01", "label": "Main"}, {"name": "Channel04", "label": "Lead"}])
        ch, _, save = self.hint(fb, 0)
        self.assertEqual(ch, "Channel04")
        save.assert_not_called()

    def test_saves_for_a_track_the_file_does_not_have(self):
        fb = FakeBridge([{"name": "New", "channelType": "MusicTrack", "synth": 0}], [])
        ch, sv, save = self.hint(fb, "New", save_ok=True)
        save.assert_called_once()
        self.assertEqual(sv.call_count, 2)

    def test_no_save_when_the_file_already_has_the_track(self):
        fb = FakeBridge([{"name": "Old", "channelType": "MusicTrack", "synth": 0}], [])
        ch, _, save = self.hint(fb, "Old", saved=(None, True))
        self.assertIsNone(ch)
        save.assert_not_called()

    def test_audio_track_needs_no_hint(self):
        fb = FakeBridge([{"name": "Drums", "channelType": "AudioTrack", "synth": None}], [])
        ch, sv, save = self.hint(fb, "Drums")
        self.assertIsNone(ch)
        sv.assert_not_called()
        save.assert_not_called()


def _folder(name):
    """A folder track as list_tracks reports it: no mediaType key (observed 2026-10-20)."""
    return {"name": name, "channelType": None}


def _music(name):
    return {"name": name, "mediaType": "Music", "channelType": "MusicTrack"}


class FolderBridge:
    """Track list with folder tracks; Bounce To New Track and duplicateTrack add a track like Studio One."""

    def __init__(self, tracks, songs_root=None):
        self.tracks = tracks
        self.songs_root = songs_root
        self.steps = []

    def listed(self):
        return [dict(t, index=i) for i, t in enumerate(self.tracks)]

    def __call__(self, op, args, wait):
        if op == "list_tracks":
            return {"tracks": self.listed()}
        if op == "list_events":
            return {"events": [{"index": 0, "start": 0.0, "length": 4.0, "mediaType": "Music"}]}
        if op == "command":
            # the bounced audio track lands right after its source, inside the same folder
            src = next(i for i, t in enumerate(self.tracks) if t["name"] == "Trumpets")
            self.tracks.insert(src + 1, {"name": "Trumpets", "mediaType": "Audio", "channelType": "AudioTrack"})
            bounces = os.path.join(self.songs_root, "Song", "Bounces")
            os.makedirs(bounces, exist_ok=True)
            with open(os.path.join(bounces, "Trumpets.wav"), "wb") as f:
                f.write(b"RIFF")
            return {}
        if op == "call":
            self.steps.extend(args["steps"])
            calls = [st.get("call") for st in args["steps"]]
            if "duplicateTrack" in calls:
                src = self.tracks[args["steps"][1]["args"][0]["track"]]
                self.tracks.append(dict(src, name=src["name"] + " 2"))
            if "removeTrack" in calls:
                del self.tracks[args["steps"][1]["args"][0]["track"]]
            return {"results": [{"value": "object"} for _ in args["steps"][:-1]] + [{"value": 1}]}
        raise AssertionError("unexpected op " + op)


class FolderTracks(unittest.TestCase):
    """Folder tracks have no mediaType: bounce and duplicate failed with KeyError: 'mediaType'."""

    def test_bounce_with_folder_tracks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            fb = FolderBridge([_folder("Brass"), _music("Trumpets"), _folder("Percussion")], root)
            with mock.patch.object(live, "bridge", fb), mock.patch.object(editing.config, "SONGS_ROOT", root), \
                    mock.patch.object(editing.time, "sleep", lambda s: None):
                r = editing.bounce("Trumpets", None, False, False, None)
        self.assertTrue(r["wav"].endswith("Trumpets.wav"))
        self.assertTrue(r["bounced_track_removed"])
        removed = [st["args"][0]["track"] for st in fb.steps if st.get("call") == "removeTrack"]
        self.assertEqual(removed, [2])
        self.assertEqual([t["name"] for t in fb.tracks], ["Brass", "Trumpets", "Percussion"])

    def test_duplicate_with_folder_tracks(self):
        fb = FolderBridge([_folder("Brass"), _music("Trumpets")])
        with mock.patch.object(live, "bridge", fb):
            r = editing.duplicate_track("Trumpets", None, False, None)
        self.assertEqual((r["index"], r["name"]), (2, "Trumpets 2"))


if __name__ == "__main__":
    unittest.main()
