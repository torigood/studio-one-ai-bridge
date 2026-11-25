"""Make a release: CalVer number, version files, tests, .mcpb, and (with --publish) commit, push and GitHub release.

    python3 packaging/release.py                     next version, tests, build dist/*.mcpb (nothing pushed)
    python3 packaging/release.py --publish --notes notes.md   also commit, push and create the GitHub release
    python3 packaging/release.py --version-only      print the version a release today would get

Version YY.W.N (server/s1bridge/version.py): two-digit year, week counted Sunday to Saturday with week 1 holding
January 1, and N = releases already made that week (from the v* tags). Studio One needs a bridge reinstall only
when the bridge sources change (setup.source_version is a hash of bridge/src), not with every release.
"""
import argparse
import datetime
import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(ROOT, "server", "s1bridge", "version.py")
PYPROJECT = os.path.join(ROOT, "pyproject.toml")


def week(day):
    """Week of the year, Sunday to Saturday, week 1 = the one holding January 1."""
    jan1 = datetime.date(day.year, 1, 1)
    offset = (jan1.weekday() + 1) % 7  # days from the Sunday before (or on) January 1
    return ((day - jan1).days + offset) // 7 + 1


def calver(day, tags):
    base = "%02d.%d" % (day.year % 100, week(day))
    taken = [int(m.group(1)) for t in tags for m in [re.fullmatch(r"v%s\.(\d+)" % re.escape(base), t)] if m]
    return "%s.%d" % (base, max(taken) + 1 if taken else 0)


def _run(cmd, **kw):
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=True, **kw)


def _tags():
    out = subprocess.run(["git", "tag", "-l", "v*"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return out.split()


def write_version(version):
    with open(VERSION_FILE, encoding="utf-8") as f:
        text = f.read()
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(re.sub(r'^VERSION = "[^"]*"', 'VERSION = "%s"' % version, text, flags=re.M))
    with open(PYPROJECT, encoding="utf-8") as f:
        text = f.read()
    with open(PYPROJECT, "w", encoding="utf-8") as f:
        f.write(re.sub(r'^version = "[^"]*"', 'version = "%s"' % version, text, count=1, flags=re.M))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--publish", action="store_true", help="commit, push and create the GitHub release")
    ap.add_argument("--notes", help="release notes file (markdown); default: GitHub's generated notes")
    ap.add_argument("--date", help="release date YYYY-MM-DD (default today)")
    ap.add_argument("--version-only", action="store_true")
    a = ap.parse_args(argv)
    day = datetime.date.fromisoformat(a.date) if a.date else datetime.date.today()
    version = calver(day, _tags())
    if a.version_only:
        print(version)
        return 0
    if a.publish:
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
        if dirty.strip():
            print("commit or stash your changes first:\n" + dirty, file=sys.stderr)
            return 1
    write_version(version)
    _run([sys.executable, "-m", "pytest", "-q"])
    _run([sys.executable, os.path.join("packaging", "build_mcpb.py")])
    _run(["npx", "-y", "@anthropic-ai/mcpb", "validate", os.path.join("dist", "manifest.json")])
    bundle = os.path.join(ROOT, "dist", "studio-one-ai-bridge-%s.mcpb" % version)
    with open(bundle, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    print("built %s\nSHA-256 %s" % (bundle, sha))
    if not a.publish:
        print("not published (pass --publish); version files now say %s" % version)
        return 0
    if subprocess.run(["git", "diff", "--quiet", "--", VERSION_FILE, PYPROJECT], cwd=ROOT).returncode:
        _run(["git", "add", VERSION_FILE, PYPROJECT])
        _run(["git", "commit", "-m", "Release %s" % version])
    _run(["git", "push"])
    notes = ["--notes-file", a.notes] if a.notes else ["--generate-notes"]
    _run(["gh", "release", "create", "v" + version, bundle, "--title", "Studio One AI Bridge %s" % version,
          "--target", "main"] + notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
