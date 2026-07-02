# Moxie — Build Plan (what comes next)

> **STATUS (2026-07-02): every phase below is implemented and tested** — 135 tests green.
>
> - **P1 action layer** ✅ email (SMTP) / guided deep-link / browser tiers behind `MOXIE_LIVE` + kill switch (`moxie/actions.py`)
> - **P9 slice: CI + packaging** ✅ GitHub Actions (tests + ruff), release workflow, templates
> - **P2 bank providers** ✅ TrueLayer / GoCardless / Plaid behind `AccountProvider` (`moxie/providers.py`, `moxie connect|sync`)
> - **P2b money picture** ✅ `moxie/snapshot.py`, grounded brain answers, `moxie budget`, `/budget`, dash card
> - **P3 receipts** ✅ local OCR + read-only IMAP + matching + evidence on disputes (`moxie/receipts.py`)
> - **P4 detectors** ✅ 8 detectors: dups, zombies, new subs, price hikes, duplicate services, bank fees, FX fees, short refunds
> - **P5 offline brain** ✅ `MOXIE_MODEL=ollama:llama3.1`
> - **P6 skills wired** ✅ routes steer channel/draft, advice adds playbooks, usage tracked, UK library seeded
> - **P7 security** ✅ encryption at rest, OS keychain, dash token+CSRF, TG rate limit, threat model (external review still open)
> - **P8 24/7** ✅ `moxie serve`, systemd/launchd/Docker, `docs/HOSTING.md`
> - **P9 distribution** ✅ code-side done; the human-only steps (PyPI publish, community launch) are in `docs/LAUNCH.md`
>
> The remaining human-only items live in **[docs/LAUNCH.md](LAUNCH.md)**. The plan below is kept as the original rationale.

*A grounded, phased roadmap for the open-source code as it stood before the build-out. Every phase names the real files to touch, why it matters, and how you'll know it's done.*

---

## 0. Where Moxie is right now (honest snapshot)

**Already built and on GitHub (`JacobBrooke1/moxie`), 46 tests passing, run on real NatWest data:**

- **CLI** — `init · scan (--csv/--pdf) · review · ask · telegram · dashboard · log · verify · skills · doctor`
- **Rules layer** (`detect.py`) — deterministic detectors: duplicate charges, zombie subscriptions. Free, explainable.
- **Brain layer** (`brain.py`) — Anthropic API over stdlib `urllib`, `moxie ask`, daily `triage`, standing instructions at `~/.moxie/instructions.md`, prompt-injection hardening (transactions pinned as untrusted DATA). Bring-your-own key.
- **Trust Vault** (`vault/`) — deny-by-default policy, fail-safe approval, hash-chained tamper-evident audit log.
- **Telegram channel** (`telegram.py`) — single-chat pairing + allowlist, two-step approvals (`/approve N` → `YES`), `/findings /scan /skip /help`, daily briefing loop, decision memory (skip = quiet for 60 days), denied chats audited.
- **Moxie Dash** (`dashboard.py`) — stdlib HTTP control panel on `127.0.0.1:8484`: status cards, findings with approve/skip, in-page setup for keys + Telegram pairing written to `~/.moxie/.env` (secret *names* audited, never values), localhost-only + SSH-tunnel guidance.
- **Import** (`statements.py`, `connectors.py`) — bank CSV (headers auto-detected) + PDF (NatWest-style, `pypdf`).
- **Memory/state** (`storage.py`, `agent.py`) — transactions persisted, remembered decisions/snoozing, `resolve(action, approved, channel)`.
- **Skills** (`skills.py`) — `SKILL.md` loader (stdlib), one example.
- **Docs** — `HOW_IT_WORKS.md`, logo, demo GIF.

**The one gap that matters most:** Moxie **drafts but does not send.** `actions.execute_action` is dry-run only; Telegram and the dashboard both say *"drafts only — nothing was sent."* It is a beautifully complete, trustworthy **advisor** that stops one inch short of being an **actor**.

**Still stubbed:** live bank connect (`connectors.PlaidConnector` → `NotImplementedError`), OCR + email receipts (`receipts.py`), offline/local brain, skills-wired-to-execution, and the SECURITY.md hardening checklist.

---

## The single most important leap

Everything below is valuable, but **Phase 1 is the product.** Today Moxie is the safest drafts-only money tool in existence; the entire thesis ("the agent that *acts*, trust-first") only becomes true when approving a finding actually sends the cancellation. Do Phase 1 first, keep every safety rail, and Moxie stops being a demo and starts being the thing.

---

