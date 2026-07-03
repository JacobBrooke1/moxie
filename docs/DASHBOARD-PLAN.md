# Moxie Control Plane — Dashboard Build Plan

*Turn Moxie Dash from a status page into the single surface you run Moxie from: chat to it, set it up, link your bank, and see all your money — the OpenClaw/Hermes control-plane feel, but money-shaped and consent-first.*

This plan is **additive**. Moxie Dash already exists (`moxie/dashboard.py`) and this enriches it — do not rebuild what's there. Every phase keeps the invariants at the bottom of this file.

---

## 0. What already exists (do NOT rebuild)

`moxie dashboard` serves a stdlib-only HTTP control panel on `127.0.0.1:8484` (`moxie/dashboard.py`, tested in `tests/test_dashboard.py`). It already has:

- **Status cards**: heartbeat, brain (ready/offline/model), Telegram (token/paired), data (txns/findings/savings), audit-chain (intact/tampered), mode (drafts/live/kill), bank (linked/consent), and a "this month" money summary.
- **Findings**: list with approve/skip through the same Trust Vault pipeline (`channel="dashboard"`), with an approve modal that shows and lets you edit the draft.
- **Setup panel**: paste Anthropic API key; paste Telegram bot token; "detect my chat id"; "pair this chat" — all written to `~/.moxie/.env`, secret *names* audited but never values.
- **Bank**: a "sync now" button hitting `/api/bank/sync`, plus an OAuth `/callback` catcher page that shows the consent code to paste.
- **Security**: binds to `127.0.0.1` only; POSTs require the `X-Moxie` CSRF header; optional `MOXIE_DASH_TOKEN` bearer locks the whole API.

**The gaps this plan fills:** no in-dashboard chat with the brain; setup is a flat panel not a guided wizard; bank *linking* (as opposed to syncing) is CLI-only; the money view is one summary card, not a real money dashboard; no live activity feed; remote/hosted access is tunnel-only.

---

## The single most important leap

**Phase 1 is the product's "wow".** Everything else is valuable, but the moment Moxie becomes *your agent you talk to* — not a CLI you run — is when you can chat to it inside the dashboard the way you chatted to otto in OpenClaw. Do Phase 1 first.

---

## Phase 1 — Chat with Moxie in the dashboard  *(effort: M · do first)*

**Goal:** a chat panel on the dashboard where you talk to Moxie's brain, grounded in your real money, and it can *point you at* actions without ever executing them itself.

**Do this (`dashboard.py`, `brain.py`, `storage.py`):**
- Add a **chat panel** to the dashboard page (a message list + input box), styled like the existing cards. No framework — extend the hand-rolled HTML/JS already in `PAGE`.
- Add `POST /api/chat` → calls `Brain.ask(message, transactions, actions, snapshot=snapshot_from_store(store))` (all of which already exist). Return the reply as JSON. Non-streaming is fine (the brain uses `urllib`); mark streaming a stretch.
- Persist recent turns: a new `chat` table in `storage.py` (encrypted like the rest via the existing cipher), so the conversation survives a refresh. Feed the last N turns back as context.
- **Keep the boundary**: the brain never executes. If the user says "cancel Netflix", the reply may *reference* the matching finding and the UI shows an inline "review this" button that scrolls to the Findings panel — approval still goes through the two-step Vault modal. Chat can advise and navigate; it cannot act.
- Audit each chat as `dashboard_chat` (question length only, never content of replies with account data beyond what's already stored).

**Acceptance:** with an API key set, you can ask "what should I cancel?" / "can I afford £120 trainers?" in the dashboard and get a grounded answer; asking it to act routes you to the approval modal, never a silent send.

**Tests:** inject a fake brain transport (the pattern in `tests/test_brain.py`) and assert `/api/chat` returns the reply and that "act" intents never call `execute_action`.

---

## Phase 2 — First-run setup wizard  *(effort: S/M)*

**Goal:** the OpenClaw onboarding feel — open the dashboard the first time and it walks you through setup, "connect your Claude API key" as step 1.

**Do this (`dashboard.py`):**
- Detect an unconfigured state (no key, no data, no Telegram) and show a **stepper**: 1) paste + **test** the Anthropic key (add `POST /api/brain/test` that makes one cheap call and reports ok/fail), 2) get data in (import a CSV **or** link a bank — link into Phase 3), 3) optional Telegram pairing (reuse the existing token + detect-chat-id flow).
- Each step shows a green tick when done; the wizard collapses into the normal dashboard once you're set up (or via a "skip setup" link).
- Keep all secret-writing on this local page only (never over chat) — unchanged from today.

