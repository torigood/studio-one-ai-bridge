#!/usr/bin/env python3
"""Zip bridge/src into dist/aibridge.package (plain ZIP, same as macros.package).

Only builds. Installing is a separate, explicit step (see install/install.py).
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
OUT = os.path.join(HERE, "dist", "aibridge.package")


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(SRC)):
            path = os.path.join(SRC, name)
            if os.path.isfile(path) and not name.startswith("."):
                z.write(path, name)
                print("  +", name)
    print("built", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
