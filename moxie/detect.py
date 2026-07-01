"""Detectors: turn transactions into proposed actions. Stdlib only.

Tuned for real bank data, not just clean demos:
- subscriptions: monthly *cadence* (≈1 charge/month), tolerant of small price
  changes (Netflix's £9.99 → £10.99 still counts) — but Tesco 8×/month doesn't
- duplicates: same merchant/amount/day, ignores small amounts (two coffees is
  not fraud) and skips pairs already netted out by a matching refund
- credits and refunds (negative amounts) are never flagged

These stay intentionally simple and explainable — Moxie should never act on a
hunch it can't show you. Each detector returns ProposedActions with a
plain-English rationale and a ready-to-review draft.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from statistics import median

from .models import ProposedAction

DUPLICATE_MIN_AMOUNT = 5.00      # below this, same-day repeats are usually just life
SUBSCRIPTION_MIN_MONTHS = 2      # months of recurrence before we call it a subscription
SUBSCRIPTION_MAX_PER_MONTH = 1.5 # more often than this looks like shopping, not a sub
PRICE_TOLERANCE = 0.20           # subscriptions may drift ±20% (price rises, VAT)
REFUND_WINDOW_DAYS = 7           # a matching credit within this window nets a duplicate


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower()) or "merchant"


def _cur(txns) -> str:
    return getattr(txns[0], "currency", "$") if txns else "$"


def cancel_draft(merchant: str) -> str:
    return (
        f"To: support@{_slug(merchant)}.com\n"
        "Subject: Cancel my subscription\n\n"
        "Hello,\n\n"
        f"Please cancel my {merchant} subscription effective immediately and confirm in "
        "writing. I do not authorize any further charges.\n\n"
        "Thank you."
    )


def dispute_draft(merchant: str, amount: float, date: str, cur: str = "$") -> str:
    return (
        f"To: support@{_slug(merchant)}.com\n"
        "Subject: Duplicate charge dispute\n\n"
        "Hello,\n\n"
        f"I was charged {cur}{amount:.2f} more than once at {merchant} on {date}. "
        "Please reverse the duplicate charge(s); my receipt is attached as proof.\n\n"
        "Thank you."
    )


def _has_matching_refund(transactions, merchant: str, amount: float, date: str) -> bool:
    """A credit for the same amount at the same merchant within the window."""
    try:
        day = dt.date.fromisoformat(date)
    except ValueError:
        return False
    for t in transactions:
        if t.merchant != merchant or t.amount >= 0:
            continue
        if abs(abs(t.amount) - amount) > 0.01:
            continue
        try:
            delta = (dt.date.fromisoformat(t.date) - day).days
        except ValueError:
            continue
        if 0 <= delta <= REFUND_WINDOW_DAYS:
            return True
    return False


def find_duplicate_charges(transactions) -> "list[ProposedAction]":
    groups = defaultdict(list)
    for t in transactions:
        if t.amount > 0:
            groups[(t.merchant, round(t.amount, 2), t.date)].append(t)

    actions = []
    for (merchant, amount, date), same_day in sorted(groups.items()):
        if len(same_day) < 2 or amount < DUPLICATE_MIN_AMOUNT:
            continue
        if _has_matching_refund(transactions, merchant, amount, date):
            continue  # already refunded — nothing to chase
        cur = _cur(same_day)
        dupes = len(same_day) - 1
        actions.append(
            ProposedAction(
                kind="dispute_charge",
                merchant=merchant,
                description=(
                    f"{len(same_day)} identical {cur}{amount:.2f} charges at {merchant} "
                    f"on {date} — likely {dupes} duplicate(s)."
                ),
                rationale="Same merchant, amount, and date; no matching refund found.",
                amount=amount,
                est_savings=round(amount * dupes, 2),
                draft=dispute_draft(merchant, amount, date, cur),
                currency=cur,
            )
        )
    return actions


def find_zombie_subscriptions(transactions) -> "list[ProposedAction]":
    by_merchant = defaultdict(list)
    for t in transactions:
        if t.amount > 0:
            by_merchant[t.merchant].append(t)

    actions = []
    for merchant, txns in sorted(by_merchant.items()):
        months = sorted({t.date[:7] for t in txns})
        if len(months) < SUBSCRIPTION_MIN_MONTHS:
            continue
        # Cadence: roughly one charge per month, not weekly shopping.
        if len(txns) / len(months) > SUBSCRIPTION_MAX_PER_MONTH:
            continue
        # Amount consistency: allow price drift, reject wildly varying spend.
        amounts = [t.amount for t in txns]
        mid = median(amounts)
        if mid <= 0 or (max(amounts) - min(amounts)) > max(PRICE_TOLERANCE * mid, 1.0):
            continue
        cur = _cur(txns)
        actions.append(
            ProposedAction(
                kind="cancel_subscription",
                merchant=merchant,
                description=(
                    f"Recurring {cur}{mid:.2f}/mo at {merchant} across {len(months)} "
                    "months — cancel if unused."
                ),
                rationale=f"Charged about monthly ({', '.join(months)}).",
                amount=mid,
                est_savings=round(mid * 12, 2),
                draft=cancel_draft(merchant),
                currency=cur,
            )
        )
    return actions


def detect_all(transactions) -> "list[ProposedAction]":
    return find_duplicate_charges(transactions) + find_zombie_subscriptions(transactions)
