"""Analysis pack: an optional Python environment for pitch/melody analysis.

    studio-one-ai-bridge analysis-setup

Creates <analysis dir>/venv (numpy, scipy, onnxruntime, librosa) with the interpreter running this
command (the release bundles CPython 3.12). It lives outside the app folder, so updating the bridge
keeps it. The server itself stays stdlib-only and runs the analysis modules in this venv as a
subprocess. The models (CREPE tiny, Basic Pitch) ship with the bridge in s1bridge/models; licenses and
credits are in models/NOTICE.
"""
import os
import subprocess
import sys

from . import config

PACKAGES = ["numpy", "scipy", "onnxruntime", "librosa"]


def venv_python():
    return os.path.join(config.ANALYSIS_DIR, "venv", "bin", "python")


def _install_venv():
    py = venv_python()
    if not os.path.exists(py):
        print("Creating %s" % os.path.dirname(os.path.dirname(py)))
        subprocess.check_call([sys.executable, "-m", "venv", os.path.dirname(os.path.dirname(py))])
    print("Installing %s" % ", ".join(PACKAGES))
    subprocess.check_call([py, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    subprocess.check_call([py, "-m", "pip", "install", "-q"] + PACKAGES)


def status():
    """What is installed, for bridge_doctor."""
    return {"dir": config.ANALYSIS_DIR, "venv": os.path.exists(venv_python())}


def cli(args):
    try:
        _install_venv()
    except (subprocess.CalledProcessError, OSError) as e:
        print("analysis-setup failed: %s" % e, file=sys.stderr)
        return 1
    print("Analysis pack ready in %s" % config.ANALYSIS_DIR)
    return 0