## Phase 1 — Make it actually act  *(effort: M/L · do first)*

**Goal:** approving a finding actually *does* the thing — via whichever channel is fastest and most reliable for that merchant — safely gated and logged.

Cancelling isn't one thing. A few merchants only take email/letter; **most let you cancel on their website** (usually faster and more reliable than email — correct); some make you phone. So the action layer is **three tiers, ordered by reliability and safety**, and Moxie picks per merchant (driven by that merchant's `SKILL.md`):

1. **Email / letter** *(built path — ship first).* For merchants that accept it. Sent from the user's own SMTP (their address + app password in the keychain), so it's legitimate and deliverable — no impersonation, no third-party passwords.
2. **Guided deep-link** *(the smart default for website cancels).* Moxie knows the exact cancel URL and the handful of clicks (from the skill), deep-links you straight to the page, and tells you what to click. Fast, reliable, and **zero password risk** — you do the final click, so any CAPTCHA / 2FA / retention-offer is handled by a human. This is usually faster and more reliable than email, and it dodges the hard security problems.
3. **Full browser automation** *(most thorough — power-user / later).* A headless browser (Playwright / a computer-use agent) logs in and clicks cancel end-to-end. **Yes, AI can do this** — but it's the hardest, riskiest tier: it needs the user's *merchant* login (kept in the keychain), must pause for 2FA/CAPTCHA (human-in-the-loop), fights deliberately obscured "dark pattern" cancel flows, and can trip bot-detection or lock the account. Do it **per-merchant via the skill library** (each skill encodes the exact URL/selectors/steps), sandboxed (Docker), always behind the Vault + approval + audit. The skill library is what turns this from a guessing agent into something reliable.

**Do this (`actions.py`, with `agent.py`/`telegram.py`/`dashboard.py` touches):**
- Define an **action-channel interface**; implement email first, then guided-deep-link, then browser automation. The merchant's `SKILL.md` declares which channel and the exact steps.
- Gate all live action behind **`MOXIE_LIVE` (default `false` = drafts)** + per-action approval (keep the two-step) + a kill switch.
- Let the user edit the draft / confirm the steps before anything runs.
- On completion: mark `sent`/`done`, audit with a reference (message-id or confirmation note), keep the receipt attached.
- **Tests:** inject fake transports (the pattern you already use for Telegram/Brain) so email and browser paths are fully tested without touching the real world.

**Acceptance:** with `MOXIE_LIVE=true` + approval, an email-based cancel really sends *and* a website cancel deep-links you to the right page with the exact clicks; both logged; flag off = identical to today.

**Sequencing:** ship tier 1 (email) + tier 2 (guided deep-link) first — together they cover most subscriptions quickly and safely. Tier 3 (full automation) follows, one merchant at a time, as skills mature.

**Stretch:** a reply-watcher that reads the merchant's response and moves a finding `disputed → refunded`.

---

## Phase 2 — Live bank connection: pluggable providers, user's choice  *(effort: M)*

**Goal:** stop needing manual CSV/PDF exports — pull transactions **and balances** read-only, automatically, from whichever provider the user prefers.

**Do this (`connectors.py` — retire the Plaid stub; build a small provider interface):**
- One `AccountProvider` interface, several implementations the user picks between in the dashboard:
  - **TrueLayer** — the default for the UK (great coverage incl. NatWest; free sandbox + PAYG).
  - **Plaid** — offered for anyone who wants it (strong US coverage; also UK). User's choice.
  - **GoCardless Bank Account Data** (ex-Nordigen) — the most generous free tier, ideal for zero-cost self-hosters.
  - **CSV / PDF import** — the existing no-cloud fallback (no third party at all).
- Each provider does read-only **AIS** only: consent/OAuth → short-lived tokens in the OS keychain (Phase 7) → pull **transactions + balances** → feed the *same* `store.save_transactions(...)` the CSV/PDF path already uses. Everything downstream is unchanged.
- Handle consent expiry (UK consents lapse ~90 days) with a re-auth prompt in the dashboard.

**Honesty note:** every aggregator is a cloud third party, so document the trade-off and keep CSV/PDF as the no-cloud path. Because Moxie is self-hosted and BYO-key, *the user* holds the provider account — not you — so the local-first story stays intact.

**Acceptance:** pick a provider in the dashboard, link NatWest read-only, transactions + balances auto-import, `moxie scan` runs, consent expiry handled gracefully.

---

## Phase 2b — The money picture (balance, income, spending, "can I afford this?")  *(effort: M · this is what makes the chat great)*

