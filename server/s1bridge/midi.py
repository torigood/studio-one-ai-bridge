"""Virtual MIDI source "AI Bridge" via CoreMIDI (ctypes, macOS only, no dependencies).

Studio One sees it as a MIDI input port. The "AI Bridge" control surface
(surface/AI Bridge.surface.xml) maps CC messages on channel 16 to commands:
  CC 119 -> Track / "AI Bridge - Run Jobs"
  CC 118 -> Transport start
  CC 117 -> Transport stop

Studio One remembers the port by CoreMIDI unique ID, not by name
(MusicDevices.settings: receivePortID="Capture/FFFFFFFFF726E7E0::AI Bridge"). A source made
per MCP server process gets a new random ID each time, so after a server restart Studio One
no longer listens to it. Instead one small daemon process owns the source with a fixed unique
ID and outlives the servers; servers send it MIDI bytes over a Unix socket and start it when
it is not running.

  python3 -m s1bridge.midi --daemon     run the daemon in the foreground
"""
import ctypes
import ctypes.util
import fcntl
import os
import socket
import subprocess
import sys
import time

from . import config

CHANNEL = 15  # 0-based -> MIDI channel 16
CC_RUN_JOBS = 119
CC_PLAY = 118
CC_STOP = 117

PORT_NAME = "AI Bridge"
UNIQUE_ID = -148445216  # 0xF726E7E0, the ID the first installs bound in Studio One
SOCKET = os.path.join(config.DATA_DIR, "midi.sock")
LOCK = os.path.join(config.DATA_DIR, "midi.lock")

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
    _cm.MIDIObjectSetIntegerProperty.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int32]
    _cm.MIDIPacketListInit.restype = ctypes.c_void_p
    _cm.MIDIPacketListInit.argtypes = [ctypes.c_void_p]
    _cm.MIDIPacketListAdd.restype = ctypes.c_void_p
    _cm.MIDIPacketListAdd.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                      ctypes.c_uint64, ctypes.c_size_t, ctypes.c_char_p]
    _cm.MIDIReceived.argtypes = [ctypes.c_uint32, ctypes.c_void_p]


def _cfstr(s):
    return _cf.CFStringCreateWithCString(None, s.encode("utf-8"), 0x08000100)  # UTF-8


def open_source(name=PORT_NAME):
    """Create the virtual source in this process once. Returns True on success."""
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
        # Fails (kMIDIIDNotUnique) while another process still holds the ID; the source then
        # keeps its random ID and works only after re-selecting it in Studio One.
        if _cm.MIDIObjectSetIntegerProperty(_source.value, _cfstr("uniqueID"), UNIQUE_ID):
            _error = "unique ID %d is taken by another process" % UNIQUE_ID
        return True
    except OSError as e:
        _error = str(e)
        return False


def last_error():
    return _error


def _send_local(data):
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


# --- daemon -----------------------------------------------------------------------------

def serve():
    """Own the source and relay lines of hex MIDI bytes from the socket. One instance only."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # already running
    if not open_source():
        sys.exit("could not create MIDI source: %s" % _error)
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET)
    srv.listen(8)
    while True:
        conn, _ = srv.accept()
        with conn, conn.makefile("rw") as f:
            for line in f:
                line = line.strip()
                if line == "status":
                    f.write("ok %s\n" % (_error or "unique ID %d" % UNIQUE_ID))
                else:
                    try:
                        _send_local(bytes.fromhex(line))
                        f.write("ok\n")
                    except (OSError, ValueError) as e:
                        f.write("err %s\n" % e)
                f.flush()


def _spawn_daemon():
    server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=server_dir)
    subprocess.Popen([sys.executable, "-m", "s1bridge.midi", "--daemon"], cwd=server_dir, env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)


def _connect():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    s.connect(SOCKET)
    return s


def _daemon():
    """Socket to the daemon, starting it if needed."""
    try:
        return _connect()
    except OSError:
        pass
    _spawn_daemon()
    for _ in range(40):
        time.sleep(0.05)
        try:
            s = _connect()
        except OSError:
            continue
        time.sleep(1.0)  # Studio One re-binds a reappearing port after a moment (0.3-1 s seen)
        return s
    raise OSError("MIDI daemon did not start (python3 -m s1bridge.midi --daemon)")


def _request(lines):
    global _error
    if sys.platform != "darwin":
        _error = "CoreMIDI is macOS only"
        raise OSError(_error)
    with _daemon() as s, s.makefile("rw") as f:
        replies = []
        for line in lines:
            if line is None:
                time.sleep(0.02)
                continue
            f.write(line + "\n")
            f.flush()
            r = f.readline().strip()
            if not r.startswith("ok"):
                raise OSError("MIDI daemon: %s" % (r or "no reply"))
            replies.append(r)
        return replies


def send(data):
    """Send raw MIDI bytes from the "AI Bridge" source."""
    _request([bytes(data).hex()])


def press(cc):
    """Button press + release (value 127 then 0) on channel 16."""
    _request([bytes([0xB0 | CHANNEL, cc, 127]).hex(), None, bytes([0xB0 | CHANNEL, cc, 0]).hex()])


def status():
    """Daemon status line, e.g. "ok unique ID -148445216"."""
    return _request(["status"])[0]


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        serve()
    else:
        print(status())
