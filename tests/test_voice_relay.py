from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading
import unittest
from unittest.mock import Mock, patch

from voice_relay import VoiceRelay, CONNECTION_GRACE, configured_hosts, configured_origins
from t3_session import DispatchRejected


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.now = 10
        self.token = 'a' * 64
        self.client = 'shark-voice:' + hashlib.sha256(self.token.encode()).hexdigest()
        self.activity = Mock(return_value=dict(updatedAt='2026-10-06T12:00:10Z', leases=[dict(
            clientId=self.client, expiresAt='2026-10-06T12:00:30Z')]))
        self.relay = VoiceRelay(self.activity, clock=lambda:self.now, timeout=.5)
        self.selection = dict(id='selection', threadId='unknown-to-local-db', environmentId='spark',
            environment='Spark', projectId='other-project', project='Remote project', title='New session', new=True)
        self.publish()

    def publish(self, **changes):
        return self.relay.exchange(self.client, dict(selection=self.selection, focused=True, visible=True, **changes))

    def test_only_secret_with_live_authenticated_t3_proof_is_accepted(self):
        self.assertEqual(self.relay.authorize(self.token), self.client)
        for token in ['invalid', 'b'*64, self.client]:
            with self.assertRaises(ValueError): self.relay.authorize(token)
        self.activity.return_value['leases'][0]['expiresAt'] = '2026-10-06T12:00:10Z'
        with self.assertRaises(ValueError): self.relay.authorize(self.token)

    def test_remote_draft_routes_without_catalogue_and_pins_selection(self):
        row = self.relay.current(self.client)
        self.assertTrue(row['browser'])
        self.assertTrue(row['new'])
        self.selection = {**self.selection, 'id':'changed', 'threadId':'another-thread'}
        self.publish()
        self.assertNotEqual(self.relay.current(self.client)['thread_id'], row['thread_id'])
        ready = threading.Event()
        original_wait = self.relay.lock.wait_for
        def wait(*args, **kwargs):
            ready.set()
            return original_wait(*args, **kwargs)
        self.relay.lock.wait_for = wait
        with ThreadPoolExecutor() as pool:
            future = pool.submit(self.relay.send, row['thread_id'], 'Spoken words', 'command', 'message')
            self.assertTrue(ready.wait(1))
            job = self.publish()['jobs'][0]
            self.assertEqual(job['selectionId'], 'selection')
            self.assertEqual(job['text'], 'Spoken words')
            # A different authenticated tab cannot acknowledge this command.
            self.relay.exchange('other-client', dict(receipts=[dict(id='command', state='sent', sequence=9)]))
            self.assertFalse(future.done())
            self.publish(receipts=[dict(id='command', state='sent', sequence=9)])
            self.assertEqual(future.result(), 9)
        self.assertFalse(self.relay.describe(row['thread_id'])['new'])
        self.assertEqual(self.relay.describe(row['thread_id'])['title'], 'Spoken words')
        self.assertEqual(self.publish()['jobs'], [])

    def test_disconnected_browser_never_sends_or_falls_back(self):
        row = self.relay.current(self.client)
        self.now += CONNECTION_GRACE + 1
        with self.assertRaises(ValueError): self.relay.current(self.client)
        with self.assertRaises(ValueError): self.relay.send(row['thread_id'], 'Words', 'cmd', 'msg')
        self.assertFalse(self.relay.pending)

    def test_launch_receipt_matches_the_pinned_remote_thread_after_navigation(self):
        row = self.relay.current(self.client)
        ready = threading.Event()
        original_wait = self.relay.lock.wait_for
        def wait(*args, **kwargs):
            ready.set()
            return original_wait(*args, **kwargs)
        self.relay.lock.wait_for = wait
        with ThreadPoolExecutor() as pool:
            future = pool.submit(self.relay.send, row['thread_id'], 'Words', 'command', 'message')
            self.assertTrue(ready.wait(1))
            self.selection = {**self.selection, 'id': 'next', 'threadId': 'newly-open-thread'}
            self.publish(receipts=[dict(id='command', state='sent', threadId='newly-open-thread')])
            self.assertFalse(future.done())
            self.publish(receipts=[dict(id='command', state='sent', threadId='unknown-to-local-db')])
            self.assertIsNone(future.result())
        self.assertEqual(self.publish()['jobs'], [])

    def test_disconnected_destination_blocks_new_capture_but_not_in_progress_capture(self):
        from listen import ListenStore
        row = self.relay.current(self.client)
        session = Mock(describe=self.relay.describe, send=self.relay.send)
        store = ListenStore(session, row['thread_id'], True)
        self.assertTrue(store.get()['destinationReady'])
        begun = store.begin({'captureId':1})['job']
        self.now += CONNECTION_GRACE + 1
        self.assertFalse(store.get()['destinationReady'])
        self.assertEqual(store.get()['job']['state'], 'listening')
        self.assertEqual(store.get()['job']['threadId'], row['thread_id'])
        store.discard()
        with self.assertRaisesRegex(ValueError, 'disconnected'): store.begin({'captureId':2})
        with self.assertRaisesRegex(ValueError, 'disconnected'):
            store.configure(dict(threadId=row['thread_id'], enabled=True))
        self.publish()
        self.assertTrue(store.get()['destinationReady'])
        self.assertEqual(store.begin({'captureId':3})['job']['threadId'], begun['threadId'])

    def test_slow_poll_has_grace_without_making_stale_window_selectable(self):
        row = self.relay.current(self.client)
        self.now += 6
        self.assertTrue(self.relay.describe(row['thread_id'])['ready'])
        with self.assertRaises(ValueError): self.relay.current(self.client)

    def test_missing_ack_removes_pending_prompt_without_retry(self):
        row = self.relay.current(self.client)
        with self.assertRaises(TimeoutError):
            self.relay.send(row['thread_id'], 'Words', 'cmd', 'msg')
        self.assertEqual(self.publish()['jobs'], [])

    def test_browser_send_returns_sending_without_blocking_microphone_pump(self):
        from listen import ListenStore
        row = self.relay.current(self.client)
        session = Mock(describe=self.relay.describe, send=self.relay.send)
        store = ListenStore(session, row['thread_id'], True)
        store.configure(dict(threadId=row['thread_id'], enabled=True, autoSend=True))
        job = store.begin({'captureId':1})['job']
        store.progress(dict(id=job['id'], state='transcribing'))
        result = store.progress(dict(id=job['id'], state='review', text='A prompt'))
        self.assertEqual(result['job']['state'], 'sending')
        with self.assertRaises(ValueError): store.configure(dict(threadId=row['thread_id'], enabled=True))
        self.publish(receipts=[dict(id='shark-voice-'+job['id'],state='sent',sequence=8)])


