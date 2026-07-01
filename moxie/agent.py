"""The orchestrator. Detects problems, then runs each proposed action through the
Trust Vault: policy -> approval -> execute -> audit. Nothing acts without clearing
the Vault. Stdlib only.
"""
from __future__ import annotations

from .actions import execute_action
from .detect import detect_all
from .vault import ALLOW, DENY, Policy, request_approval


class Agent:
    def __init__(self, config, store, audit, policy=None):
        self.config = config
        self.store = store
        self.audit = audit
        self.policy = policy or Policy(config.data.get("policy"))

    def scan(self, transactions):
        actions = detect_all(transactions)
        self.store.clear_actions()
        for action in actions:
            self.store.save_action(action)
        self.audit.append("scan", {"transactions": len(transactions), "found": len(actions)})
        return actions

    def review(self, approve_fn=None):
        """Walk proposed actions. Each must pass policy and (if needed) your approval
        before it 'executes' (dry-run in the scaffold). Every step is logged."""
        approve_fn = approve_fn or request_approval
        results = []
        for action in self.store.load_actions():
            if action.status != "proposed":
                continue

            decision = self.policy.evaluate(action)
            self.audit.append(
                "policy_eval",
                {"action": action.id, "kind": action.kind,
                 "outcome": decision.outcome, "reason": decision.reason},
            )

            if decision.outcome == DENY:
                action.status = "denied"
                self.store.save_action(action)
                results.append((action, "denied", decision.reason))
                continue

            approved = True if decision.outcome == ALLOW else approve_fn(action)
            if not approved:
                action.status = "skipped"
                self.store.save_action(action)
                self.audit.append("action_skipped", {"action": action.id})
                results.append((action, "skipped", "you declined"))
                continue

            result = execute_action(action, dry_run=True)
            action.status = "executed"
            self.store.save_action(action)
            self.audit.append(
                "action_executed",
                {"action": action.id, "kind": action.kind,
                 "merchant": action.merchant, "dry_run": result["dry_run"]},
            )
            results.append((action, "executed", "drafted (dry-run)"))
        return results
