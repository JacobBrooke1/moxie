"""Tests for the Trust Vault — the part that must never be wrong.

Run with:  pytest -q     (from the repo root)
"""
import json

from moxie.models import ProposedAction
from moxie.vault import DENY, NEEDS_APPROVAL, AuditLog, Policy


def test_audit_chain_intact(tmp_path):
    log = AuditLog(tmp_path / "audit.log")
    log.append("scan", {"found": 3})
    log.append("action_executed", {"merchant": "FitClub"})
    ok, bad = log.verify()
    assert ok and bad is None


def test_audit_detects_tampering(tmp_path):
    path = tmp_path / "audit.log"
    log = AuditLog(path)
    log.append("scan", {"found": 3})
    log.append("action_executed", {"merchant": "FitClub"})

    # Tamper with a past entry's data.
    lines = path.read_text().splitlines()
    first = json.loads(lines[0])
    first["data"] = {"found": 999}
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n")

    ok, bad = log.verify()
    assert not ok
    assert bad == 0


def test_policy_denies_money_movement():
    decision = Policy().evaluate(
        ProposedAction(kind="move_money", merchant="X", description="send $100")
    )
    assert decision.outcome == DENY


def test_policy_requires_approval_for_actions():
    decision = Policy().evaluate(
        ProposedAction(kind="cancel_subscription", merchant="X", description="cancel")
    )
    assert decision.outcome == NEEDS_APPROVAL
