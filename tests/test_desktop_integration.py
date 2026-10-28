import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


from integrations import install_t3_desktop as installer


def fixture_archive(contents):
    root = {'files': {}}
    offset = 0
    for name, content in contents.items():
        entry = root
        parts = name.split('/')
        for part in parts[:-1]:
            entry = entry['files'].setdefault(part, {'files': {}})
        digest = hashlib.sha256(content).hexdigest()
        entry['files'][parts[-1]] = {'size': len(content), 'offset': str(offset),
            'integrity': {'algorithm': 'SHA256', 'hash': digest, 'blockSize': 4194304, 'blocks': [digest]}}
        offset += len(content)
    root['files']['native.node'] = {'size': 4, 'unpacked': True}
    encoded = json.dumps(root, separators=(',', ':')).encode()
    padding = b'\0' * (-len(encoded) % 4)
    header = struct.pack('<II', 4 + len(encoded) + len(padding), len(encoded)) + encoded + padding
    return struct.pack('<II', 4, len(header)) + header + b''.join(contents.values())


class DesktopInstallerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.archive = Path(self.directory.name) / 'app.asar'
        self.runtime = installer.RUNTIME_HOOK + b'return r;}' + installer.IDENTITY_HOOK
        self.chat = installer.CHAT_HOOK
        self.contents = {installer.RUNTIME_PATH: self.runtime,
                         installer.CHAT_PATH: self.chat, 'package.json': b'{"name":"T3"}'}
        self.original = fixture_archive(self.contents)
        self.archive.write_bytes(self.original)
        self.hashes = patch.multiple(installer,
            RUNTIME_SHA256=hashlib.sha256(self.runtime).hexdigest(),
            CHAT_SHA256=hashlib.sha256(self.chat).hexdigest())
        self.hashes.start()

    def tearDown(self):
        self.hashes.stop()
        self.directory.cleanup()

    def test_package_patch_keeps_other_files_and_updates_integrity(self):
        modified = installer.patched_archive(self.original, b'Pc();', b'context;')
        header, offset = installer.read_header(modified)
        for name in self.contents:
            entry = installer.lookup(header, name)
            content = modified[offset + int(entry['offset']):offset + int(entry['offset']) + entry['size']]
            if name == 'package.json':
                self.assertEqual(content, self.contents[name])
            elif name == installer.RUNTIME_PATH:
                self.assertIn(b'sharkVoiceIdentity(Cf())', content)
                self.assertTrue(content.endswith(b'xf();\n'))
            else:
                self.assertIn(b'protocolVersion:2', content)
            self.assertEqual(entry['integrity']['hash'], hashlib.sha256(content).hexdigest())
        self.assertTrue(header['files']['native.node']['unpacked'])

    def test_check_install_repeat_and_exact_restore(self):
        self.assertEqual(installer.install(self.archive, b'Pc();', b'context;', check=True), 'compatible')
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.archive])
        self.assertEqual(installer.install(self.archive, b'Pc();', b'context;'), 'installed')
        modified = self.archive.read_bytes()
        self.assertNotEqual(modified, self.original)
        self.assertEqual(installer.install(self.archive, b'Pc();', b'context;'), 'already installed')
        self.assertEqual(installer.install(self.archive, b'Pc();', b'context;', check=True), 'installed')
        self.assertEqual(installer.install(self.archive, b'Pc();', b'context;', remove=True), 'removed')
        self.assertEqual(self.archive.read_bytes(), self.original)

    def test_unknown_build_never_writes_the_package_or_backup(self):
        with patch.object(installer, 'RUNTIME_SHA256', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                installer.install(self.archive, b'helper', b'context')
        self.assertEqual(self.archive.read_bytes(), self.original)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.archive])

    def test_unexpected_package_edits_are_preserved(self):
        installer.install(self.archive, b'Pc();', b'context;')
        changed = self.archive.read_bytes() + b'Unexpected change'
        self.archive.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'changed'):
            installer.install(self.archive, b'Pc();', b'context;', remove=True)
        self.assertEqual(self.archive.read_bytes(), changed)

    def test_reconfiguration_and_removal_use_the_recorded_package_hash(self):
        helper = Path(installer.__file__).with_name('t3-current-session.js').read_bytes()
        first = installer.configured_helper(helper, 'https://display.example.test/shark-voice/api/voice-client',
                                            allow_relative=False)
        second = installer.configured_helper(helper, 'http://127.0.0.1:8874/api/voice-client',
                                             allow_relative=False)
        self.assertEqual(installer.install(self.archive, first, b'context;'), 'installed')
        self.assertEqual(installer.install(self.archive, second, b'context;', check=True), 'compatible')
        self.assertEqual(installer.install(self.archive, second, b'context;'), 'installed')
        modified = self.archive.read_bytes()
        self.assertIn(b'http://127.0.0.1:8874/api/voice-client', modified)
        self.assertEqual(installer.install(self.archive, second, b'context;'), 'already installed')
        self.assertEqual(installer.install(self.archive, helper, b'context;', remove=True), 'removed')
        self.assertEqual(self.archive.read_bytes(), self.original)

    def test_desktop_notifier_mapping_preserves_the_configured_endpoint(self):
        helper = Path(installer.__file__).with_name('t3-current-session.js').read_bytes()
        endpoint = 'https://display.example.test/Pc/api/voice-client'
        helper = installer.configured_helper(helper, endpoint, allow_relative=False)
        modified = installer.patched_archive(self.original, helper, b'context;')
        runtime = installer.archive_member(modified, installer.RUNTIME_PATH)
        self.assertIn(json.dumps(endpoint).encode(), runtime)
        self.assertIn(b'xf();', runtime)
        self.assertNotIn(b'Pc();', runtime)

    def test_known_legacy_helper_migrates_only_when_the_entire_package_matches(self):
        helper = b'previous known helper'
        previous = installer.patched_archive(self.original, helper, b'context;')
        self.archive.with_name(self.archive.name + '.shark-voice-original').write_bytes(self.original)
        self.archive.write_bytes(previous)
        with patch.object(installer, 'LEGACY_DESKTOP_HELPER_SHA256', {hashlib.sha256(helper).hexdigest()}):
            self.assertEqual(installer.install(self.archive, b'new helper', b'context;', check=True), 'compatible')
            changed = previous + b'unknown edit'
            self.archive.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, 'changed'):
                installer.install(self.archive, b'new helper', b'context;')
            self.assertEqual(self.archive.read_bytes(), changed)
            self.archive.write_bytes(previous)
            self.assertEqual(installer.install(self.archive, b'new helper', b'context;'), 'installed')

    def test_receipt_failure_restores_the_previous_package_and_receipt(self):
        installer.install(self.archive, b'previous helper', b'context;')
        receipt = self.archive.with_name(self.archive.name + '.shark-voice-installed.json')
        previous = {path: path.read_bytes() for path in (self.archive, receipt)}
        write = installer.atomic_write
        def fail_receipt(path, data):
            if path == receipt:
                raise OSError('simulated receipt failure')
            write(path, data)
        with patch.object(installer, 'atomic_write', side_effect=fail_receipt), \
                self.assertRaisesRegex(OSError, 'receipt failure'):
            installer.install(self.archive, b'new helper', b'new context')
        for path, data in previous.items():
            self.assertEqual(path.read_bytes(), data)

    def test_preparation_does_not_change_the_installation_or_backup(self):
        output = Path(self.directory.name) / 'prepared.asar'
        self.assertEqual(installer.install(self.archive, b'helper', b'context', output=output), 'prepared')
        self.assertEqual(self.archive.read_bytes(), self.original)
        self.assertEqual(set(Path(self.directory.name).iterdir()), {self.archive, output})
        backup = self.archive.with_name(self.archive.name + '.shark-voice-original')
        receipt = self.archive.with_name(self.archive.name + '.shark-voice-installed.json')
        for path in (self.archive, backup, receipt):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'output'):
                installer.install(self.archive, b'helper', b'context', output=path)
        self.assertEqual(self.archive.read_bytes(), self.original)

    def test_cli_requires_a_portable_explicit_endpoint_before_reading_the_archive(self):
        for options in ([], ['--relay-url', '/shark-voice/api/voice-client']):
            result = subprocess.run([sys.executable, installer.__file__, '--archive', '/absent.asar',
                                     '--check', *options], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Relay URL', result.stderr)
            self.assertNotIn('No such file', result.stderr)

    def test_invalid_receipt_is_preserved_without_changing_the_package(self):
        installer.install(self.archive, b'helper', b'context;')
        current = self.archive.read_bytes()
        receipt = self.archive.with_name(self.archive.name + '.shark-voice-installed.json')
        receipt.write_text('{"version":1,"sha256":"invalid"}')
        with self.assertRaisesRegex(ValueError, 'receipt'):
            installer.install(self.archive, b'new helper', b'context;')
        self.assertEqual(self.archive.read_bytes(), current)
        self.assertEqual(receipt.read_text(), '{"version":1,"sha256":"invalid"}')