**Goal:** because Moxie sees your account, the brain should actually *know your finances* — balance, income, spend, and what's left — so "can I afford these £120 trainers?" and "how should I budget?" get real, grounded answers instead of vibes.

**Do this (new `snapshot.py`, feeding `brain.py`):**
- Compute a **financial snapshot** from transactions + balances: current balance, monthly income (recurring credits), monthly outgoings, committed spend (subscriptions + regular bills), and **disposable income** (what's genuinely free this month), plus simple category breakdowns and a month-over-month trend.
- Feed that snapshot into every `brain.ask` / `triage` call — today the brain only sees raw transactions. Then "can I afford X?" is answered against real disposable income and upcoming commitments; "how do I budget?" points at actual categories.
- Surface it: a **"This month — in / out / left"** card on the dashboard, and `/budget` in Telegram.

**Honest guardrail:** Moxie shows you the *figures* and the trade-offs and lets you decide — it states what's committed and what's left; it does **not** pose as a regulated financial adviser or give confident "yes, buy it" verdicts. Being straight about this is on-brand and keeps you clear of financial-advice regulation.

**Acceptance:** `moxie ask "can I afford £120 trainers this month?"` answers using real balance + disposable income + upcoming bills; the dashboard shows in/out/left for the month.

**Depends on** Phase 2 for live balances (works on imported CSV/PDF history in the meantime).

---

## Phase 3 — Receipts: evidence + the safe on-ramp  *(effort: M)*

**Goal:** deliver the receipt vault the README already promises, and use receipts as dispute evidence.

**Do this (`receipts.py` — retire both stubs):**
- **Photo OCR** via local **Tesseract** (`pytesseract`, optional extra) → parse merchant/date/amount → store as a `Receipt` → match to a transaction.
- **Email e-receipts** via read-only **IMAP** scan (or Gmail API) → extract the same fields.
- **Wire it in:** when a dispute action fires, auto-attach the matching stored receipt as evidence.
- Keep it offline-first (local OCR; images never leave the machine).

**Acceptance:** snap a receipt photo → it's parsed, filed, and matched to a transaction; a dispute references its receipt.

---

## Phase 4 — Smarter detection  *(effort: M · great community work)*

**Goal:** find more real money, with a low false-positive rate.

**Do this (`detect.py` — each detector explainable + unit-tested):**
- Missed/rejected refunds; bank fees (overdraft, unarranged, late); price-hike renewals (same merchant, higher amount); free-trial→paid conversions; duplicate services (two streaming/music subs); FX / non-sterling fees; dormant subscriptions (let the brain ask "when did you last use this?").
- Lean on `brain.triage` (already built) to separate signal from noise.

**Acceptance:** each detector ships with sample data + tests; triage flags the likely false positives. **Each detector is an ideal "good first issue."**

---

## Phase 5 — Offline brain (Ollama)  *(effort: S)*

**Goal:** give the no-key / privacy-max crowd (the Firefly III audience) the brain too.

**Do this (`brain.py`):** add a transport for a **local model via Ollama** (`http://localhost:11434`), selected by `MOXIE_MODEL=ollama:llama3.1`. Same instructions, same guardrails. Today `offline` just disables the brain — this turns it on with no cloud key.

**Acceptance:** with Ollama running and `MOXIE_MODEL=ollama:…`, `moxie ask` works fully offline.

---

## Phase 6 — Wire the skill ecosystem (the compounding moat)  *(effort: M)*

**Goal:** make the `SKILL.md` library actually *drive* actions, and make contributing skills the main way people help.

**Do this (`skills.py` + `agent.py`/`actions.py`):**
- When acting on merchant X, look up a matching `SKILL.md` (by merchant/action_type) and use its steps/template to shape the draft and the escalation path (e.g. "decline retention offer → request written confirmation → chargeback after 14 days"). Track per-skill success rate.
- Seed a real starter library: top UK subscriptions (gym chains, streaming, telcos) and the NatWest dispute flow.
- Provide a share path — a `moxie-skills` community repo / ClawHub-style index — so contributed know-how accrues. **This is the moat the giants can't crowdsource.**

**Acceptance:** acting on a covered merchant uses its skill; adding a new merchant is one folder + a PR.

---

## Phase 7 — Security hardening (the gate to real-money use)  *(effort: M/L · required before promoting real data)*

**Goal:** tick the SECURITY.md checklist so the "don't use with real data" warning can come off.

**Do this:**
- **Encrypt at rest** — the store + receipt vault (SQLCipher, or an encrypted blob). Financial docs shouldn't sit in plaintext SQLite.
- **Secrets to the OS keychain** (`keyring`) — move off plaintext `~/.moxie/.env`. The dashboard already avoids *logging* secret values; go further and stop *storing* them in plaintext.
- **Sandbox** any future browser/portal automation (Docker by default, as OpenClaw does for untrusted sessions).
- Harden the dashboard: optional access token even on localhost; basic CSRF protection on POSTs. Rate-limit Telegram.
- Write the **threat model** into `HOW_IT_WORKS.md`; commission an **external review** before softening the warning.

**Acceptance:** checklist ticked; secrets no longer on disk in plaintext; threat model documented.

---

## Phase 8 — Run it 24/7  *(effort: S/M)*

**Goal:** the daily loop, Telegram bot, and dashboard run always-on, surviving reboots.

**Do this:**
- A combined **`moxie serve`** that runs Telegram + dashboard + daily loop together.
- Service units: **systemd** (Linux VPS) + **launchd** plist (Mac mini) + a **Dockerfile**.
- `docs/HOSTING.md`: **a Mac mini at home is the ideal host** — always-on and your bank data never leaves a machine you own (the whole thesis). A VPS works but your data lives on a rented box → reach the dash only via SSH tunnel and encrypt at rest (Phase 7).

**Acceptance:** one command installs Moxie as a service that survives reboot and sends the morning briefing.

---

## Phase 9 — Distribution & community (turn it into stars + contributors)  *(effort: S/M · ongoing)*

**Goal:** make it trivial to install, safe to contribute to, and discoverable where your users already are.

**Do this:**
- **PyPI**: publish `moxie-agent` so `pip install moxie-agent` works; host `install.sh` at a domain for the one-liner.
- **CI**: GitHub Actions running the 46 tests + a linter on every PR; add the green badge. (Cheap insurance for every future change.)
- **Repo hygiene**: issue/PR templates, `CODE_OF_CONDUCT.md`, a curated set of **good-first-issues** (each Phase-4 detector and each seed skill is one).
- **The bridge**: publish a thin **Moxie skill** for Hermes/OpenClaw so it's discoverable in ClawHub — the host agent asks, but the money-actions + vault run inside Moxie (keeps the security boundary; wins the distribution).
- **Launch**: Show HN, r/selfhosted, r/UKPersonalFinance, the OpenClaw/Hermes communities. You already have a demo GIF.

**Acceptance:** pip-installable, CI badge green, listed in the communities your users live in.

---

## Invariants — never break these (they're the whole brand)

1. **Core stays stdlib-first.** Heavy deps (pypdf, pytesseract, keyring, provider SDKs) go behind extras and import lazily. You've held this line — keep it.
2. **Nothing acts without the Vault.** The brain never executes; live actions stay two-step; deny-by-default remains the default.
3. **Local-first, zero-account, honest about any cloud.** Anyone downloads the code, runs their own server, and adds their own keys — Moxie the project runs no servers and never sees user data. If a step touches a cloud aggregator or cloud LLM, say so and keep a local fallback.
4. **Honest copy.** Never say "sent" when it drafted; never claim capability you haven't shipped (the DoNotPay-FTC lesson is the reason Moxie exists).

---

## Definition of done for **v1.0** (safe to use on real money)

Phase 1 (real send) **+** Phase 2 (live UK bank) **+** Phase 7 (security checklist) **+** you running it on your own NatWest data for a month with zero bad actions. Everything else is enhancement that can happen in parallel or via the community.

---

## Suggested sequence & why

**P1 (act) → CI + PyPI (P9 slice) → P2 (bank) → P7 (security) → P3/P4/P5/P6 in parallel → P8 (deploy) → P9 (launch).**

Act first because it's the product. Do the cheap CI + PyPI slice next so every later change is protected and anyone can install. Then bank + security, because those two gate real-money use. Detectors, receipts, offline brain, and skills are parallelizable and make perfect community contributions. Deploy + launch last, once it's genuinely safe and useful.

---

## Do this week (the immediate five)

1. **Phase 1 spike** — SMTP send behind `MOXIE_LIVE`, with an injected fake-transport test. Ship drafts → real send.
2. **Add GitHub Actions CI** — run the 46 tests on every push/PR (protects everything else) + issue/PR templates + 5 good-first-issues.
3. **Phase 2 spike** — TrueLayer sandbox (with Plaid + GoCardless as switchable options) → pull NatWest transactions + balances read-only into the store.
4. **Publish to PyPI** as `moxie-agent` so people can `pip install moxie-agent`.
5. **Write `docs/HOSTING.md`** + a combined `moxie serve` runner (Telegram + dashboard + daily loop).
