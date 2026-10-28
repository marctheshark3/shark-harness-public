"""Transient browser voice routing, authenticated by T3 activity proof.

Only a SHA-256 hash of the browser's random secret appears in T3 activity.
The raw secret travels over HTTPS to this relay; T3 credentials never do.
"""
import hashlib
import os
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

from t3_session import DispatchRejected

CLIENT_PREFIX = 'shark-voice:'
RELAY_PATH = '/api/voice-client'
DEFAULT_ORIGINS = frozenset({'http://127.0.0.1:3773', 'http://localhost:3773'})
CONNECTION_GRACE = 15  # Survive one slow/retried poll without flapping the mic.


def configured_origins():
    """Exact browser origins; an invalid configuration grants no access."""
    values = os.environ.get('SHARK_VOICE_ORIGINS', ','.join(sorted(DEFAULT_ORIGINS)))
    origins = {value.strip() for value in values.split(',') if value.strip()}
    try:
        for origin in origins:
            if origin == 't3code://app':
                continue
            parsed = urlsplit(origin)
            if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or
                    parsed.username is not None or parsed.password is not None or
                    parsed.path or parsed.query or parsed.fragment or
                    origin != f'{parsed.scheme}://{parsed.netloc}' or
                    any(c.isspace() for c in origin) or '*' in origin or '\\' in origin):
                return frozenset()
            parsed.port  # Reject malformed and out-of-range ports.
    except ValueError:
        return frozenset()
    return frozenset(origins)


