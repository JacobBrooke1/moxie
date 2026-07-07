# Changelog

All notable changes to Moxie. Versions follow [semantic versioning](https://semver.org);
Moxie stays pre-1.0 until an independent security review (see [SECURITY.md](SECURITY.md)).

## Unreleased

- **The document vault.** Moxie now owns one folder for your money papers —
  `~/.moxie/vault/` with receipts, statements, bills, and confirmations. A
  Documents section on the dashboard lists, uploads, downloads, and deletes;
  imported CSVs auto-archive a dated copy; `moxie receipt` files the source
  image; `moxie vault list|add` gives CLI parity. Built defensively: path
  traversal hard-blocked, extension whitelist (no browser-executable types),
  10 MB cap, downloads served as attachments with nosniff (never rendered
  inline), files Fernet-encrypted at rest when `moxie encrypt` is on, and
  every add/remove/download audited by name — never contents.
- **Hardening from real-world use:** the Telegram bot survives a failing
  message (admits the error in-chat instead of dying silently), storage is
  thread-safe under the dashboard's parallel requests, unreadable rows are
  skipped and reported by `moxie doctor` instead of taking the page down,
  and cp1252 consoles can't crash any entry point.

## 0.3.0 — see all your money, and a dashboard that grows on request

- **The money dashboard.** A full Money section: per-account balances (once a
  bank is linked), this-month in/out/left, a where-it-went bar chart, a
  spend-by-month trend line (hand-rolled SVG — still zero dependencies, still
  offline), upcoming committed bills with their expected day, and your
  recurring subscriptions each wired to its live finding for one-click review
  through the Trust Vault. Figures you decide on — never financial advice.
- **Chat-built widgets.** Ask Moxie in chat — "track my Netflix spend",
  "keep my eating out under £150 a month" — and it proposes a dashboard card
  you confirm with one click. Cards persist (encrypted like everything else),
  can be removed by chat or the ✕, and chat can pin/hide the built-in status
  cards too. Security by construction: the model only ever emits a small JSON
  spec validated against a strict whitelist — never HTML, never code — and
  everything renders through Moxie's own escaped templates, so a poisoned
  merchant name can't turn the brain into a code-injection vector. Every
  add/remove is audited.

## 0.2.0 — the dashboard release

Moxie Dash becomes the single front door — most people never need the terminal.

- **Frictionless onboarding.** `moxie dashboard` opens your browser to a three-step
  wizard: connect + live-test your Claude API key, get data in (a bank CSV read in
  the browser, or one-click sample data), and optionally pair Telegram. Bare `moxie`
  on a terminal opens the dashboard too.
- **Chat with Moxie in the dashboard.** A chat panel grounded in your real money;
  it advises and points you at the approval modal but never executes — the Trust
  Vault boundary is enforced and tested.
- **Link your bank in the browser.** TrueLayer / GoCardless / Plaid, read-only,
  no terminal — pick a provider, consent, done.
- **Activity feed + heartbeat.** The hash-chained audit log as a readable timeline
  (honest "drafted" vs "SENT" wording), plus when the next daily scan will run.
- **Real hosted-mode security.** With `MOXIE_DASH_TOKEN` set the dashboard has a
  proper login (session cookie, rate-limited); Moxie now *refuses* to bind to a
  non-loopback interface without a token.
- **Polish.** New honey-badger logo, a locally-served favicon, and a mobile layout.

Everything stays local-first, stdlib-only in the core, and drafts-by-default.

## 0.1.0 — first release

The consent-first money agent that acts, only with your approval: the Trust Vault
(deny-by-default policy, human approval, hash-chained audit log), a three-tier
action layer behind `MOXIE_LIVE` (email / guided deep-link / browser), pluggable
read-only bank providers, the money picture, the receipt vault, eight explainable
detectors, an offline brain via Ollama, a skill library, encryption at rest and
OS-keychain secrets, and `moxie serve` for 24/7 self-hosting.
