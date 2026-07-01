# Security

Moxie handles people's financial documents and acts on their accounts. Security isn't a feature here — it's the precondition for the whole project. This file is deliberately honest about what is and isn't done yet.

## Status: early scaffold — do NOT use with real financial data yet

The consent-first control flow (policy → approval → tamper-evident audit) is implemented. The following **must be done before any real-data / production use**:

- [ ] **Encryption at rest** for the local store and receipt vault (currently plain SQLite + files — see `moxie/storage.py`).
- [ ] **OS keychain** integration for API keys and tokens (no secrets on disk in plaintext).
- [ ] **Sandboxed execution** for action adapters (especially future browser automation) — Docker by default, as OpenClaw does for untrusted (non-main) sessions.
- [ ] **External security review / threat model** before promoting real-money use.
- [ ] Scoped, revocable, read-only Plaid tokens; never store full bank credentials.

## Principles

- **Local-first.** Data stays on the user's machine; no central honeypot.
- **Read-only money access.** Moxie reads accounts (via Plaid) and never moves money.
- **Least privilege.** Request the narrowest scope; make access revocable.
- **Offline option.** Support local OCR + local LLM so receipt contents never leave the device.
- **Tamper-evident.** The audit log is hash-chained; `moxie verify` detects any change to past entries.
- **Fail-safe.** Non-interactive or uncertain → no action.

## Reporting a vulnerability

Please report security issues **privately** (do not open a public issue): open a GitHub security advisory or email the maintainers. We'll acknowledge within a few days.
