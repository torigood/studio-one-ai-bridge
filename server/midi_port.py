#!/usr/bin/env python3
"""Keep the "AI Bridge" virtual MIDI port open (for setting up the device in Studio One),
or send one button press.

  python3 server/midi_port.py            # hold the port open until Ctrl+C
  python3 server/midi_port.py run|play|stop
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from s1bridge import midi  # noqa: E402

CCS = {"run": midi.CC_RUN_JOBS, "play": midi.CC_PLAY, "stop": midi.CC_STOP}

if not midi.open_source():
    sys.exit("could not create MIDI source: %s" % midi.last_error())
if len(sys.argv) > 1:
    midi.press(CCS[sys.argv[1]])
    time.sleep(0.2)
    print("sent", sys.argv[1])
else:
    print('"AI Bridge" MIDI source is open. Ctrl+C to close.')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
