#!/usr/bin/env python3
"""Run the "AI Bridge" MIDI daemon in the foreground (for setting up the device in Studio One),
or send one button press through it (starting it if needed).

  python3 server/midi_port.py            # run the daemon until Ctrl+C
  python3 server/midi_port.py run|play|stop|status
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from s1bridge import midi  # noqa: E402

CCS = {"run": midi.CC_RUN_JOBS, "play": midi.CC_PLAY, "stop": midi.CC_STOP}

if len(sys.argv) > 1:
    if sys.argv[1] == "status":
        print(midi.status())
    else:
        midi.press(CCS[sys.argv[1]])
        print("sent", sys.argv[1])
else:
    print('"AI Bridge" MIDI daemon running (exits at once if one is already running). Ctrl+C to stop.')
    try:
        midi.serve()
    except KeyboardInterrupt:
        pass
