# Security

## Reporting a vulnerability

Use [GitHub's private vulnerability reporting form](https://github.com/marctheshark3/shark-harness-public/security/advisories/new).
Private vulnerability reporting is enabled for this repository. Sign in to
GitHub and use **Security → Report a vulnerability** to send a private report.
Do not post an exploit, credential, private database, recording, or flash backup
in a public issue.

Include the affected commit, impact, and minimal reproduction using synthetic
data. There is no published supported-version or response-time commitment.

## Local access and credentials

The bridge binds to `127.0.0.1` and checks Host and Origin headers. Its API assumes
trusted local processes; loopback does not authenticate other users or programs
on the computer. With a configured companion, the API can expose session
metadata, control USB/audio, and submit prompts through T3. Keep it off public
networks and preserve its origin checks.

T3 metadata is read from SQLite in read-only mode. Prompt submission uses a
separate credential, normally stored at
`~/.local/share/shark-harness/t3-token`. Keep it outside the checkout and browser,
restrict file permissions, set a bounded lifetime, and revoke it in T3 if
exposed. Demo mode does not read T3 credentials or authenticate to T3.

The browser-voice relay uses exact origin and host allowlists, configured with
`SHARK_VOICE_ORIGINS` and `SHARK_VOICE_HOSTS`. Configure remote access deliberately
and review the client helper for the T3 build in use; keep the rest of the local
API private. USB devices are physically trusted: checksums and length bounds
detect malformed transfers, not a malicious device's identity.

## Data handling

The companion holds recordings and pending transcripts in memory rather than
recording files. Submitted text still passes through T3 and its selected model
provider. Provider retention and permission settings apply. Session selection
is saved locally, and diagnostics or externally captured crash dumps can contain
private paths and metadata.

Use synthetic sessions for reports and screenshots. Downloaded models and
original firmware backups stay outside source/release artifacts; backups can
contain credentials from previous firmware. Release verification and distribution boundaries are recorded in
[docs/release-readiness.md](docs/release-readiness.md).
