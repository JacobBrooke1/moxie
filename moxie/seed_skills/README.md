# The skill library — Moxie's compounding moat

Every folder here is one piece of encoded know-how: *how to cancel with X,
how to dispute with Y*. Skills follow the `SKILL.md` convention used across
the OpenClaw / Hermes ecosystem (agentskills.io), so they're portable.

When Moxie proposes an action for a merchant with a skill, the skill **shapes
the proposal before you see it**: the right channel (email / deep-link /
browser), the verified address or exact cancel URL, the steps, and the
escalation path. What you approve is what runs.

## Anatomy

```markdown
---
name: Cancel ExampleGym membership
merchant: ExampleGym          # exact merchant name, or "*" for bank-route advice
action_type: cancel_subscription   # | dispute_charge | chase_refund | negotiate
channel: deeplink             # email | deeplink | browser (how to act)
url: https://examplegym.com/account/cancel     # deeplink target
email: member-services@examplegym.com          # verified support address
---

# Title

Why this route works, traps to expect.

## Steps                      <- numbered list = human guidance (deeplink)

1. Log in with the account email.
2. Membership → Cancel; decline the retention offer.

```moxie-steps                 <- fenced block = machine verbs (browser tier)
goto https://examplegym.com/login
fill #email={account_email}
pause complete 2FA
click text=Cancel membership
```

```moxie-draft                 <- fenced block = email template override
To: member-services@examplegym.com
Subject: Cancel my membership

Please cancel my {merchant} membership and confirm in writing.
```
```

All frontmatter beyond `name` is optional. `{merchant}`, `{amount}`,
`{currency}` are the template placeholders.

## The two skill classes

- **Route skills** (exact `merchant:`) — steer delivery: channel, URL, address,
  draft. One merchant each.
- **Advice skills** (`merchant: "*"`) — bank-route playbooks (e.g. *dispute any
  charge via NatWest*). They add their escalation steps to the proposal's
  rationale but never change how it's delivered.

## Contributing

First-hand experience is the whole value. If you've actually cancelled or
disputed with a merchant:

1. Copy any folder here, rename it, fill in what you know.
2. `moxie skills` should list it; `pytest -q` must stay green.
3. Open a PR (there's a "New merchant skill" issue template too).

Never include real account numbers, full card numbers, or anything from
someone else's account. Success rates aren't guessed — Moxie tracks usage
locally (`moxie skills` shows used/sent/failed counts on your machine).
