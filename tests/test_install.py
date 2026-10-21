import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("office_install", SCRIPTS / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class PluginLayoutTests(unittest.TestCase):
    def test_native_94_and_100_registry_layouts_are_both_installed_without_launch(self):
        root = Path("/isolated/data")
        directories = installer.onlyoffice_directories(root)
        self.assertEqual(len(directories), 2)
        self.assertEqual({p.parent for p in directories}, {
            root / "onlyoffice/desktopeditors/sdkjs-plugins",
            root / "onlyoffice/desktopeditors/data/sdkjs-plugins"})
        self.assertEqual(directories[0].name, directories[1].name)

    def test_explicit_directory_is_used_exactly(self):
        target = Path("/isolated/custom-plugin")
        self.assertEqual(installer.onlyoffice_directories(Path("/data"), target), [target])
