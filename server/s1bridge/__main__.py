"""Stdio MCP server (JSON-RPC 2.0, newline-delimited). Standard library only.

Run:  python3 -m s1bridge          (from the server/ directory)
      python3 -m s1bridge setup|doctor|uninstall   install / check the Studio One side (setup.py)
      python3 -m s1bridge connect <app> [--apply]  register with another AI app (clients.py)
      python3 -m s1bridge share                    share over HTTPS for the ChatGPT chat app (share.py)
"""
import json
import math
import sys
import traceback

from . import tools

SERVER_INFO = {"name": "studio-one-ai-bridge", "version": "0.6.0"}  # keep equal to pyproject.toml
INSTRUCTIONS = (
    "Studio One 5 bridge. daw_* tools act live on the open song through the in-DAW bridge (check daw_status "
    "first); song_* tools work on saved .song files. Conventions for all tools: song = absolute .song path "
    "(song_list). track = exact track name or 0-based index. wait = seconds to wait for the bridge (default "
    "10). beat = quarter notes from song start (0 = bar 1); bar = 1-based. Instrument tracks have no mixer "
    "channel of their own: channel tools use their instrument output channel. replace (default true) drops "
    "old content in the written range. song_write_*/song_add_*/song_set_*/song_remove_* edit the file: if the "
    "song is open it is saved, closed, edited and reopened automatically (~8 s, undo history lost), and the "
    "original is backed up first."
)
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")


def _clean(o):
    # JSON has no -inf (silent channels report volume_db=-inf)
    if isinstance(o, float) and (math.isinf(o) or math.isnan(o)):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def _reply(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _remote_call(name, args):
    if name in tools.REMOTE_HIDDEN:
        raise tools.ToolError("%s is not available over a shared URL; use it from an app on this Mac" % name)
    if name == "bridge_share":
        from . import share
        if args.get("action") == "off":
            share.stop_soon()  # after this reply has gone out: stopping ends this process
            return {"sharing": "stopping", "note": "This chat loses the Studio One tools in a few seconds. "
                    "Turn sharing on again on the Mac (Claude: 'share Studio One', or: share --enable)."}
        if args.get("action") != "status":
            raise tools.ToolError("sharing can only be stopped or checked from here")
        st = share.status()
        return {"sharing": st["running"], "start_at_login": st["start_at_login"], "provider": st["provider"]}
    return tools.call(name, args)


def handle(msg, remote=False):
    """remote: the request came over the shared HTTPS URL (httpserver.py), not from a local app."""
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        return {
            "protocolVersion": requested if requested in SUPPORTED else SUPPORTED[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tools.listed_tools(remote)}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            if remote:
                data = _remote_call(name, args)
            else:
                data = tools.call(name, args)
            # compact JSON: indentation costs tokens on every result
            text = json.dumps(_clean(data), ensure_ascii=False, separators=(",", ":"))
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except tools.ToolError as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:  # report, keep serving
            traceback.print_exc(file=sys.stderr)
            return {"content": [{"type": "text", "text": "%s: %s" % (type(e).__name__, e)}], "isError": True}
    raise KeyError(method)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "share":
        from . import share
        sys.exit(share.cli(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "analysis-setup":
        from . import analysis_pack
        sys.exit(analysis_pack.cli(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] in ("setup", "doctor", "uninstall", "connect"):
        from . import setup
        sys.exit(setup.cli(sys.argv[1:]))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _reply(None, error={"code": -32700, "message": "parse error"})
            continue
        if "id" not in msg:  # notification (e.g. notifications/initialized)
            continue
        try:
            _reply(msg["id"], handle(msg))
        except KeyError:
            _reply(msg["id"], error={"code": -32601, "message": "method not found: %s" % msg.get("method")})
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            _reply(msg["id"], error={"code": -32603, "message": str(e)})


if __name__ == "__main__":
    main()
