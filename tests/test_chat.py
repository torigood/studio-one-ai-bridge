"""Chat agent: backend command lines and replies, sessions, the local web page (fake agent, no AI call)."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from s1bridge import chat  # noqa: E402


class Result:
    def __init__(self, stdout, stderr=""):
        self.stdout, self.stderr, self.returncode = stdout, stderr, 0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = [mock.patch.object(chat, "DIR", self.tmp), mock.patch.object(chat, "SETTINGS",
                                                                               os.path.join(self.tmp, "s.json")),
                  mock.patch.object(chat, "LOG", os.path.join(self.tmp, "log.jsonl"))]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.tmp)


class Backends(Base):
    def test_claude_command_and_resume(self):
        cmd = chat.claude_command("hi", "abc", "opus")
        self.assertIn("--resume", cmd)
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "mcp__s1")
        cfg = json.loads(cmd[cmd.index("--mcp-config") + 1])
        self.assertIn("s1", cfg["mcpServers"])
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")

    def test_parse_claude(self):
        ok = json.dumps({"type": "result", "is_error": False, "result": "done", "session_id": "s1"})
        self.assertEqual(chat.parse_claude(ok), ("done", "s1", None))
        bad = json.dumps({"is_error": True, "result": "Invalid API key · Please run /login", "session_id": "s2"})
        reply, sid, err = chat.parse_claude(bad)
        self.assertIsNone(reply)
        self.assertIn("/login", err)

    def test_codex_command_and_parse(self):
        cmd = chat.codex_command("hi", None, None, "/tmp/o.txt")
        self.assertEqual(cmd[:2][1], "exec")
        self.assertIn('mcp_servers.studio-one.default_tools_approval_mode="approve"', cmd)
        self.assertTrue(cmd[-1].endswith("hi"))
        self.assertIn("resume", chat.codex_command("hi", "t1"))
        out = os.path.join(self.tmp, "o.txt")
        with open(out, "w") as f:
            f.write("added a bass track")
        self.assertEqual(chat.parse_codex('{"type":"thread.started","thread_id":"t9"}', out),
                         ("added a bass track", "t9", None))
        os.remove(out)
        reply, _t, err = chat.parse_codex('{"type":"error","message":"The \'x\' model is not supported"}', out)
        self.assertIsNone(reply)
        self.assertIn("chat --model", err)

    def test_ask_keeps_session(self):
        s = chat.load_settings()
        s["backend"] = "claude"
        seen = []

        def runner(cmd, **kw):
            seen.append(cmd)
            return Result(json.dumps({"is_error": False, "result": "ok %d" % len(seen), "session_id": "S"}))
        self.assertEqual(chat.ask("one", s, runner), ("ok 1", None))
        self.assertEqual(chat.ask("two", s, runner), ("ok 2", None))
        self.assertNotIn("--resume", seen[0])
        self.assertEqual(seen[1][seen[1].index("--resume") + 1], "S")
        self.assertEqual(chat.load_settings()["session"], "S")


class Page(Base):
    def test_send_and_poll(self):
        s = chat.load_settings()
        s["backend"] = "claude"

        def runner(cmd, **kw):
            prompt = cmd[cmd.index("-p") + 1]
            return Result(json.dumps({"is_error": False, "result": "echo: " + prompt, "session_id": "S"}))
        c = chat.Chat(s, runner)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), chat.make_handler(c))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d/%s" % (httpd.server_address[1], s["token"])
        try:
            page = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn("Studio One AI Chat", page)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen("http://127.0.0.1:%d/wrong/" % httpd.server_address[1])
            req = urllib.request.Request(base + "/send", data=json.dumps({"text": "베이스 트랙 추가해줘"}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req).read()
            for _ in range(50):
                msgs = json.loads(urllib.request.urlopen(base + "/messages?after=0").read())["messages"]
                if len(msgs) >= 2:
                    break
                time.sleep(0.05)
            self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
            self.assertEqual(msgs[1]["text"], "echo: 베이스 트랙 추가해줘")
            urllib.request.urlopen(urllib.request.Request(base + "/new", data=b"", method="POST")).read()
            self.assertNotIn("session", chat.load_settings())
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
