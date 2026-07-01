"""Deny-by-default policy engine.

The default posture is: nothing executes automatically. Consequential, one-way
actions always require explicit human approval, and money movement is hard-denied
in v1 (it needs licensing we don't have). Stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass

ALLOW = "allow"
NEEDS_APPROVAL = "needs_approval"
DENY = "deny"


@dataclass
class Decision:
    outcome: str   # ALLOW | NEEDS_APPROVAL | DENY
    reason: str


class Policy:
    def __init__(self, config: "dict | None" = None):
        config = config or {}
        self.denied_kinds = set(
            config.get("denied_kinds", ["move_money", "transfer", "pay_bill", "trade"])
        )
        # Intentionally empty by default: in v1 everything that acts needs approval.
        self.auto_allow_kinds = set(config.get("auto_allow_kinds", []))

    def evaluate(self, action) -> Decision:
        if action.kind in self.denied_kinds:
            return Decision(
                DENY,
                f"'{action.kind}' is disabled in v1 — moving money requires licensing (see SECURITY.md).",
            )
        if action.kind in self.auto_allow_kinds:
            return Decision(ALLOW, "Pre-approved low-risk action type.")
        return Decision(
            NEEDS_APPROVAL,
            "Consequential and one-way — requires your explicit approval.",
        )
