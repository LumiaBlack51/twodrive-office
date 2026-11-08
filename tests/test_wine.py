import io
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from twodrive_office.bridge import Bridge
from twodrive_office.policy import Settings, SaveError, fingerprint
from twodrive_office.wine import flat_opc, save_office, prepare_native, complete_native


def document(text='original'):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', '<document>' + text + '</document>')
    return out.getvalue()


def flat(parts):
    return ('<pkg:package xmlns:pkg="http://schemas.microsoft.com/office/2006/xmlPackage" '
            'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml">' + parts + '</pkg:package>').encode()


def part(name='/word/document.xml', data='<pkg:xmlData><w:document mc:Ignorable="w14"><w:body/></w:document></pkg:xmlData>'):
    return '<pkg:part pkg:name="' + name + '" pkg:contentType="application/xml">' + data + '</pkg:part>'


class FlatOPCTests(unittest.TestCase):
    def test_prefix_bindings_media_and_content_types_survive(self):
        out = flat_opc(flat(part() + part('/word/media/image.png', '<pkg:binaryData>AAECAw==</pkg:binaryData>')))
        with zipfile.ZipFile(io.BytesIO(out)) as z:
            text = z.read('word/document.xml')
            self.assertIn(b'mc:Ignorable="w14"', text)
            self.assertIn(b'xmlns:w14=', text)
            self.assertEqual(z.read('word/media/image.png'), bytes(range(4)))
            self.assertIn(b'/word/media/image.png', z.read('[Content_Types].xml'))

    def test_untrusted_package_refused(self):
        for data in (b'<!DOCTYPE x><x/>', flat(part('../escape')), flat(part('/word/vbaProject.bin')),
                     flat(part() + part()), flat(part(data='<pkg:binaryData>!!!</pkg:binaryData>')),
                     flat(part('/_xmlsignatures/sig1.xml'))):
            with self.subTest(data=data[:50]), self.assertRaises(SaveError):
                flat_opc(data)


class WineQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mount = self.root / 'mount'; self.mount.mkdir()
        self.source = self.mount / 'source.docx'; self.source.write_bytes(document())
        self.settings = Settings(self.mount, self.mount / 'copies', require_mount=False,
                                 bridge_token='isolated-wine-test-' + 'x' * 32, sync_interval_seconds=1)
        self.now = [0]
        self.bridge = Bridge(self.settings, state_root=self.root / 'state', clock=lambda: self.now[0])
        self.key = self.bridge.create({'source_path': str(self.source), 'extension': 'docx',
                                      'confirm_source': True, 'native_publish': True})['session_id']
        self.content = document('edited')

    def tearDown(self):
        self.bridge.local_saves.shutdown(); self.temp.cleanup()

    def stage(self, revision=1):
        return save_office(self.bridge, self.key, self.content, revision, str(self.source), 'ooxml')

    def test_native_source_uses_durable_stage_and_never_background_atomic_replace(self):
        before = self.source.read_bytes(); result = self.stage()
        self.assertEqual(Path(result['local_path']).read_bytes(), self.content)
        self.now[0] = 100; self.bridge.local_saves.tick()
        self.assertEqual(self.source.read_bytes(), before)
        token = prepare_native(self.bridge, self.key)['native_token']
        self.source.write_bytes(self.content)  # native Office Save, on its own STA
        complete_native(self.bridge, self.key, self.content, token)
        self.assertFalse(self.bridge.local_saves.pending())
        self.assertEqual(self.bridge.sessions[self.key].local.expected, fingerprint(self.source))

    def test_failed_native_save_or_stale_ack_preserves_revision(self):
        self.stage(); token = prepare_native(self.bridge, self.key)['native_token']
        with self.assertRaises(SaveError): complete_native(self.bridge, self.key, self.content, token)
        self.stage(2)
        self.source.write_bytes(self.content)
        with self.assertRaises(SaveError): complete_native(self.bridge, self.key, self.content, token)
        self.assertTrue(self.bridge.local_saves.pending())

    def test_native_preparation_rechecks_mount_destination_and_external_writes(self):
        self.stage(); self.source.write_bytes(document('external'))
        with self.assertRaises(SaveError): prepare_native(self.bridge, self.key)
        self.assertTrue(self.bridge.local_saves.pending())
        self.source.unlink(); self.source.symlink_to(self.root / 'outside')
        with self.assertRaises(SaveError): prepare_native(self.bridge, self.key)

    def test_source_change_and_macros_cannot_replace_pending(self):
        self.stage()
        with self.assertRaises(SaveError): save_office(self.bridge, self.key, self.content, 2, str(self.root / 'other.docx'), 'ooxml')
        out = io.BytesIO(self.content)
        with zipfile.ZipFile(out, 'a') as z: z.writestr('word/vbaProject.bin', b'macro')
        with self.assertRaises(SaveError): save_office(self.bridge, self.key, out.getvalue(), 2, str(self.source), 'ooxml')
        self.assertEqual(self.bridge.sessions[self.key].last_revision, 1)

    def test_close_failure_defers_recovery_until_all_editors_exit(self):
        before = self.source.read_bytes(); self.stage(); self.bridge.close(self.key)
        self.now[0] = 100; self.bridge.local_saves.tick()
        self.assertEqual(self.source.read_bytes(), before)
        with self.assertRaises(SaveError): self.bridge.create({'source_path': str(self.source), 'confirm_source': True})
        self.bridge.local_saves.allow_recovery(); self.bridge.local_saves.tick()
        self.assertEqual(self.source.read_bytes(), self.content)

    def test_explicit_native_save_event_reconciles_only_current_file(self):
        self.stage(); self.source.write_bytes(document('Ctrl S'))
        with self.assertRaises(SaveError): prepare_native(self.bridge, self.key)
        token = prepare_native(self.bridge, self.key, explicit=True)['native_token']
        complete_native(self.bridge, self.key, self.source.read_bytes(), token)
        self.assertFalse(self.bridge.local_saves.pending())
        with self.assertRaises(SaveError): complete_native(self.bridge, self.key, self.source.read_bytes(), token)


class WineMappingTests(unittest.TestCase):
    def test_real_mapping_case_folding_and_remap_are_rechecked(self):
        from twodrive_office.wine import resolve_wine_path
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); prefix = root / 'prefix'; prefix.mkdir()
            (prefix / 'user.reg').touch(); (prefix / 'drive_c').mkdir(); (prefix / 'dosdevices').mkdir()
            first, second = root / 'one', root / 'two'
            for folder in (first, second): folder.mkdir(); (folder / '文档.DOCX').touch()
            drive = prefix / 'dosdevices/t:'; drive.symlink_to(first)
            self.assertEqual(resolve_wine_path(str(prefix), 'T:\\文档.docx'), str(first / '文档.DOCX'))
            drive.unlink(); drive.symlink_to(second)
            self.assertEqual(resolve_wine_path(str(prefix), 'T:\\文档.docx'), str(second / '文档.DOCX'))
            for source in ('T:relative.docx', '\\\\server\\share\\file.docx', 'T:\\..\\outside', 'U:\\file.docx'):
                with self.assertRaises(SaveError): resolve_wine_path(str(prefix), source)
