"""Register this MCP server with AI apps other than Claude Desktop's extension. Standard library only.

  python3 -m s1bridge connect --list            which apps were found on this Mac
  python3 -m s1bridge connect codex             dry run: print what would change
  python3 -m s1bridge connect codex --apply     register
  python3 -m s1bridge connect codex --remove --apply

Apps with their own CLI (Claude Code, Codex) are registered through it; the others by merging
one entry into their JSON config, after a backup into <data dir>/backups/. A config that is not
plain JSON (VS Code allows comments) is left alone and the entry is printed for you to paste.

The server command is the bundle launcher (bin/start.sh, with its own Python) when this runs
from the Claude Desktop extension, else the running Python with server/run.py of the checkout.

ChatGPT (the chat app) accepts only remote HTTPS MCP servers, so it cannot start this local
server; ChatGPT subscribers can use Codex, which can.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

from . import config

NAME = "studio-one"
HOME = os.path.expanduser("~")
_APP_SUPPORT = os.path.join(HOME, "Library", "Application Support")

# client -> how it is registered. "json": (config file, key holding the servers, entry style).
CLIENTS = {
    "claude-desktop": {"title": "Claude Desktop (without the .mcpb extension)",
                       "json": (os.path.join(_APP_SUPPORT, "Claude", "claude_desktop_config.json"), "mcpServers", "plain"),
                       "detect": os.path.join(_APP_SUPPORT, "Claude")},
    "claude-code": {"title": "Claude Code", "cli": "claude"},
    "codex": {"title": "Codex (OpenAI; included with ChatGPT plans)", "cli": "codex"},
    "gemini": {"title": "Gemini CLI",
               "json": (os.path.join(HOME, ".gemini", "settings.json"), "mcpServers", "plain"),
               "detect": os.path.join(HOME, ".gemini")},
    "cursor": {"title": "Cursor",
               "json": (os.path.join(HOME, ".cursor", "mcp.json"), "mcpServers", "plain"),
               "detect": os.path.join(HOME, ".cursor")},
    "lmstudio": {"title": "LM Studio",
                 "json": (os.path.join(HOME, ".lmstudio", "mcp.json"), "mcpServers", "plain"),
                 "detect": os.path.join(HOME, ".lmstudio")},
    "vscode": {"title": "VS Code (GitHub Copilot agent mode)",
               "json": (os.path.join(_APP_SUPPORT, "Code", "User", "mcp.json"), "servers", "typed"),
               "detect": os.path.join(_APP_SUPPORT, "Code", "User")},
}


class ClientError(Exception):
    pass


def server_command():
    """argv that starts this server: the extension's launcher, else this Python + run.py."""
    launcher = os.path.join(config.PROJECT_ROOT, "bin", "start.sh")
    if os.path.isfile(launcher):
        return ["/bin/sh", launcher]
    python = sys.executable
    if python.startswith(("/Applications/Xcode.app/", "/Library/Developer/CommandLineTools/")):
        python = "/usr/bin/python3"  # the stable shim, not a path that moves with Xcode updates
    return [python, os.path.join(config.PROJECT_ROOT, "server", "run.py")]


def command(*args):
    """A Terminal command that runs this server's CLI with args, from any folder (absolute paths, quoted).
    Error messages show it so a user, or an AI relaying it, can paste it as is."""
    return shlex.join(server_command() + list(args))


def entry(style, argv):
    e = {"command": argv[0], "args": argv[1:]}
    return dict({"type": "stdio"}, **e) if style == "typed" else e


def snippet(client=None, argv=None):
    """JSON to paste by hand into an app's MCP settings."""
    argv = argv or server_command()
    key, style = "mcpServers", "plain"
    if client in CLIENTS and "json" in CLIENTS[client]:
        _, key, style = CLIENTS[client]["json"]
    return json.dumps({key: {NAME: entry(style, argv)}}, indent=2)


