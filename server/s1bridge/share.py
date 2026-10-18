"""Share the Studio One tools with the ChatGPT chat app (or any remote MCP client).

The ChatGPT app runs connector tools from OpenAI's servers, so it needs a public HTTPS address that
reaches this Mac (Claude Desktop and Codex start the server locally and need none of this). `share`
runs the MCP server over HTTP on 127.0.0.1 (httpserver.py) and opens a tunnel to it:

  cloudflare (default)  Cloudflare quick tunnel: no account, but a new random
                        https://<words>.trycloudflare.com address on every start.
  ngrok                 ngrok with your free static domain (https://<name>.ngrok-free.app): the address
                        never changes. Needs a free ngrok account; its authtoken is kept in the macOS
                        Keychain (service "studio-one-ai-bridge", account "ngrok").
  openai                OpenAI Secure MCP Tunnel (ChatGPT connector "Connection: Tunnel"): needs an
                        OpenAI Platform tunnel and a runtime API key (Keychain account "openai-tunnel").

  python3 -m s1bridge share                       start in this terminal (Ctrl+C stops)
  python3 -m s1bridge share --ngrok-domain NAME.ngrok-free.app   switch to ngrok (remembered)
  python3 -m s1bridge share --set-ngrok-token     store the ngrok authtoken (asked on the terminal)
  python3 -m s1bridge share --provider cloudflare switch back
  python3 -m s1bridge share --enable              start now and at every login, in the background
  python3 -m s1bridge share --disable             stop and do not start at login
  python3 -m s1bridge share --status              on/off, the connector URL, the log file
  python3 -m s1bridge share --new-token           new secret (the old URL stops working)
  python3 -m s1bridge share --no-tunnel           local only: http://127.0.0.1:<port>/<secret>/mcp

The secret in the URL path is the only access check: anyone with the full URL can control Studio One
and read your songs while sharing is on. Keep it private and use --new-token if it leaked.
Downloaded helpers (cloudflared, ngrok, tunnel-client) are pinned releases checked against SHA-256 and
kept in <data dir>/bin; installed copies (PATH, Homebrew) are used first.
"""
import argparse
import hashlib
import io
import json
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
SETTINGS_FILE = os.path.join(config.DATA_DIR, "share.json")
STATE_FILE = os.path.join(config.DATA_DIR, "share-state.json")
LOG_FILE = os.path.join(config.DATA_DIR, "share.log")
BIN_DIR = os.path.join(config.DATA_DIR, "bin")
LABEL = "com.studio-one-ai-bridge.share"
PLIST = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % LABEL)
KEYCHAIN_SERVICE = "studio-one-ai-bridge"
DEFAULT_PORT = 8765

CLOUDFLARED_VERSION = "2026.9.3"
CLOUDFLARED = {  # uname -m -> (asset, sha256)
    "arm64": ("cloudflared-darwin-arm64.tgz", "587c2cfb1c230fe36c7fa7727da78be459dae028cabe8c001291999350f07095"),
    "x86_64": ("cloudflared-darwin-amd64.tgz", "d1155d0837487f261183b15c1eab6c4ebcad9dc49b94675f1524c3564cea3977"),
}
CLOUDFLARE_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# Same file as Homebrew's ngrok cask 3.39.11 (its sha256); Apple Silicon only. Intel: brew install ngrok.
NGROK_URL = "https://bin.ngrok.com/a/dy27whJwwmb/ngrok-v3-3.39.11-darwin-arm64.zip"
NGROK_SHA256 = "9324a6552d74e25d5bdfdbedc4b32422c96f044fda37877498ad8ef10bddf7f7"
NGROK_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9-]+)+$")

TUNNEL_CLIENT_VERSION = "v0.0.15"
TUNNEL_CLIENT = {  # uname -m -> (asset, sha256)
    "arm64": ("tunnel-client-v0.0.15-darwin-arm64.zip", "b2cae3aa9df45b4c2fe9b1d700ebacce39f9feb6a6b46b86e6499f9a51bf72ff"),
    "x86_64": ("tunnel-client-v0.0.15-darwin-amd64.zip", "9dcae1e2fb121287e73271edb7b853dda52aa86b7bfca1df91bc275371261bdb"),
}
TUNNEL_PROFILES = os.path.join(config.DATA_DIR, "tunnel-profiles")
TUNNEL_ID_RE = re.compile(r"^tunnel_[A-Za-z0-9]{8,}$")


# --- small helpers ---------------------------------------------------------------------

