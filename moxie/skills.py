"""The community skill library — encoded know-how for acting with a given provider.

Skills follow the same convention as the OpenClaw / Hermes ecosystem (the
agentskills.io `SKILL.md` standard): one folder per skill, each containing a
`SKILL.md` with YAML-ish frontmatter + a markdown body of instructions:

    skills/
      cancel-examplegym/
        SKILL.md

This parser is dependency-free (no PyYAML needed) so the core stays stdlib-only.
The library is the compounding asset — an open, growing set of "how to cancel
with X / dispute with Y", each carrying its own success rate.

Frontmatter keys a skill can set (all optional beyond name):
  merchant       exact merchant name, or "*" for bank-route skills
  action_type    cancel_subscription | dispute_charge | chase_refund | negotiate
  channel        email | deeplink | browser  — how to act for this merchant
  url            the exact page (deeplink), email: the verified support address
Body blocks: numbered steps (human guidance), ```moxie-steps``` (browser
verbs), ```moxie-draft``` (an email template with {merchant} placeholders).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    merchant: str = ""
    action_type: str = ""          # cancel_subscription | dispute_charge | chase_refund | negotiate
    success_rate: float = 0.0
    instructions: str = ""         # the markdown body
    path: str = ""
    channel: str = ""              # email | deeplink | browser — how to act for this merchant
    url: str = ""                  # deep-link target (channel: deeplink)
    email: str = ""                # verified support address (channel: email)


def _parse_frontmatter(text: str):
    """Minimal front-matter parser: a block between leading '---' lines.

    Returns (meta: dict, body: str). Supports `key: value` lines. No external deps.
    """
    meta, body = {}, text
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is not None:
            for raw in lines[1:end]:
                if not raw.strip() or ":" not in raw:
                    continue
                key, _, value = raw.partition(":")
                meta[key.strip()] = value.strip().strip('"').strip("'")
            body = "\n".join(lines[end + 1 :]).strip()
    return meta, body


class SkillRegistry:
    def __init__(self):
        self.skills: "list[Skill]" = []

    def load_dir(self, directory: "Path | str") -> "SkillRegistry":
        """Load every skills/<name>/SKILL.md under `directory`."""
        root = Path(directory)
        if not root.exists():
            return self
        for skill_md in sorted(root.glob("*/SKILL.md")):
            meta, body = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            name = meta.get("name") or skill_md.parent.name
            try:
                rate = float(meta.get("success_rate", 0) or 0)
            except ValueError:
                rate = 0.0
            self.skills.append(
                Skill(
                    name=name,
                    merchant=meta.get("merchant", ""),
                    action_type=meta.get("action_type", ""),
                    success_rate=rate,
                    instructions=body,
                    path=str(skill_md),
                    channel=meta.get("channel", ""),
                    url=meta.get("url", ""),
                    email=meta.get("email", ""),
                )
            )
        return self

    def find(self, merchant: str = None, action_type: str = None) -> "list[Skill]":
        """Best matches first: exact merchant beats the `merchant: "*"`
        wildcards (bank-route skills like 'dispute via NatWest')."""
        out = self.skills
        if action_type:
            out = [s for s in out if s.action_type == action_type]
        if merchant:
            exact = [s for s in out if s.merchant.lower() == merchant.lower()]
            wild = [s for s in out if s.merchant == "*"]
            out = exact + wild
        return out


_DRAFT_BLOCK = re.compile(r"```moxie-draft\s*\n(.*?)```", re.S)


class _Defaults(dict):
    def __missing__(self, key):          # unknown {placeholder} stays literal
        return "{" + key + "}"


def draft_template(skill) -> "str | None":
    """A ```moxie-draft``` block in the skill body overrides the default
    draft. Placeholders: {merchant} {amount} {currency} {date}."""
    m = _DRAFT_BLOCK.search(skill.instructions or "")
    return m.group(1).strip() if m else None


def render_draft(skill, action) -> "str | None":
    template = draft_template(skill)
    if not template:
        return None
    return template.format_map(_Defaults(
        merchant=action.merchant,
        amount=f"{action.amount:.2f}",
        currency=getattr(action, "currency", "£"),
        date="",
    ))


def default_registry(config) -> SkillRegistry:
    """Skills from the usual places: MOXIE_SKILLS, ./skills, and the user's
    workspace (~/.moxie/workspace/skills) — same lookup the CLI uses."""
    import os
    reg = SkillRegistry()
    dirs = [
        os.environ.get("MOXIE_SKILLS") or "skills",
        str(config.home / "workspace" / "skills"),
    ]
    for d in dirs:
        if d and Path(d).exists():
            reg.load_dir(d)
    return reg
