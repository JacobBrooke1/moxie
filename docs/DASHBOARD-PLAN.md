# Moxie Control Plane — Dashboard Build Plan

*Turn Moxie Dash from a status page into the single surface you run Moxie from: chat to it, set it up, link your bank, and see all your money — the OpenClaw/Hermes control-plane feel, but money-shaped and consent-first.*

This plan is **additive**. Moxie Dash already exists (`moxie/dashboard.py`) and this enriches it — do not rebuild what's there. Every phase keeps the invariants at the bottom of this file.

> **STATUS (2026-07-06): first pass built and released as 0.2.0.** ✅ P2 onboarding (auto-open browser, first-run wizard, live key test, in-browser CSV/sample data), ✅ P1 chat (multi-turn, grounded, never executes), ✅ P3 in-dashboard bank linking, ✅ P5 activity feed + heartbeat, ✅ P6 secure remote (login page, session cookies, non-loopback refusal), ✅ P7 polish (mobile, new logo, favicon).
>
> **Second build pass (2026-07-06): ✅ built.** ✅ P4 the money dashboard (per-account balances, stats, hand-rolled SVG charts, upcoming bills, recurring subs wired to the Vault) and ✅ P8 chat-built widgets ("ask Moxie to add a card" — the model emits strictly-validated specs, never code; human-confirmed, encrypted at rest, escaped at render, audited).
>
> Original scope note: build **every phase except Phase 4 (the money dashboard)**, deferred to a later pass. The **top priority is frictionless onboarding** — the dashboard must become the single front door so that anyone can: find the repo → install → run one command → the dashboard opens → paste their Claude API key there → and set up everything else (Telegram, bank, scanning, chat) from that one screen, whether they're running locally or on a VPS.

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

## The two most important leaps

1. **Frictionless onboarding (Phase 2) — the gate everyone hits.** If a newcomer can't get from "I found the repo" to "the dashboard is open and my key is connected" in a couple of minutes, nothing else matters. The dashboard must be the front door: one command launches it and opens the browser; the first screen is a guided wizard whose first step is connecting the Claude API key. **Build this first.**
2. **Chat with Moxie (Phase 1) — the "wow".** Once someone's in, the moment Moxie becomes *an agent you talk to* — not a CLI you run — is the chat panel, the way you chatted to otto in OpenClaw. **Build this second.**

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

## Phase 2 — Frictionless onboarding: the dashboard is the front door  *(effort: M · DO FIRST)*

**Goal:** download → one command → the dashboard opens → connect your Claude API key there → set up everything else from that screen. Anyone, local or VPS, in a couple of minutes. This is the headline of this build.

**Do this (`dashboard.py`, `cli.py`, `serve.py`, `README.md`):**
- **One-command launch.** `moxie dashboard` starts the server and **auto-opens the browser** (`webbrowser.open`, best-effort, skip if headless/`MOXIE_NO_BROWSER`). Make bare `moxie` (no command) launch the dashboard too (or print a single clear "run `moxie dashboard` to get started" line). Keep `python -m moxie dashboard` working as the robust fallback and document it once.
- **First-run wizard.** Detect an unconfigured state (no key, no data, no Telegram) and land on a **stepper**, not the flat panel: 1) paste + **test** the Anthropic key live (add `POST /api/brain/test` — one cheap call, reports ok/fail with a friendly error), 2) get data in (import a CSV **or** link a bank — routes into Phase 3), 3) optional Telegram pairing (reuse the existing token + detect-chat-id flow). Each step shows a green tick; the wizard collapses into the normal dashboard once set up (with a "skip for now" link).
- **Rewrite the README quickstart** so the *primary* path is exactly: (1) install (`pip install moxie-agent`, or clone + `pip install -e .`), (2) run `moxie dashboard`, (3) do everything else in the browser — connect your key, add your data, pair Telegram. The CLI commands become the "power user / automation" alternative, not the first thing a newcomer sees. Keep the "runs on sample data, no bank or key needed" promise for the very first look.
- Keep all secret-writing on this local page only (never over chat) — unchanged from today.

**Acceptance:** a brand-new user with a fresh `~/.moxie` runs one command, the browser opens to a 3-step wizard, pastes their key (which is tested live), and lands on a working dashboard — having touched the terminal exactly once. The README's first section reflects this exact flow.

**Tests:** `/api/brain/test` with a fake transport (ok + fail cases); wizard-state JSON reflects what's configured; the browser-open is guarded so it never fires in CI/headless.

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

## Phase 4 — The money dashboard (see all your money)  *(effort: M/L · ▶ NOW IN SCOPE — second build pass)*

