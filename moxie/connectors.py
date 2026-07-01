"""Read-only account connectors. Stdlib CSV import works; Plaid is stubbed."""
from __future__ import annotations

import csv

from .models import Transaction


def import_csv(path: str) -> "list[Transaction]":
    """Import transactions from a CSV with columns: date, merchant, amount, description."""
    txns = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            txns.append(
                Transaction(
                    date=row["date"].strip(),
                    merchant=row["merchant"].strip(),
                    amount=float(row["amount"]),
                    description=(row.get("description") or "").strip(),
                )
            )
    return txns


class PlaidConnector:
    """Read-only bank data via Plaid (bring-your-own Plaid keys).

    Plaid is read-only for our purposes: it returns balances and transactions, the
    user enters credentials in Plaid's own UI (never exposed to the agent), and it
    cannot move money. That read-only boundary is a feature, not a limitation.

    TODO: implement with plaid-python using PLAID_CLIENT_ID / PLAID_SECRET.
    Not implemented in the scaffold.
    """

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Plaid connector is a stub — see moxie/connectors.py and the build spec."
        )
