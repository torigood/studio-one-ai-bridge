"""Named snapshots of a song, so a run of AI edits can be taken back in one step.

A snapshot is a copy of the saved .song in <data dir>/snapshots/<song name>/. Taking one saves the open
song first (surface CC 116). Restoring copies the snapshot over the song through live.offline_edit (save,
close, copy, reopen when it is open); the state before the restore is backed up like any offline write.
Media files (recordings, bounces) are not copied: a snapshot restores the arrangement, notes, mixer and
plug-in settings, and refers to the same audio files.
"""
import os
import re
import shutil
import time

from . import config, live, midi, songwrite
from .common import ToolError, song_path

ROOT = os.path.join(config.DATA_DIR, "snapshots")
KEEP = 50  # per song; the oldest go first


def _song(path):
    if path:
        return song_path(path)
    p = live.active_song_path(must_exist=False)
    if p and not os.path.isfile(p) and os.path.isdir(os.path.dirname(p)):
        # a new song that was never saved: File/Save writes it to its folder (no dialog)
        midi.press(midi.CC_SAVE)
        for _ in range(40):
            time.sleep(0.25)
            if os.path.isfile(p):
                time.sleep(0.5)
                break
    if not p or not os.path.isfile(p):
        raise ToolError("no saved song is open in Studio One; ask the user to save it once (Cmd+S), or pass path")
    return p


def _folder(path):
    return os.path.join(ROOT, os.path.splitext(os.path.basename(path))[0])


def _entries(path):
    folder = _folder(path)
    if not os.path.isdir(folder):
        return []
    out = []
    for f in sorted(os.listdir(folder)):
        if f.endswith(".song"):
            m = re.match(r"(\d{8}-\d{6})(?: (.*))?\.song$", f)
            out.append({"id": f[:-5], "label": (m.group(2) if m else None) or "",
                        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.strptime(m.group(1), "%Y%m%d-%H%M%S"))
                        if m else None, "file": os.path.join(folder, f)})
    return out


def take(label=None, path=None):
    p = _song(path)
    saved = live.save_open_song(p) if live.open_in_daw(p) else None
    label = re.sub(r"[^\w .,()+-]", "_", (label or "").strip())[:60]
    folder = _folder(p)
    os.makedirs(folder, exist_ok=True)
    stem = time.strftime("%Y%m%d-%H%M%S") + (" " + label if label else "")
    dest = os.path.join(folder, stem + ".song")
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(folder, "%s (%d).song" % (stem, n))
    shutil.copy2(p, dest)
    for old in _entries(p)[:-KEEP]:
        os.remove(old["file"])
    out = {"song": p, "snapshot": os.path.basename(dest)[:-5]}
    if saved is False:
        out["note"] = ("the file did not change on Save: either nothing was unsaved, or Studio One did not save in time "
                       "(then the snapshot has the last saved state)")
    return out


def listing(path=None):
    p = _song(path)
    return {"song": p, "snapshots": [{k: e[k] for k in ("id", "label", "time")} for e in _entries(p)]}


def restore(snapshot, path=None):
    p = _song(path)
    entries = _entries(p)
    if snapshot in (None, "", "latest"):
        if not entries:
            raise ToolError("no snapshots of %s" % os.path.basename(p))
        hit = entries[-1]
    else:
        hits = [e for e in entries if e["id"] == snapshot] or [e for e in entries if e["label"] == snapshot]
        if not hits:
            raise ToolError("snapshot not found: %r (daw_snapshot action=list)" % snapshot)
        hit = hits[-1]

    def write(target):
        bak = songwrite.backup(target)
        shutil.copy2(hit["file"], target)
        return {"restored": hit["id"], "backup": bak}

    return live.offline_edit(p, write)
