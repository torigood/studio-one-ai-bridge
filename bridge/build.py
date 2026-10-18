#!/usr/bin/env python3
"""Zip bridge/src into dist/aibridge.package (plain ZIP, same as macros.package).

Only builds, for inspecting the package. Installing builds it again by itself
(python3 server/run.py setup, see server/s1bridge/setup.py).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))

from s1bridge import setup  # noqa: E402

OUT = os.path.join(HERE, "dist", "aibridge.package")


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(setup.build_package())
    print("built", OUT, "(bridge %s)" % setup.source_version())
    return 0


if __name__ == "__main__":
    sys.exit(main())
