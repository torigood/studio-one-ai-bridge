"""Register the "AI Bridge" control surface in Studio One's MusicDevices.settings.

This is what Options > External Devices > Add (local / AI Bridge, Receive From: AI Bridge)
writes; doing it here removes that manual step. Studio One rewrites the file when it quits,
so only change it while Studio One is not running.

The entry is edited as text (not re-serialized) to keep Studio One's formatting, BOM and the
x: namespace prefix exactly as they are.
"""
import os
import re
import uuid

from . import config, midi

SETTINGS = os.path.join(config.SETTINGS_DIR, "MusicDevices.settings")
MODEL_ID = "{8C927C40-0146-4160-8F9C-2535B49B6F6F}"  # classID in surface/AI Bridge.device
# Studio One stores the CoreMIDI unique ID sign-extended to 64 bits, in hex.
PORT_ID = "Capture/%016X::%s" % (midi.UNIQUE_ID & 0xFFFFFFFFFFFFFFFF, midi.PORT_NAME)

_EMPTY = ('﻿<?xml version="1.0" encoding="UTF-8"?>\n'
          '<Settings xmlns:x="https://www.presonus.software/xml/ccl" name="MusicDevices" version="1">\n'
          '</Settings>\n')
_ENTRY_RE = re.compile(r'[ \t]*<MusicDeviceDescription\b(?:(?!</MusicDeviceDescription>).)*?'
                       + re.escape(MODEL_ID) + r'.*?</MusicDeviceDescription>\n?', re.S)


def _entry(indent):
    instance = "{%s}" % str(uuid.uuid4()).upper()
    return ('%s<MusicDeviceDescription name="%s" receivePortID="%s">\n'
            '%s\t<UID x:id="modelID" uid="%s"/>\n'
            '%s\t<UID x:id="instanceID" uid="%s"/>\n'
            '%s</MusicDeviceDescription>\n') % (indent, midi.PORT_NAME, PORT_ID, indent, MODEL_ID,
                                                 indent, instance, indent)


def _read(path):
    if not os.path.isfile(path):
        return _EMPTY
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def is_registered(path=SETTINGS):
    return MODEL_ID.lower() in _read(path).lower()


def register(path=SETTINGS):
    """Add the AI Bridge device entry. Returns "present" (left alone) or "added"."""
    text = _read(path)
    if MODEL_ID.lower() in text.lower():
        return "present"
    section = re.search(r'<Section path="MusicDeviceList">\s*<Attributes>(.*?)([ \t]*)</Attributes>', text, re.S)
    if section:
        pos = section.start(2)
        text = text[:pos] + _entry(section.group(2) + "\t") + text[pos:]
    else:
        block = ('\t<Section path="MusicDeviceList">\n\t\t<Attributes>\n%s\t\t</Attributes>\n\t</Section>\n'
                 % _entry("\t\t\t"))
        pos = text.rindex("</Settings>")
        text = text[:pos] + block + text[pos:]
    _write(path, text)
    return "added"


def unregister(path=SETTINGS):
    """Remove the AI Bridge device entry. Returns "removed" or "absent"."""
    if not os.path.isfile(path):
        return "absent"
    text = _read(path)
    new = _ENTRY_RE.sub("", text)
    if new == text:
        return "absent"
    _write(path, new)
    return "removed"


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    target = sys.argv[2] if len(sys.argv) > 2 else SETTINGS
    if cmd == "register":
        print(register(target))
    elif cmd == "unregister":
        print(unregister(target))
    else:
        print("registered" if is_registered(target) else "not registered", target)
