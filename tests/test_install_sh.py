import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SCRIPT = os.path.join(ROOT, "install", "install.sh")

# stands in for the release launcher: records its arguments instead of starting the server
START_SH = """#!/bin/sh
echo "$*" >>"${0%/*}/../calls.log"
"""


def fake_mcpb(path, release=True):
    with zipfile.ZipFile(path, "w") as z:
        if release:
            z.writestr("bin/start.sh", START_SH)
        z.writestr("server/s1bridge/__main__.py", 'if sys.argv[1] == "share": pass\n')
        z.writestr("manifest.json", "{}")


@unittest.skipUnless(sys.platform == "darwin", "install.sh is macOS only")
class InstallScript(unittest.TestCase):
    """install/install.sh without network, Studio One or sharing: a fake .mcpb, a temp HOME."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = self.tmp.name
        self.app = os.path.join(d, "app dir")  # paths with spaces, like Application Support
        self.bin = os.path.join(d, "bin")
        self.mcpb = os.path.join(d, "bridge.mcpb")
        fake_mcpb(self.mcpb)
        self.env = {"HOME": d, "PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % self.bin,
                    "S1BRIDGE_INSTALL_DIR": self.app, "S1BRIDGE_BIN_DIR": self.bin}

    def run_sh(self, *args, **env):
        return subprocess.run(["/bin/sh", SCRIPT] + list(args), env=dict(self.env, **env),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)

    def calls(self):
        with open(os.path.join(self.app, "calls.log")) as f:
            return f.read().splitlines()

    def test_syntax(self):
        self.assertEqual(subprocess.run(["/bin/sh", "-n", SCRIPT]).returncode, 0)

    def test_unknown_option(self):
        r = self.run_sh("--bogus")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown option", r.stderr)

    def test_install_local_mcpb(self):
        r = self.run_sh("--mcpb", self.mcpb, "--no-setup", "--no-share")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cmd = os.path.join(self.bin, "studio-one-bridge")
        self.assertTrue(os.access(cmd, os.X_OK))
        subprocess.run([cmd, "share", "--status"], env=self.env, check=True)
        self.assertEqual(self.calls(), ["doctor --no-ping", "share --status"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, ".zprofile")))  # bin dir already on PATH

    def test_reinstall_replaces_the_app(self):
        self.assertEqual(self.run_sh("--mcpb", self.mcpb, "--no-setup", "--no-share").returncode, 0)
        with open(os.path.join(self.app, "stale.txt"), "w") as f:
            f.write("old")
        self.assertEqual(self.run_sh("--mcpb", self.mcpb, "--no-setup", "--no-share").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.app, "stale.txt")))
        self.assertFalse(os.path.exists(self.app + ".new") or os.path.exists(self.app + ".old"))

    def test_rejects_other_zip(self):
        other = os.path.join(self.tmp.name, "other.mcpb")
        fake_mcpb(other, release=False)
        r = self.run_sh("--mcpb", other, "--no-setup", "--no-share")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not look like", r.stderr)
        self.assertFalse(os.path.exists(self.app))

    def release_api(self, digest):
        url = "file://" + self.mcpb
        release = {"tag_name": "v9.9.9", "assets": [
            {"name": "notes.txt", "digest": "sha256:" + "0" * 64, "browser_download_url": "file:///nope.txt"},
            {"name": "bridge.mcpb", "digest": "sha256:" + digest, "browser_download_url": url}]}
        path = os.path.join(self.tmp.name, "release.json")
        with open(path, "w") as f:
            json.dump(release, f, indent=2)  # GitHub's API answers with indented JSON
        return "file://" + path

    def test_download_checks_sha256(self):
        with open(self.mcpb, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        r = self.run_sh("--no-setup", "--no-share", S1BRIDGE_RELEASE_API=self.release_api(sha))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SHA-256 checked", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.app, "bin", "start.sh")))

    def test_download_with_wrong_sha256_installs_nothing(self):
        r = self.run_sh("--no-setup", "--no-share", S1BRIDGE_RELEASE_API=self.release_api("f" * 64))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SHA-256", r.stderr)
        self.assertFalse(os.path.exists(self.app))


if __name__ == "__main__":
    unittest.main()
