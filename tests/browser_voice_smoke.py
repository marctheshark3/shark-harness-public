"""Browser relay + device UI + capture pump regression, with fake hardware/dispatch.

Exercises real HTTP and browser helper code without USB, audio, or real T3 prompts.
"""
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import os
import tempfile
import threading
import time
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bridge import Handler, SceneStore
from device_ui import DeviceUI
from listen import ListenPump, ListenStore
from speech import bridge_request
from t3_session import T3Session
from voice_relay import CONNECTION_GRACE
from playwright.sync_api import sync_playwright


class Device:
    """Simulate the firmware's capture gate; no physical hold timing is claimed."""
    def __init__(self):
        self.mic = dict(id=0, ready=True, enabled=False, recording=False,
                        available=False, error=0, bytes=0, level=0, sampleRate=16000)
        self.ui = dict(event=0, gesture=0, x=233, y=233, holdToTalk=False)

    def request(self, op, **fields):
        if op == 'mic.configure': self.mic['enabled'] = fields['enabled']
        if op == 'mic.clear': self.mic.update(available=False, recording=False)
        return dict(mic=dict(self.mic), ui=dict(self.ui), audio=dict(playing=False))

    def send_frame(self, pixels, **fields):
        self.ui.update(frame=f"{fields['ui_token']:08x}", holdToTalk=fields['hold_to_talk'])
        return self.request('status')

    def input(self, gesture, x=330, y=365):
        self.ui.update(event=self.ui['event']+1, gesture=gesture, x=x, y=y, eventFrame=self.ui['frame'])

    def hold(self):
        if not self.ui['holdToTalk'] or not self.mic['enabled']: return False
        self.mic.update(recording=True, id=self.mic['id']+1)
        return True

    def release(self):
        assert self.mic['recording']
        self.mic.update(recording=False, available=True, bytes=16000)

    def capture_audio(self, mic): return bytes(16000)


