# How Moxie works — the trust boundary

Most AI agents are built around capability: what can it do? Moxie is built around a different question: **what will you let it do when the action can't be undone?**

You can't cleanly un-cancel a subscription or un-send a dispute letter. So Moxie's safety lives *before* the action, not in a promised undo after it. Everything below follows from that one constraint.

## The pipeline

Every proposed action — no exceptions — passes through the Trust Vault before anything executes:

```
DETECT      find issues in transactions (zombie subs, duplicates, missing refunds)
   ↓
POLICY      deny-by-default engine — is this action type even allowed?
   ↓
APPROVAL    explicit human consent, previewed with the exact draft to be sent
   ↓
EXECUTE     draft the cancellation / dispute / refund request
   ↓
AUDIT       append a hash-chained, tamper-evident log entry
```

The orchestrator (`moxie/agent.py`) is deliberately boring: it walks proposed actions and refuses to skip a stage. The interesting decisions live in the three vault modules.

## 1. Policy — deny by default (`moxie/vault/policy.py`)

The policy engine answers with one of three outcomes: `DENY`, `NEEDS_APPROVAL`, or `ALLOW`.

The defaults encode the product's hard lines:

- **Money movement is hard-denied.** `move_money`, `transfer`, `pay_bill`, `trade` are refused outright — not gated behind approval, refused. Acting on money (cancelling, disputing) and *moving* money are different risk classes; the latter is a licensing and liability minefield Moxie deliberately stays out of.
- **The auto-allow list ships empty.** In v1, every action that does anything requires human approval. Auto-allow exists as a mechanism so users can *later* opt low-risk action types out of prompting — but that's a choice the user makes, never a default.

## 2. Approval — fail-safe consent (`moxie/vault/approval.py`)

The approval step shows you the full action card — what it is, what it saves, the *exact draft* that will be sent, and a warning that it can't be undone — then asks.

The detail that matters most is one line:

```python
if not sys.stdin.isatty():
    return False   # no human present means no consent
```

If Moxie is running unattended — a cron job, a hijacked script, a pipe — approval is **declined by default**. An agent that can't reach a human must not act. Silence is a "no".

## 3. Audit — tamper-evident by construction (`moxie/vault/audit.py`)

Every event (scans, policy decisions, approvals, skips, executions) is appended to a log where each entry embeds the SHA-256 hash of the previous one:

```
hash_n = SHA256(prev_hash + timestamp + event + data)
```

Editing or deleting *any* past entry breaks the chain for every entry after it, and `moxie verify` catches it and names the first bad entry. This matters because an agent acting on your money is only trustworthy if the record of what it did is more trustworthy than the agent itself. You don't have to trust Moxie's memory — you can check the math.

## Threat model (honest version)

| If this is compromised… | …the damage is bounded by |
|---|---|
| The LLM (prompt injection, bad output) | Policy denies money movement; every one-way action still needs your approval with the draft shown |
| The machine running Moxie unattended | `isatty()` fail-safe: no human, no consent |
| The audit log (tampering after the fact) | Hash chain — `moxie verify` fails loudly |
| A chat channel (future WhatsApp/Telegram bridge) | Channels can ask and notify; sensitive setup (keys, account links) lives only in the local dashboard, never over chat |
| Moxie itself (bug, malicious fork) | Local-first + open source: no server of ours holds your data, and every line is readable |

What the model does **not** defend against: a fully compromised local machine with an interactive user session. That's true of every local tool, including your banking browser tab.

## Why standalone, not a skill inside a bigger agent?

The features could ship as an OpenClaw/Hermes skill — those platforms already have the agent loop, channels, and scheduling. What can't ship as a skill is the *boundary*: a skill's approval gates and audit log are only as trustworthy as the host executing them, and general-purpose agent hosts have had real, large-scale compromises. Moxie owns its execution boundary; the planned integration is a thin bridge skill where the host can *talk to* Moxie but never holds the keys or bypasses the Vault.

## Design constraints

- **Stdlib-only core.** The vault (policy, approval, audit) has zero third-party dependencies — nothing to supply-chain-attack, and the whole trust layer is a few hundred readable lines.
- **Local-first.** Receipts, transactions, and the audit log live in `~/.moxie` on your machine.
- **Bring your own key.** Your LLM API key or a fully local model — Moxie has no cloud, so there's nothing to trust but the code.
- **Dry-run scaffold.** Execution currently drafts (dry-run) rather than sends; Plaid, OCR, and email ingestion are stubbed with clear TODOs. See [SECURITY.md](../SECURITY.md) for what must be done before real financial data.
