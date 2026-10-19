import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from s1bridge import clients  # noqa: E402

ARGV = ["/bin/sh", "/ext/bin/start.sh"]


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.files = {"gemini": os.path.join(d, "gemini", "settings.json"),
                      "vscode": os.path.join(d, "Code", "mcp.json"),
                      "lmstudio": os.path.join(d, "lmstudio", "mcp.json")}
        table = dict(clients.CLIENTS)
        table["gemini"] = dict(table["gemini"], json=(self.files["gemini"], "mcpServers", "plain"))
        table["vscode"] = dict(table["vscode"], json=(self.files["vscode"], "servers", "typed"))
        table["lmstudio"] = dict(table["lmstudio"], json=(self.files["lmstudio"], "mcpServers", "plain"))
        p = mock.patch.object(clients, "CLIENTS", table)
        p.start()
        self.addCleanup(p.stop)
        self.log = []

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, client, text):
        os.makedirs(os.path.dirname(self.files[client]), exist_ok=True)
        with open(self.files[client], "w", encoding="utf-8") as f:
            f.write(text)

    def get(self, client):
        with open(self.files[client], encoding="utf-8") as f:
            return json.load(f)

    def run_connect(self, client, remove=False, do_apply=True):
        return clients.connect(client, remove, do_apply, argv=ARGV, log=self.log.append,
                               backups=os.path.join(self.tmp.name, "backups"))

    def test_merge_keeps_other_servers_and_settings(self):
        self.put("gemini", json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}))
        self.assertEqual(self.run_connect("gemini"), 0)
        d = self.get("gemini")
        self.assertEqual(d["theme"], "dark")
        self.assertEqual(d["mcpServers"]["other"], {"command": "x"})
        self.assertEqual(d["mcpServers"]["studio-one"], {"command": "/bin/sh", "args": ["/ext/bin/start.sh"]})
        self.assertTrue(os.listdir(os.path.join(self.tmp.name, "backups")))

    def test_creates_missing_file(self):
        self.assertEqual(self.run_connect("gemini"), 0)
        self.assertIn("studio-one", self.get("gemini")["mcpServers"])

    def test_vscode_entry_has_type(self):
        self.assertEqual(self.run_connect("vscode"), 0)
        self.assertEqual(self.get("vscode")["servers"]["studio-one"]["type"], "stdio")

    def test_lmstudio_empty_list(self):
        self.put("lmstudio", '{"mcpServers": []}')
        self.assertEqual(self.run_connect("lmstudio"), 0)
        self.assertIn("studio-one", self.get("lmstudio")["mcpServers"])

    def test_comments_are_left_alone(self):
        text = '{\n  // my servers\n  "servers": {}\n}\n'
        self.put("vscode", text)
        self.assertEqual(self.run_connect("vscode"), 1)
        with open(self.files["vscode"], encoding="utf-8") as f:
            self.assertEqual(f.read(), text)
        self.assertTrue(any('"studio-one"' in line for line in self.log))

    def test_dry_run_and_remove(self):
        self.assertEqual(self.run_connect("gemini", do_apply=False), 0)
        self.assertFalse(os.path.exists(self.files["gemini"]))
        self.run_connect("gemini")
        self.assertEqual(self.run_connect("gemini", remove=True), 0)
        self.assertNotIn("studio-one", self.get("gemini")["mcpServers"])

    def test_cli_clients(self):
        with mock.patch.object(clients, "find_cli", lambda name: "/bin/" + name):
            kind, argv = clients.plan("codex", argv=ARGV)
            self.assertEqual(argv, ["/bin/codex", "mcp", "add", "studio-one", "--"] + ARGV)
            kind, argv = clients.plan("claude-code", argv=ARGV)
            self.assertEqual(argv[:6], ["/bin/claude", "mcp", "add", "--scope", "user", "studio-one"])
        with mock.patch.object(clients, "find_cli", lambda name: None):
            with self.assertRaises(clients.ClientError) as e:
                clients.plan("codex", argv=ARGV)
            self.assertIn("codex mcp add studio-one", str(e.exception))

    def test_server_command_prefers_bundle_launcher(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "bin"))
            open(os.path.join(root, "bin", "start.sh"), "w").close()
            with mock.patch.object(clients.config, "PROJECT_ROOT", root):
                self.assertEqual(clients.server_command(), ["/bin/sh", os.path.join(root, "bin", "start.sh")])

    def test_command_runs_from_any_folder(self):
        """Paths with spaces (e.g. "2. 코딩") come out quoted and absolute, so the line can be pasted as is."""
        import shlex
        root = "/Users/x/2. 코딩/studio-one-ai-bridge"
        with mock.patch.object(clients.config, "PROJECT_ROOT", root), \
                mock.patch.object(clients.sys, "executable", "/usr/bin/python3"):
            cmd = clients.command("setup", "--apply")
        self.assertEqual(shlex.split(cmd), ["/usr/bin/python3", root + "/server/run.py", "setup", "--apply"])
        self.assertIn("'%s/server/run.py'" % root, cmd)


if __name__ == "__main__":
    unittest.main()