**Goal:** the "banking dashboard" — a real money view, not one card. When someone
links a bank (TrueLayer etc.), they should *see their accounts* and manage their
money picture from here; CSV-only users get everything except live balances.

**Do this (new dashboard section, `snapshot.py` extensions, `dashboard.py`):**
- A **Money section** on the dashboard rendering: **balance per account** (name,
  current, available — from the balances the bank sync already stores),
  this-month in/out/left, disposable income, committed vs. free, a **category
  breakdown**, a **month-over-month spend trend** (extend the snapshot to a
  per-month series over the data window, not just last-vs-this), an **upcoming
  committed bills** list (recurring merchants not yet charged this month, with
  expected day-of-month from their history), and the **recurring subscriptions**
  list with an inline "review" button (routing to the Findings approval modal —
  the Vault boundary, always).
- Charts as **hand-rolled inline SVG** (bars for categories, a line for the
  trend) — NO charting library. The dashboard must stay dependency-free and
  work offline; keep the stdlib-core ethos.
- A `GET /api/money` endpoint returning the full computed picture; the section
  refreshes with the rest of the page.
- Honest framing throughout: "figures you decide on", never financial advice
  (same guardrail as the brain).

**Acceptance:** with a linked bank (fake transport in tests), the Money section
shows each account's balance, in/out/left, a category bar chart, a spend trend
line, upcoming bills, and recurring subs with review buttons — all local, no
external requests. CSV-only data shows the same minus balances.

**Tests:** extend `tests/test_snapshot.py` for the new fields (per-month series,
upcoming-bills projection); `/api/money` shape; SVG output contains no external
URLs; the review button routes to an existing finding id.

---

## Phase 8 — Chat-built widgets: ask Moxie to grow your dashboard  *(effort: M/L · NEW — second build pass)*

**Goal:** "can you add a card that tracks my eating-out spend?" — and the
dashboard grows that card. Personal, self-extending, *without ever letting the
model write code*.

**The security line (non-negotiable, this is the whole design):** the brain
NEVER emits HTML/JS/CSS and nothing the model outputs is ever rendered as
markup or executed. Transaction text feeds the brain, so a malicious merchant
name could try to steer it — if the model could write code into the page that
holds API keys and approves money actions, that's prompt-injection →
key-exfiltration. Instead the model emits a **widget SPEC**: a small JSON
object validated against a strict whitelist, rendered entirely by Moxie's own
audited code with all strings escaped.

