# Security

Moxie handles people's financial documents and acts on their accounts. Security isn't a feature here — it's the precondition for the whole project. This file is deliberately honest about what is and isn't done.

## Status: hardening implemented — external review still pending

The consent-first control flow (policy → approval → tamper-evident audit) has been in since day one. The hardening checklist now stands:

- [x] **Encryption at rest** — `moxie encrypt on` seals the store's payloads (actions, receipts, transactions) and the bank-token file with Fernet (`cryptography`, optional `[secure]` extra). Honest limits: the decisions table's merchant/kind keys and the audit log's event metadata remain plaintext.
- [x] **OS keychain** — `moxie secret set NAME` moves API keys, bot tokens, and SMTP passwords off disk into the platform keychain (`keyring`, `[secure]` extra). Environment/.env still works and always wins if set — the keychain is the fallback store, not a lock-in.
- [x] **Live-action gates** — nothing sends unless `MOXIE_LIVE=true` *and* the action passed policy + your explicit approval; a `KILL` file (`moxie kill`) forces drafts-only regardless. Browser automation additionally needs `MOXIE_BROWSER_OK=true` and per-merchant skill steps.
- [x] **Dashboard hardening** — binds to 127.0.0.1 only; POSTs require a custom `X-Moxie` header (CSRF guard); optional `MOXIE_DASH_TOKEN` bearer token locks the whole API (use it if you tunnel).
- [x] **Telegram hardening** — single-chat pairing, allowlist with audited denials, two-step approvals, rate limiting.
- [x] **Read-only bank access** — AIS scopes only, bring-your-own provider account, consent expiry surfaced. Moxie has no payment scope to abuse.
- [x] **Threat model documented** — see [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md), including what is *not* defended against.
- [ ] **External security review** — not yet done. Until an independent review has happened, treat Moxie as suitable for your own risk appetite, not as audited software. If you can offer a serious review, please open a private advisory — it's the most valuable contribution possible.
- [ ] **Sandboxed browser tier in CI** — the Playwright tier is double-gated and skill-driven, but automated sandbox tests (Docker) are still to come. Run it in the provided Docker image if you enable it.

## Principles

- **Local-first.** Data stays on the user's machine; no central honeypot.
- **Read-only money access.** Moxie reads accounts and never moves money — `move_money`/`transfer`/`pay_bill`/`trade` are hard-denied in policy, not just unbuilt.
- **Least privilege.** App passwords, read-only scopes, revocable consents, single-chat pairing.
- **Offline option.** Local OCR (Tesseract) + local LLM (Ollama) so financial contents never leave the device.
- **Tamper-evident.** The audit log is hash-chained; `moxie verify` detects any change to past entries.
- **Fail-safe.** Non-interactive or uncertain → no action. Flag off → drafts. Kill switch beats everything.

## Reporting a vulnerability

Please report security issues **privately** (do not open a public issue): open a GitHub security advisory or email the maintainers. We'll acknowledge within a few days.
