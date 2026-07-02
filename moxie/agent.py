"""The orchestrator. Detects problems, then runs each proposed action through the
Trust Vault: policy -> approval -> execute -> audit. Nothing acts without clearing
the Vault. Decisions are remembered so Moxie never nags you twice. Stdlib only.

Live actions (Phase 1): execution respects MOXIE_LIVE (default false = drafts)
and the kill switch. When a matching SKILL.md exists for the merchant, it picks
the action channel (email / deeplink / browser) and supplies the steps.
"""
from __future__ import annotations

import datetime as dt

from .actions import execute_action
from .detect import detect_all
from .vault import ALLOW, DENY, Policy, request_approval

# How long a remembered decision suppresses re-proposing the same finding.
SNOOZE_DAYS = {"skipped": 60, "executed": 90, "sent": 90, "denied": 365, "failed": 7}


class Agent:
    def __init__(self, config, store, audit, policy=None, skills=None, channels=None):
        self.config = config
        self.store = store
        self.audit = audit
        self.policy = policy or Policy(config.data.get("policy"))
        self.skills = skills          # SkillRegistry or None (lazy default below)
        self.channels = channels or {}  # injectable action channels for tests
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
        # Disputes get their receipt attached as evidence (Phase 3).
        from .receipts import attach_evidence
        attach_evidence(actions, self.store.load_receipts())
        self.store.clear_actions()
        for action in actions:
            self.store.save_action(action)
        self.audit.append(
            "scan",
            {"transactions": len(transactions), "found": len(found), "suppressed": suppressed},
        )
        return actions

    # --- helpers --------------------------------------------------------------
    def _skill_for(self, action):
        """The best-matching SKILL.md for this merchant + action type, if any."""
        if self.skills is None:
            try:
                from .skills import default_registry
                self.skills = default_registry(self.config)
            except Exception:
                self.skills = False  # tried and failed; don't retry every action
        if not self.skills:
            return None
        matches = self.skills.find(merchant=action.merchant, action_type=action.kind)
        return matches[0] if matches else None

    def _receipt_for(self, action):
        if not action.evidence_receipt_id:
            return None
        for r in self.store.load_receipts():
            if r.id == action.evidence_receipt_id:
                return r
        return None

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

        skill = self._skill_for(action)
        result = execute_action(
            action, self.config,
            skill=skill, receipt=self._receipt_for(action), channels=self.channels,
        )

        if result.get("error") and not result.get("sent"):
            action.status = "failed"
            action.reference = ""
            self.store.save_action(action)
            self.store.save_decision(action.merchant, action.kind, "failed")
            self.audit.append(
                "action_failed",
                {"action": action.id, "kind": action.kind, "merchant": action.merchant,
                 "channel_used": result.get("channel"), "error": result["error"][:300],
                 "channel": channel},
            )
            return (action, "failed", result["note"])

        action.status = "sent" if result.get("sent") else "executed"
        action.channel = result.get("channel", action.channel)
        action.reference = result.get("reference", "")
        self.store.save_action(action)
        self.store.save_decision(action.merchant, action.kind, action.status)
        self.audit.append(
            "action_executed",
            {"action": action.id, "kind": action.kind, "merchant": action.merchant,
             "dry_run": result.get("dry_run", True), "sent": result.get("sent", False),
             "channel_used": result.get("channel"), "reference": result.get("reference", ""),
             "channel": channel},
        )
        if result.get("sent"):
            return (action, "sent", result["note"])
        note = result["note"] if result.get("channel") == "deeplink" else "drafted (dry-run)"
        return (action, "executed", note)

    def review(self, approve_fn=None, channel="cli"):
        """Walk proposed actions. Each must pass policy and (if needed) your approval
        before it executes (a real send only when MOXIE_LIVE=true; otherwise a
        draft). Every step is logged."""
        approve_fn = approve_fn or request_approval
        results = []
        for action in self.store.load_actions():
            if action.status != "proposed":
                continue
            results.append(self._run_one(action, approve_fn, channel))
        return results

    def resolve(self, action_id, approved, channel="telegram", edited_draft=None):
        """Approve or skip ONE stored action by id (chat channels use this).
        Same Vault pipeline, same audit trail -- just one action at a time.
        An edited draft replaces the original before anything runs (and is
        what the audit trail's execution entry refers to)."""
        for action in self.store.load_actions():
            if action.id == action_id and action.status == "proposed":
                if edited_draft is not None and edited_draft.strip() and approved:
                    action.draft = edited_draft
                    self.store.save_action(action)
                    self.audit.append("draft_edited",
                                      {"action": action.id, "chars": len(edited_draft),
                                       "channel": channel})
                return self._run_one(action, approved, channel)
        return None
