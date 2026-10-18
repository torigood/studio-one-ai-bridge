import io
import os
import plistlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from s1bridge import share  # noqa: E402


class ShareSettings(unittest.TestCase):
    """share settings and the LaunchAgent definition, without network, Keychain or launchctl."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        patches = [mock.patch.object(share, n, os.path.join(d, f)) for n, f in
                   (("SETTINGS_FILE", "share.json"), ("STATE_FILE", "state.json"), ("TOKEN_FILE", "token"),
                    ("LOG_FILE", "share.log"), ("PLIST", "agent.plist"))]
        patches.append(mock.patch.object(share, "is_running", lambda: False))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = share.cli(list(argv))
        return code, out.getvalue()

    def test_defaults(self):
        s = share.load_settings()
        self.assertEqual((s["provider"], s["port"]), ("cloudflare", share.DEFAULT_PORT))

    def test_ngrok_domain_is_normalized_and_remembered(self):
        with mock.patch.object(share, "run", lambda settings, **kw: 0):
            code, _ = self.cli("--ngrok-domain", "https://My-Name.ngrok-free.app/")
        self.assertEqual(code, 0)
        s = share.load_settings()
        self.assertEqual((s["provider"], s["ngrok_domain"]), ("ngrok", "my-name.ngrok-free.app"))
        self.assertEqual(oct(os.stat(share.SETTINGS_FILE).st_mode & 0o777), "0o600")

    def test_bad_domain(self):
        code, out = self.cli("--ngrok-domain", "not a domain")
        self.assertEqual(code, 1)
        self.assertIn("does not look like a domain", out)

    def test_token_is_private_and_stable(self):
        t1 = share.load_token()
        self.assertEqual(share.load_token(), t1)
        self.assertNotEqual(share.load_token(new=True), t1)
        self.assertEqual(oct(os.stat(share.TOKEN_FILE).st_mode & 0o777), "0o600")

    def test_launch_agent(self):
        p = plistlib.loads(share._plist())
        self.assertEqual(p["Label"], share.LABEL)
        self.assertEqual(p["ProgramArguments"][-2:], ["share", "--service"])
        self.assertEqual(p["KeepAlive"], {"SuccessfulExit": False})  # a stop is not restarted
        self.assertTrue(p["RunAtLoad"])

    def test_plist_keeps_paths(self):
        # launchd does not pass on the caller's environment
        with mock.patch.dict(os.environ, {"S1BRIDGE_DATA_DIR": "/tmp/s1data", "OTHER": "x"}):
            env = plistlib.loads(share._plist())["EnvironmentVariables"]
        self.assertEqual((env["S1BRIDGE_DATA_DIR"], env["HOME"]), ("/tmp/s1data", os.path.expanduser("~")))
        self.assertNotIn("OTHER", env)

    def test_status_when_off(self):
        code, out = self.cli("--status")
        self.assertEqual(code, 0)
        self.assertIn("sharing now:    no", out)
        self.assertIn("start at login: off", out)

    def test_status_ignores_dead_pid(self):
        share._write_private(share.STATE_FILE, '{"url": "https://x/secret/mcp", "pid": 999999999}')
        self.assertIsNone(share.status()["url"])


if __name__ == "__main__":
    unittest.main()
