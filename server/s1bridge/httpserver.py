"""MCP over HTTP ("streamable HTTP", JSON responses only) for clients that cannot start a local
process, such as the ChatGPT chat app (remote connectors only). Standard library only.

  POST /<token>/mcp   one JSON-RPC message (or a batch); replies application/json,
                      202 for notifications only
  anything else       404 (also a wrong token), GET/DELETE on the endpoint 405

The server listens on 127.0.0.1 only; share.py puts a tunnel in front of it. The secret token in
the path is the access control: whoever has the full URL can use every tool, so the URL is shown
only to the user and never logged. Requests that carry an Origin header (a web page in a browser)
are refused, which also blocks DNS-rebinding attacks on the local port.
"""
import hmac
import json
import sys
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .__main__ import handle

MAX_BODY = 4 * 1024 * 1024
_lock = threading.Lock()  # one tool call at a time: Studio One runs jobs one by one anyway


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def respond(msg):
    """JSON-RPC reply for one message, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid request")
    if "id" not in msg:
        return None
    try:
        with _lock:
            return {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
    except KeyError:
        return _error(msg["id"], -32601, "method not found: %s" % msg.get("method"))
    except Exception as e:  # report, keep serving
        traceback.print_exc(file=sys.stderr)
        return _error(msg["id"], -32603, str(e))


def make_handler(token, log=None):
    path_ok = "/%s/mcp" % token

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "studio-one-ai-bridge"

        def log_message(self, fmt, *args):  # never print the path: it holds the token
            if log:
                log("%s %s" % (self.command, self.headers.get("User-Agent", "")[:60]))

        def _send(self, code, body=None, headers=None):
            data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            if not hmac.compare_digest(path.encode("utf-8"), path_ok.encode("utf-8")):
                self._send(404, {"error": "not found"})
                return False
            if self.headers.get("Origin"):
                self._send(403, {"error": "requests from web pages are not accepted"})
                return False
            return True

        def do_POST(self):
            if not self._authorized():
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                return self._send(413 if length > MAX_BODY else 400, _error(None, -32600, "bad body length"))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                return self._send(400, _error(None, -32700, "parse error"))
            headers = {}
            msgs = body if isinstance(body, list) else [body]
            if any(isinstance(m, dict) and m.get("method") == "initialize" for m in msgs):
                headers["Mcp-Session-Id"] = uuid.uuid4().hex
            replies = [r for r in (respond(m) for m in msgs) if r is not None]
            if not replies:
                return self._send(202, None, headers)
            self._send(200, replies if isinstance(body, list) else replies[0], headers)

        def do_GET(self):
            if self._authorized():
                self._send(405, {"error": "no server-sent events; use POST"}, {"Allow": "POST"})

        def do_DELETE(self):
            if self._authorized():
                self._send(200, {})  # sessions hold no state here

    return Handler


def serve(token, port=8765, host="127.0.0.1", log=None):
    """Create the HTTP server (not started). Port 0 picks a free port."""
    if len(token) < 24:
        raise ValueError("token too short")
    return ThreadingHTTPServer((host, port), make_handler(token, log))
