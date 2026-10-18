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
        self.out = build_mcpb.build(self.tmp.name, python=False)  # offline and fast

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
        self.assertEqual(m["server"]["mcp_config"]["command"], "python3")
        self.assertEqual(m["compatibility"]["platforms"], ["darwin"])
        self.assertIn("bin/start.sh", names)

    def test_bundled_manifest_starts_launcher(self):
        m = build_mcpb.manifest("1.0.0", bundled=True)
        self.assertEqual(m["server"]["type"], "binary")
        self.assertEqual(m["server"]["mcp_config"]["args"], ["${__dirname}/bin/start.sh"])
        self.assertNotIn("runtimes", m["compatibility"])

    def test_python_trim(self):
        keep = build_mcpb.keep_python_member
        self.assertEqual(keep("python/bin/python3.12"), "bin/python3")
        self.assertEqual(keep("python/lib/python3.12/json/decoder.py"), "lib/python3.12/json/decoder.py")
        self.assertEqual(keep("python/lib/python3.12/lib-dynload/_ctypes.cpython-312-darwin.so"),
                         "lib/python3.12/lib-dynload/_ctypes.cpython-312-darwin.so")
        for drop in ("python/bin/pip3", "python/include/python3.12/Python.h", "python/lib/libtcl9.0.dylib",
                     "python/lib/python3.12/tkinter/__init__.py", "python/lib/python3.12/site-packages/pip/x.py",
                     "python/lib/python3.12/test/test_os.py", "python/lib/python3.12/json/__pycache__/a.pyc"):
            self.assertIsNone(keep(drop), drop)

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
