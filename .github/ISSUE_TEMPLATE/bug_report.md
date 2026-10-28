---
name: Bug report
about: Describe a reproducible setup, preview, or device problem.
---

For vulnerabilities, follow SECURITY.md. Use synthetic data; omit tokens,
conversation content, private hostnames, device serial numbers, and recordings.

**Expected and actual behavior**

Describe what you tried and what happened.

**Reproduction**

Give minimal steps, preferably using `python3 bridge.py --demo --port 8875` or
test fixtures.

**Environment**

Commit, OS, Python/Node/T3 versions, and board/firmware when relevant.

**Diagnostics**

Include sanitized errors or synthetic screenshots. Do not attach a database or
flash backup. State whether this occurs in the preview, fake-device tests, or
physical hardware.
