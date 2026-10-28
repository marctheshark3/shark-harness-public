"""Install/remove the version-checked browser voice integration for T3 0.0.45."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

ASSET = "runtime-CGAQ9uAu.js"
ORIGINAL_SHA256 = "4cc3efae31bbc0e1d67d1ee1acd9ded1d59bc56c933d7ceea4491b17923e536d"
V1_SHA256 = "cead2e3274ac76c959aa652ee92f44be80489a3ad5423759d88467124921a7e6"
CHAT_ASSET = "_chat-DWO1wYmt.js"
CHAT_SHA256 = "a2cd0be0a039c1b7c9072551a9d6ee592a5590a1bb8098cfe29f27e7fde72b38"
CHAT_V1_SHA256 = "2fb03daf3f3cc0a4c5f863d0c70d02ef9431e8a4091d2a47e64ffa16b4befd9b"
# The previous checked-in helper can be recognized without retaining its
# machine-specific endpoint in public source.
LEGACY_HELPER_SHA256 = {
    "b62432f3e68af77f535bd2aa02a90baa5d80518d4f6e3df3c06a5e6e51d8f3f1",
    "0c0cb7729f5f39753e09ca47b03082093ffb0a5a477e27d3a28daed16c3865bf",
}
DEFAULT_RELAY_URL = "/shark-voice/api/voice-client"
ENDPOINT_LINE = b'const endpoint = "/shark-voice/api/voice-client";'
HOOK = b"function zc(e,t,n){let r=[...jc];"
REPLACEMENT = b"function zc(e,t,n){let r=[...jc,...sharkCurrentThreadScopes(e)];"
IDENTITY_HOOK = b"clientId:Ic(),clientKind:Lc()"
IDENTITY_REPLACEMENT = b"clientId:sharkVoiceIdentity(Ic()),clientKind:Lc()"
CHAT_HOOK = b"__.current=Qg;let v_="
CHAT_REPLACEMENT = b'''__.current=Qg;sharkRegisterConversation({
route:r===`draft`?`/draft/${l}`:`/${t}/${n}`,
environmentId:t,environment:Ao?.target?.label??Ao?.environment?.label??`T3`,
thread:Y,project:ha,runtimeMode:Yr,newThread:Xr,envMode:dh,baseBranch:nh,
makeWorktreeBranch:()=>Xo(pn),startFromOrigin:ch,blocked:qa||Uu||g||!Fa,
read:()=>pt.current?.getSendContext(),dispatch:V,
onCreated:()=>{if(l){G.getState().markDraftThreadPromoting(l,{environmentId:t,threadId:Y.id});
G.getState().finalizePromotedDraftThread(l);
if(window.location.pathname===`/draft/${l}`)Oe({to:`/$environmentId/$threadId`,params:{environmentId:t,threadId:Y.id}})}}
});let v_='''


def digest(content):
    return hashlib.sha256(content).hexdigest()


def transform(original, expected, edits, helper):
    if digest(original) != expected or any(original.count(a) != 1 for a, _ in edits):
        raise ValueError("Unsupported or modified T3 client. No files changed; review this build first.")
    for before, after in edits:
        original = original.replace(before, after, 1)
    return original + b"\n" + helper + b"\n"


def patched(original, helper):
    return transform(original, ORIGINAL_SHA256,
                     [(HOOK, REPLACEMENT), (IDENTITY_HOOK, IDENTITY_REPLACEMENT)], helper)


def configured_helper(helper, relay_url, *, allow_relative=True):
    """Inject a deliberate endpoint without allowing executable JavaScript."""
    description = ('a root-relative path or an HTTPS URL' if allow_relative else
                   'an explicit HTTPS URL') + ' (HTTP is allowed only on loopback).'
    if (not isinstance(relay_url, str) or not relay_url or
            any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in relay_url) or '\\' in relay_url):
        raise ValueError('Relay URL must be ' + description)
    try:
        parsed = urlsplit(relay_url)
        parsed.port
    except ValueError as error:
        raise ValueError('Relay URL is malformed.') from error
    if parsed.username is not None or parsed.password is not None or '?' in relay_url or '#' in relay_url:
        raise ValueError('Relay URL must not contain credentials, a query, or a fragment.')
    local_path = allow_relative and relay_url.startswith('/') and not relay_url.startswith('//') and not parsed.netloc
    loopback = parsed.hostname == 'localhost'
    try:
        loopback = loopback or ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        pass
    if not local_path and not (parsed.hostname and (parsed.scheme == 'https' or (parsed.scheme == 'http' and loopback))):
        raise ValueError('Relay URL must be ' + description)
    if helper.count(ENDPOINT_LINE) != 1:
        raise ValueError('Unrecognized helper endpoint marker; no files changed.')
    replacement = ('const endpoint = ' + json.dumps(relay_url) + ';').encode()
    return helper.replace(ENDPOINT_LINE, replacement, 1)


def atomic_write(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".shark-focus-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(client, helper, *, context=None, check=False, remove=False):
    client = Path(client).resolve()
    context = context if context is not None else Path(__file__).with_name('t3-voice-context.js').read_bytes()
    backup_root = client.parent / 'shark-current-session-backup'
    receipt_path = backup_root / 'installed.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {'version': 1, 'assets': {}}
    if (not isinstance(receipt, dict) or receipt.get('version') != 1 or
            not isinstance(receipt.get('assets'), dict) or
            any(name not in {ASSET, CHAT_ASSET} or not isinstance(value, str) or
                not re.fullmatch('[a-f0-9]{64}', value) for name, value in receipt['assets'].items())):
        raise ValueError('Invalid installation receipt. Refusing to overwrite the client.')
    plans = []
    for name, expected, edits, extra, previous in [
            (ASSET, ORIGINAL_SHA256, [(HOOK, REPLACEMENT), (IDENTITY_HOOK, IDENTITY_REPLACEMENT)], helper, {V1_SHA256}),
            (CHAT_ASSET, CHAT_SHA256, [(CHAT_HOOK, CHAT_REPLACEMENT)], context, {CHAT_V1_SHA256})]:
        asset = client / 'assets' / name
        backup = backup_root / name
        current = asset.read_bytes()
        original = current if digest(current) == expected else backup.read_bytes() if backup.exists() else current
        replacement = transform(original, expected, edits, extra)
        # An earlier configured endpoint is accepted only with a matching
        # installation receipt or a verified historical helper hash.
        known_legacy = False
        if name == ASSET:
            prefix = transform(original, expected, edits, b'')[:-1]
            known_legacy = (current.startswith(prefix) and current.endswith(b'\n') and
                            digest(current[len(prefix):-1]) in LEGACY_HELPER_SHA256)
        known_hashes = previous | {receipt['assets'].get(name)}
        if current not in (original, replacement) and digest(current) not in known_hashes and not known_legacy:
            raise ValueError('T3 client changed since installation. Refusing to overwrite it.')
        if backup.exists() and backup.read_bytes() != original:
            raise ValueError('Backup differs from this build. Refusing to overwrite it.')
        plans.append((asset, backup, current, original, replacement))
    installed = all(current == replacement for _, _, current, _, replacement in plans)
    if check: return 'installed' if installed else 'compatible'
    if installed and not remove and all(receipt['assets'].get(asset.name) == digest(replacement)
                                       for asset, _, _, _, replacement in plans):
        return 'already installed'
    written = []
    try:
        for asset, backup, current, original, replacement in plans:
            backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not backup.exists(): atomic_write(backup, original)
            atomic_write(asset, original if remove else replacement)
            written.append((asset, current))
        # Commit the receipt after both assets; a failed write rolls them back.
        next_receipt = {'version': 1, 'assets': {asset.name: digest(original if remove else replacement)
                        for asset, _, _, original, replacement in plans}}
        atomic_write(receipt_path, (json.dumps(next_receipt, sort_keys=True) + '\n').encode())
    except OSError:
        for asset, current in reversed(written): atomic_write(asset, current)
        raise
    return 'removed' if remove else 'already installed' if installed else 'installed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', required=True, type=Path)
    parser.add_argument('--relay-url', default=DEFAULT_RELAY_URL,
                        help='Browser relay endpoint; defaults to the same-origin /shark-voice/api/voice-client route.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--remove', action='store_true')
    args = parser.parse_args()
    try:
        helper = configured_helper(Path(__file__).with_name('t3-current-session.js').read_bytes(), args.relay_url)
        result = install(args.client, helper,
                         check=args.check, remove=args.remove)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Current-session helper: {error}\n')
    print(f'Current-session helper: {result}.')
    if not args.check: print('Refresh T3 in the browser to load the change.')


if __name__ == '__main__':
    main()