**Acceptance:** a fresh `~/.moxie` opens straight into a 3-step wizard; finishing it lands you on the live dashboard with the brain ready.

**Tests:** `/api/brain/test` with a fake transport (ok + fail cases); wizard-state JSON reflects what's configured.

---

## Phase 3 — Link your bank from the dashboard  *(effort: M)*

**Goal:** connect a bank read-only without touching the terminal — provider choice, consent, done, all in the browser.

**Do this (`dashboard.py`, `providers.py`):**
- Add `POST /api/bank/start` (body: provider name) → `provider.start_link()` → return `{url, state}`; store `state` server-side keyed to the session.
- The existing `/callback` catcher already receives the OAuth redirect; wire it to `POST /api/bank/complete` (code + saved state) → `provider.complete_link()` → `BankLink.save()` → auto-sync once.
- A **"Connect bank"** card: provider picker (TrueLayer / GoCardless / Plaid, with the one-line trade-offs from `providers.py`), a credentials hint if env vars are missing, then the consent button. Show consent-expiry and a **re-auth** button when `needs_reauth` is true.
- Everything stays read-only AIS — no new capability, just a UI over the existing `AccountProvider` interface.

**Acceptance:** pick TrueLayer in the dashboard, consent at your bank, land back, and transactions + balances import — no CLI.

**Tests:** fake provider transport (the pattern in `tests/test_providers.py`); assert start→complete→sync populates the store and audits `bank_linked`.

---

## Phase 4 — The money dashboard (see all your money)  *(effort: M/L)*

**Goal:** the "banking dashboard" — a real money view, not one card. This is the "later" feature you described, built on data `snapshot.py` already computes.

