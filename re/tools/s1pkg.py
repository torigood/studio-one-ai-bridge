#!/usr/bin/env python3
"""Extract a Studio One script package (read-only on the source).

Two container formats ship with Studio One 5:
  * plain ZIP (e.g. Extensions/studioonemacros/scripts/macros.package)
  * "PACKAGEF": 8-byte magic, zlib-compressed file blobs, then a directory
    that starts at the last b"Root\\x01\\0\\0\\0".

PACKAGEF directory layout (little endian), observed in Studio One 5.5:
  Root   : "Root" u32(1) u32(count) entries...
  File   : "File" u32(2) utf16le-name "\\0\\0" date[8] flag[1]
           u64 offset u64 compressed_size u64 raw_size
  Fold   : "Fold" u32(0) utf16le-name "\\0\\0" u32(count) entries...

Usage: s1pkg.py <file.package> <output-dir>
"""
import os
import struct
import sys
import zipfile
import zlib


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
            off, csize, _rsize = struct.unpack("<QQQ", d[p:p + 24])
            p += 24
            blob = d[off:off + csize]
            try:
                data = zlib.decompress(blob)
            except zlib.error:
                data = blob  # stored uncompressed
            out.append((os.path.join(base, name), data))
        elif tag == b"Fold":
            p += 4
            name, p = _name(d, p)
            (sub,) = struct.unpack("<I", d[p:p + 4])
            p += 4
            p = _entries(d, p, sub, os.path.join(base, name), out)
        else:
            raise ValueError("unknown directory tag %r at %d" % (tag, p - 4))
    return p


def read_package(path):
    """Return a list of (relative_path, bytes)."""
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(path) as z:
            return [(n, z.read(n)) for n in z.namelist() if not n.endswith("/")]
    if d[:8] != b"PACKAGEF":
        raise ValueError("not a Studio One package: %s" % path)
    root = d.rfind(b"Root\x01\x00\x00\x00")
    if root < 0:
        raise ValueError("directory not found")
    (count,) = struct.unpack("<I", d[root + 8:root + 12])
    out = []
    _entries(d, root + 12, count, "", out)
    return out


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dst = argv[1], argv[2]
    for rel, data in read_package(src):
        target = os.path.normpath(os.path.join(dst, rel))
        if not target.startswith(os.path.normpath(dst) + os.sep):
            raise ValueError("unsafe path in package: %s" % rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
        print(rel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
