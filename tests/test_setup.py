import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from s1bridge import devices, setup  # noqa: E402


def checks_by_name(checks):
    return {c["check"]: c for c in checks}


class SetupTests(unittest.TestCase):
    """setup against empty temp folders standing in for Studio One's settings and our data dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.paths = setup.Paths(app=os.path.join(root, "Studio One 5.app"),
                                 settings_dir=os.path.join(root, "settings"),
                                 data_dir=os.path.join(root, "data"))
        self.log = []

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, do_apply=True, running=False):
        steps = setup.plan_install(self.paths, daemon=False)
        return setup.run(steps, self.paths, do_apply, running=lambda: running, log=self.log.append)

    def doctor(self):
        return checks_by_name(setup.doctor(self.paths, ping=False, running=lambda: False, system=False))

    def test_dry_run_writes_nothing(self):
        self.assertEqual(self.install(do_apply=False), 0)
        self.assertFalse(os.path.exists(self.paths.settings_dir))
        self.assertFalse(os.path.exists(self.paths.data_dir))

    def test_refuses_while_studio_one_runs(self):
        self.assertEqual(self.install(running=True), 1)
        self.assertFalse(os.path.exists(self.paths.settings_dir))

    def test_install_puts_every_piece_in_place(self):
        self.assertEqual(self.install(), 0)
        package = os.path.join(self.paths.ext_dir, "scripts", "aibridge.package")
        self.assertEqual(setup.installed_version(package), setup.source_version())
        with zipfile.ZipFile(package) as z:
            self.assertIn("bridge.js", z.namelist())
        with open(os.path.join(self.paths.ext_dir, "metainfo.xml"), encoding="utf-8") as f:
            self.assertIn('value="%s"' % setup.source_version(), f.read())
        self.assertTrue(os.path.isfile(os.path.join(self.paths.ext_dir, "installdata.xml")))
        self.assertTrue(os.path.isfile(os.path.join(self.paths.surface_dir, "AI Bridge.device")))
        self.assertTrue(devices.is_registered(self.paths.devices))
        self.assertTrue(any("Studio One not found" in line for line in self.log))  # no app, no catalog

        c = self.doctor()
        for name in ("bridge extension", "control surface", "device registered"):
            self.assertIs(c[name]["ok"], True, name)
        self.assertIs(c["Studio One installed"]["ok"], False)

    def test_install_twice_keeps_one_device_and_backs_up(self):
        self.install()
        with open(self.paths.devices, encoding="utf-8") as f:
            first = f.read()
        self.install()
        with open(self.paths.devices, encoding="utf-8") as f:
            self.assertEqual(f.read(), first)
        backups = os.listdir(self.paths.backups)
        self.assertTrue(backups)
        latest = os.path.join(self.paths.backups, sorted(backups)[-1])
        self.assertTrue(any(n.endswith("MusicDevices.settings") for n in os.listdir(latest)))

    def test_bridge_xml_versions_match_bridge_js(self):
        """classfactory.xml and metainfo.xml carry the same version as kBridgeVersion."""
        import re
        want = setup.source_version()
        for name in ("classfactory.xml", "metainfo.xml"):
            with open(os.path.join(setup.BRIDGE_SRC, name), encoding="utf-8") as f:
                self.assertEqual(re.findall(r'(?:version|value)="(\d+\.\d+\.\d+)"', f.read()), [want], name)

    def test_doctor_flags_outdated_bridge(self):
        self.install()
        package = os.path.join(self.paths.ext_dir, "scripts", "aibridge.package")
        with zipfile.ZipFile(package, "w") as z:
            z.writestr("bridge.js", 'const kBridgeVersion = "0.0.1";\n')
        c = self.doctor()["bridge extension"]
        self.assertIs(c["ok"], False)
        self.assertIn("0.0.1", c["detail"])

    def test_doctor_on_empty_machine(self):
        c = self.doctor()
        for name in ("bridge extension", "control surface", "device registered"):
            self.assertIs(c[name]["ok"], False, name)
            self.assertIn("setup --apply", c[name]["fix"])

    def test_uninstall_removes_what_setup_added(self):
        self.install()
        steps = setup.plan_uninstall(self.paths)
        self.assertEqual(setup.run(steps, self.paths, True, running=lambda: False, log=self.log.append), 0)
        self.assertFalse(os.path.exists(self.paths.ext_dir))
        self.assertFalse(os.path.exists(self.paths.surface_dir))
        self.assertFalse(devices.is_registered(self.paths.devices))


if __name__ == "__main__":
    unittest.main()
