# Contributing to Moxie

Thanks for helping build a money agent people can actually trust.

## The most valuable contribution: skills

A **skill** is encoded know-how for getting one thing done with one provider — how to cancel a specific gym, dispute a charge with a specific bank, appeal a specific insurer. The library of skills is what makes Moxie genuinely useful (and is something no closed competitor can crowdsource).

A skill is a `SKILL.md` file in its own folder under [`skills/`](skills/), following the
same convention as OpenClaw and the [agentskills.io](https://agentskills.io) standard —
YAML-ish frontmatter plus a markdown body:

```
skills/
  cancel-examplegym/
    SKILL.md
```

```markdown
---
name: Cancel ExampleGym membership
merchant: ExampleGym
action_type: cancel_subscription   # cancel_subscription | dispute_charge | chase_refund | negotiate
success_rate: 0.82                 # your honest estimate, 0–1
---

# Cancel ExampleGym membership

## Steps
1. Locate the account email used at sign-up.
2. Email member-services@ with the account email + last 4 of the card.
3. Decline any retention offer; request written confirmation.
4. Escalate to a card chargeback only if no confirmation in 14 days.
```

The loader is dependency-free (no PyYAML), so adding a skill never adds a dependency.

**Rules for skills**
- Only lawful, consumer-protective actions (cancel, dispute, refund, negotiate). No deception, no impersonation, no accessing accounts that aren't the user's own.
- Never include real personal data or credentials.
- Keep `success_rate` honest — it's how Moxie ranks approaches.

## Code

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

- Core must keep running on the **standard library alone** (the demo has zero required third-party deps). Put optional integrations behind extras and import them lazily.
- **Nothing executes without passing the Trust Vault** (`moxie/vault/`). Any new action type must go through policy → approval → audit.
- Default to **fail-safe**: if unsure, don't act — ask.

## Ground rules

- Be honest about what the agent can and can't do (overstating AI capability is literally what got a competitor fined).
- Security issues: see [SECURITY.md](SECURITY.md) — please report privately.
