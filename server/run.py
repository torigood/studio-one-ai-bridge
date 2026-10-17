#!/usr/bin/env python3
"""Entry point usable from any working directory: python3 /path/to/server/run.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from s1bridge.__main__ import main  # noqa: E402

main()
