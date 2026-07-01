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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    name: str
    merchant: str = ""
    action_type: str = ""          # cancel_subscription | dispute_charge | chase_refund | negotiate
    success_rate: float = 0.0
    instructions: str = ""         # the markdown body
    path: str = ""


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
                )
            )
        return self

    def find(self, merchant: str = None, action_type: str = None) -> "list[Skill]":
        out = self.skills
        if merchant:
            out = [s for s in out if s.merchant.lower() == merchant.lower()]
        if action_type:
            out = [s for s in out if s.action_type == action_type]
        return out