def run():
    session = T3Session(Path('/absent-test-database'))
    snapshot = dict(updatedAt='2026-10-06T12:00:10Z', leases=[])
    session.rpc = Mock(side_effect=lambda *args, **kwargs: snapshot)
    offset = 0
    session.relay.clock = lambda: time.monotonic() + offset
    store = ListenStore(session)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler,
        database=Path('/absent-test-database'), scenes=SceneStore(), listen=store))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    pump = None
    transcribing = threading.Event()
    finish_transcription = threading.Event()
    try:
        with tempfile.TemporaryDirectory() as temporary, sync_playwright() as p, \
                patch.dict('os.environ', {'SHARK_VOICE_ORIGINS': f'http://127.0.0.1:{server.server_port}',
                                         'SHARK_VOICE_HOSTS': ''}):
            options = {'headless': True}
            if os.environ.get('BROWSER_EXECUTABLE'):
                options['executable_path'] = os.environ['BROWSER_EXECUTABLE']
            browser = p.chromium.launch(**options)
            errors = []
            acknowledgements = []
            port = server.server_port
            def new_window(draft):
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                def proxy(route):
                    acknowledgements.extend(route.request.post_data_json.get('receipts', []))
                    headers = dict(route.request.headers)
                    headers['Origin'] = f'http://127.0.0.1:{port}'
                    response = route.fetch(url=f'http://127.0.0.1:{port}/api/voice-client', headers=headers)
                    route.fulfill(response=response, headers={**response.headers,
                        'access-control-allow-origin': f'http://127.0.0.1:{port}'})
                page.route(f'http://127.0.0.1:{port}/shark-voice/api/voice-client', proxy)
                page.route('**/test-page', lambda route: route.fulfill(content_type='text/html',
                    body='<html><title>Voice relay test</title><body>Voice relay test</body></html>'))
                page.goto(f'http://127.0.0.1:{port}/test-page')
                # Separate computers can both have a focused T3 browser window.
                page.evaluate('window.Pc=()=>{};document.hasFocus=()=>true')
                page.add_script_tag(path=str(ROOT / 'integrations/t3-current-session.js'))
                page.add_script_tag(path=str(ROOT / 'integrations/t3-voice-context.js'))
                page.wait_for_function('sharkVoiceIdentity("").startsWith("shark-voice:")')
                client = page.evaluate('sharkVoiceIdentity("")')
                page.evaluate('''draft => {
                  const route = draft ? '/draft/browser:draft%3Achild' : '/other/mcp:existing%3Achild';
                  const encoded = route.split('/').map(encodeURIComponent).join('/');
                  history.replaceState({}, '', draft ? '/#'+encoded : encoded);
                  window.sentCommands=[];
                  sharkRegisterConversation({route,environmentId:'remote-environment',
                    environment:'Other computer',newThread:draft,
                    thread:{id:draft?'remote-draft':'mcp:existing%3Achild',title:'Existing session',worktreePath:null},
                    project:{id:'remote-project',title:'Remote project',workspaceRoot:'/remote'},
                    runtimeMode:'approval-required',envMode:'local',baseBranch:null,
                    protocolVersion:draft?2:1,
                    read:()=>({providerAvailable:true,selectedModelSelection:{instanceId:'p',model:'chosen'},interactionMode:'plan'}),
                    dispatch:async request=>{sentCommands.push(request);await new Promise(resolve=>setTimeout(resolve,1800));
                      return {_tag:'Success',value:draft?{threadId:'remote-draft'}:{sequence:21}};},onCreated:()=>{window.promoted=true;}});
                }''', draft)
                return page, dict(clientId=client, visible=True, focused=True, appState='active',
                    updatedAt='2026-10-06T12:00:09Z', expiresAt='2026-10-06T12:00:55Z')

            other, other_lease = new_window(False)
            page, draft_lease = new_window(True)
            device = Device()
            def api(path, value=None, method=None):
                if path == '/api/status': return dict(connected=True, threads=[], projects=[], overlay=None)
                return bridge_request(port, path, value, method)
            ui = DeviceUI(device, port, lambda:None, selection=Path(temporary)/'selection.json', api=api)
            pump = ListenPump(device, port)
            dictated = ('Voice test. ' * 1600).strip()  # Exceeds the old transcript and HTTP request limits.
            def transcribe(pcm):
                transcribing.set()
                if not finish_transcription.wait(15):
                    raise AssertionError('Transcription was not released by the test')
                return dictated
            pump.voice.transcribe = transcribe
            def tick():
                pump.next_tick = 0; pump.tick()
                ui.last_refresh = 0; ui.tick(device.request('status'))
            def press(gesture, x=330, y=365):
                device.input(gesture, x, y); tick()
            def until(condition, timeout=10):
                deadline = time.monotonic()+timeout
                while time.monotonic()<deadline:
                    tick()
                    if condition(): return
                    other.wait_for_timeout(100)
                raise AssertionError('Timed out waiting for device/relay state')

            tick()
            press(5)
            assert ui.presented['kind']=='current-error'
            assert not device.hold(), 'An error screen allowed a recording'
            assert store.get()['target'] is None
            snapshot['leases'] = [other_lease, draft_lease, dict(other_lease,
                clientId='old-window', updatedAt='2026-10-06T12:00:00Z')]
            until(lambda: len(session.relay.clients)==2)
            press(1)  # Retry on screen, without requiring another physical BOOT press.
            assert ui.presented['kind']=='t3-choices'
            assert ui.presented['total']==2, 'An old focused lease blocked selection'
            assert not device.hold()
            press(2)
            assert ui.presented['title']=='New session'
            press(1)
            tick()  # ListenPump consumes the selection's cancel and enables capture.
            assert ui.presented['kind']=='home' and device.ui['holdToTalk']
            assert store.get()['target']['new']
            assert device.hold()
            tick()
            job = store.get()['job']
            assert job['state']=='listening'
            pinned = job['threadId']
            # A BOOT press cannot retarget an active recording.
            press(5)
            assert store.get()['job']['threadId']==pinned
            device.release()
            until(transcribing.is_set)
            assert store.get()['job']['state']=='transcribing'
            # Navigate to a real registered conversation while STT is pending,
            # with a different environment, thread and composer model.
            page.evaluate('''() => {
              const route='/changed-environment/changed-thread';
              history.replaceState({}, '', route);
              sharkRegisterConversation({route,environmentId:'changed-environment',environment:'Changed computer',
                newThread:false,thread:{id:'changed-thread',title:'Changed session',worktreePath:null},
                project:{id:'changed-project',title:'Changed project',workspaceRoot:'/changed'},
                runtimeMode:'full-access',envMode:'local',baseBranch:null,
                read:()=>({providerAvailable:true,selectedModelSelection:{instanceId:'new',model:'new-model'},interactionMode:'default'}),
                dispatch:async request=>{sentCommands.push(request);return {_tag:'Success',value:{sequence:99}};},
                onCreated:()=>{throw Error('Existing thread cannot be promoted');}});
            }''')
            until(lambda: session.relay.clients[draft_lease['clientId']]['selection']['threadId']=='changed-thread')
            assert page.evaluate('sentCommands')==[]
            assert store.get()['job']['threadId']==pinned
            finish_transcription.set()
            until(lambda: store.get()['job']['state']=='sent')
            commands = page.evaluate('sentCommands')
            assert len(commands)==1, 'Repeated polling dispatched twice'
            assert other.evaluate('sentCommands')==[], 'Voice went to another window'
            request = commands[0]
            assert request['input']['message']['text']==dictated
            assert request['environmentId']=='remote-environment'
            assert request['input']['threadId']=='remote-draft'
            assert request['input']['bootstrap']['createThread']['projectId']=='remote-project'
            assert request['input']['runtimeMode']=='approval-required'
            assert request['input']['interactionMode']=='plan'
            assert request['input']['modelSelection']==dict(instanceId='p',model='chosen')
            assert any(receipt.get('threadId')=='remote-draft' and receipt.get('state')=='sent'
                       for receipt in acknowledgements), 'The pinned native draft was not acknowledged'
            page.wait_for_timeout(1200)  # Another poll must not repeat an acknowledged send.
            assert len(page.evaluate('sentCommands'))==1
            assert page.evaluate('promoted')
            assert not store.get()['target']['new']
            # Navigation also leaves the next recording on the device's selected
            # destination, until BOOT explicitly selects another conversation.
            until(lambda: ui.presented['kind']=='home')
            assert store.get()['target']['id']==pinned
            assert device.hold()
            tick()
            assert store.get()['job']['threadId']==pinned
            store.discard(); tick()
            assert len(page.evaluate('sentCommands'))==1
            # Reload/disconnect removes readiness and exposes a direct recovery action.
            page.close()
            snapshot['leases'] = [other_lease]
            offset = CONNECTION_GRACE + 1
            until(lambda: ui.presented['kind']=='voice-offline')
            assert not device.hold(), 'Disconnected browser allowed a recording'
            until(lambda: bool(session.relay.clients[other_lease['clientId']]['seen'] > time.monotonic()))
            press(5)
            tick()
            assert ui.presented['kind']=='home' and ui.presented['title']=='Existing session'
            assert device.ui['holdToTalk'] and device.mic['enabled']
            assert not errors, errors
            browser.close()
            print('Browser/device voice smoke passed: multiple windows, stale lease, retry, capture gate, '
                  'remote draft, switch after release during transcription, pinned next recording, '
                  'single acknowledged dispatch, disconnect and recovery.')
    finally:
        finish_transcription.set()
        if pump is not None: pump.close()
        server.shutdown(); server.server_close(); worker.join()


if __name__ == '__main__': run()
