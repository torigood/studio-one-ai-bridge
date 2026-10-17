"""Virtual MIDI source "AI Bridge" via CoreMIDI (ctypes, macOS only, no dependencies).

Studio One sees it as a MIDI input port. The "AI Bridge" control surface
(surface/AI Bridge.surface.xml) maps CC messages on channel 16 to commands:
  CC 119 -> Track / "AI Bridge - Run Jobs"
  CC 118 -> Transport start
  CC 117 -> Transport stop

The source exists only while this process is alive.
"""
import ctypes
import ctypes.util
import sys
import time

CHANNEL = 15  # 0-based -> MIDI channel 16
CC_RUN_JOBS = 119
CC_PLAY = 118
CC_STOP = 117

_cf = _cm = None
_client = ctypes.c_uint32(0)
_source = ctypes.c_uint32(0)
_error = None


def _load():
    global _cf, _cm
    _cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    _cm = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreMIDI"))
    _cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    _cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    _cm.MIDIClientCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    _cm.MIDISourceCreate.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    _cm.MIDIPacketListInit.restype = ctypes.c_void_p
    _cm.MIDIPacketListInit.argtypes = [ctypes.c_void_p]
    _cm.MIDIPacketListAdd.restype = ctypes.c_void_p
    _cm.MIDIPacketListAdd.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                      ctypes.c_uint64, ctypes.c_size_t, ctypes.c_char_p]
    _cm.MIDIReceived.argtypes = [ctypes.c_uint32, ctypes.c_void_p]


def _cfstr(s):
    return _cf.CFStringCreateWithCString(None, s.encode("utf-8"), 0x08000100)  # UTF-8


def open_source(name="AI Bridge"):
    """Create the virtual source once. Returns True on success."""
    global _error
    if _source.value:
        return True
    if sys.platform != "darwin":
        _error = "CoreMIDI is macOS only"
        return False
    try:
        if _cm is None:
            _load()
        err = _cm.MIDIClientCreate(_cfstr("studio-one-ai-bridge"), None, None, ctypes.byref(_client))
        if err:
            raise OSError("MIDIClientCreate %d" % err)
        err = _cm.MIDISourceCreate(_client.value, _cfstr(name), ctypes.byref(_source))
        if err:
            raise OSError("MIDISourceCreate %d" % err)
        return True
    except OSError as e:
        _error = str(e)
        return False


def last_error():
    return _error


def send(data):
    """Send raw MIDI bytes from the virtual source."""
    if not open_source():
        raise OSError(_error)
    buf = ctypes.create_string_buffer(1024)
    pkt = _cm.MIDIPacketListInit(buf)
    pkt = _cm.MIDIPacketListAdd(buf, len(buf), pkt, 0, len(data), bytes(data))
    if not pkt:
        raise OSError("MIDIPacketListAdd failed")
    err = _cm.MIDIReceived(_source.value, buf)
    if err:
        raise OSError("MIDIReceived %d" % err)


def press(cc):
    """Button press + release (value 127 then 0) on channel 16."""
    send([0xB0 | CHANNEL, cc, 127])
    time.sleep(0.02)
    send([0xB0 | CHANNEL, cc, 0])
