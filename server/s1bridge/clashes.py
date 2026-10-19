"""Clash check for arranging (stdlib only): where instrument parts rub against the vocal, each other
or the chord, and where the arrangement crowds the vocal or moves in lockstep.

Input notes are {"beat", "len", "pitch"} (quarter notes from song start). Kinds of issue:
  vocal_clash   an instrument a minor 2nd / minor 9th from the vocal note sounding with it, or a
                major 7th when the instrument's note is outside the chord, overlapping at least
                min_overlap beats; detail says which side is off the chord
  part_clash    two instrument parts a minor 2nd / minor 9th apart, overlapping at least 0.5 beat
  off_chord     a held instrument note (>= 1 beat) outside the Chord Track chord, other than a 9th;
                a 4th held over a major chord (third present) is called an avoid note
  crowding      an instrument within 3 semitones of the vocal (not the same pitch) for >= 1 beat in
                a bar: competes with the voice for the same register
  lockstep      four or more parts with the same onsets in a bar (at least two onsets)
Each issue: {bar, pos, kind, tracks, notes, detail, beats}; sorted by bar and position.
"""
from . import notegrid

HARSH = {1, 13}  # semitones apart
TENSE = {11}
CROWD = 3


def _overlap(a, b):
    return min(a["beat"] + a["len"], b["beat"] + b["len"]) - max(a["beat"], b["beat"])


def _where(sigs, beat):
    bar, pos = notegrid.bar_pos(sigs, beat)
    return bar, round(pos, 2)


def _chord_at(chords, beat):
    for c in chords:
        if c["start_beats"] <= beat < c["start_beats"] + c["length_beats"]:
            return c
    return None


def check(tracks, vocal=None, chords=None, sigs=None, lo=None, hi=None, min_overlap=0.25):
    """tracks: {name: [notes]}; vocal: [notes] (may carry "layer": True for a doubled/harmony voice);
    chords: Song.chords(); lo/hi: beat range."""
    sigs, chords, vocal = sigs or [], chords or [], vocal or []
    inside = lambda n: (lo is None or n["beat"] + n["len"] > lo) and (hi is None or n["beat"] < hi)
    tracks = {k: sorted((n for n in v if inside(n)), key=lambda n: n["beat"]) for k, v in tracks.items()}
    tracks = {k: v for k, v in tracks.items() if v}
    vocal = [n for n in vocal if inside(n)]
    issues = []

    def add(beat, kind, names, pitches, detail, beats):
        bar, pos = _where(sigs, beat)
        issues.append({"bar": bar, "pos": pos, "kind": kind, "tracks": names,
                       "notes": [notegrid.note_name(p) for p in pitches], "detail": detail, "beats": round(beats, 2)})

    crowd = {}
    for name, notes in tracks.items():
        for n in notes:
            for v in vocal:
                ov = _overlap(n, v)
                if ov <= 0:
                    continue
                d = abs(n["pitch"] - v["pitch"])
                voice = "vocal layer" if v.get("layer") else "vocal"
                if ov >= min_overlap and (d in HARSH or d in TENSE):
                    at = max(n["beat"], v["beat"])
                    c = _chord_at(chords, at)
                    part_in = c is None or n["pitch"] % 12 in c["pcs"]
                    if d in TENSE and part_in:
                        continue  # the vocal's major 7th over a chord tone is colour, not a clash
                    detail = "%s vs %s" % ("minor 2nd" if d == 1 else "minor 9th" if d == 13 else "major 7th", voice)
                    if c is not None:
                        off = [w for w, p in ((name, n["pitch"]), (voice, v["pitch"])) if p % 12 not in c["pcs"]]
                        detail += "; off %s: %s" % (c["name"], ", ".join(off)) if off else "; both in %s" % c["name"]
                    add(at, "vocal_clash", [name, voice], [n["pitch"], v["pitch"]], detail, ov)
                if 0 < d <= CROWD and not v.get("layer"):
                    bar = _where(sigs, max(n["beat"], v["beat"]))[0]
                    crowd[(name, bar)] = crowd.get((name, bar), 0) + ov

    for (name, bar), beats in sorted(crowd.items(), key=lambda kv: kv[0][1]):
        if beats >= 1:
            issues.append({"bar": bar, "pos": 1.0, "kind": "crowding", "tracks": [name, "vocal"], "notes": [],
                           "detail": "within %d semitones of the vocal" % CROWD, "beats": round(beats, 2)})

    names = sorted(tracks)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for n in tracks[a]:
                for m in tracks[b]:
                    if m["beat"] >= n["beat"] + n["len"]:
                        break
                    ov = _overlap(n, m)
                    if ov >= 0.5 and abs(n["pitch"] - m["pitch"]) in HARSH:
                        add(max(n["beat"], m["beat"]), "part_clash", [a, b], [n["pitch"], m["pitch"]],
                            "minor 2nd" if abs(n["pitch"] - m["pitch"]) == 1 else "minor 9th", ov)

    for name, notes in tracks.items():
        for n in notes:
            if n["len"] < 1:
                continue
            c = _chord_at(chords, n["beat"] + n["len"] / 2)
            if c is None or n["pitch"] % 12 in c["pcs"]:
                continue
            iv = (n["pitch"] - c["root"]) % 12
            if iv == 2:  # a 9th colours the chord
                continue
            major = (c["root"] + 4) % 12 in c["pcs"]
            detail = "avoid note (4th over %s)" % c["name"] if iv == 5 and major else "not in %s" % c["name"]
            add(n["beat"], "off_chord", [name], [n["pitch"]], detail, n["len"])

    by_bar = {}
    for name, notes in tracks.items():
        for n in notes:
            bar, pos = _where(sigs, n["beat"])
            by_bar.setdefault(bar, {}).setdefault(name, set()).add(pos)
    for bar, parts in sorted(by_bar.items()):
        groups = {}
        for name, onsets in parts.items():
            if len(onsets) >= 2:
                groups.setdefault(tuple(sorted(onsets)), []).append(name)
        for onsets, members in groups.items():
            if len(members) >= 4:
                issues.append({"bar": bar, "pos": onsets[0], "kind": "lockstep", "tracks": sorted(members),
                               "notes": [], "detail": "same rhythm: %s" % " ".join("%g" % o for o in onsets),
                               "beats": 0})
    order = {"vocal_clash": 0, "part_clash": 1, "off_chord": 2, "crowding": 3, "lockstep": 4}
    return sorted(issues, key=lambda x: (x["bar"], x["pos"], order[x["kind"]]))


def summary(issues):
    """{kind: count} and the tracks most involved."""
    kinds, per_track = {}, {}
    for x in issues:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
        for t in x["tracks"]:
            if not t.startswith("vocal"):
                per_track[t] = per_track.get(t, 0) + 1
    top = sorted(per_track.items(), key=lambda kv: -kv[1])[:8]
    return {"by_kind": kinds, "by_track": dict(top)}
