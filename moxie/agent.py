"""The orchestrator. Detects problems, then runs each proposed action through the
Trust Vault: policy -> approval -> execute -> audit. Nothing acts without clearing
the Vault. Decisions are remembered so Moxie never nags you twice. Stdlib only.
"""
from __future__ import annotations

import datetime as dt

from .actions import execute_action
from .detect import detect_all
from .vault import ALLOW, DENY, Policy, request_approval

# How long a remembered decision suppresses re-proposing the same finding.
SNOOZE_DAYS = {"skipped": 60, "executed": 90, "denied": 365}


class Agent:
    def __init__(self, config, store, audit, policy=None):
        self.config = config
        self.store = store
        self.audit = audit
        self.policy = policy or Policy(config.data.get("policy"))
        self.last_suppressed = 0

    def scan(self, transactions):
        found = detect_all(transactions)
        today = dt.date.today()
        actions, suppressed = [], 0
        for action in found:
            d = self.store.get_decision(action.merchant, action.kind)
            if d:
                try:
                    age = (today - dt.date.fromisoformat(d["date"])).days
                except (ValueError, TypeError):
                    age = 0
                if age <= SNOOZE_DAYS.get(d["status"], 60):
                    suppressed += 1
                    continue
            actions.append(action)
        self.last_suppressed = suppressed
        self.store.clear_actions()
        for action in actions:
            self.store.save_action(action)
        self.audit.append(
            "scan",
            {"transactions": len(transactions), "found": len(found), "suppressed": suppressed},
        )
        return actions

    # --- the Vault pipeline for a single action ------------------------------
    def _run_one(self, action, approved_or_fn, channel):
        decision = self.policy.evaluate(action)
        self.audit.append(
            "policy_eval",
            {"action": action.id, "kind": action.kind,
             "outcome": decision.outcome, "reason": decision.reason, "channel": channel},
        )

        if decision.outcome == DENY:
            action.status = "denied"
            self.store.save_action(action)
            self.store.save_decision(action.merchant, action.kind, "denied")
            return (action, "denied", decision.reason)

        if decision.outcome == ALLOW:
            approved = True
        elif callable(approved_or_fn):
            approved = approved_or_fn(action)
        else:
            approved = bool(approved_or_fn)

        if not approved:
            action.status = "skipped"
            self.store.save_action(action)
            self.store.save_decision(action.merchant, action.kind, "skipped")
            self.audit.append("action_skipped", {"action": action.id, "channel": channel})
            return (action, "skipped", "you declined")

        result = execute_action(action, dry_run=True)
        action.status = "executed"
        self.store.save_action(action)
        self.store.save_decision(action.merchant, action.kind, "executed")
        self.audit.append(
            "action_executed",
            {"action": action.id, "kind": action.kind, "merchant": action.merchant,
             "dry_run": result["dry_run"], "channel": channel},
        )
        return (action, "executed", "drafted (dry-run)")

    def review(self, approve_fn=None, channel="cli"):
        """Walk proposed actions. Each must pass policy and (if needed) your approval
        before it 'executes' (dry-run in the scaffold). Every step is logged."""
        approve_fn = approve_fn or request_approval
        results = []
        for action in self.store.load_actions():
            if action.status != "proposed":
                continue
            results.append(self._run_one(action, approve_fn, channel))
        return results

    def resolve(self, action_id, approved, channel="telegram"):
        """Approve or skip ONE stored action by id (chat channels use this).
        Same Vault pipeline, same audit trail -- just one action at a time."""
        for action in self.store.load_actions():
            if action.id == action_id and action.status == "proposed":
                return self._run_one(action, approved, channel)
        return None
