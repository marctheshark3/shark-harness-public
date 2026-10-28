"""Version-checked BOOT/voice integration for the T3 Code desktop client.

Use --check before installing. --output prepares a reviewable package without
changing T3. Quit T3 before replacing its package; --remove restores the backup.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile

if __package__:
    from .install_t3_focus import configured_helper, digest
else:
    from install_t3_focus import configured_helper, digest

CLIENT = 'apps/server/dist/client/assets/'
RUNTIME_PATH = CLIENT + 'runtime-BVExcDcv.js'
RUNTIME_SHA256 = '73882d630b1d2c93f8f7dd7476eb77181253e6fe1a12030fb2b1c7cabeb24d03'
CHAT_PATH = CLIENT + '_chat-BdvROAFK.js'
CHAT_SHA256 = '5b50c2f30df029ce6d9fbb1ac9f2dff6fc0faa343806df914c5a062c38fc22e1'
RUNTIME_HOOK = b'function Ef(e,t,n){let r=[...vf];'
IDENTITY_HOOK = b'clientId:Cf()'
RUNTIME_EDITS = [
    (RUNTIME_HOOK, b'function Ef(e,t,n){let r=[...vf,...sharkCurrentThreadScopes(e)];'),
    (IDENTITY_HOOK, b'clientId:sharkVoiceIdentity(Cf())'),
]
# Recognize the previous verified helper without publishing its private URL.
LEGACY_DESKTOP_HELPER_SHA256 = {'1e42d9df6a3ac4a85066181adb58c6187a213283e3dae77d0d571705e740dddd'}
CHAT_HOOK = b'let Yp=Jp,Xp=lr?Lp('
CHAT_REPLACEMENT = b'''sharkRegisterConversation({
route:i===`draft`?`/draft/${f}`:`/${n}/${r}`,
environmentId:n,environment:R.get(n)?.label??`T3`,
thread:lr,project:Ja,runtimeMode:oi,newThread:!sr,envMode:kp,baseBranch:Ap,
startFromOrigin:jp,blocked:ge||Hl||!_l(n,`orchestration:operate`),protocolVersion:2,
read:()=>se.current?.getSendContext(),dispatch:O,
onCreated:()=>{if(f&&(window.location.pathname===`/draft/${f}`||
window.location.hash.split(`?`)[0]===`#/draft/${f}`))
ke({to:`/$environmentId/$threadId`,params:{environmentId:n,threadId:lr.id}})}
});let Yp=Jp,Xp=lr?Lp('''


def read_header(data):
    if len(data) < 16:
        raise ValueError('Invalid desktop package header.')
    magic, size, _, length = struct.unpack('<IIII', data[:16])
    if magic != 4 or length > size - 8 or 8 + size > len(data):
        raise ValueError('Invalid desktop package header.')
    return json.loads(data[16:16 + length]), 8 + size


def lookup(header, path):
    entry = header
    for part in path.split('/'):
        entry = entry['files'][part]
    return entry


def packed_entries(header):
    for entry in header.get('files', {}).values():
        if 'files' in entry:
            yield from packed_entries(entry)
        elif 'offset' in entry and not entry.get('unpacked'):
            yield entry


def archive_member(data, path):
    header, start = read_header(data)
    entry = lookup(header, path)
    offset = start + int(entry['offset'])
    return data[offset:offset + entry['size']]


def known_legacy_archive(original, current, context):
    """Accept an old helper only if recreating it matches the whole package."""
    try:
        prefix = archive_member(original, RUNTIME_PATH)
        for before, after in RUNTIME_EDITS:
            prefix = prefix.replace(before, after, 1)
        prefix += b'\n'
        runtime = archive_member(current, RUNTIME_PATH)
        if not runtime.startswith(prefix) or not runtime.endswith(b'\n'):
            return False
        helper = runtime[len(prefix):-1]
        return (digest(helper) in LEGACY_DESKTOP_HELPER_SHA256 and
                current == patched_archive(original, helper, context))
    except (KeyError, ValueError, TypeError):
        return False


def desktop_helper(helper):
    # The endpoint is a JSON string on its own line. Rename the verified
    # runtime notifier without rewriting that user-configured string.
    return b''.join(line if line.lstrip().startswith(b'const endpoint = ') else
                    line.replace(b'Pc', b'xf') for line in helper.splitlines(keepends=True))


def patched_archive(original, helper, context):
    header, start = read_header(original)
    replacements = {}
    for path, expected, edits, extra in [
        (RUNTIME_PATH, RUNTIME_SHA256, RUNTIME_EDITS, desktop_helper(helper)),
        (CHAT_PATH, CHAT_SHA256, [(CHAT_HOOK, CHAT_REPLACEMENT)], context),
    ]:
        try:
            entry = lookup(header, path)
        except KeyError as error:
            raise ValueError('Unsupported T3 desktop build. No files changed.') from error
        offset = start + int(entry['offset'])
        content = original[offset:offset + entry['size']]
        if hashlib.sha256(content).hexdigest() != expected or any(content.count(before) != 1 for before, _ in edits):
            raise ValueError('Unsupported or modified T3 desktop build. No files changed.')
        for before, after in edits:
            content = content.replace(before, after, 1)
        replacements[id(entry)] = content + b'\n' + extra + b'\n'
    chunks, offset = [], 0
    for entry in sorted(packed_entries(header), key=lambda value: int(value['offset'])):
        old_offset = start + int(entry['offset'])
        content = replacements.get(id(entry), original[old_offset:old_offset + entry['size']])
        if id(entry) in replacements and 'integrity' in entry:
            integrity = entry['integrity']
            size = integrity['blockSize']
            integrity['hash'] = hashlib.sha256(content).hexdigest()
            integrity['blocks'] = [hashlib.sha256(content[i:i + size]).hexdigest()
                                   for i in range(0, len(content), size)]
        entry.update(size=len(content), offset=str(offset))
        chunks.append(content)
        offset += len(content)
    encoded = json.dumps(header, separators=(',', ':'), ensure_ascii=False).encode()
    padding = b'\0' * (-len(encoded) % 4)
    pickle = struct.pack('<II', 4 + len(encoded) + len(padding), len(encoded)) + encoded + padding
    return struct.pack('<II', 4, len(pickle)) + pickle + b''.join(chunks)


def atomic_write(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.shark-desktop-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(archive, helper, context, *, check=False, remove=False, output=None):
    archive = Path(archive).resolve()
    backup = archive.with_name(archive.name + '.shark-voice-original')
    receipt_path = archive.with_name(archive.name + '.shark-voice-installed.json')
    if output is not None and Path(output).resolve() in {archive, backup, receipt_path}:
        raise ValueError('--output must differ from the installed package, backup, and receipt.')
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {'version': 1, 'sha256': None}
    if (not isinstance(receipt, dict) or receipt.get('version') != 1 or
            (receipt.get('sha256') is not None and (not isinstance(receipt['sha256'], str) or
             not re.fullmatch('[a-f0-9]{64}', receipt['sha256'])))):
        raise ValueError('Invalid installation receipt. Refusing to overwrite the package.')
    current = archive.read_bytes()
    original = backup.read_bytes() if backup.exists() else current
    replacement = patched_archive(original, helper, context)
    if (current not in (original, replacement) and digest(current) != receipt.get('sha256')
            and not known_legacy_archive(original, current, context)):
        raise ValueError('T3 desktop package changed since installation. No files changed.')
    if output is not None:
        atomic_write(Path(output), replacement)
        return 'prepared'
    if check:
        return 'installed' if current == replacement else 'compatible'
    installed = current == replacement
    if installed and not remove and receipt.get('sha256') == digest(replacement):
        return 'already installed'
    if not backup.exists():
        atomic_write(backup, original)
    next_package = original if remove else replacement
    atomic_write(archive, next_package)
    try:
        atomic_write(receipt_path, (json.dumps({'version': 1, 'sha256': digest(next_package)},
                                              sort_keys=True) + '\n').encode())
    except OSError:
        atomic_write(archive, current)
        raise
    return 'removed' if remove else 'already installed' if installed else 'installed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--relay-url',
                        help='Explicit HTTPS or loopback HTTP relay URL; required except for --remove.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--remove', action='store_true')
    mode.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    try:
        helper = (root / 't3-current-session.js').read_bytes()
        if args.relay_url is not None or not args.remove:
            helper = configured_helper(helper, args.relay_url, allow_relative=False)
        result = install(args.archive, helper,
                         (root / 't3-voice-context.js').read_bytes(),
                         check=args.check, remove=args.remove, output=args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Desktop voice helper: {error}\n')
    print(f'Desktop voice helper: {result}.')
    if args.output:
        print(f'Prepared package: {args.output}')


if __name__ == '__main__':
    main()
