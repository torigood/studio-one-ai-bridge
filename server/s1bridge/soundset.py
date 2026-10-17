"""Read-only access to Studio One Sound Sets (*.soundset, "PACKAGEF" container).

Same container as script packages (see re/tools/s1pkg.py), but sound sets are
hundreds of MB, so only the directory at the end of the file is read; entries
are read one at a time.

Directory (little endian), observed in Studio One 5.5:
  Root : "Root" u32(1) u32(count) entries...
  File : "File" u32(2) utf16le-name "\\0\\0" date[8] flag[1] u64 offset u64 csize u64 rsize
  Fold : "Fold" u32(0) utf16le-name "\\0\\0" u32(count) entries...
"""
import os
import struct
import zlib

_MAGIC = b"PACKAGEF"
_ROOT = b"Root\x01\x00\x00\x00"


def _name(d, p):
    e = p
    while d[e:e + 2] != b"\0\0":
        e += 2
    return d[p:e].decode("utf-16le"), e + 2


def _entries(d, p, count, base, out):
    for _ in range(count):
        tag = d[p:p + 4]
        p += 4
        if tag == b"File":
            p += 4
            name, p = _name(d, p)
            p += 8 + 1  # date + flag
            off, csize, rsize = struct.unpack("<QQQ", d[p:p + 24])
            p += 24
            out.append({"path": base + name, "offset": off, "csize": csize, "size": rsize})
        elif tag == b"Fold":
            p += 4
            name, p = _name(d, p)
            (sub,) = struct.unpack("<I", d[p:p + 4])
            p += 4
            p = _entries(d, p, sub, base + name + "/", out)
        else:
            raise ValueError("unknown directory tag %r at %d" % (tag, p - 4))
    return p


def list_entries(path):
    """Files in a sound set: [{path, offset, csize, size}]."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if f.read(8) != _MAGIC:
            raise ValueError("not a PACKAGEF sound set: %s" % path)
        tail = 1 << 20
        while True:
            start = max(8, size - tail)
            f.seek(start)
            d = f.read(size - start)
            root = d.rfind(_ROOT)
            if root >= 0 or start == 8:
                break
            tail *= 4
    if root < 0:
        raise ValueError("directory not found: %s" % path)
    (count,) = struct.unpack("<I", d[root + 8:root + 12])
    out = []
    _entries(d, root + 12, count, "", out)
    return out


def read_entry(path, entry):
    with open(path, "rb") as f:
        f.seek(entry["offset"])
        blob = f.read(entry["csize"])
    if entry["csize"] == entry["size"]:
        return blob
    try:
        return zlib.decompress(blob)
    except zlib.error:
        return blob
