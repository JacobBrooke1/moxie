"""Plain data models. Stdlib only."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional


def _id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Transaction:
    date: str          # ISO date "YYYY-MM-DD"
    merchant: str
    amount: float
    description: str = ""
    id: str = field(default_factory=_id)


@dataclass
class Receipt:
    merchant: str
    date: str
    amount: float
    source: str = "email"   # "email" | "photo"
    path: str = ""
    text: str = ""
    id: str = field(default_factory=_id)


@dataclass
class ProposedAction:
    """Something Moxie wants to do. It is only ever *proposed* until the
    Trust Vault clears it (policy + your approval)."""
    kind: str               # cancel_subscription | dispute_charge | chase_refund | negotiate | move_money ...
    merchant: str
    description: str
    rationale: str = ""
    amount: float = 0.0
    est_savings: float = 0.0
    draft: str = ""                       # the email/letter we would send
    evidence_receipt_id: Optional[str] = None
    status: str = "proposed"              # proposed | approved | skipped | executed | denied
    id: str = field(default_factory=_id)
