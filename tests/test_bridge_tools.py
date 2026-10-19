import io
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from s1bridge import live, midi, setup, tools  # noqa: E402


class BridgeToolTests(unittest.TestCase):
    """bridge_setup / bridge_doctor / version checks against temp folders."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        app = os.path.join(root, "Studio One 5.app")
        os.makedirs(app)
        self.paths = setup.Paths(app=app, settings_dir=os.path.join(root, "settings"),
                                 data_dir=os.path.join(root, "data"))
        patches = [
            mock.patch.object(setup, "Paths", lambda: self.paths),
            mock.patch.object(setup, "studio_one_running", lambda: False),
            mock.patch.object(midi, "status", lambda: "ok unique ID test"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def setup_tool(self):
        def noisy_catalog(paths, out):
            print("commands: 1, edit tasks: 1 -> %s" % out)  # like extract_catalog
        with mock.patch.object(setup, "build_catalog", noisy_catalog):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = tools.call("bridge_setup", {})
        return result, stdout.getvalue()

    def test_setup_keeps_stdout_clean(self):
        result, stdout = self.setup_tool()
        self.assertEqual(stdout, "")  # stdout carries MCP JSON-RPC
        self.assertEqual(result["installed"], setup.source_version())
        self.assertIn("daw_ping", result["next"])

    def test_setup_refuses_while_studio_one_runs(self):
        with mock.patch.object(setup, "studio_one_running", lambda: True):
            with self.assertRaises(tools.ToolError) as e:
                tools.call("bridge_setup", {})
        self.assertIn("quit Studio One", str(e.exception))
        self.assertFalse(os.path.exists(self.paths.settings_dir))

    def test_status_not_installed(self):
        r = live.version_check(None)
        self.assertIsNone(r["bridge_installed"])
        self.assertIn("bridge_setup", r["problem"])

    def test_status_after_setup_has_no_problem(self):
        self.setup_tool()
        r = live.version_check({"loaded": True, "version": setup.source_version()})
        self.assertEqual(r["bridge_installed"], setup.source_version())
        self.assertNotIn("problem", r)

    def test_status_outdated_install(self):
        self.setup_tool()
        with zipfile.ZipFile(os.path.join(self.paths.ext_dir, "scripts", "aibridge.package"), "w") as z:
            z.writestr("bridge.js", 'const kBridgeVersion = "0.0.1";\n')
        r = live.version_check({"loaded": True, "version": "0.0.1"})
        self.assertIn("older than this server", r["problem"])

    def test_status_studio_one_needs_restart(self):
        self.setup_tool()
        r = live.version_check({"loaded": True, "version": "0.0.1"})
        self.assertIn("restart", r["problem"])

    def test_ping_warns_on_old_bridge(self):
        with mock.patch.object(live, "bridge", lambda op, args, wait: {"pong": True, "version": "0.0.1"}):
            r = tools.call("daw_ping", {})
        self.assertIn("bridge_setup", r["warning"])

    def test_doctor_tool(self):
        with mock.patch.object(setup, "doctor", lambda: [{"check": "x", "ok": False, "detail": "", "fix": "y"}]):
            r = tools.call("bridge_doctor", {})
        self.assertFalse(r["ok"])

    def test_every_tool_has_a_handler(self):
        self.assertEqual(sorted(t["name"] for t in tools.TOOLS), sorted(tools.HANDLERS))


if __name__ == "__main__":
    unittest.main()
