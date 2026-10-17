"""Minimal UBJSON (draft 12) decoder, enough for Studio One .musicx / .envelopex.

Studio One writes object keys as an int8-length string without the 'S' marker,
which is standard UBJSON: object keys omit the type marker.
"""
import struct

_FIXED = {
    b"i": (">b", 1), b"U": (">B", 1), b"I": (">h", 2), b"l": (">i", 4),
    b"L": (">q", 8), b"d": (">f", 4), b"D": (">d", 8),
}


class UBJSONError(ValueError):
    pass


class _Reader:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def take(self, n):
        if self.p + n > len(self.d):
            raise UBJSONError("unexpected end of data at %d" % self.p)
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def peek(self):
        return self.d[self.p:self.p + 1]

    def marker(self):
        m = self.take(1)
        while m == b"N":  # no-op
            m = self.take(1)
        return m

    def length(self):
        v = self.value(self.marker())
        if not isinstance(v, int) or v < 0:
            raise UBJSONError("bad length %r" % (v,))
        return v

    def string(self):
        return self.take(self.length()).decode("utf-8", errors="replace")

    def value(self, m):
        if m in _FIXED:
            fmt, n = _FIXED[m]
            return struct.unpack(fmt, self.take(n))[0]
        if m == b"Z":
            return None
        if m == b"T":
            return True
        if m == b"F":
            return False
        if m == b"C":
            return self.take(1).decode("latin1")
        if m in (b"S", b"H"):
            return self.string()
        if m == b"[":
            return self.array()
        if m == b"{":
            return self.obj()
        raise UBJSONError("unknown marker %r at %d" % (m, self.p - 1))

    def _container_header(self):
        typ = count = None
        if self.peek() == b"$":
            self.take(1)
            typ = self.take(1)
        if self.peek() == b"#":
            self.take(1)
            count = self.length()
        return typ, count

    def array(self):
        typ, count = self._container_header()
        out = []
        if count is not None:
            for _ in range(count):
                out.append(self.value(typ if typ else self.marker()))
            return out
        while True:
            m = self.marker()
            if m == b"]":
                return out
            out.append(self.value(m))

    def obj(self):
        typ, count = self._container_header()
        out = {}
        if count is not None:
            for _ in range(count):
                k = self.string()
                out[k] = self.value(typ if typ else self.marker())
            return out
        while True:
            if self.peek() == b"}":
                self.take(1)
                return out
            k = self.string()
            out[k] = self.value(self.marker())


def loads(data):
    r = _Reader(data)
    return r.value(r.marker())
