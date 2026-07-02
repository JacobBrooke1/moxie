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
    amount: float      # spend positive; credits/refunds negative
    description: str = ""
    currency: str = "$"
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
    currency: str = "$"
    evidence_receipt_id: Optional[str] = None
    # proposed | skipped | denied | executed (drafted, dry-run) | sent (live) | failed
    status: str = "proposed"
    channel: str = "email"                # email | deeplink | browser (skill can override)
    reference: str = ""                   # message-id / URL / confirmation once acted
    id: str = field(default_factory=_id)
