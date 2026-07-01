"""The Trust Vault — nothing acts without passing through here.

Three pieces:
  * AuditLog       — append-only, hash-chained, tamper-evident record of everything.
  * Policy         — deny-by-default rules (money movement is hard-denied in v1).
  * request_approval — explicit, fail-safe human consent for one-way actions.
"""
from .approval import request_approval
from .audit import AuditLog
from .policy import ALLOW, DENY, NEEDS_APPROVAL, Decision, Policy

__all__ = [
    "AuditLog",
    "Policy",
    "Decision",
    "request_approval",
    "ALLOW",
    "NEEDS_APPROVAL",
    "DENY",
]
