# Launch checklist (the parts only a human can do)

Everything below needs real accounts or publishes something irreversible —
by design, Moxie's maintainer does these, not an agent.

## 1. Publish to PyPI

The release workflow ([.github/workflows/release.yml](../.github/workflows/release.yml))
publishes on any `v*` tag, tests-first. One-time setup:

1. Create a PyPI account → create project `moxie-agent` (or reserve the name
   by first manual upload).
2. PyPI → project → Publishing → **Add a trusted publisher**: repo
   `JacobBrooke1/moxie`, workflow `release.yml`, environment `pypi`.
   (Alternative: create an API token and set it as the `PYPI_API_TOKEN`
   secret, then swap the commented line in the workflow.)
3. Create the `pypi` environment in GitHub → Settings → Environments.
4. Tag and push:

   ```bash
   git tag v0.1.0 && git push origin v0.1.0
   ```

5. Verify: `pip install moxie-agent && moxie doctor`.

## 2. Point install.sh at a domain (optional nicety)

Host `install.sh` somewhere stable (e.g. `get.moxie.sh` or GitHub raw) so the
README one-liner works: `curl -fsSL https://…/install.sh | bash`.

## 3. Turn on the repo's community surface

- GitHub → Settings: enable Issues + Discussions; add topics
  (`ai-agent`, `personal-finance`, `local-first`, `self-hosted`, `privacy`).
- Create ~5 issues from the good-first-issues list in CONTRIBUTING.md and
  label them `good first issue` (the new-skill template feeds this).
- Branch protection on `main`: require the CI check.

## 4. The bridge listing

Publish `integrations/moxie-bridge/SKILL.md` to ClawHub / the agentskills.io
index so OpenClaw/Hermes users discover Moxie from inside their agent.

## 5. Announce

You already have the demo GIF. In rough order of fit:

- **Show HN** — "Show HN: Moxie – open-source money agent that acts only
  with your approval". Lead with the Trust Vault + local-first angle; HN
  will test the honesty claims, which is the brand working.
- **r/selfhosted** — emphasise: no server, no account, your box, Ollama mode.
- **r/UKPersonalFinance** — emphasise findings: zombie subs, fees, price
  hikes on real NatWest data. (Mind rule 6 — talk value, not promo.)
- **OpenClaw / Hermes communities** — the bridge skill is the hook.

## 6. Before inviting real-money use at scale

- Commission the **external security review** (SECURITY.md) — it's the one
  unchecked hardening box.
- Run Moxie on your own NatWest data for a month with `MOXIE_LIVE=true`
  and `MOXIE_EMAIL_OVERRIDE_TO=<you>` first, then live — the build plan's
  own definition of v1.0.
