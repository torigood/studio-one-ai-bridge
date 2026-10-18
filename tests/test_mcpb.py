import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "packaging"))

import build_mcpb  # noqa: E402
from s1bridge import config, tools  # noqa: E402


class McpbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = build_mcpb.build(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bundle_layout(self):
        with zipfile.ZipFile(self.out) as z:
            names = set(z.namelist())
            m = json.loads(z.read("manifest.json"))
        for need in ("manifest.json", "server/run.py", "server/s1bridge/setup.py", "bridge/src/bridge.js",
                     "surface/AI Bridge.device", "re/tools/extract_catalog.py", "LICENSE"):
            self.assertIn(need, names)
        self.assertFalse([n for n in names if "__pycache__" in n or n.startswith("tests/")])
        self.assertEqual(m["server"]["entry_point"], "server/run.py")
        self.assertEqual(m["compatibility"]["platforms"], ["darwin"])

    def test_manifest_lists_every_tool(self):
        with zipfile.ZipFile(self.out) as z:
            m = json.loads(z.read("manifest.json"))
        self.assertEqual([t["name"] for t in m["tools"]], [t["name"] for t in tools.TOOLS])
        self.assertTrue(all(t["description"] for t in m["tools"]))

    def test_server_version_matches_project(self):
        from s1bridge.__main__ import SERVER_INFO
        self.assertEqual(SERVER_INFO["version"], build_mcpb.project_version())

    def test_unsubstituted_setting_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {"X_TEST": "${user_config.studio_one_app}"}):
            self.assertEqual(config._env("X_TEST", "dflt"), "dflt")
        with mock.patch.dict(os.environ, {"X_TEST": ""}):
            self.assertEqual(config._env("X_TEST", "dflt"), "dflt")
        with mock.patch.dict(os.environ, {"X_TEST": "${HOME}/Music/Studio One 5"}):  # as Claude Desktop passes it
            self.assertEqual(config._env("X_TEST", "dflt"), os.path.expanduser("~/Music/Studio One 5"))


if __name__ == "__main__":
    unittest.main()
