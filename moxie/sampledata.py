"""Built-in sample data so `moxie scan` works with zero setup — no bank, no key.

Designed to trigger the detectors: two recurring subscriptions and one duplicate
charge, mixed in with ordinary one-off spending.
"""
from __future__ import annotations

from .models import Receipt, Transaction


def sample_transactions() -> "list[Transaction]":
    return [
        # Recurring subscription (3 months) -> cancel candidate
        Transaction(date="2026-04-02", merchant="FitClub", amount=29.99, description="Gym membership"),
        Transaction(date="2026-05-02", merchant="FitClub", amount=29.99, description="Gym membership"),
        Transaction(date="2026-06-02", merchant="FitClub", amount=29.99, description="Gym membership"),
        # Recurring subscription (2 months) -> cancel candidate
        Transaction(date="2026-05-14", merchant="StreamMax", amount=15.99, description="Streaming"),
        Transaction(date="2026-06-14", merchant="StreamMax", amount=15.99, description="Streaming"),
        # Duplicate charge on the same day -> dispute candidate
        Transaction(date="2026-06-03", merchant="CloudHost", amount=40.00, description="Hosting"),
        Transaction(date="2026-06-03", merchant="CloudHost", amount=40.00, description="Hosting (double charge?)"),
        # Ordinary one-offs -> ignored
        Transaction(date="2026-06-09", merchant="Corner Grocery", amount=63.20, description="Groceries"),
        Transaction(date="2026-06-11", merchant="Daily Coffee", amount=4.75, description="Coffee"),
    ]


def sample_receipts() -> "list[Receipt]":
    return [
        Receipt(merchant="CloudHost", date="2026-06-03", amount=40.00, source="email",
                text="CloudHost invoice #A-2231 — $40.00"),
        Receipt(merchant="Corner Grocery", date="2026-06-09", amount=63.20, source="photo",
                path="receipts/grocery_0609.jpg"),
    ]
