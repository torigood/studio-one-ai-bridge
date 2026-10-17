"""Write chords into a saved .song's Chord Track (no script API creates chord events; docs/re-findings.md).

song.xml:
  <ChordTrack ...>
    <ChordEvent start="2" timeFormat="2" length="2">
      <Attributes x:id="chord" root="9" intervals="FF 0 0 0 FF 0 0 FF 0 0 0 0"/>
    </ChordEvent>
root and bass are positions on the circle of fifths, keeping the spelling: C=0, G=1, D=2, A=3,
E=4, B=5, F#=6, C#=7, G#=8, D#=9; F=-1, Bb=-2, Eb=-3, Ab=-4 (a sharp adds 7, a flat subtracts 7).
Observed 0.6.0: root="9" read back live as "D#m7"; without a bass attribute the bass equals the root.
intervals are 12 flags for semitones above the root ("FF" = in the chord). start/length in quarter notes.
"""
import re

from .songwrite import SongWriteError, backup, rewrite_zip
import zipfile

_FIFTHS = {"F": -1, "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5}


def note_fifths(name):
    """'F#' -> 6, 'Bb' -> -2."""
    return _FIFTHS[name[0]] + {"#": 7, "b": -7, "": 0}[name[1:]]

# quality suffix -> semitones above the root
QUALITIES = {
    "": (0, 4, 7), "maj": (0, 4, 7), "M": (0, 4, 7),
    "m": (0, 3, 7), "min": (0, 3, 7), "-": (0, 3, 7),
    "dim": (0, 3, 6), "aug": (0, 4, 8), "+": (0, 4, 8),
    "sus2": (0, 2, 7), "sus4": (0, 5, 7), "sus": (0, 5, 7),
    "5": (0, 7),
    "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9),
    "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "M7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10), "min7": (0, 3, 7, 10), "mM7": (0, 3, 7, 11),
    "m7b5": (0, 3, 6, 10), "dim7": (0, 3, 6, 9), "7sus4": (0, 5, 7, 10),
    "add9": (0, 2, 4, 7), "madd9": (0, 2, 3, 7),
    "9": (0, 2, 4, 7, 10), "maj9": (0, 2, 4, 7, 11), "m9": (0, 2, 3, 7, 10),
}


def parse_chord(name):
    """'F#m7' -> (6, (0, 3, 7, 10), None); 'C/E' -> (0, (0, 4, 7), 4). Roots/basses on the circle of fifths."""
    m = re.fullmatch(r"\s*([A-G][#b]?)([^/\s]*)(?:/([A-G][#b]?))?\s*", name or "")
    if not m or m.group(2) not in QUALITIES:
        raise SongWriteError("unknown chord %r; qualities: %s" % (name, ", ".join(q for q in QUALITIES if q)))
    return note_fifths(m.group(1)), QUALITIES[m.group(2)], note_fifths(m.group(3)) if m.group(3) else None


def _num(x):
    return ("%.6f" % x).rstrip("0").rstrip(".")


def _event_xml(start, length, root, intervals, bass):
    flags = " ".join("FF" if i in intervals else "0" for i in range(12))
    start_attr = ' start="%s"' % _num(start) if start else ""
    bass_attr = ' bass="%d"' % bass if bass is not None and bass != root else ""
    return ('\n\t\t\t\t<ChordEvent%s timeFormat="2" length="%s">'
            '\n\t\t\t\t\t<Attributes x:id="chord" root="%d"%s intervals="%s"/>'
            '\n\t\t\t\t</ChordEvent>' % (start_attr, _num(length), root, bass_attr, flags))


def write_chords(path, chords, replace=True):
    """chords: [{"beat": quarter notes from song start, "length": quarter notes, "chord": "Am7"}].
    replace drops existing chord events that overlap the written range."""
    if not chords:
        raise SongWriteError("no chords")
    items = []
    for c in sorted(chords, key=lambda c: float(c["beat"])):
        root, iv, bass = parse_chord(c["chord"])
        if float(c["length"]) <= 0:
            raise SongWriteError("chord length must be > 0")
        items.append((float(c["beat"]), float(c["length"]), root, iv, bass, c["chord"]))

    with zipfile.ZipFile(path) as z:
        xml = z.read("Song/song.xml").decode("utf-8-sig")
    m = re.search(r"<ChordTrack\b[^>]*>", xml)
    if not m:
        raise SongWriteError("the song has no Chord Track (show it once in Studio One and save)")
    end = xml.index("</ChordTrack>", m.end())
    body = xml[m.end():end]
    removed = 0
    if replace:
        lo, hi = items[0][0], max(it[0] + it[1] for it in items)
        def keep(ev):
            nonlocal removed
            s = float(re.search(r'start="([^"]*)"', ev).group(1)) if 'start="' in ev.split(">")[0] else 0.0
            ln = float(re.search(r'length="([^"]*)"', ev).group(1))
            if s < hi and s + ln > lo:
                removed += 1
                return ""
            return ev
        body = re.sub(r"\s*<ChordEvent\b.*?</ChordEvent>", lambda mm: keep(mm.group(0)), body, flags=re.S)
    new_xml = "".join(_event_xml(b, l, r, iv, bs) for b, l, r, iv, bs, _ in items)
    # chord events go before the channel UID, like Studio One writes them
    uid = body.find('<UID x:id="channelID"')
    body = body[:uid].rstrip() + new_xml + "\n\t\t\t\t" + body[uid:] if uid >= 0 else body + new_xml
    bak = backup(path)
    rewrite_zip(path, {"Song/song.xml": ("﻿" + xml[:m.end()] + body + xml[end:]).encode("utf-8")})
    return {"written": [{"beat": it[0], "length": it[1], "chord": it[5]} for it in items],
            "replaced": removed, "backup": bak}
