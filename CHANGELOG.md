# Changelog

All notable changes to Moxie. Versions follow [semantic versioning](https://semver.org);
Moxie stays pre-1.0 until an independent security review (see [SECURITY.md](SECURITY.md)).

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
