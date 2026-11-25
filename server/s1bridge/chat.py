"""Chat with an AI that drives Studio One, from a small local web page (opened from inside Studio One with
Track > "AI Bridge - Open Chat", or in any browser on this Mac).

    python3 server/run.py chat                    run in this terminal and open the page
    python3 server/run.py chat --enable|--disable|--status   run in the background at login (LaunchAgent)
    python3 server/run.py chat --backend claude|codex [--model M]   pick the AI (saved)

The model is called by a command-line agent the user is already signed in to, with this bridge as its MCP
server, so no API key is handled here:
  claude  claude -p ... --mcp-config (this server) --allowedTools mcp__s1 --permission-mode dontAsk --resume
  codex   codex exec --json -c mcp_servers.studio-one... (tools auto-approved), then codex exec resume <thread>
One conversation continues across messages (the agent's session id); "new chat" starts a fresh one.

The page lives at http://127.0.0.1:<port>/<secret>/ (the secret keeps other local pages out); the URL is
also written to <mailbox>/chat_url.txt for the Studio One command.
"""
import html
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import clients, config
from .mailbox import Mailbox

DIR = os.path.join(config.DATA_DIR, "chat")
SETTINGS = os.path.join(DIR, "settings.json")
LOG = os.path.join(DIR, "messages.jsonl")
LABEL = "com.studio-one-ai-bridge.chat"
PLIST = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % LABEL)
SERVICE_LOG = os.path.join(DIR, "service.log")
DEFAULT_PORT = 8766
TIMEOUT = 600  # seconds for one answer (tool runs, offline edits with reopen)

SYSTEM = ("You are a music production assistant inside Studio One, talking to the composer through a small chat "
          "window. Use the s1 tools to act on the open song (check daw_status first when unsure). Before larger "
          "changes take daw_snapshot. Answer briefly, in the user's language, and say what you changed.")


# ---- settings and transcript ------------------------------------------------------------------------------