class RelayHTTPTests(unittest.TestCase):
    setUp = RelayTests.setUp
    publish = RelayTests.publish
    @patch.dict('os.environ', {'SHARK_VOICE_ORIGINS': 'https://t3.example.test,t3code://app',
                              'SHARK_VOICE_HOSTS': 'display.example.test'})
    def test_only_relay_endpoint_accepts_configured_host_and_exact_origin(self):
        from functools import partial
        from http.server import ThreadingHTTPServer
        from pathlib import Path
        from types import SimpleNamespace
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        import json
        from bridge import Handler, SceneStore
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler,
            database=Path('/unused'), scenes=SceneStore(),
            listen=SimpleNamespace(session=SimpleNamespace(relay=self.relay))))
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        origin = 'https://t3.example.test'
        base = f'http://127.0.0.1:{server.server_port}'
        headers = {'Host':'display.example.test','Origin':origin,'Content-Type':'application/json'}
        try:
            def exchange(extra, path='/api/voice-client', method='POST'):
                return urlopen(Request(base+path, data=b'{}' if method=='POST' else None,
                    headers={**headers,**extra}, method=method))
            for extra, code in [({},401), ({'Origin':'https://unrelated.example.test'},403),
                    ({'Origin':'https://t3.example.test.evil.example.test'},403),
                    ({'Origin':'https://t3.example.test/'},403),
                    ({'Origin':'t3code://app.evil.example.test'},403),
                    ({'Origin':'t3code://app/'},403),
                    ({'Origin':'t3code://app:443'},403),
                    ({'Origin':'t3code://app?query'},403),
                    ({'Origin':'t3code://app#fragment'},403),
                    ({'Origin':'t3code://user@app'},403),
                    ({'Origin':'t3code://other'},403),
                    ({'Origin':'null'},403),
                    ({'Origin':''},403),
                    ({'Host':'other.example.test'},403),
                    ({'Authorization':'Bearer '+ 'b'*64},401)]:
                with self.assertRaises(HTTPError) as error: exchange(extra)
                self.assertEqual(error.exception.code,code)
                if code == 403:
                    self.assertIsNone(error.exception.headers.get('Access-Control-Allow-Origin'))
                error.exception.close()
            with exchange({}, method='OPTIONS') as response:
                self.assertEqual(response.headers['Access-Control-Allow-Origin'],origin)
            with exchange({'Authorization':'Bearer '+self.token}) as response:
                self.assertEqual(json.load(response),{'jobs':[]})
            with exchange({'Authorization':'Bearer '+self.token, 'Origin':'t3code://app'}) as response:
                self.assertEqual(json.load(response),{'jobs':[]})
                self.assertEqual(response.headers['Access-Control-Allow-Origin'], 't3code://app')
            with self.assertRaises(HTTPError) as error:
                exchange({'Authorization':'Bearer '+self.token}, path='/api/listen', method='GET')
            self.assertEqual(error.exception.code,403)
            error.exception.close()
        finally:
            server.shutdown(); server.server_close(); worker.join()

    def test_default_and_invalid_configurations_fail_closed(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(configured_origins(), {'http://127.0.0.1:3773', 'http://localhost:3773'})
            self.assertEqual(configured_hosts(8874), {'127.0.0.1:8874', 'localhost:8874', '[::1]:8874'})
        with patch.dict('os.environ', {'SHARK_VOICE_ORIGINS': 't3code://app,https://t3.example.test'}):
            self.assertEqual(configured_origins(), {'t3code://app', 'https://t3.example.test'})
        for origin in ('*', 'null', 'https://*.example.test', 'https://user@t3.example.test',
                       'https://t3.example.test/path', 'https://t3.example.test:bad',
                       'https://t3.example.test/#fragment', 'https://t3.example.test/\\evil',
                       'https://t3.example.test\n.evil.example.test',
                       't3code://other', 't3code://app/', 't3code://app:443',
                       't3code://app?query', 't3code://app#fragment', 't3code://user@app',
                       't3code://app.evil.example.test', 't3code://app,https://*.example.test'):
            with self.subTest(origin=origin), patch.dict('os.environ', {'SHARK_VOICE_ORIGINS': origin}):
                self.assertFalse(configured_origins())
        for host in ('*', '*.example.test', 'https://display.example.test', 'user@display.example.test',
                     'display.example.test/path', 'display.example.test:bad', '[invalid'):
            with self.subTest(host=host), patch.dict('os.environ', {'SHARK_VOICE_HOSTS': host}):
                self.assertFalse(configured_hosts(8874))
        with patch.dict('os.environ', {'SHARK_VOICE_ORIGINS': ''}):
            self.assertFalse(configured_origins())

    @patch.dict('os.environ', {}, clear=True)
    def test_local_origin_still_requires_proof_and_proxy_hosts_are_opt_in(self):
        from functools import partial
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer
        from pathlib import Path
        from types import SimpleNamespace
        from bridge import Handler, SceneStore
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler,
            database=Path('/unused'), scenes=SceneStore(),
            listen=SimpleNamespace(session=SimpleNamespace(relay=self.relay))))
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            local_host = f'127.0.0.1:{server.server_port}'
            cases = [([('Host', local_host), ('Origin', 'http://localhost:3773')], None, 401),
                     ([('Host', local_host), ('Origin', 'http://localhost:3773')], self.token, 200),
                     ([('Host', 'display.example.test'), ('Origin', 'http://localhost:3773')], self.token, 403),
                     ([('Host', local_host), ('Origin', 'https://t3.example.test')], self.token, 403),
                     ([('Host', local_host), ('Origin', 't3code://app')], self.token, 403),
                     ([('Host', local_host)], self.token, 403),
                     ([('Host', local_host), ('Host', local_host), ('Origin', 'http://localhost:3773')], self.token, 403),
                     ([('Host', local_host), ('Origin', 'http://localhost:3773'),
                       ('Origin', 'https://evil.example.test')], self.token, 403)]
            for headers, token, expected in cases:
                with self.subTest(headers=headers, token=bool(token)):
                    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                    try:
                        connection.putrequest('POST', '/api/voice-client', skip_host=True)
                        for key, value in headers: connection.putheader(key, value)
                        if token: connection.putheader('Authorization', 'Bearer ' + token)
                        connection.putheader('Content-Type', 'application/json')
                        connection.putheader('Content-Length', '2')
                        connection.endheaders(b'{}')
                        response = connection.getresponse()
                        self.assertEqual(response.status, expected)
                        if expected == 403: self.assertIsNone(response.getheader('Access-Control-Allow-Origin'))
                        response.read()
                    finally:
                        connection.close()
        finally:
            server.shutdown(); server.server_close(); worker.join()
