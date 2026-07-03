<div align="center">

<img src="docs/logo.svg" alt="Moxie the honey badger" width="360">

# 🦡 Moxie

**The open-source money agent that *acts* — and never without your say-so.**

*Moxie doesn't care about a company's excuses. It just gets your money back — and asks you first, every time.*

[![CI](https://github.com/JacobBrooke1/moxie/actions/workflows/ci.yml/badge.svg)](https://github.com/JacobBrooke1/moxie/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: early scaffold](https://img.shields.io/badge/status-early%20scaffold-orange.svg)](#status)
[![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen.svg)](#privacy--security)

*Named for the honey badger — small, fearless, famously relentless. It badgers companies until your money comes back.*

![Moxie demo — scan, review with approval, verify the audit log](docs/demo.gif)

*The whole loop in 30 seconds: **scan** finds ~$591/yr of waste in the sample data, **review** shows you each fix and asks first (that `n` is the point — you're in control), **verify** proves the audit log hasn't been touched. Runs on bundled sample data — no bank, no API key.*

</div>

---

## Why Moxie exists

AI agents today split into two camps: ones that **reach everywhere** (OpenClaw) and ones that **get smarter over time** (Hermes). Neither answers the question that actually matters with your money: *what will you let it do when the downside is real?*

Look at who already touches your money:

- **Receipt & finance organizers** (Expensify, Firefly III, Receiptor AI) — they *file and track*. They don't act.
- **Money-action services** (DoNotPay, Rocket Money, Pine AI) — they *act*, but as **closed black boxes** that have burned users' trust (DoNotPay was FTC-fined for overstating its AI; Rocket Money has acted *as* users without asking).
- **ChatGPT + Plaid** — **read-only by design**: it can spot a subscription to cancel, but it won't cancel it.

**Moxie bridges the gap, trust-first.** It files your receipts (email + photo), reads your accounts, finds waste and wrong charges, and *acts* on them — cancelling, disputing, chasing refunds — but **every action is previewed, approved by you, logged in a tamper-evident audit trail, and backed by the receipt as evidence.** It's open-source and local, so you can read every line and your data never has to leave your machine.

> **Moxie never moves money.** It cancels, disputes, and negotiates on your behalf. Paying, transferring, and trading are deliberately out of scope (that's a licensing and liability minefield). See [the build spec](#design).

---

## What it does

- 🧾 **Receipt vault** — `moxie receipt photo.jpg` (local Tesseract OCR — images never leave your machine) or `moxie receipt --email` (read-only IMAP scan). Parsed, filed, matched to transactions, and **attached to disputes as evidence automatically**.
- 🔎 **Finds problems** — zombie subscriptions, duplicate/wrong charges, missing refunds, gouge renewals.
- ✅ **Acts — with your consent** — drafts the cancellation/dispute, shows it to you (editable), and sends it **only** when you approve *and* `MOXIE_LIVE=true`. Default is drafts-only. Receipt attached as proof.
- 📮 **Three action tiers** — email from *your own* mailbox (SMTP), guided deep-links (Moxie shows the exact cancel page + clicks; you click), and per-merchant browser automation (optional, double-gated, sandboxed).
- 🛡️ **Trust Vault** — deny-by-default policy engine, preview/simulate, approval gates, and a **hash-chained, tamper-evident audit log**.
- 🧩 **Community skill library** — reusable "how to cancel with X / dispute with Y" skills, each carrying its own success rate.
- 🔒 **Local-first & BYO key** — runs on your machine with your own LLM API key, or fully offline with a local model.

---

## Quickstart

Two commands. Everything else happens in your browser.

```bash
pip install moxie-agent   # or from source:  git clone https://github.com/JacobBrooke1/moxie.git && cd moxie && pip install -e .
moxie dashboard           # ← your browser opens; do everything from there
```

The dashboard walks you through setup in three steps, all on your machine:

1. **Connect your Claude API key** — pasted locally, tested live, stored in `~/.moxie` (or skip it: Moxie also runs a local Ollama model, or rules-only).
2. **Get your transactions in** — drop in any bank CSV (parsed in the browser, read-only) or click **"Try with sample data"** to see the whole consent-first loop with no bank and no key.
3. **Pair Telegram** *(optional)* — text Moxie like a PA and approve findings from your phone.

Works the same locally or on a VPS (see [docs/HOSTING.md](docs/HOSTING.md)). If `moxie` isn't recognized on Windows, use `python -m moxie dashboard` — pip's Scripts dir isn't on PATH; both work everywhere.

<details>
<summary><b>Prefer the terminal?</b> The full CLI (power users & automation)</summary>

```bash
moxie init            # set up ~/.moxie
moxie scan            # find issues (add --csv statement.csv or --pdf statement.pdf)
moxie review          # approve or skip each fix — nothing sends without your yes
moxie budget          # this month: in / out / left
moxie connect truelayer   # link a bank read-only (or gocardless / plaid)
moxie sync            # pull fresh transactions + balances
moxie log             # the tamper-evident audit trail
moxie verify          # confirm the log hasn't been altered
moxie doctor          # check your whole setup
```

Bank linking honesty note: every aggregator is a cloud third party. *You* hold the provider account (Moxie the project runs no servers), access is read-only AIS — Moxie cannot move money by construction — and CSV/PDF stays the fully no-cloud path. UK consents lapse ~90 days; the dashboard and `moxie doctor` tell you when to re-consent.

**Going live** (optional — everything works drafts-only without this): approving an action really sends it only when you flip the flag *and* configure your own mailbox:

```bash
# .env — your own email account (use an app password, never your real one)
MOXIE_SMTP_HOST=smtp.gmail.com
MOXIE_SMTP_USER=you@gmail.com
MOXIE_SMTP_PASSWORD=your-app-password
MOXIE_LIVE=true                   # default: false = drafts only

moxie review                      # 🔴 live: an approved cancel actually emails
moxie kill                        # panic button: force drafts-only until --release
```

Cancellations that work better on the merchant's website use **guided deep-links**: Moxie shows the exact URL and clicks (from the merchant's skill) and *you* do the final click — no passwords, no CAPTCHA fights.

</details>

> ⚠️ **Status:** feature-complete, pre-review. The Trust Vault, live action layer, bank providers, receipts, and the security hardening checklist (encryption at rest, OS keychain, dashboard token/CSRF, rate limiting) are all implemented and tested. What's missing is an **independent security review** — until then, use your own judgment with real financial data, keep `MOXIE_LIVE` off unless you've read the code, and see [SECURITY.md](SECURITY.md) for exactly where the edges are.

---

## How it works

```
CAPTURE receipts (email + photo/OCR)  +  CONNECT accounts (Plaid / CSV, read-only)
   → ORGANIZE   file receipts, match to transactions
   → DETECT     zombie subs, duplicate charges, missing refunds
   → PROPOSE    an action card: "Dispute this $40 double charge? I have the receipt."
   → APPROVE    you confirm  (because it can't be undone)
   → EXECUTE    cancellation / dispute / refund email
   → LOG        append-only, hash-chained audit trail with the receipt attached
```

Nothing in the right-hand column happens without passing the **Trust Vault**. For the full security model — the deny-by-default policy engine, the fail-safe consent design, the hash-chain math, and the threat model — see **[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)**.

### Why preview-and-approve, not "undo"

Most money actions are **one-way** — you can't cleanly un-cancel a subscription or un-send a dispute. So Moxie's safety is *before* the action (simulate → approve), not a promise to reverse it after. That's the whole reason consent is mandatory.

---

## Run it 24/7

```bash
moxie serve            # dashboard + Telegram bot + daily loop, one process
```

The daily loop re-scans every morning and pings you only when there's something new to decide. **A Mac mini at home is the ideal host** — always-on, and your bank data never leaves a machine you own. systemd/launchd units and a Dockerfile ship in [`deploy/`](deploy/); the full guide is [docs/HOSTING.md](docs/HOSTING.md).

## Moxie Dash — the control plane

```bash
moxie dashboard        # → http://127.0.0.1:8484
```

A local status page in the OpenClaw / Hermes tradition, but money-shaped: **heartbeat**, **brain**, **Telegram**, **data**, and **audit-chain** status at a glance, findings with approve/skip (same Trust Vault pipeline), and — most importantly — **the setup home**: paste your API key and BotFather token here, click *detect my chat id*, and it walks you through Telegram pairing. Keys are written to `~/.moxie/.env` on the machine Moxie runs on; the audit log records *that* setup changed, never the secrets themselves.

It binds to `127.0.0.1` only. Running Moxie on a Mac mini or a VPS? Reach the dash through an SSH tunnel (`ssh -L 8484:127.0.0.1:8484 you@host`) — never expose it to the open internet.

## The brain & the Telegram channel

Moxie has three layers, and you can stop at any of them:

1. **Rules** (no key needed) — deterministic, explainable detectors. Everything above runs on these. Eight of them: duplicate charges, zombie subscriptions, trials-that-stuck, price-hike renewals, duplicate services, bank fees, FX fees, and short refunds.
2. **The brain** (bring your own Anthropic key) — set `MOXIE_API_KEY` in a `.env` file and ask it things: `moxie ask "can I afford £120 trainers this month?"`. Answers are grounded in **the money picture** — real income, committed subscriptions, and what's genuinely left this month (`moxie budget` shows the same figures; balance appears once a bank is linked). It states figures and trade-offs and lets you decide — it's not a financial adviser and won't pretend to be. Its standing orders live in `~/.moxie/instructions.md` — a plain-English list of what it should do each day. **Edit it**; that file *is* the agent.
3. **The offline brain** (no key, no cloud) — run a local model instead: install [Ollama](https://ollama.com), `ollama pull llama3.1`, and set `MOXIE_MODEL=ollama:llama3.1`. Same instructions, same guardrails, zero cloud calls.
4. **The Telegram channel** (optional) — `moxie telegram` runs a bot you can text like a PA, plus a daily loop that re-scans and messages you *only* when there's something new to decide. Decisions are remembered — skip something once and Moxie won't nag you about it for 60 days.

```bash
# .env: TELEGRAM_BOT_TOKEN from @BotFather, then pair:
moxie telegram        # message your bot; it replies with your chat id
# put MOXIE_TELEGRAM_CHAT_ID=<that id> in .env, restart, done
```

Channel security (borrowed from OpenClaw's design): the bot is **paired to exactly one chat** and ignores everyone else; approvals are two-step (`/approve 2`, then `YES`); the brain never executes anything — every action still passes the Trust Vault; and sensitive setup (keys, bank links) only ever happens on your computer, never over chat.

---

## Privacy & security

- **Local-first.** Your receipts, transactions, and audit log live on your machine — encrypted at rest once you run `moxie encrypt on`.
- **Bring your own key.** Moxie uses *your* LLM API key, or a **local/offline model** (Ollama) + **local OCR** (Tesseract) so receipt images never touch a cloud service. `moxie secret set` keeps keys in the OS keychain instead of a file.
- **Least privilege.** Bank access is read-only AIS via a provider *you* choose and own; Moxie never moves money — it's hard-denied in policy.
- **Tamper-evident.** The audit log is hash-chained — any edit to past entries fails `moxie verify`.

Security is the precondition for everything else here — see [SECURITY.md](SECURITY.md).

---

## Built on the OpenClaw / Hermes ecosystem

Moxie deliberately fits the world it came from, so the plumbing is familiar and only the moat is new:

- **Language & install** — Python (Hermes is ~82% Python), installed via a one-line `curl … | bash` that prefers [`uv`](https://github.com/astral-sh/uv), exactly like Hermes.
- **Skills** — the same `SKILL.md` convention used by OpenClaw and the [agentskills.io](https://agentskills.io) standard (they live in `moxie/seed_skills/` and ship in the package), so skills stay portable and shareable (think ClawHub, but for money-actions).
- **Familiar CLI** — `moxie doctor` and friends echo `hermes doctor` / `openclaw` so anyone from that world feels at home.
- **Sandboxing** — action execution is designed to run sandboxed (Docker by default, as OpenClaw does for untrusted sessions).

What's *not* borrowed is the whole point: the **Trust Vault** (consent-first, tamper-evident) and the money-action layer are ours.

## Contributing

The most valuable contribution is **skills** — encoded know-how for cancelling/disputing with a specific merchant, bank, or service; they genuinely drive how Moxie acts. See [moxie/seed_skills/README.md](moxie/seed_skills/README.md) for the format, [CONTRIBUTING.md](CONTRIBUTING.md) for good first issues, and [`integrations/moxie-bridge/`](integrations/moxie-bridge/SKILL.md) if you want your OpenClaw/Hermes agent to talk to Moxie (look, never touch).

---

## Design

The security model and architecture rationale live in [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) — including why Moxie is standalone rather than a skill inside a general-purpose agent, and exactly what the Trust Vault does and doesn't defend against.

## License

[MIT](LICENSE) — free and open. Use it, fork it, learn from it.
