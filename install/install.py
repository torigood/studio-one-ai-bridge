#!/usr/bin/env python3
"""Install / uninstall the AI Bridge into Studio One 5 (older interface to s1bridge.setup).

Prefer:  python3 server/run.py setup [--apply]   (or doctor / uninstall), which installs the
extension, the control surface, the device registration and the catalog in one go.

DEFAULT IS A DRY RUN: it only prints what would happen. Nothing is written
unless --apply is given. Studio One must not be running.

  python3 install/install.py --surface            # dry run: extension + surface + device + catalog
  python3 install/install.py --surface --apply    # install
  python3 install/install.py --surface --uninstall --apply
  python3 install/install.py --catalog --apply    # only (re)build the command catalog

Without --surface only the extension and the catalog are handled. Backups go to
<data dir>/backups/<timestamp>/ (see server/s1bridge/config.py).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

from s1bridge import setup  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", choices=["extension", "app-scripts"], default="extension")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--surface", action="store_true",
                    help="also install the AI Bridge control surface and register it as a device")
    ap.add_argument("--catalog", action="store_true", help="only build the command catalog")
    ap.add_argument("--apply", action="store_true", help="actually change files (default: dry run)")
    ap.add_argument("--i-understand-app-bundle-change", action="store_true")
    args = ap.parse_args()

    paths = setup.Paths()
    if args.target == "app-scripts" and not args.i_understand_app_bundle_change:
        print("app-scripts modifies %s and breaks its code signature.\n"
              "Re-run with --i-understand-app-bundle-change if you really want this." % paths.app)
        return 1
    if args.catalog:
        steps = [("catalog", paths.catalog, None)]
        print(setup.describe(steps))
        if args.apply:
            setup.apply(paths, steps)
            print("done.")
        return 0
    if args.uninstall:
        steps = setup.plan_uninstall(paths, args.target, surface=args.surface, device=args.surface)
    else:
        steps = setup.plan_install(paths, args.target, surface=args.surface, device=args.surface)
    print("%s (%s, target=%s)" % ("APPLY" if args.apply else "DRY RUN",
                                  "uninstall" if args.uninstall else "install", args.target))
    return setup.run(steps, paths, args.apply)


if __name__ == "__main__":
    sys.exit(main())