def configured_hosts(port):
    """Explicit proxy Host values plus the bridge's own loopback address."""
    hosts = {value.strip() for value in os.environ.get('SHARK_VOICE_HOSTS', '').split(',') if value.strip()}
    try:
        for host in hosts:
            parsed = urlsplit('//' + host)
            if (not parsed.hostname or parsed.username is not None or parsed.password is not None or
                    parsed.path or parsed.query or parsed.fragment or parsed.netloc != host or
                    any(c.isspace() for c in host) or '*' in host or '\\' in host):
                return frozenset()
            parsed.port
    except ValueError:
        return frozenset()
    return frozenset(hosts | {f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}'})


class VoiceRelay:
    def __init__(self, activity, *, clock=time.monotonic, timeout=90):
        self.activity, self.clock, self.timeout = activity, clock, timeout
        self.lock = threading.Condition(threading.RLock())
        self.clients, self.targets, self.pending = {}, {}, {}

    def authorize(self, token):
        if not isinstance(token, str) or not re.fullmatch('[a-f0-9]{64}', token):
            raise ValueError('Browser voice connection is not authenticated.')
        client = CLIENT_PREFIX + hashlib.sha256(token.encode()).hexdigest()
        snapshot = self.activity()
        # Activity is removed on disconnect; its timestamps are server generated.
        from datetime import datetime
        now = datetime.fromisoformat(snapshot['updatedAt'].replace('Z', '+00:00'))
        for lease in snapshot.get('leases', []):
            if lease.get('clientId') != client:
                continue
            expires = datetime.fromisoformat(lease['expiresAt'].replace('Z', '+00:00'))
            if expires > now:
                return client
        raise ValueError('Refresh T3 and connect the environment running this companion.')

    def exchange(self, client, value):
        if not isinstance(value, dict):
            raise ValueError('Expected browser voice status.')
        selection = value.get('selection')
        if selection is not None:
            if not isinstance(selection, dict):
                raise ValueError('Invalid voice destination.')
            for field in ('id', 'threadId', 'environmentId', 'environment', 'projectId', 'project', 'title'):
                if not isinstance(selection.get(field), str) or not 1 <= len(selection[field]) <= 200:
                    raise ValueError('Incomplete voice destination.')
            if type(selection.get('new')) is not bool:
                raise ValueError('Invalid voice destination state.')
            selection = {k: selection[k] for k in ('id', 'threadId', 'environmentId', 'environment', 'projectId', 'project', 'title', 'new')}
        receipts = value.get('receipts', [])
        if not isinstance(receipts, list) or len(receipts) > 20:
            raise ValueError('Invalid voice acknowledgements.')
        with self.lock:
            now = self.clock()
            self.clients[client] = dict(selection=selection, seen=now,
                focused=value.get('focused') is True, visible=value.get('visible') is True,
                error=str(value.get('error', 'Open a conversation in T3.'))[:180])
            for receipt in receipts:
                if not isinstance(receipt, dict): continue
                job = self.pending.get(receipt.get('id'))
                if not job or job['client'] != client or job['result'] is not None: continue
                if receipt.get('state') not in {'sent', 'error', 'uncertain'}: continue
                if (receipt['state'] == 'sent' and type(receipt.get('sequence')) is not int
                        and receipt.get('threadId') != job['nativeThreadId']): continue
                job['result'] = {k: receipt.get(k) for k in ('state', 'sequence')}
            jobs = [j['wire'] for j in self.pending.values() if j['client'] == client and j['result'] is None]
            # Keep only recent unbound reports. Bound destinations remain pinned.
            self.clients = {k: v for k, v in self.clients.items() if now - v['seen'] < 120}
            self.lock.notify_all()
            return {'jobs': jobs}

    def current(self, client):
        with self.lock:
            report = self.clients.get(client)
            if not report or self.clock() - report['seen'] > 5:
                raise ValueError('Waiting for the browser voice connection. Refresh T3 and press BOOT again.')
            if not report['focused'] or not report['visible']:
                raise ValueError('Focus your T3 conversation, then press BOOT again.')
            selection = report['selection']
            if not selection:
                raise ValueError(report['error'])
            key = (client, selection['id'])
            for target in self.targets.values():
                if target['_key'] == key:
                    return self.describe(target['thread_id'])
            row = dict(thread_id='voice-' + uuid.uuid4().hex, title=selection['title'],
                project_id=selection['projectId'], projectTitle=selection['project'],
                environment=selection['environment'], environmentId=selection['environmentId'],
                browser=True, new=selection['new'], nativeThreadId=selection['threadId'], _key=key)
            self.targets[row['thread_id']] = row
            # Only disconnected clients can be pruned; never evict a live
            # destination just because another window publishes many selections.
            self.targets = {k: v for k, v in self.targets.items()
                            if v['_key'][0] in self.clients}
            return self.describe(row['thread_id'])

    def describe(self, thread_id):
        with self.lock:
            target = self.targets.get(thread_id)
            if target is None:
                raise ValueError('Choose the open T3 conversation with BOOT again.')
            report = self.clients.get(target['_key'][0])
            ready = bool(report and self.clock() - report['seen'] <= CONNECTION_GRACE)
            return dict(target, ready=ready, error='' if ready else
                        'T3 browser disconnected. Open the conversation and press BOOT to reconnect.')

    def send(self, thread_id, text, command_id, message_id):
        with self.lock:
            target = self.describe(thread_id)
            client, selection_id = target['_key']
            if not target['ready']:
                raise ValueError(target['error'])
            if command_id in self.pending:
                raise ValueError('This voice prompt has already been submitted.')
            job = dict(client=client, result=None, nativeThreadId=target['nativeThreadId'], wire=dict(id=command_id, messageId=message_id,
                selectionId=selection_id, text=text))
            self.pending[command_id] = job
            try:
                if not self.lock.wait_for(lambda: job['result'] is not None, timeout=self.timeout):
                    raise TimeoutError('No acknowledgement from the T3 browser.')
                result = job['result']
                if result['state'] == 'error':
                    raise DispatchRejected('T3 rejected the voice prompt.')
                if result['state'] != 'sent':
                    raise TimeoutError('The T3 acknowledgement is uncertain.')
                if target['new'] and thread_id in self.targets:
                    self.targets[thread_id].update(new=False, title=text[:60])
                return result['sequence']
            finally:
                self.pending.pop(command_id, None)
