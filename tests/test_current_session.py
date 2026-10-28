from copy import deepcopy
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from t3_session import T3Session


class CurrentSessionTests(unittest.TestCase):
    def setUp(self):
        self.session = T3Session(Path('/unused'))
        self.lease = dict(visible=True, focused=True, appState='active',
                          updatedAt='2026-10-06T12:00:09Z', expiresAt='2026-10-06T12:00:54Z',
                          scopes=[dict(type='thread', threadId='chosen')])

    def resolve(self, leases):
        with patch.object(self.session, 'rpc', return_value=dict(
                updatedAt='2026-10-06T12:00:10Z', leases=leases)) as rpc, \
                patch.object(self.session, 'describe', side_effect=lambda tid: {'thread_id': tid}) as describe:
            result = self.session.current_thread()
            rpc.assert_called_once_with('server.getBackgroundPolicy', {}, timeout=2)
            describe.assert_called_once_with('chosen')
            return result

    def test_exact_foreground_selection_ignores_background_workspaces(self):
        background = dict(self.lease, focused=False, scopes=[dict(type='thread', threadId='other')])
        self.lease['scopes'].append(dict(type='vcs-status', workspacePath='/unrelated'))
        self.assertEqual(self.resolve([background, self.lease]), {'thread_id': 'chosen'})

    def test_missing_ambiguous_stale_and_malformed_reports_fail_closed(self):
        cases = [[]]
        for changes in [dict(visible=False), dict(focused=False), dict(appState='background'),
                        dict(expiresAt='2026-10-06T12:00:10Z'),
                        dict(updatedAt='2026-10-06T12:00:04Z'),
                        dict(updatedAt='2026-10-06T12:00:11Z'), dict(updatedAt=None),
                        dict(scopes=[]), dict(scopes=None),
                        dict(scopes=[dict(type='thread', threadId='chosen'), dict(type='thread', threadId='other')])]:
            cases.append([dict(self.lease, **changes)])
        for leases in cases:
            with self.subTest(leases=leases), self.assertRaises(ValueError):
                self.resolve(leases)

    def test_stale_window_does_not_block_a_fresh_selection(self):
        stale = dict(self.lease, updatedAt='2026-10-06T12:00:00Z',
                     scopes=[dict(type='thread', threadId='old')])
        self.assertEqual(self.resolve([stale, self.lease]), {'thread_id': 'chosen'})

    def test_duplicate_client_leases_use_latest_focus_state(self):
        chosen = dict(self.lease, clientId='browser')
        self.assertEqual(self.resolve([chosen, deepcopy(chosen)]), {'thread_id': 'chosen'})
        blurred = dict(chosen, focused=False, updatedAt='2026-10-06T12:00:10Z')
        with self.assertRaises(ValueError): self.resolve([blurred, chosen])

    def test_multiple_fresh_windows_offer_choices_without_guessing(self):
        other = dict(self.lease, clientId='another', scopes=[dict(type='thread', threadId='other')])
        with patch.object(self.session, 'rpc', return_value=dict(
                updatedAt='2026-10-06T12:00:10Z', leases=[self.lease, other])), \
                patch.object(self.session, 'describe', side_effect=lambda tid: {'thread_id': tid}):
            self.assertEqual(self.session.current_threads(), [{'thread_id':'chosen'}, {'thread_id':'other'}])
            with self.assertRaisesRegex(ValueError, 'Choose one on the device'):
                self.session.current_thread()

    def test_unconnected_window_does_not_block_valid_browser(self):
        unconnected = dict(self.lease, clientId='unconnected', scopes=[])
        self.assertEqual(self.resolve([unconnected, self.lease]), {'thread_id':'chosen'})

    def test_selection_still_requires_existing_available_thread(self):
        with patch.object(self.session, 'rpc', return_value=dict(
                updatedAt='2026-10-06T12:00:10Z', leases=[self.lease])), \
                patch.object(self.session, 'describe', side_effect=ValueError('unavailable')):
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                self.session.current_thread()


