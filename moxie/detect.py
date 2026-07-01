"""Detectors: turn transactions into proposed actions. Stdlib only.

These are intentionally simple and explainable — Moxie should never act on a
hunch it can't show you. Each detector returns ProposedActions with a plain-English
rationale and a ready-to-review draft.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .models import ProposedAction


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower()) or "merchant"


def cancel_draft(merchant: str) -> str:
    return (
        f"To: support@{_slug(merchant)}.com\n"
        "Subject: Cancel my subscription\n\n"
        "Hello,\n\n"
        f"Please cancel my {merchant} subscription effective immediately and confirm in "
        "writing. I do not authorize any further charges.\n\n"
        "Thank you."
    )


def dispute_draft(merchant: str, amount: float, date: str) -> str:
    return (
        f"To: support@{_slug(merchant)}.com\n"
        "Subject: Duplicate charge dispute\n\n"
        "Hello,\n\n"
        f"I was charged ${amount:.2f} more than once at {merchant} on {date}. "
        "Please reverse the duplicate charge(s); my receipt is attached as proof.\n\n"
        "Thank you."
    )


def find_duplicate_charges(transactions) -> "list[ProposedAction]":
    groups = defaultdict(list)
    for t in transactions:
        groups[(t.merchant, round(t.amount, 2))].append(t)

    actions = []
    for (merchant, amount), txns in groups.items():
        by_date = defaultdict(list)
        for t in txns:
            by_date[t.date].append(t)
        for date, same_day in by_date.items():
            if len(same_day) >= 2 and amount > 0:
                dupes = len(same_day) - 1
                actions.append(
                    ProposedAction(
                        kind="dispute_charge",
                        merchant=merchant,
                        description=f"{len(same_day)} identical ${amount:.2f} charges at {merchant} on {date} — likely {dupes} duplicate(s).",
                        rationale="Same merchant, amount, and date.",
                        amount=amount,
                        est_savings=round(amount * dupes, 2),
                        draft=dispute_draft(merchant, amount, date),
                    )
                )
    return actions


def find_zombie_subscriptions(transactions) -> "list[ProposedAction]":
    groups = defaultdict(list)
    for t in transactions:
        groups[(t.merchant, round(t.amount, 2))].append(t)

    actions = []
    for (merchant, amount), txns in groups.items():
        months = sorted({t.date[:7] for t in txns})
        if len(months) >= 2 and amount > 0:
            actions.append(
                ProposedAction(
                    kind="cancel_subscription",
                    merchant=merchant,
                    description=f"Recurring ${amount:.2f}/mo at {merchant} across {len(months)} months — cancel if unused.",
                    rationale=f"Charged every month ({', '.join(months)}).",
                    amount=amount,
                    est_savings=round(amount * 12, 2),
                    draft=cancel_draft(merchant),
                )
            )
    return actions


def detect_all(transactions) -> "list[ProposedAction]":
    return find_duplicate_charges(transactions) + find_zombie_subscriptions(transactions)