def _write_private(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path + ".part", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(path + ".part", path)


def load_token(new=False):
    """The secret path segment, created once and kept in the data dir (owner-only file)."""
    if not new and os.path.isfile(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            tok = f.read().strip()
        if len(tok) >= 24:
            return tok
    tok = secrets.token_urlsafe(32)
    _write_private(TOKEN_FILE, tok + "\n")
    return tok


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.setdefault("provider", "cloudflare")
    s.setdefault("port", DEFAULT_PORT)
    return s


def save_settings(s):
    _write_private(SETTINGS_FILE, json.dumps(s, indent=1) + "\n")


def read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def keychain_get(account):
    r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _ask_secret(what):
    """Ask once for a secret: a macOS dialog with a hidden field, else a hidden terminal prompt."""
    if config.IS_MAC:
        script = ('text returned of (display dialog "Paste your %s and click OK. It is saved only in your Mac\'s '
                  'Keychain." default answer "" with hidden answer with title "Studio One AI Bridge" '
                  'buttons {"Cancel", "OK"} default button "OK")' % what.replace('"', "'"))
        r = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
        if "-128" in r.stderr:  # Cancel
            return None
    import getpass
    return getpass.getpass("Paste your %s (hidden) and press Enter: " % what).strip()


def keychain_set(account, what):
    """Store a secret in the login Keychain. It is asked in a dialog, not typed into chat or saved in files;
    it is passed to /usr/bin/security once (briefly visible to local process listings)."""
    secret = _ask_secret(what)
    if not secret:
        print("nothing saved.")
        return 1
    r = subprocess.run(["/usr/bin/security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", account,
                        "-w", secret], capture_output=True, text=True)
    if r.returncode != 0:
        print("could not save to the Keychain: %s" % (r.stderr or r.stdout).strip())
        return 1
    print("saved the %s in the Keychain (%s / %s)." % (what, KEYCHAIN_SERVICE, account))
    return 0


def _download(url, sha, what, log):
    log("downloading %s (once) ..." % what)
    with urllib.request.urlopen(url) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != sha:
        raise RuntimeError("%s download failed its SHA-256 check; nothing was installed" % what)
    return data


def _installed(name, extra=()):
    for p in (shutil.which(name), "/opt/homebrew/bin/" + name, "/usr/local/bin/" + name) + tuple(extra):
        if p and os.access(p, os.X_OK):
            return p
    return None


def _save_binary(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.chmod(path + ".part", 0o755)
    os.replace(path + ".part", path)


def _arch():
    return "arm64" if os.uname().machine == "arm64" else "x86_64"


# --- tunnels -----------------------------------------------------------------------------

def find_cloudflared(log=print):
    found = _installed("cloudflared", (os.path.join(BIN_DIR, "cloudflared"),))
    if found:
        return found
    if not config.IS_MAC:
        raise RuntimeError("install cloudflared (https://github.com/cloudflare/cloudflared) and try again")
    asset, sha = CLOUDFLARED[_arch()]
    data = _download("https://github.com/cloudflare/cloudflared/releases/download/%s/%s" % (CLOUDFLARED_VERSION, asset),
                     sha, "cloudflared " + CLOUDFLARED_VERSION, log)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        member = next(m for m in tar.getmembers() if m.isfile() and os.path.basename(m.name) == "cloudflared")
        binary = tar.extractfile(member).read()
    path = os.path.join(BIN_DIR, "cloudflared")
    _save_binary(path, binary)
    return path


def find_ngrok(log=print):
    found = _installed("ngrok", (os.path.join(BIN_DIR, "ngrok"),))
    if found:
        return found
    if not config.IS_MAC or _arch() != "arm64":
        raise RuntimeError("install ngrok first: brew install ngrok (https://ngrok.com/download)")
    import zipfile
    data = _download(NGROK_URL, NGROK_SHA256, "ngrok 3.39.11", log)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        binary = z.read("ngrok")
    path = os.path.join(BIN_DIR, "ngrok")
    _save_binary(path, binary)
    return path


def _wait_for(proc, pattern, timeout, what):
    """Read proc.stderr+stdout lines until pattern matches; returns the match. Keeps draining afterwards."""
    found = []

    def read(stream):
        for line in stream:
            m = pattern.search(line)
            if m and not found:
                found.append(m.group(0))
    for s in (proc.stdout, proc.stderr):
        if s:
            threading.Thread(target=read, args=(s,), daemon=True).start()
    end = time.time() + timeout
    while time.time() < end and not found and proc.poll() is None:
        time.sleep(0.2)
    if not found:
        proc.terminate()
        raise RuntimeError("%s did not report a public address (network down, or see %s)" % (what, LOG_FILE))
    return found[0]


def start_cloudflare(port, log=print):
    proc = subprocess.Popen([find_cloudflared(log), "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % port],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    return proc, _wait_for(proc, CLOUDFLARE_URL_RE, 40, "cloudflared")


def start_ngrok(port, domain, log=print):
    token = keychain_get("ngrok")
    if not token:
        raise RuntimeError("no ngrok authtoken in the Keychain yet; run: python3 server/run.py share --set-ngrok-token")
    env = dict(os.environ, NGROK_AUTHTOKEN=token)
    proc = subprocess.Popen([find_ngrok(log), "http", "127.0.0.1:%d" % port, "--url", "https://" + domain,
                             "--log", "stdout", "--log-format", "json"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _wait_for(proc, re.compile(r'"msg":"started tunnel".*?"url":"https://%s"' % re.escape(domain)), 40, "ngrok")
    return proc, "https://" + domain


# --- running -------------------------------------------------------------------------------

RESTART_EXIT = 75  # non-zero, so the LaunchAgent (KeepAlive SuccessfulExit=false) starts it again
CODE_CHECK_SECONDS = 10


def code_fingerprint():
    """Changes when the server code changes (git pull, an edit, a new .mcpb): newest mtime and file count."""
    root = os.path.join(config.PROJECT_ROOT, "server")
    newest, count = 0.0, 0
    for folder, _, files in os.walk(root):
        if "__pycache__" in folder:
            continue
        for n in files:
            if n.endswith(".py"):
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(folder, n)))
                    count += 1
                except OSError:
                    pass
    return newest, count


def run(settings, new_token=False, no_tunnel=False, service=False):
    """Serve until stopped. Writes STATE_FILE (url, provider, pid) while running."""
    from . import httpserver

    def stop(signum, frame):  # kill, logout, closed terminal, launchctl: stop the tunnel too
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, stop)
    token = load_token(new_token)
    srv = httpserver.serve(token, int(settings.get("port", DEFAULT_PORT)),
                           log=None if service else (lambda m: print("  request:", m, flush=True)))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tunnel = None
    provider = "local" if no_tunnel else settings["provider"]
    try:
        if no_tunnel:
            base = "http://127.0.0.1:%d" % port
        elif provider == "ngrok":
            if not settings.get("ngrok_domain"):
                raise RuntimeError("no ngrok domain set; run: share --ngrok-domain <name>.ngrok-free.app")
            tunnel, base = start_ngrok(port, settings["ngrok_domain"])
        else:
            tunnel, base = start_cloudflare(port)
        url = "%s/%s/mcp" % (base, token)
        _write_private(STATE_FILE, json.dumps({"url": url, "provider": provider, "pid": os.getpid(),
                                               "since": time.strftime("%Y-%m-%d %H:%M:%S")}) + "\n")
        print("\nStudio One is shared (%s). Connector URL (keep it private):\n\n  %s\n" % (provider, url), flush=True)
        if not service:
            print("ChatGPT: Settings > Apps & Connectors > Advanced > Developer mode on, then Create:\n"
                  "  name 'Studio One', server URL = the URL above, authentication: none.\n" +
                  ("  The address changes on every start; use --ngrok-domain for a fixed one.\n"
                   if provider == "cloudflare" else "") + "Press Ctrl+C to stop sharing.", flush=True)
        started_code, seen, last_check = code_fingerprint(), None, time.time()
        while True:
            time.sleep(1)
            if tunnel and tunnel.poll() is not None:
                print("the tunnel stopped (exit %s)" % tunnel.returncode, flush=True)
                return 1
            if service and time.time() - last_check >= CODE_CHECK_SECONDS:
                last_check = time.time()
                now = code_fingerprint()
                # restart once the code changed and stayed the same for one check (a git pull writes many
                # files), and not in the middle of a tool call; the address and secret stay the same
                if now != started_code and now == seen and httpserver._lock.acquire(blocking=False):
                    print("server code changed; restarting to load it", flush=True)
                    return RESTART_EXIT
                seen = now
    except KeyboardInterrupt:
        print("\nstopped sharing.", flush=True)
        return 0
    except RuntimeError as e:
        print("could not share: %s" % e, flush=True)
        return 1
    finally:
        if tunnel and tunnel.poll() is None:
            tunnel.terminate()
        srv.shutdown()
        st = read_state()
        if st and st.get("pid") == os.getpid():
            try:
                os.remove(STATE_FILE)
            except OSError:
                pass


# --- start at login (LaunchAgent) ----------------------------------------------------------

def _launch_command():
    from . import clients
    return clients.server_command() + ["share", "--service"]


def _plist():
    import plistlib
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": _launch_command(),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # restart after a crash or a dropped tunnel, not after a stop
        "ThrottleInterval": 30,
        "StandardOutPath": LOG_FILE,
        "StandardErrorPath": LOG_FILE,
        "ProcessType": "Background",
    })


def _launchctl(*args):
    return subprocess.run(["/bin/launchctl"] + list(args), capture_output=True, text=True)


def is_enabled():
    return os.path.isfile(PLIST)


def is_running():
    return _launchctl("print", "gui/%d/%s" % (os.getuid(), LABEL)).returncode == 0


def enable():
    os.makedirs(os.path.dirname(PLIST), exist_ok=True)
    if is_running():
        _launchctl("bootout", "gui/%d/%s" % (os.getuid(), LABEL))
        time.sleep(1)
    with open(PLIST, "wb") as f:
        f.write(_plist())
    if os.path.exists(LOG_FILE):
        os.chmod(LOG_FILE, 0o600)
    else:
        _write_private(LOG_FILE, "")
    r = _launchctl("bootstrap", "gui/%d" % os.getuid(), PLIST)
    if r.returncode != 0:
        raise RuntimeError("launchctl bootstrap failed: %s" % (r.stderr or r.stdout).strip())


def disable():
    if is_running():
        _launchctl("bootout", "gui/%d/%s" % (os.getuid(), LABEL))
    if os.path.isfile(PLIST):
        os.remove(PLIST)


def status():
    s = load_settings()
    st = read_state()
    alive = bool(st) and _pid_alive(st.get("pid"))
    return {"start_at_login": is_enabled(), "running": alive, "provider": s["provider"],
            "ngrok_domain": s.get("ngrok_domain"), "url": st.get("url") if alive else None, "log": LOG_FILE}


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, OSError):
        return False


def wait_for_url(timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        st = status()
        if st["url"]:
            return st
        time.sleep(1)
    return status()


# --- OpenAI Secure MCP Tunnel (optional) ---------------------------------------------------

def find_tunnel_client(log=print):
    found = shutil.which("tunnel-client")
    if found:
        return found
    folder = os.path.join(BIN_DIR, "tunnel-client-" + TUNNEL_CLIENT_VERSION)
    path = os.path.join(folder, "tunnel-client")
    if os.access(path, os.X_OK):
        return path
    if not config.IS_MAC:
        raise RuntimeError("install tunnel-client (https://github.com/openai/tunnel-client) and try again")
    import zipfile
    asset, sha = TUNNEL_CLIENT[_arch()]
    data = _download("https://github.com/openai/tunnel-client/releases/download/%s/%s" % (TUNNEL_CLIENT_VERSION, asset),
                     sha, "OpenAI tunnel-client " + TUNNEL_CLIENT_VERSION, log)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in ("tunnel-client", "cloudflared", "LICENSE", "NOTICE"):
            if name in z.namelist():
                if name in ("tunnel-client", "cloudflared"):
                    _save_binary(os.path.join(folder, name), z.read(name))
                else:
                    with open(os.path.join(folder, name), "wb") as f:
                        f.write(z.read(name))
    return path


def run_openai_tunnel(tunnel_id):
    """tunnel-client starts this server over stdio itself; no HTTP server or secret path."""
    from . import clients
    key = keychain_get("openai-tunnel")
    if not key:
        print("No OpenAI runtime key in the Keychain yet. Run: python3 server/run.py share --set-openai-key")
        return 1
    tc = find_tunnel_client()
    command = " ".join("'%s'" % a.replace("'", "'\\''") for a in clients.server_command())
    r = subprocess.run([tc, "init", "--force", "--profile", "studio-one", "--profile-dir", TUNNEL_PROFILES,
                        "--tunnel-id", tunnel_id, "--health-listen-addr", "127.0.0.1:0", "--mcp-command", command],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("tunnel-client init failed:\n" + (r.stderr or r.stdout)[-2000:])
        return 1

    def stop(signum, frame):
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    print("Sharing Studio One through OpenAI tunnel %s.\nChatGPT: connector with Connection 'Tunnel', this "
          "tunnel.\nPress Ctrl+C to stop.\n" % tunnel_id, flush=True)
    proc = subprocess.Popen([tc, "run", "--profile", "studio-one", "--profile-dir", TUNNEL_PROFILES,
                             "--log.format", "struct-text"], env=dict(os.environ, CONTROL_PLANE_API_KEY=key))
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("\nstopped sharing.")
        return 0


# --- CLI ---------------------------------------------------------------------------------

def cli(argv):
    ap = argparse.ArgumentParser(prog="python3 -m s1bridge share", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["cloudflare", "ngrok"], help="tunnel to use from now on")
    ap.add_argument("--ngrok-domain", metavar="NAME.ngrok-free.app", help="your ngrok static domain (switches to ngrok)")
    ap.add_argument("--set-ngrok-token", action="store_true", help="store the ngrok authtoken in the Keychain")
    ap.add_argument("--enable", action="store_true", help="share now and at every login, in the background")
    ap.add_argument("--disable", action="store_true", help="stop background sharing and do not start at login")
    ap.add_argument("--status", action="store_true", help="show whether sharing is on and its URL")
    ap.add_argument("--new-token", action="store_true", help="make a new secret; the old URL stops working")
    ap.add_argument("--port", type=int, help="local port (default %d)" % DEFAULT_PORT)
    ap.add_argument("--no-tunnel", action="store_true", help="local HTTP only, no public address")
    ap.add_argument("--service", action="store_true", help=argparse.SUPPRESS)  # run by the LaunchAgent
    ap.add_argument("--openai-tunnel", metavar="TUNNEL_ID", help="use this OpenAI Secure MCP Tunnel (remembered)")
    ap.add_argument("--openai", action="store_true", help="use the remembered OpenAI tunnel")
    ap.add_argument("--set-openai-key", action="store_true", help="store the OpenAI runtime key in the Keychain")
    args = ap.parse_args(argv)

    if args.set_ngrok_token:
        return keychain_set("ngrok", "ngrok authtoken")
    if args.set_openai_key:
        return keychain_set("openai-tunnel", "OpenAI runtime API key")

    settings = load_settings()
    changed = False
    if args.ngrok_domain:
        domain = args.ngrok_domain.strip().lower()
        domain = re.sub(r"^https?://", "", domain).rstrip("/")
        if not NGROK_DOMAIN_RE.match(domain):
            print("that does not look like a domain: %r" % args.ngrok_domain)
            return 1
        settings.update(ngrok_domain=domain, provider="ngrok")
        changed = True
    if args.provider:
        settings["provider"] = args.provider
        changed = True
    if args.port:
        settings["port"] = args.port
        changed = True
    if args.openai_tunnel:
        if not TUNNEL_ID_RE.match(args.openai_tunnel):
            print("that does not look like a tunnel ID (tunnel_...)")
            return 1
        settings["openai_tunnel"] = args.openai_tunnel
        changed = True
    if changed:
        save_settings(settings)
    if args.new_token:
        load_token(True)

    if args.status:
        st = status()
        print("start at login: %s\nsharing now:    %s\ntunnel:         %s%s\nconnector URL:  %s\nlog:            %s"
              % ("on" if st["start_at_login"] else "off", "yes" if st["running"] else "no", st["provider"],
                 " (%s)" % st["ngrok_domain"] if st["provider"] == "ngrok" else "", st["url"] or "-", st["log"]))
        return 0
    if args.disable:
        disable()
        print("background sharing is off (also at login).")
        return 0
    if args.enable:
        if settings["provider"] == "ngrok" and not keychain_get("ngrok"):
            print("store the ngrok authtoken first: python3 server/run.py share --set-ngrok-token")
            return 1
        try:
            enable()
        except RuntimeError as e:
            print("could not enable: %s" % e)
            return 1
        st = wait_for_url()
        if st["url"]:
            print("background sharing is on (also at every login).\n\nConnector URL (keep it private):\n\n  %s\n"
                  % st["url"])
            return 0
        print("background sharing is enabled but has no address yet; see %s" % LOG_FILE)
        return 1
    if args.openai or args.openai_tunnel:
        if not settings.get("openai_tunnel"):
            print("no OpenAI tunnel remembered yet; pass --openai-tunnel tunnel_...")
            return 1
        try:
            return run_openai_tunnel(settings["openai_tunnel"])
        except RuntimeError as e:
            print("could not share: %s" % e)
            return 1
    if changed and not args.service and is_running():
        print("settings saved; restarting background sharing ...")
        enable()
        st = wait_for_url()
        print("connector URL: %s" % (st["url"] or "(not up yet, see %s)" % LOG_FILE))
        return 0
    if is_running() and not args.service:
        print("Sharing already runs in the background (share --status shows the URL; share --disable stops it).")
        return 0
    return run(settings, no_tunnel=args.no_tunnel, service=args.service)