def find_cli(name):
    """The app's CLI: on PATH, or in the usual install folders (hosts may start us with a short PATH)."""
    found = shutil.which(name)
    if found:
        return found
    for d in (os.path.join(HOME, ".local", "bin"), "/opt/homebrew/bin", "/usr/local/bin"):
        p = os.path.join(d, name)
        if os.access(p, os.X_OK):
            return p
    return None


def detect():
    """{client: True/False} whether the app seems installed."""
    out = {}
    for name, c in CLIENTS.items():
        out[name] = bool(find_cli(c["cli"])) if "cli" in c else os.path.isdir(c["detect"])
    return out


def _cli_argv(client, cli, argv, remove):
    if client == "claude-code":
        return [cli, "mcp", "remove", "--scope", "user", NAME] if remove else \
            [cli, "mcp", "add", "--scope", "user", NAME, "--"] + argv
    return [cli, "mcp", "remove", NAME] if remove else [cli, "mcp", "add", NAME, "--"] + argv


def _read_json(path):
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    return data if isinstance(data, dict) else None


def plan(client, remove=False, argv=None):
    """(kind, detail) describing the change; kind is "cli" (argv list) or "json" (path, new data)."""
    if client not in CLIENTS:
        raise ClientError("unknown client %r; choose from: %s" % (client, ", ".join(CLIENTS)))
    argv = argv or server_command()
    c = CLIENTS[client]
    if "cli" in c:
        cli = find_cli(c["cli"])
        if not cli:
            raise ClientError("%s not found (command '%s'). Install it, or run by hand:\n  %s" % (
                c["title"], c["cli"], " ".join(_cli_argv(client, c["cli"], argv, remove))))
        return "cli", _cli_argv(client, cli, argv, remove)
    path, key, style = c["json"]
    data = _read_json(path)
    if data is None:
        raise ClientError("%s is not plain JSON (comments?), so it was not changed. Add this by hand:\n%s"
                          % (path, snippet(client, argv)))
    servers = data.get(key)
    if servers in (None, []):  # LM Studio starts with "mcpServers": []
        servers = {}
    if not isinstance(servers, dict):
        raise ClientError("%s: %r is not an object, so it was not changed. Add this by hand:\n%s"
                          % (path, key, snippet(client, argv)))
    servers = dict(servers)
    if remove:
        servers.pop(NAME, None)
    else:
        servers[NAME] = entry(style, argv)
    data = dict(data, **{key: servers})
    return "json", (path, data)


def connect(client, remove=False, do_apply=False, argv=None, log=print, backups=None):
    """Register (or remove) the server with one client. Returns a process exit code."""
    try:
        kind, detail = plan(client, remove, argv)
    except ClientError as e:
        log(str(e))
        return 1
    if kind == "cli":
        log("  run  %s" % " ".join(detail))
    else:
        path, data = detail
        log("  %s %s entry %r in %s" % ("remove" if remove else "write", CLIENTS[client]["title"], NAME, path))
    if not do_apply:
        log("Dry run, nothing changed. Add --apply to perform this.")
        return 0
    if kind == "cli":
        r = subprocess.run(detail, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        if out:
            log("  " + out.replace("\n", "\n  "))
        if r.returncode != 0 and not (remove and "not found" in out.lower()):
            return 1
    else:
        path, data = detail
        if os.path.isfile(path):
            dest = os.path.join(backups or os.path.join(config.DATA_DIR, "backups"),
                                time.strftime("%Y%m%d-%H%M%S") + "-" + client)
            os.makedirs(dest, exist_ok=True)
            shutil.copy2(path, dest)
            log("  backup -> %s" % dest)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".s1bridge-tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    log("done. Restart %s so it loads the change." % CLIENTS[client]["title"])
    return 0


def describe_all():
    found = detect()
    lines = ["%-15s %-9s %s" % (n, "found" if found[n] else "-", c["title"]) for n, c in CLIENTS.items()]
    lines.append("server command: %s" % " ".join(server_command()))
    return "\n".join(lines)
