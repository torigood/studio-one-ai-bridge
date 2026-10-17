"""Parameter names of installed VST3 plug-ins, for daw_plugin_param.

Studio One names a VST3 parameter "vstp<ParamID>" (docs/re-findings.md). The live scripting
API gives no titles, so tools/vst3dump (a tiny VST3 host) reads getParameterInfo () from each
plug-in once and the result is cached as JSON in <data dir>/vst3/<bundle name>.json.

  python3 -m s1bridge.vst3params            dump every plug-in in the VST3 folders
  python3 -m s1bridge.vst3params Vital      dump one
"""
import glob
import json
import os
import subprocess
import sys

from . import config

VST3_DIRS = [p for p in (os.environ.get("S1BRIDGE_VST3_DIRS") or "").split(os.pathsep) if p] or \
    ["/Library/Audio/Plug-Ins/VST3", os.path.expanduser("~/Library/Audio/Plug-Ins/VST3")]
CACHE_DIR = os.path.join(config.DATA_DIR, "vst3")
DUMPER = os.environ.get("S1BRIDGE_VST3DUMP",
                        os.path.join(config.PROJECT_ROOT, "tools", "vst3dump", "build", "vst3dump"))


def bundles():
    out = {}
    for d in VST3_DIRS:
        for p in sorted(glob.glob(os.path.join(d, "*.vst3"))):
            out.setdefault(os.path.splitext(os.path.basename(p))[0], p)
    return out


def dump(name, timeout=120):
    """Run vst3dump for one bundle (separate process: plug-ins may crash or hang) and cache it."""
    path = bundles().get(name)
    if not path:
        raise ValueError("VST3 plug-in not found: %s" % name)
    if not os.path.exists(DUMPER):
        raise ValueError("vst3dump not built: sh tools/vst3dump/build.sh")
    r = subprocess.run([DUMPER, path], capture_output=True, text=True, timeout=timeout)
    data = json.loads(r.stdout)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def load(name):
    """Cached dump for a plug-in name (bundle name, e.g. "Vital"), or None."""
    p = os.path.join(CACHE_DIR, name + ".json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def params(name):
    """[{param: "vstp<id>", title, units, steps, display}] for automatable, visible parameters."""
    data = load(name)
    if data is None:
        return None
    out = []
    for c in data.get("classes", []):
        for p in c.get("params", []):
            t = (p.get("title") or "").strip()
            # JUCE adds ~2000 "MIDI CC n|m" proxy parameters; unnamed ones ("", "-") are unused slots.
            if p.get("hidden") or p.get("readonly") or t in ("", "-") or t.startswith("MIDI CC "):
                continue
            out.append({"param": "vstp%d" % p["id"], "title": p["title"], "units": p.get("units") or None,
                        "steps": p.get("steps"), "display": p.get("display")})
    return out


def find(name, query, limit=40):
    """Parameters whose title contains every word of query (case-insensitive)."""
    ps = params(name)
    if ps is None:
        return None
    words = [w for w in (query or "").lower().split() if w]
    hits = [p for p in ps if all(w in p["title"].lower() for w in words)]
    return {"total": len(hits), "params": hits[:limit]}


def resolve(name, title):
    """'vstp<id>' for an exact (case-insensitive) title, or None."""
    for p in params(name) or []:
        if p["title"].lower() == title.lower():
            return p["param"]
    return None


if __name__ == "__main__":
    names = sys.argv[1:] or list(bundles())
    for n in names:
        try:
            d = dump(n)
            print("%-28s %d params" % (n, sum(len(c.get("params", [])) for c in d.get("classes", []))))
        except (ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            print("%-28s FAILED: %s" % (n, e))
