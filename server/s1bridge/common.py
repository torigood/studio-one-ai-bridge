"""Small helpers shared by the tool modules: the error a tool reports to the AI, and lookups by name."""
import os
import urllib.parse


class ToolError(Exception):
    """Reported to the AI as a tool error (isError), not a server failure."""


def song_path(path):
    path = os.path.expanduser(path)
    if not path.endswith(".song"):
        raise ToolError("not a .song file: %s" % path)
    if not os.path.isfile(path):
        raise ToolError("song not found: %s (see song_list)" % path)
    return path


def find_track(tracks, ref):
    for t in tracks:
        if (isinstance(ref, int) and t["index"] == ref) or t["name"] == ref:
            return t
    raise ToolError("track not found: %r" % (ref,))


def media_path(song_path, url):
    """Local file for a media pool url (file:///... or a name looked up in the song's Media folder)."""
    if not url:
        return None
    if url.startswith("file://"):
        p = urllib.parse.unquote(url[len("file://"):])
        if os.path.isfile(p):
            return p
    name = os.path.basename(urllib.parse.unquote(url))
    p = os.path.join(os.path.dirname(song_path), "Media", name)
    return p if os.path.isfile(p) else None
