"""Stdio MCP server (JSON-RPC 2.0, newline-delimited). Standard library only.

Run:  python3 -m s1bridge          (from the server/ directory)
      python3 -m s1bridge setup|doctor|uninstall   install / check the Studio One side (setup.py)
"""
import json
import math
import sys
import traceback

from . import tools

SERVER_INFO = {"name": "studio-one-ai-bridge", "version": "0.2.0"}  # keep equal to pyproject.toml
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


def handle(msg):
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        return {
            "protocolVersion": requested if requested in SUPPORTED else SUPPORTED[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "Studio One 5 bridge. song_* tools read saved songs offline. daw_* tools act on "
                            "the open song through the in-DAW bridge; check daw_status first.",
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tools.TOOLS}
    if method == "tools/call":
        name = params.get("name")
        try:
            data = tools.call(name, params.get("arguments") or {})
            text = json.dumps(_clean(data), ensure_ascii=False, indent=1)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except tools.ToolError as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:  # report, keep serving
            traceback.print_exc(file=sys.stderr)
            return {"content": [{"type": "text", "text": "%s: %s" % (type(e).__name__, e)}], "isError": True}
    raise KeyError(method)


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("setup", "doctor", "uninstall"):
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
