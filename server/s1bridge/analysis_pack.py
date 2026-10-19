"""Analysis pack: an optional Python environment plus models for pitch/melody analysis.

    studio-one-ai-bridge analysis-setup [--model-dir DIR]

Creates <analysis dir>/venv (numpy, scipy, onnxruntime) with the interpreter running this command
(the release bundles CPython 3.12) and puts the CREPE ONNX models in <analysis dir>/models. It lives
outside the app folder, so updating the bridge keeps it. The server itself stays stdlib-only and
runs the analysis modules in this venv as a subprocess.

Models: CREPE (MIT, github.com/marl/crepe) weights from torchcrepe (MIT), exported to ONNX by
tools/export_crepe_onnx.py; licenses and credits in models/NOTICE, which the release asset ships too. They are taken from --model-dir if given, else downloaded from the
project's GitHub release and checked against the SHA-256 below.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request

from . import config

PACKAGES = ["numpy", "scipy", "onnxruntime"]
MODEL_URL = "https://github.com/torigood/studio-one-ai-bridge/releases/download/models-1/%s"
MODELS = {
    "crepe-full.onnx": "939c3f282e7fa1a4faf6e2bcf51caa5beaafec4ff2efd1d39e4f5b5d3becb0e7",
    "crepe-tiny.onnx": "006c4415089298a0ab96b52a3ead335e0e364e87ffa63218bb2a35ef7ea36513",
}


def venv_python():
    return os.path.join(config.ANALYSIS_DIR, "venv", "bin", "python")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _install_venv():
    py = venv_python()
    if not os.path.exists(py):
        print("Creating %s" % os.path.dirname(os.path.dirname(py)))
        subprocess.check_call([sys.executable, "-m", "venv", os.path.dirname(os.path.dirname(py))])
    print("Installing %s" % ", ".join(PACKAGES))
    subprocess.check_call([py, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    subprocess.check_call([py, "-m", "pip", "install", "-q"] + PACKAGES)


def _install_models(model_dir=None):
    dest = os.path.join(config.ANALYSIS_DIR, "models")
    os.makedirs(dest, exist_ok=True)
    for name, sha in MODELS.items():
        out = os.path.join(dest, name)
        if os.path.isfile(out) and _sha256(out) == sha:
            continue
        tmp = out + ".part"
        if model_dir:  # a local export (may differ byte-wise from the release build): not checked
            shutil.copyfile(os.path.join(model_dir, name), tmp)
        else:
            print("Downloading %s" % name)
            urllib.request.urlretrieve(MODEL_URL % name, tmp)
            if _sha256(tmp) != sha:
                os.remove(tmp)
                raise RuntimeError("%s failed its SHA-256 check" % name)
        os.replace(tmp, out)


def status():
    """What is installed, for bridge_doctor."""
    models = {n: os.path.isfile(os.path.join(config.ANALYSIS_DIR, "models", n)) for n in MODELS}
    return {"dir": config.ANALYSIS_DIR, "venv": os.path.exists(venv_python()), "models": models}


def cli(args):
    model_dir = None
    if "--model-dir" in args:
        model_dir = os.path.expanduser(args[args.index("--model-dir") + 1])
    try:
        _install_venv()
        _install_models(model_dir)
    except (subprocess.CalledProcessError, OSError, RuntimeError) as e:
        print("analysis-setup failed: %s" % e, file=sys.stderr)
        return 1
    print("Analysis pack ready in %s" % config.ANALYSIS_DIR)
    return 0