def _private(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    if not s.get("backend"):
        # Codex signed in (~/.codex/auth.json) first: the Claude CLI may be installed but not logged in
        s["backend"] = "codex" if _exe("codex") and os.path.isfile(os.path.expanduser("~/.codex/auth.json")) \
            else "claude"
    if "model" not in s:
        s["model"] = codex_default_model() if s["backend"] == "codex" else None
    s.setdefault("port", DEFAULT_PORT)
    if not s.get("token"):
        s["token"] = secrets.token_urlsafe(18)
        save_settings(s)
    return s


def save_settings(s):
    _private(SETTINGS, json.dumps(s, indent=1))


def codex_default_model():
    """A model the signed-in Codex account can use: the first chat model in Codex's own model cache
    (a ChatGPT account rejects the newest API-only models, observed 2026-11-25)."""
    try:
        with open(os.path.expanduser("~/.codex/models_cache.json"), encoding="utf-8") as f:
            models = json.load(f).get("models", [])
    except (OSError, ValueError, AttributeError):
        return None
    for m in models:
        slug = m.get("slug") or m.get("id") or ""
        if slug.startswith("gpt-") and "reserve" not in slug and "review" not in slug:
            return slug
    return None


def _home_bin(name):
    return os.path.expanduser("~/.local/bin/" + name)


def _exe(name):
    return shutil.which(name) or (_home_bin(name) if os.path.exists(_home_bin(name)) else None)


def read_log(after=0):
    out = []
    try:
        with open(LOG, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= after and line.strip():
                    out.append(json.loads(line))
    except (OSError, ValueError):
        pass
    return out


def append_log(entry):
    os.makedirs(DIR, exist_ok=True)
    entry.setdefault("time", time.strftime("%H:%M:%S"))
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    os.chmod(LOG, 0o600)


# ---- backends ----------------------------------------------------------------------------------------------

def _mcp_server():
    cmd = clients.server_command()
    return cmd[0], cmd[1:]


def claude_command(prompt, session, model=None):
    exe = _exe("claude") or "claude"
    command, args = _mcp_server()
    cfg = json.dumps({"mcpServers": {"s1": {"command": command, "args": args}}})
    cmd = [exe, "-p", prompt, "--output-format", "json", "--mcp-config", cfg, "--strict-mcp-config",
           "--allowedTools", "mcp__s1", "--permission-mode", "dontAsk", "--append-system-prompt", SYSTEM]
    if model:
        cmd += ["--model", model]
    if session:
        cmd += ["--resume", session]
    return cmd


def parse_claude(stdout):
    """(reply, session id, error) from claude -p --output-format json."""
    try:
        r = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None, None, "no answer from claude: %s" % stdout.strip()[-300:]
    if r.get("is_error"):
        msg = r.get("result") or r.get("subtype") or "error"
        if "login" in msg.lower() or "api key" in msg.lower():
            msg += " (in Terminal: claude, then /login)"
        return None, r.get("session_id"), msg
    return r.get("result") or "", r.get("session_id"), None


def codex_command(prompt, session, model=None, out_file=None):
    exe = _exe("codex") or "codex"
    command, args = _mcp_server()
    # Same name as `connect codex` registers (clients.NAME): a second entry would leave the registered one
    # without auto-approval, and a call through it is cancelled in exec mode (seen 2026-11-25).
    n = clients.NAME
    cfg = ["-c", "mcp_servers.%s.command=%s" % (n, json.dumps(command)),
           "-c", "mcp_servers.%s.args=%s" % (n, json.dumps(args)),
           "-c", 'mcp_servers.%s.default_tools_approval_mode="approve"' % n]
    cmd = [exe, "exec"] + (["resume", session] if session else []) + ["--skip-git-repo-check", "--json"] + cfg
    if model:
        cmd += ["-m", model]
    if out_file:
        cmd += ["-o", out_file]
    return cmd + [SYSTEM + "\n\n" + prompt if not session else prompt]


def parse_codex(stdout, out_file):
    """(reply, thread id, error) from codex exec --json and its -o file."""
    thread, error = None, None
    for line in stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "thread.started":
            thread = e.get("thread_id")
        elif e.get("type") in ("error", "turn.failed"):
            err = e.get("message") or (e.get("error") or {}).get("message") or json.dumps(e)
            error = err
    reply = None
    if out_file and os.path.isfile(out_file):
        with open(out_file, encoding="utf-8") as f:
            reply = f.read().strip() or None
    if reply is None and error is None:
        error = "no answer from codex"
    if error and "model" in error and "not supported" in error:
        error += " (pick another model: chat --model <name>)"
    return reply, thread, error if reply is None else None


def ask(prompt, settings, runner=subprocess.run):
    """One message to the agent. Returns (reply, error) and keeps the session id in settings."""
    backend, model, session = settings["backend"], settings.get("model"), settings.get("session")
    env = dict(os.environ, S1BRIDGE_CHAT="1")
    if backend == "claude":
        r = runner(claude_command(prompt, session, model), capture_output=True, text=True, timeout=TIMEOUT, env=env,
                   stdin=subprocess.DEVNULL,
                   cwd=DIR)
        reply, sid, err = parse_claude(r.stdout or r.stderr or "")
    elif backend == "codex":
        fd, out_file = tempfile.mkstemp(suffix=".txt", dir=DIR)
        os.close(fd)
        try:
            # stdin closed: codex exec appends piped stdin to the prompt and waits for its end (hung 2026-11-25)
            r = runner(codex_command(prompt, session, model, out_file), capture_output=True, text=True,
                       timeout=TIMEOUT, env=env, cwd=DIR, stdin=subprocess.DEVNULL)
            reply, sid, err = parse_codex((r.stdout or "") + "\n" + (r.stderr or ""), out_file)
        finally:
            os.remove(out_file)
        sid = sid or session
    else:
        return None, "unknown backend %r (claude or codex)" % backend
    if sid and sid != session:
        settings["session"] = sid
        save_settings(settings)
    return reply, err


# ---- worker and web page -------------------------------------------------------------------------------

class Chat:
    def __init__(self, settings, runner=subprocess.run):
        self.settings, self.runner = settings, runner
        self.jobs = queue.Queue()
        self.busy = False
        threading.Thread(target=self._work, daemon=True).start()

    def send(self, text):
        append_log({"role": "user", "text": text})
        self.jobs.put(text)

    def new_chat(self):
        self.settings.pop("session", None)
        save_settings(self.settings)
        append_log({"role": "system", "text": "new chat"})

    def _work(self):
        while True:
            text = self.jobs.get()
            self.busy = True
            try:
                reply, err = ask(text, self.settings, self.runner)
            except subprocess.TimeoutExpired:
                reply, err = None, "no answer in %d s" % TIMEOUT
            except OSError as e:
                reply, err = None, "could not start %s: %s" % (self.settings["backend"], e)
            append_log({"role": "assistant", "text": reply} if reply is not None else {"role": "error", "text": err})
            self.busy = self.jobs.qsize() > 0


PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Studio One AI Chat</title><style>
:root{--bg:#1e1f22;--fg:#e8e8e8;--muted:#9aa0a6;--me:#2d5bd7;--ai:#2b2d31;--err:#8b2c2c;--line:#3a3c41}
@media (prefers-color-scheme: light){:root{--bg:#f6f6f7;--fg:#1d1d1f;--muted:#6b6b70;--me:#2d5bd7;--ai:#fff;--err:#f6d6d6;--line:#dcdce0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,system-ui,sans-serif;
display:flex;flex-direction:column;height:100vh}header{padding:10px 16px;border-bottom:1px solid var(--line);display:flex;
gap:12px;align-items:center}header b{flex:1}header span{color:var(--muted);font-size:13px}button{font:inherit;border:1px solid var(--line);
background:transparent;color:var(--fg);border-radius:8px;padding:6px 12px;cursor:pointer}#log{flex:1;overflow:auto;padding:16px;
display:flex;flex-direction:column;gap:10px}.m{max-width:80%;padding:8px 12px;border-radius:12px;white-space:pre-wrap;word-break:break-word}
.user{align-self:flex-end;background:var(--me);color:#fff}.assistant{align-self:flex-start;background:var(--ai);border:1px solid var(--line)}
.error{align-self:flex-start;background:var(--err)}.system{align-self:center;color:var(--muted);font-size:13px}
form{display:flex;gap:8px;padding:12px 16px;border-top:1px solid var(--line)}textarea{flex:1;resize:none;font:inherit;padding:8px 10px;
border-radius:8px;border:1px solid var(--line);background:var(--ai);color:var(--fg);height:64px}#send{background:var(--me);color:#fff;border:0}
</style></head><body><header><b>Studio One AI Chat</b><span id="st">__BACKEND__</span><button id="new">새 대화</button></header>
<div id="log"></div><form id="f"><textarea id="t" placeholder="무엇을 할까요? (Enter 보내기, Shift+Enter 줄바꿈)"></textarea>
<button id="send">보내기</button></form><script>
const base=location.pathname.replace(/\\/$/,'');let n=0;const log=document.getElementById('log'),t=document.getElementById('t'),st=document.getElementById('st');
function add(m){const d=document.createElement('div');d.className='m '+m.role;d.textContent=m.text;log.appendChild(d);log.scrollTop=log.scrollHeight}
async function poll(){try{const r=await fetch(base+'/messages?after='+n);const j=await r.json();j.messages.forEach(add);n+=j.messages.length;
st.textContent=j.backend+(j.busy?' · 작업 중…':'')}catch(e){st.textContent='연결 끊김'}setTimeout(poll,1000)}
async function send(){const v=t.value.trim();if(!v)return;t.value='';await fetch(base+'/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:v})})}
document.getElementById('f').onsubmit=e=>{e.preventDefault();send()};
t.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send()}});
document.getElementById('new').onclick=()=>fetch(base+'/new',{method:'POST'});poll();t.focus();
</script></body></html>"""


def make_handler(chat):
    token = chat.settings["token"]

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _ok(self, body, ctype="application/json"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _route(self):
            u = urllib.parse.urlparse(self.path)
            parts = u.path.strip("/").split("/")
            if not parts or not secrets.compare_digest(parts[0], token):
                self.send_error(404)
                return None, None
            return "/".join(parts[1:]), urllib.parse.parse_qs(u.query)

        def do_GET(self):
            path, q = self._route()
            if path is None:
                return
            if path == "":
                label = chat.settings["backend"] + (" · " + chat.settings["model"] if chat.settings.get("model") else "")
                return self._ok(PAGE.replace("__BACKEND__", html.escape(label)), "text/html")
            if path == "messages":
                after = int((q.get("after") or ["0"])[0])
                return self._ok(json.dumps({"messages": read_log(after), "busy": chat.busy,
                                            "backend": chat.settings["backend"]}, ensure_ascii=False))
            self.send_error(404)

        def do_POST(self):
            path, _q = self._route()
            if path is None:
                return
            if self.headers.get("Origin") and not self.headers["Origin"].startswith("http://127.0.0.1"):
                self.send_error(403)
                return
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0) or 0)
            if path == "send":
                text = (json.loads(body or b"{}").get("text") or "").strip()
                if text:
                    chat.send(text[:8000])
                return self._ok("{}")
            if path == "new":
                chat.new_chat()
                return self._ok("{}")
            self.send_error(404)
    return H


def url(settings):
    return "http://127.0.0.1:%d/%s/" % (settings["port"], settings["token"])


def serve(settings, open_browser=True, runner=subprocess.run):
    chat = Chat(settings, runner)
    httpd = ThreadingHTTPServer(("127.0.0.1", settings["port"]), make_handler(chat))
    link = url(settings)
    try:  # for Track > "AI Bridge - Open Chat" in Studio One
        with open(os.path.join(Mailbox().root, "chat_url.txt"), "w", encoding="utf-8") as f:
            f.write(link)
    except OSError:
        pass
    print("chat: %s (backend %s)" % (link, settings["backend"]), file=sys.stderr, flush=True)
    if open_browser:
        subprocess.run(["/usr/bin/open", link], check=False)
    httpd.serve_forever()


# ---- background service ------------------------------------------------------------------------------------

def _plist():
    import plistlib
    env = {k: v for k, v in os.environ.items() if k.startswith("S1BRIDGE_")}
    env["HOME"] = os.path.expanduser("~")
    env["PATH"] = os.pathsep.join([os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin",
                                   "/usr/bin", "/bin"])
    return plistlib.dumps({"Label": LABEL, "ProgramArguments": clients.server_command() + ["chat", "--service"],
                           "EnvironmentVariables": env, "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False},
                           "ThrottleInterval": 30, "StandardOutPath": SERVICE_LOG, "StandardErrorPath": SERVICE_LOG,
                           "ProcessType": "Background"})


def _launchctl(*args):
    return subprocess.run(["/bin/launchctl"] + list(args), capture_output=True, text=True)


def enable():
    os.makedirs(os.path.dirname(PLIST), exist_ok=True)
    os.makedirs(DIR, exist_ok=True)
    _launchctl("bootout", "gui/%d/%s" % (os.getuid(), LABEL))
    with open(PLIST, "wb") as f:
        f.write(_plist())
    r = _launchctl("bootstrap", "gui/%d" % os.getuid(), PLIST)
    if r.returncode != 0:
        raise RuntimeError("launchctl bootstrap failed: %s" % (r.stderr or r.stdout).strip())


def disable():
    _launchctl("bootout", "gui/%d/%s" % (os.getuid(), LABEL))
    if os.path.isfile(PLIST):
        os.remove(PLIST)


def cli(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="chat", description="Chat page that drives Studio One through an AI agent")
    ap.add_argument("--backend", choices=["claude", "codex"])
    ap.add_argument("--model", help="model for the backend ('' = the backend's default)")
    ap.add_argument("--port", type=int)
    ap.add_argument("--enable", action="store_true", help="run in the background now and at login")
    ap.add_argument("--disable", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--service", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    s = load_settings()
    changed = False
    for k in ("backend", "port"):
        if getattr(a, k) is not None:
            s[k], changed = getattr(a, k), True
    if a.model is not None:
        s["model"], changed = (a.model or None), True
    if changed:
        s.pop("session", None)  # another backend cannot resume this one's session
        save_settings(s)
    if a.disable:
        disable()
        print("chat: off")
        return 0
    if a.enable:
        enable()
        print("chat: on (also at login) -> %s" % url(s))
        return 0
    if a.status:
        on = _launchctl("print", "gui/%d/%s" % (os.getuid(), LABEL)).returncode == 0
        print("chat: %s, backend %s%s -> %s" % ("running" if on else "off", s["backend"],
                                              " (%s)" % s["model"] if s.get("model") else "", url(s)))
        return 0
    if not _exe(s["backend"]):
        print("chat: %s is not installed (Claude Code: https://claude.com/claude-code; Codex: npm i -g @openai/codex)"
              % s["backend"], file=sys.stderr)
        return 1
    serve(s, open_browser=not (a.no_browser or a.service))
    return 0
