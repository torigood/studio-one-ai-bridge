"""The release version, the only place it is written (packaging/release.py sets it).

CalVer YY.W.N: two-digit year, week of the year counted Sunday to Saturday (week 1 holds January 1), and the
release number within that week starting at 0. No zero padding (semver forbids leading zeros): 26.8.0, 26.48.1.
"""
VERSION = "26.48.0"