**Do this (`dashboard.py`, `brain.py`, `storage.py`, `snapshot.py`):**
- **Widget spec vocabulary** (the whole universe of what chat can build):
  - `stat_card` — a single figure (spend at merchant X / in category Y, this
    month or trailing N months)
  - `merchant_tracker` — one merchant's monthly history as an SVG mini-bar
  - `category_total` — sum over a keyword list, with optional monthly budget
  - `goal_progress` — a target amount vs. actual (e.g. "keep eating out under
    £150/mo") as a progress bar
  - `trend_chart` — spend trend for a filter, as an SVG line
  Spec fields: `type` (enum above), `title` (≤40 chars, escaped), `merchants`
  and/or `keywords` (lists of plain strings, escaped, matched against the
  user's own transactions), `months` (1–12), `target` (number, optional).
  ANYTHING else — unknown keys, nested objects, HTML in strings — is rejected.
- **Server-side validation** (`widgets.py` or in `dashboard.py`): a pure
  function `validate_widget_spec(dict) -> spec | error`, unit-tested against
  hostile inputs (script tags in titles, absurd lengths, unknown types).
- **Brain intent**: extend the dashboard chat so "add/track/watch…" requests
  make the brain propose a spec (a constrained instruction + the vocabulary in
  the prompt; the reply carries the JSON in a fenced block Moxie parses). The
  user sees a confirmation chip — "Add this card? [Add] [No]" — the widget is
  only saved on click (the human confirms, as ever). "Remove the X card" works
  the same way.
- **Storage**: widgets persist in the store (encrypted like the rest);
  `GET /api/widgets`, `POST /api/widgets` (add, from a validated spec),
  `POST /api/widgets/remove`. Every add/remove is audited (spec summary, never
  raw model output).
- **Rendering**: a "Your cards" grid on the dashboard, computed server-side
  from the same snapshot/transaction data, drawn with Moxie's own SVG/HTML.
  Layout prefs too: chat can pin/hide built-in cards via the same spec-not-code
  mechanism (`layout` spec type).

**Acceptance:** asking "track my Netflix spend" in chat yields an "Add this
card?" confirmation; clicking Add puts a live Netflix tracker on the dashboard
that survives restart; "remove the Netflix card" removes it; a hostile spec
(script tag in the title, unknown type, HTML in a keyword) is rejected server-
side and renders as nothing; the model's raw text is never inserted as markup.

**Tests:** validation against hostile specs; add/remove round-trip via the API;
fake-brain chat flow producing a spec → confirmation → persisted widget;
escaping test (a widget titled `<script>alert(1)</script>` renders escaped);
the execute_action guard still holds (widgets can't trigger actions).

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

## Phase 9 — The document vault: Moxie's own folder for your money papers  *(✅ built 2026-07-06)*

**Goal:** give Moxie one dedicated folder it owns — `~/.moxie/vault/` — where it
files and serves your money documents: receipts, past bank statements, bills,
confirmation emails. A "Documents" section on the dashboard to browse, upload,
and download them.

**Do this (`documents.py` new, `dashboard.py`, `receipts.py`, `cli.py`):**
- **The folder**: `~/.moxie/vault/{receipts,statements,bills,confirmations}/`,
  created by `moxie init` / first dashboard run. Moxie only ever reads/writes
  inside this tree — path traversal hard-blocked (every filename sanitised, no
  separators/`..`, resolved paths must stay under the vault root; hostile-name
  tests mandatory).
- **Auto-filing**: CSV imports get archived to `statements/` (dated copy);
  receipts captured by OCR/email save their source file to `receipts/`;
  cancellation/dispute confirmations the user drags in go to `confirmations/`.
- **Dashboard Documents section**: list per category (name, date, size),
  upload (browser file → POST, size-capped, extension-whitelisted:
  pdf/png/jpg/csv/txt/eml), download, delete — all audited (`document_added`
  etc., names only). Files SERVED with `Content-Disposition: attachment` and
  a no-sniff header — never rendered inline (an uploaded HTML/SVG must not
  execute in the dashboard origin; enforce by whitelist + headers, tested).
- **Encryption**: when `moxie encrypt on` is active, files are stored
  Fernet-sealed (same cipher) and decrypted on download; plaintext mode
  documented honestly in SECURITY.md.
- **Wiring**: dispute evidence can reference a vault file; the receipts flow
  (Phase 3 of the core plan) stores its images here.
- **CLI**: `moxie vault list|add <file> [--category]` for parity.

**Acceptance:** drop a statement PDF and a receipt photo into the dashboard →
they're filed under the right category, listed with dates, downloadable,
audited; with encryption on the bytes on disk are ciphertext; a file named
`../../evil` or `x.html` is rejected; nothing in the vault is ever served
inline or executed.

**Tests:** path-traversal names, extension rejection, size cap, upload/download
round-trip (incl. encrypted), attachment headers, audit entries, CSV-import
auto-archive.

---

## Invariants — never break these (identical to the core build)

1. **Nothing acts without the Trust Vault.** The dashboard chat and money views can advise and navigate, but every real action still passes policy → your approval → audit. The brain never executes.
2. **Live actions stay behind `MOXIE_LIVE` (default drafts) + approval + the kill switch.** The dashboard never bypasses this.
3. **Core stays stdlib-first.** No front-end framework, no charting library, no CDN dependency — the dashboard must run locally and offline. Any heavy dep goes behind an optional extra, imported lazily.
4. **Local-first, secrets on the device.** Keys and pairing are entered on the local dashboard and written to `~/.moxie/.env` (or the OS keychain); the audit log records that setup changed, never the secret values. The dashboard binds to `127.0.0.1` by default and is never exposed unauthenticated.
5. **Honest copy.** Never say "sent" when it drafted; the money view states figures and trade-offs, never financial advice.
6. **Every new path has tests**, using the injectable-transport pattern (fake brain, fake provider) so nothing touches the real world in CI. The full suite stays green after every change.

---

## Suggested sequence (for this build)

**P2 (onboarding / front door) → P1 (chat) → P3 (link bank in-dashboard) → P5 (activity feed) → P6 (secure remote) → P7 (polish).  ⏸ P4 (money view) is deferred.**

Onboarding first because it's the gate everyone hits — the dashboard must be the front door. Chat next because it's the "wow". Then in-dashboard bank-linking, the activity feed, and secure-remote make it a real always-on control plane. Polish last. The money dashboard (P4) comes in a later pass once this is being used.

## The one thing to leave for the human

Anything needing real accounts or irreversible external effects: real bank OAuth credentials, a real Anthropic key, exposing a real host. Build and fully test these behind fakes/flags, document exactly how to finish them, and never fire a real external side effect autonomously.