**Do this (new dashboard section, `snapshot.py` extensions, `dashboard.py`):**
- A **Money page/tab** rendering: balance per account, this-month in/out/left, disposable income, committed vs. free, a **category breakdown**, a **month-over-month trend**, an **upcoming committed bills** list, and the **recurring subscriptions** list with an inline "review/cancel" button (routing to the Vault, per Phase 1's boundary).
- Charts as **hand-rolled inline SVG** (bars for categories, a line for the trend) — NO charting library. The dashboard must stay dependency-free and work offline; keep the stdlib-core ethos. `snapshot.py` already has categories, recurring, trend, committed — extend it with per-account balances and a simple upcoming-bills projection.
- Honest framing throughout: "figures you decide on", never financial advice (same guardrail as the brain).

**Acceptance:** the Money page shows balances, in/out/left, a category bar chart, a spend trend, and upcoming bills — all from your imported/synced data, all local.

**Tests:** extend `tests/test_snapshot.py` for the new fields; assert the money API returns them; SVG renders without external requests.

---

## Phase 5 — Activity feed + live heartbeat  *(effort: S/M)*

**Goal:** the control-plane feel — see what Moxie has been doing, at a glance.

**Do this (`dashboard.py`, over the existing `AuditLog`):**
- An **activity feed** card: the last N audit events (scans, approvals, sends, syncs, chats) as a human-readable timeline — the data is already in the hash-chained log, just surface it.
- Richer **heartbeat**: last scan time, next scheduled daily scan (`scan_hour`), Telegram bot up/down, live/drafts mode, uptime since `serve` started.
- Auto-refresh the feed (the page already polls `/api/status` every 15s).
- **Honesty note:** Moxie is a single money-agent, not a fleet — don't invent OpenClaw-style multiple "agents". The equivalent surface is the daily loop + detectors + this feed. Label it accurately ("Activity", not "Agents").

**Acceptance:** the dashboard shows a live timeline of everything Moxie did, and a heartbeat that says when it last ran and when it'll run next.

**Tests:** feed endpoint returns audit events newest-first; heartbeat fields reflect store/meta state.

---

## Phase 6 — Secure remote / self-host access  *(effort: M · required before any hosted use)*

**Goal:** run Moxie on a host (a Mac mini, a VPS like Hostinger) and reach the dashboard smoothly — **without** turning the keys-and-approvals surface into an open door.

**Do this (`dashboard.py`, `serve.py`, `docs/HOSTING.md`):**
- A proper **login page**: session cookie from `MOXIE_DASH_TOKEN` (replace the `prompt()` token flow), so a hosted dashboard has a real gate, not a JS prompt.
- Keep **`127.0.0.1` the default bind**. For remote, document two safe paths: (a) SSH tunnel (already documented), and (b) a TLS reverse proxy (Caddy/nginx) in front, with the token/login required — with loud warnings that this surface holds your keys and can approve money actions.
- **Never** ship a "bind 0.0.0.0 with no auth" path. If `MOXIE_DASH_HOST` is non-loopback and no token is set, refuse to start and say why.
- Rate-limit the login endpoint.

**Acceptance:** on a VPS you can reach the dashboard behind HTTPS + login, and Moxie refuses to expose an unauthenticated dashboard to a non-loopback interface.

**Tests:** login issues/clears a session cookie; non-loopback bind without a token raises at startup; authed vs unauthed requests get 200 vs 401.

---

## Phase 7 — Polish  *(effort: S)*

- **Mobile-responsive** dashboard (you'll check it from your phone) — the existing CSS grid mostly handles this; verify and fix.
- Ship the **new logo** + a favicon.
- A **dark/light** toggle is optional (it's already dark-themed).

---

## Invariants — never break these (identical to the core build)

1. **Nothing acts without the Trust Vault.** The dashboard chat and money views can advise and navigate, but every real action still passes policy → your approval → audit. The brain never executes.
2. **Live actions stay behind `MOXIE_LIVE` (default drafts) + approval + the kill switch.** The dashboard never bypasses this.
3. **Core stays stdlib-first.** No front-end framework, no charting library, no CDN dependency — the dashboard must run locally and offline. Any heavy dep goes behind an optional extra, imported lazily.
4. **Local-first, secrets on the device.** Keys and pairing are entered on the local dashboard and written to `~/.moxie/.env` (or the OS keychain); the audit log records that setup changed, never the secret values. The dashboard binds to `127.0.0.1` by default and is never exposed unauthenticated.
5. **Honest copy.** Never say "sent" when it drafted; the money view states figures and trade-offs, never financial advice.
6. **Every new path has tests**, using the injectable-transport pattern (fake brain, fake provider) so nothing touches the real world in CI. The full suite stays green after every change.

---

## Suggested sequence

**P1 (chat) → P2 (wizard) → P3 (link bank in-dashboard) → P4 (money view) → P5 (activity feed) → P6 (secure remote) → P7 (polish).**

Chat first because it's the "wow". Wizard next so onboarding is one screen. Then bank-linking and the money view, because together they make the dashboard the place you actually live. Activity feed and secure-remote make it a real always-on control plane. Polish last.

## The one thing to leave for the human

Anything needing real accounts or irreversible external effects: real bank OAuth credentials, a real Anthropic key, exposing a real host. Build and fully test these behind fakes/flags, document exactly how to finish them, and never fire a real external side effect autonomously.
