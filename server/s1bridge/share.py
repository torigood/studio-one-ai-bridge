"""Share the Studio One tools with the ChatGPT chat app (or any remote MCP client) while it runs.

  python3 -m s1bridge share              start: prints the connector URL, Ctrl+C stops everything
  python3 -m s1bridge share --new-token  start with a new secret (the old URL stops working)
  python3 -m s1bridge share --no-tunnel  local HTTP only (http://127.0.0.1:<port>/<token>/mcp)

ChatGPT connects only to public HTTPS servers, so this starts the MCP HTTP server on 127.0.0.1
(httpserver.py) and a Cloudflare quick tunnel (cloudflared, no account) in front of it. The quick
tunnel gets a new random https://<words>.trycloudflare.com address on every start, so the connector
URL in ChatGPT has to be updated each time. The secret token in the path is the only access check:
anyone with the full URL can control Studio One and read your songs while this runs. Keep it
private, stop sharing when done, and use --new-token if it leaked.

cloudflared: the installed one (PATH, Homebrew) or, if none, the pinned release below downloaded
once into <data dir>/bin after checking its SHA-256.
"""
import argparse
import hashlib
import io
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request

from . import config

TOKEN_FILE = os.path.join(config.DATA_DIR, "share_token")
BIN_DIR = os.path.join(config.DATA_DIR, "bin")
CLOUDFLARED_VERSION = "2026.9.3"
CLOUDFLARED = {  # uname -m -> (asset, sha256)
    "arm64": ("cloudflared-darwin-arm64.tgz", "587c2cfb1c230fe36c7fa7727da78be459dae028cabe8c001291999350f07095"),
    "x86_64": ("cloudflared-darwin-amd64.tgz", "d1155d0837487f261183b15c1eab6c4ebcad9dc49b94675f1524c3564cea3977"),
}
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def load_token(new=False):
    """The secret path segment, created once and kept in the data dir (owner-only file)."""
    if not new and os.path.isfile(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            tok = f.read().strip()
        if len(tok) >= 24:
            return tok
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(tok + "\n")
    return tok


def find_cloudflared(download=True, log=print):
    for p in (shutil.which("cloudflared"), "/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared",
              os.path.join(BIN_DIR, "cloudflared")):
        if p and os.access(p, os.X_OK):
            return p
    if not download:
        return None
    if not config.IS_MAC:
        raise RuntimeError("install cloudflared (https://github.com/cloudflare/cloudflared) and try again")
    arch = os.uname().machine
    asset, sha = CLOUDFLARED["arm64" if arch == "arm64" else "x86_64"]
    url = "https://github.com/cloudflare/cloudflared/releases/download/%s/%s" % (CLOUDFLARED_VERSION, asset)
    log("downloading cloudflared %s (once) ..." % CLOUDFLARED_VERSION)
    with urllib.request.urlopen(url) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise RuntimeError("cloudflared download failed its SHA-256 check; nothing was installed")
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        member = next(m for m in tar.getmembers() if m.isfile() and os.path.basename(m.name) == "cloudflared")
        binary = tar.extractfile(member).read()
    os.makedirs(BIN_DIR, exist_ok=True)
    path = os.path.join(BIN_DIR, "cloudflared")
    with open(path + ".part", "wb") as f:
        f.write(binary)
    os.chmod(path + ".part", 0o755)
    os.replace(path + ".part", path)
    return path


def start_tunnel(cloudflared, port, timeout=40):
    """Start a quick tunnel to 127.0.0.1:port. Returns (process, public base URL)."""
    proc = subprocess.Popen([cloudflared, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % port],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    found = []

    def read():
        for line in proc.stderr:
            m = URL_RE.search(line)
            if m and not found:
                found.append(m.group(0))
    threading.Thread(target=read, daemon=True).start()
    end = time.time() + timeout
    while time.time() < end and not found and proc.poll() is None:
        time.sleep(0.2)
    if not found:
        proc.terminate()
        raise RuntimeError("cloudflared did not report a tunnel address (network down?)")
    return proc, found[0]


def cli(argv):
    ap = argparse.ArgumentParser(prog="python3 -m s1bridge share", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new-token", action="store_true", help="make a new secret; the old URL stops working")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-tunnel", action="store_true", help="local HTTP only, no public address")
    args = ap.parse_args(argv)

    from . import httpserver

    def stop(signum, frame):  # kill, logout, closed terminal: stop the tunnel too
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, stop)
    token = load_token(args.new_token)
    srv = httpserver.serve(token, args.port, log=lambda m: print("  request:", m, flush=True))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tunnel = None
    try:
        if args.no_tunnel:
            url = "http://127.0.0.1:%d/%s/mcp" % (port, token)
        else:
            tunnel, base = start_tunnel(find_cloudflared(), port)
            url = "%s/%s/mcp" % (base, token)
        print("\nStudio One is shared. Connector URL (keep it private):\n\n  %s\n" % url)
        print("ChatGPT: Settings > Apps & Connectors > Advanced > Developer mode on, then Create connector:\n"
              "  name 'Studio One', MCP Server URL = the URL above, Authentication: No authentication.\n"
              "  The address changes on every start: edit the connector's URL after restarting this.\n")
        print("Press Ctrl+C to stop sharing.", flush=True)
        while True:
            time.sleep(1)
            if tunnel and tunnel.poll() is not None:
                print("the tunnel stopped (exit %s)" % tunnel.returncode)
                return 1
    except KeyboardInterrupt:
        print("\nstopped sharing.")
        return 0
    except RuntimeError as e:
        print("could not share: %s" % e)
        return 1
    finally:
        if tunnel and tunnel.poll() is None:
            tunnel.terminate()
        srv.shutdown()
