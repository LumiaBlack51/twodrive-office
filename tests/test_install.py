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


spec = importlib.util.spec_from_file_location('wine_install', SCRIPTS / 'install_wine.py')
wine_installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wine_installer)

class WineRegistryTests(unittest.TestCase):
    def test_patch_replaces_only_owned_sections_and_uninstall_preserves_other_keys(self):
        original = 'WINE REGISTRY Version 2\n#arch=win64\n\n[Other\\\\User] 12\n"keep"="data"\n\n'
        sections = wine_installer.registry_sections('C:\\private\\plugin')
        updated = wine_installer.patch_registry(original, sections)
        self.assertTrue(updated.startswith(original))
        self.assertEqual(updated.count('"LoadBehavior"=dword:00000003'), 3)
        twice = wine_installer.patch_registry(updated, sections)
        self.assertEqual(twice.count('"LoadBehavior"=dword:00000003'), 3)
        removed = wine_installer.patch_registry(twice, sections, uninstall=True)
        self.assertTrue(removed.startswith(original))
        self.assertNotIn(wine_installer.PROGID, removed)

    def test_install_never_launches_wine_and_preserves_user_registry(self):
        import tempfile
        from unittest.mock import patch
        from twodrive_office.policy import Settings
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'drive_c/users/test/AppData/Local').mkdir(parents=True)
            original = b'WINE REGISTRY Version 2\n#arch=win64\n\n[Other] 1\n"keep"="yes"\n'
            (root / 'system.reg').write_bytes(original)
            (root / 'user.reg').write_bytes(original)
            binary = bytearray(256); binary[:2] = b'MZ'
            import struct
            struct.pack_into('<I', binary, 0x3c, 0x80); binary[0x80:0x84] = b'PE\0\0'
            struct.pack_into('<H', binary, 0x84, 0x8664)
            dll = root / 'input.dll'; dll.write_bytes(binary)
            settings = Settings(root / 'mount', root / 'mount/copies', bridge_token='x' * 48)
            with patch('subprocess.run', side_effect=AssertionError('No programs may be launched')):
                destination = wine_installer.install(root, dll, settings)
                # A subsequent upgrade must not follow an owned-file symlink.
                external = root / 'outside'; external.write_bytes(b'preserve')
                installed_dll = destination / 'TwoDrive.Wine.dll'
                installed_bytes = installed_dll.read_bytes(); installed_dll.unlink(); installed_dll.symlink_to(external)
                from twodrive_office.policy import SaveError
                with self.assertRaises(SaveError): wine_installer.install(root, dll, settings)
                self.assertEqual(external.read_bytes(), b'preserve')
                installed_dll.unlink(); installed_dll.write_bytes(installed_bytes)
                self.assertEqual((root / 'user.reg').read_bytes(), original)
                self.assertEqual((destination / 'bridge.ini').stat().st_mode & 0o777, 0o600)
                self.assertIn(str(root), (destination / 'bridge.ini').read_text(encoding='utf-16'))
                wine_installer.install(root, dll, settings, uninstall=True)
                self.assertNotIn(wine_installer.PROGID, (root / 'system.reg').read_text())

    def test_busy_prefix_is_refused_before_program_or_registry_changes(self):
        import tempfile
        from unittest.mock import patch
        from twodrive_office.policy import Settings, SaveError
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'drive_c').mkdir()
            for name in ('system.reg', 'user.reg'): (root / name).write_text('unchanged')
            with patch.object(wine_installer, 'prefix_running', return_value=True):
                with self.assertRaises(SaveError): wine_installer.install(root, root / 'missing.dll', Settings.defaults())
            self.assertEqual((root / 'system.reg').read_text(), 'unchanged')