spec = importlib.util.spec_from_file_location('focus_installer',
    Path(__file__).parents[1] / 'integrations/install_t3_focus.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    @contextmanager
    def client_fixture(self):
        original = b'prefix;' + installer.HOOK + b'return r;}' + installer.IDENTITY_HOOK
        chat = installer.CHAT_HOOK
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(installer, 'ORIGINAL_SHA256', installer.digest(original)), \
                patch.object(installer, 'CHAT_SHA256', installer.digest(chat)):
            client = Path(directory) / 'client'
            assets = client / 'assets'
            assets.mkdir(parents=True)
            (assets / installer.ASSET).write_bytes(original)
            (assets / installer.CHAT_ASSET).write_bytes(chat)
            yield client, original, chat

    def test_endpoint_validation_and_javascript_string_escaping(self):
        helper = Path(installer.__file__).with_name('t3-current-session.js').read_bytes()
        for url in (installer.DEFAULT_RELAY_URL, '/voice/api/voice-client',
                    'https://display.example.test/shark-voice/api/voice-client',
                    'http://127.0.0.1:8874/api/voice-client', 'http://localhost:8874/api/voice-client',
                    'http://[::1]:8874/api/voice-client', 'https://display.example.test/";throw(42);//'):
            with self.subTest(url=url):
                result = installer.configured_helper(helper, url)
                line = next(line.strip() for line in result.decode().splitlines() if 'const endpoint = ' in line)
                self.assertEqual(json.loads(line[len('const endpoint = '):-1]), url)
        for url in ('', 'relative/path', '//display.example.test/path', 'javascript:alert(1)',
                    'http://display.example.test/api/voice-client', 'https://user:secret@display.example.test/path',
                    'https://display.example.test/path?token=secret', 'https://display.example.test/path#fragment',
                    'https://display.example.test:70000/path', 'https://display.example.test/\npath',
                    '/\\display.example.test/path', 'https://display.example.test/\x00path',
                    'https://display.example.test/path?', '/path#'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                installer.configured_helper(helper, url)
        with self.assertRaisesRegex(ValueError, 'marker'):
            installer.configured_helper(b'unknown helper', installer.DEFAULT_RELAY_URL)

    def test_desktop_endpoint_requires_an_explicit_https_or_loopback_url(self):
        helper = Path(installer.__file__).with_name('t3-current-session.js').read_bytes()
        for url in ('https://display.example.test/api/voice-client',
                    'http://127.0.0.1:8874/api/voice-client', 'http://[::1]:8874/api/voice-client'):
            with self.subTest(url=url):
                self.assertIn(json.dumps(url).encode(),
                              installer.configured_helper(helper, url, allow_relative=False))
        for url in (None, installer.DEFAULT_RELAY_URL, '//display.example.test/path'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                installer.configured_helper(helper, url, allow_relative=False)

    def test_reconfiguration_uses_recorded_asset_hashes_and_can_remove_without_old_url(self):
        helper = Path(installer.__file__).with_name('t3-current-session.js').read_bytes()
        configured = installer.configured_helper(helper, 'https://display.example.test/shark-voice/api/voice-client')
        with self.client_fixture() as (client, original, chat):
            self.assertEqual(installer.install(client, helper), 'installed')
            self.assertEqual(installer.install(client, configured, check=True), 'compatible')
            self.assertEqual(installer.install(client, configured), 'installed')
            self.assertEqual(installer.install(client, configured), 'already installed')
            asset = client / 'assets' / installer.ASSET
            self.assertIn(b'https://display.example.test/', asset.read_bytes())
            receipt = json.loads((client.parent / 'shark-current-session-backup/installed.json').read_text())
            self.assertEqual(receipt['assets'][installer.ASSET], installer.digest(asset.read_bytes()))
            self.assertEqual(installer.install(client, helper, remove=True), 'removed')
            self.assertEqual(asset.read_bytes(), original)
            self.assertEqual((asset.parent / installer.CHAT_ASSET).read_bytes(), chat)

    def test_verified_legacy_helper_upgrades_without_an_installation_receipt(self):
        old_helper = b'known previous helper'
        with self.client_fixture() as (client, original, chat), \
                patch.object(installer, 'LEGACY_HELPER_SHA256', {installer.digest(old_helper)}):
            installer.install(client, old_helper)
            (client.parent / 'shark-current-session-backup/installed.json').unlink()
            self.assertEqual(installer.install(client, b'new helper'), 'installed')
            self.assertIn(b'new helper', (client / 'assets' / installer.ASSET).read_bytes())

    def test_failed_receipt_write_restores_both_assets_and_previous_receipt(self):
        with self.client_fixture() as (client, original, chat):
            installer.install(client, b'previous helper', context=b'previous context')
            asset = client / 'assets' / installer.ASSET
            chat_asset = asset.parent / installer.CHAT_ASSET
            receipt = client.parent / 'shark-current-session-backup/installed.json'
            previous = {path: path.read_bytes() for path in (asset, chat_asset, receipt)}
            real_write = installer.atomic_write
            def write(path, content):
                if path == receipt: raise OSError('simulated receipt failure')
                real_write(path, content)
            with patch.object(installer, 'atomic_write', side_effect=write), \
                    self.assertRaisesRegex(OSError, 'receipt failure'):
                installer.install(client, b'new helper', context=b'new context')
            for path, content in previous.items():
                self.assertEqual(path.read_bytes(), content)

    def test_unknown_conversation_bundle_leaves_both_assets_and_backups_untouched(self):
        with self.client_fixture() as (client, original, chat):
            runtime = client / 'assets' / installer.ASSET
            conversation = runtime.with_name(installer.CHAT_ASSET)
            conversation.write_bytes(chat + b'unknown edit')
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                installer.install(client, b'helper')
            self.assertEqual(runtime.read_bytes(), original)
            self.assertEqual(conversation.read_bytes(), chat + b'unknown edit')
            self.assertEqual(list(client.parent.iterdir()), [client])

    def test_invalid_receipt_and_changed_backup_are_not_overwritten(self):
        with self.client_fixture() as (client, original, chat):
            installer.install(client, b'helper')
            backup = client.parent / 'shark-current-session-backup'
            receipt = backup / 'installed.json'
            installed = {path: path.read_bytes() for path in (client / 'assets').iterdir()}
            previous_receipt = receipt.read_bytes()
            receipt.write_text('{"version":1,"assets":{"unknown":"' + '0' * 64 + '"}}')
            with self.assertRaisesRegex(ValueError, 'receipt'):
                installer.install(client, b'new helper')
            receipt.write_bytes(previous_receipt)
            original_backup = backup / installer.ASSET
            original_backup.write_bytes(original + b'unknown edit')
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                installer.install(client, b'new helper')
            self.assertEqual(original_backup.read_bytes(), original + b'unknown edit')
            for path, content in installed.items():
                self.assertEqual(path.read_bytes(), content)

    def test_check_install_idempotence_and_exact_rollback(self):
        original = b'prefix;' + installer.HOOK + b'return r;}' + installer.IDENTITY_HOOK
        chat = installer.CHAT_HOOK
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(installer, 'ORIGINAL_SHA256', installer.digest(original)), \
                patch.object(installer, 'CHAT_SHA256', installer.digest(chat)):
            client = Path(directory) / 'client'
            asset = client / 'assets' / installer.ASSET
            asset.parent.mkdir(parents=True)
            asset.write_bytes(original)
            (asset.parent / installer.CHAT_ASSET).write_bytes(chat)
            self.assertEqual(installer.install(client, b'helper', check=True), 'compatible')
            self.assertEqual(list(Path(directory).iterdir()), [client])
            self.assertEqual(installer.install(client, b'helper'), 'installed')
            self.assertEqual(installer.install(client, b'helper'), 'already installed')
            self.assertEqual(installer.install(client, b'helper', check=True), 'installed')
            changed = asset.read_bytes() + b'unexpected edit'
            asset.write_bytes(changed)
            with self.assertRaises(ValueError):
                installer.install(client, b'helper', remove=True)
            self.assertEqual(asset.read_bytes(), changed)
            asset.write_bytes(installer.patched(original, b'helper'))
            installer.install(client, b'helper', remove=True)
            self.assertEqual(asset.read_bytes(), original)

    def test_unknown_build_is_never_patched(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            installer.patched(installer.HOOK, b'helper')

    def test_verified_previous_conversation_helper_can_be_upgraded_and_removed(self):
        original = b'prefix;' + installer.HOOK + b'return r;}' + installer.IDENTITY_HOOK
        chat = installer.CHAT_HOOK
        previous = installer.transform(chat, installer.digest(chat),
            [(installer.CHAT_HOOK, installer.CHAT_REPLACEMENT)], b'old context')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(installer, 'ORIGINAL_SHA256', installer.digest(original)), \
                patch.object(installer, 'CHAT_SHA256', installer.digest(chat)), \
                patch.object(installer, 'CHAT_V1_SHA256', installer.digest(previous)):
            client = Path(directory) / 'client'
            asset = client / 'assets' / installer.ASSET
            asset.parent.mkdir(parents=True)
            asset.write_bytes(original)
            chat_asset = asset.parent / installer.CHAT_ASSET
            chat_asset.write_bytes(chat)
            installer.install(client, b'helper', context=b'old context')
            self.assertEqual(installer.install(client, b'helper', context=b'new context'), 'installed')
            self.assertIn(b'new context', chat_asset.read_bytes())
            self.assertEqual(installer.install(client, b'helper', context=b'new context', remove=True), 'removed')
            self.assertEqual(chat_asset.read_bytes(), chat)
