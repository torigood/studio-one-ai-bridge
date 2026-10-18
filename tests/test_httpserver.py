import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from s1bridge import httpserver  # noqa: E402

TOKEN = "t" * 40


class HttpServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = httpserver.serve(TOKEN, 0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def post(self, body, path=None, headers=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path or "/%s/mcp" % TOKEN)
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers=dict({"Content-Type": "application/json",
                                                   "Accept": "application/json, text/event-stream"}, **(headers or {})))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, None, dict(e.headers)

    def test_initialize_and_list(self):
        status, body, headers = self.post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                           "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["serverInfo"]["name"], "studio-one-ai-bridge")
        self.assertIn("Mcp-Session-Id", headers)
        status, body, _ = self.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in body["result"]["tools"]]
        self.assertIn("daw_ping", names)

    def test_notification_and_batch(self):
        status, body, _ = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((status, body), (202, None))
        status, body, _ = self.post([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                     {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                     {"jsonrpc": "2.0", "id": 2, "method": "nope"}])
        self.assertEqual([r["id"] for r in body], [1, 2])
        self.assertEqual(body[1]["error"]["code"], -32601)

    def test_access(self):
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.post(ping, path="/wrong/mcp")[0], 404)
        self.assertEqual(self.post(ping, path="/%s/other" % TOKEN)[0], 404)
        self.assertEqual(self.post(ping, path="/mcp")[0], 404)
        self.assertEqual(self.post(ping, headers={"Origin": "https://evil.example"})[0], 403)
        req = urllib.request.Request("http://127.0.0.1:%d/%s/mcp" % (self.port, TOKEN))
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(e.exception.code, 405)

    def test_short_token_refused(self):
        with self.assertRaises(ValueError):
            httpserver.serve("short", 0)


if __name__ == "__main__":
    unittest.main()
