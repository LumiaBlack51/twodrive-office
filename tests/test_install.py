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
    def test_bridge_upgrade_preserves_existing_settings_bytes(self):
        import io
        import json
        import os
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config = root / 'twodrive-office/settings.json'
            config.parent.mkdir()
            data = {'mount_root': str(root / 'mount'), 'copy_directory': str(root / 'mount/copies'),
                    'bridge_token': 'existing-private-' + 'x' * 32, 'custom_metadata': 'preserve'}
            content = (json.dumps(data, indent='\t') + '\n').encode(); config.write_bytes(content)
            with patch.dict(os.environ, XDG_CONFIG_HOME=str(root)), patch.object(
                    sys, 'argv', ['install_wine.py', '--prefix', str(root / 'prefix'), '--bridge']), patch.object(
                    wine_installer, 'prefix_running', return_value=False), patch.object(
                    wine_installer, 'install', return_value=root / 'adapter'), patch(
                    'bridge_install.install_bridge'), patch('sys.stdout', new=io.StringIO()):
                wine_installer.main()
            self.assertEqual(config.read_bytes(), content)

    def test_sandbox_path_units_parse_absolute_paths_with_spaces(self):
        import subprocess
        import tempfile
        from twodrive_office.policy import SaveError
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prefix = root / 'bottle with spaces'; prefix.mkdir()
            runtime = root / 'runtime with spaces'; runtime.mkdir()
            destination = prefix / 'adapter'; destination.mkdir()
            _, definitions = wine_installer.sandbox_units(prefix, destination, runtime)
            paths = []
            for filename, value in definitions.items():
                path = root / filename; path.write_text(value); paths.append(str(path))
            subprocess.run(['systemd-analyze', '--user', 'verify', *paths], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with self.assertRaises(SaveError):
                wine_installer.sandbox_units(prefix, destination, Path('/bad\npath'))

    def test_unreleased_process_report_ignores_zombies_without_claiming_an_open_window(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); prefix = root / 'prefix'; prefix.mkdir()
            proc = root / 'proc'; proc.mkdir()
            for pid, state in ((123, 'Z'), (124, 'S')):
                process = proc / str(pid); process.mkdir()
                (process / 'environ').write_bytes(b'WINEPREFIX=' + str(prefix).encode() + b'\0')
                (process / 'stat').write_text(f'{pid} (POWERPNT.EXE) {state} 0 0 0\n')
                (process / 'comm').write_text('POWERPNT.EXE\n')
            self.assertEqual(wine_installer.prefix_processes(prefix, proc),
                             [{'pid': 124, 'name': 'POWERPNT.EXE', 'state': 'S'}])

    def test_patch_replaces_only_owned_sections_and_uninstall_preserves_other_keys(self):
        original = 'WINE REGISTRY Version 2\n#arch=win64\n\n[Other\\\\User] 12\n"keep"="data"\n\n'
        sections = wine_installer.registry_sections('C:\\private\\plugin')
        updated = wine_installer.patch_registry(original, sections)
        self.assertTrue(updated.startswith(original))
        self.assertEqual(updated.count('"LoadBehavior"=dword:00000003'), 6)
        twice = wine_installer.patch_registry(updated, sections)
        self.assertEqual(twice.count('"LoadBehavior"=dword:00000003'), 6)
        removed = wine_installer.patch_registry(twice, sections, uninstall=True)
        self.assertTrue(removed.startswith(original))
        self.assertNotIn(wine_installer.PROGID, removed)

    def test_c2r_disconnected_override_is_repaired_without_disabling_other_addins(self):
        key = ('Software\\Microsoft\\Office\\ClickToRun\\REGISTRY\\Machine\\Software\\Microsoft\\Office'
               '\\PowerPoint\\Addins\\' + wine_installer.PROGID)
        original = ('WINE REGISTRY Version 2\n\n[Other\\\\Addin]\n"LoadBehavior"=dword:00000002\n\n['
                    + key.replace('\\', '\\\\').upper() + '] 123\n"LoadBehavior"=dword:00000002\n\n')
        updated = wine_installer.patch_registry(original, wine_installer.registry_sections('C:\\private\\plugin'))
        self.assertIn('[Other\\\\Addin]\n"LoadBehavior"=dword:00000002', updated)
        self.assertEqual(updated.count('"LoadBehavior"=dword:00000002'), 1)
        self.assertIn('ClickToRun', updated)

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
