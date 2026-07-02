"""Detectors: turn transactions into proposed actions. Stdlib only.

Tuned for real bank data, not just clean demos:
- subscriptions: monthly *cadence* (≈1 charge/month), tolerant of small price
  changes (Netflix's £9.99 → £10.99 still counts) — but Tesco 8×/month doesn't
- duplicates: same merchant/amount/day, ignores small amounts (two coffees is
  not fraud) and skips pairs already netted out by a matching refund
- credits and refunds (negative amounts) are never flagged
- plus (Phase 4): brand-new subscriptions (the trial that stuck), price-hike
  renewals, two-of-a-kind services, bank fees worth contesting, FX fees, and
  partial refunds worth chasing

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

NEW_SUB_DAYS = 45                # a sub that started this recently = "trial that stuck?"
PRICE_HIKE_MIN_PCT = 0.05        # renewals must rise ≥5% ...
PRICE_HIKE_MIN_ABS = 1.00        # ... and ≥ this, before we call it a hike
PARTIAL_REFUND_WINDOW = 21       # days to pair a partial credit with its charge
PARTIAL_REFUND_MIN_GAP = 10.00   # only chase meaningful shortfalls

FEE_KEYWORDS = re.compile(
    r"\b(overdraft|unarranged|late payment|returned (d\.?d\.?|direct debit)|"
    r"unpaid (item|transaction)|account fee|maintenance fee|monthly fee|"
    r"interest charged)\b", re.I)
FX_KEYWORDS = re.compile(
    r"\b(non[- ]sterling|foreign (transaction|exchange)|fx fee|"
    r"currency conversion|non[- ]gbp)\b", re.I)

# Two of these at once is usually one too many. Keyword → category.
SERVICE_CATEGORIES = {
    "streaming": ("netflix", "disney", "prime video", "primevideo", "hulu",
                  "hbo", "paramount", "peacock", "apple tv", "appletv",
                  "now tv", "nowtv", "streammax", "crunchyroll"),
    "music": ("spotify", "apple music", "tidal", "deezer", "youtube music",
              "amazon music"),
    "cloud storage": ("dropbox", "google one", "icloud", "onedrive", "pcloud"),
    "gym": ("gym", "fitness", "fitclub", "peloton", "david lloyd", "nuffield"),
}


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


def recurring_monthly(transactions) -> "list[dict]":
    """Recurring ~monthly charges (subscriptions, standing-order bills):
    merchant, typical monthly amount, the months seen, and each raw charge.
    Shared by the zombie-subscription detector and the money snapshot."""
    by_merchant = defaultdict(list)
    for t in transactions:
        if t.amount > 0:
            by_merchant[t.merchant].append(t)

    out = []
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
        out.append({
            "merchant": merchant,
            "monthly": round(mid, 2),
            "months": months,
            "currency": _cur(txns),
            "transactions": sorted(txns, key=lambda t: t.date),
        })
    return out


def _latest_date(transactions) -> "dt.date | None":
    """The data's own 'now' — detectors anchor to it, not the wall clock,
    so an old statement gives the same (testable) answers."""
    dates = []
    for t in transactions:
        try:
            dates.append(dt.date.fromisoformat(t.date))
        except (ValueError, TypeError):
            continue
    return max(dates) if dates else None


def _is_new_sub(sub, anchor: "dt.date | None") -> bool:
    if anchor is None:
        return False
    try:
        first = dt.date.fromisoformat(sub["transactions"][0].date)
    except (ValueError, TypeError):
        return False
    return (anchor - first).days <= NEW_SUB_DAYS


def find_zombie_subscriptions(transactions) -> "list[ProposedAction]":
    anchor = _latest_date(transactions)
    actions = []
    for sub in recurring_monthly(transactions):
        if _is_new_sub(sub, anchor):
            continue  # brand-new subs get better copy from find_new_subscriptions
        merchant, mid, months, cur = (sub["merchant"], sub["monthly"],
                                      sub["months"], sub["currency"])
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


def find_new_subscriptions(transactions) -> "list[ProposedAction]":
    """The free trial that quietly became a paid sub: recurring charges whose
    first payment is recent. Same cancel action, honest copy."""
    anchor = _latest_date(transactions)
    actions = []
    for sub in recurring_monthly(transactions):
        if not _is_new_sub(sub, anchor):
            continue
        merchant, mid, cur = sub["merchant"], sub["monthly"], sub["currency"]
        first = sub["transactions"][0].date
        actions.append(
            ProposedAction(
                kind="cancel_subscription",
                merchant=merchant,
                description=(
                    f"New recurring {cur}{mid:.2f}/mo at {merchant} (first charge "
                    f"{first}) — a trial that stuck? Cancel if not deliberate."
                ),
                rationale=(f"First payment {first}, already {len(sub['months'])} "
                           "charge(s) — new subscriptions are the easiest to forget."),
                amount=mid,
                est_savings=round(mid * 12, 2),
                draft=cancel_draft(merchant),
                currency=cur,
            )
        )
    return actions


def find_price_hikes(transactions) -> "list[ProposedAction]":
    """Same subscription, quietly dearer: a stable earlier price, then a
    latest charge ≥5% and ≥£1 above it. Runs its own cadence check (not
    recurring_monthly) so BIG hikes — which break the ±20% consistency
    filter — are exactly the ones it still catches."""
    by_merchant = defaultdict(list)
    for t in transactions:
        if t.amount > 0:
            by_merchant[t.merchant].append(t)

    actions = []
    for merchant, txns in sorted(by_merchant.items()):
        txns.sort(key=lambda t: t.date)
        months = sorted({t.date[:7] for t in txns})
        if len(months) < 3 or len(txns) / len(months) > SUBSCRIPTION_MAX_PER_MONTH:
            continue  # need monthly cadence and history to call it a renewal
        earlier = [t.amount for t in txns[:-1]]
        old, new = median(earlier), txns[-1].amount
        # the earlier price must have been STABLE — otherwise it's shopping
        if max(earlier) - min(earlier) > max(PRICE_TOLERANCE * old, 1.0):
            continue
        if new - old < max(PRICE_HIKE_MIN_ABS, old * PRICE_HIKE_MIN_PCT):
            continue
        cur = _cur(txns)
        actions.append(
            ProposedAction(
                kind="negotiate",
                merchant=merchant,
                description=(
                    f"{merchant} renewal went {cur}{old:.2f} → {cur}{new:.2f}/mo "
                    f"(+{(new - old) / old:.0%}) — ask for the old price or cancel."
                ),
                rationale=(f"Earlier charges centred on {cur}{old:.2f}; the latest "
                           f"({txns[-1].date}) is {cur}{new:.2f}."),
                amount=new,
                est_savings=round((new - old) * 12, 2),
                draft=(
                    f"To: support@{_slug(merchant)}.com\n"
                    "Subject: Price increase on my account\n\n"
                    "Hello,\n\n"
                    f"My {merchant} subscription has gone from {cur}{old:.2f} to "
                    f"{cur}{new:.2f} per month. I'd like to stay at my previous "
                    "price; otherwise please cancel my subscription and confirm "
                    "in writing.\n\nThank you."
                ),
                currency=cur,
            )
        )
    return actions


def find_duplicate_services(transactions) -> "list[ProposedAction]":
    """Two live subscriptions doing the same job (two streaming, two music…).
    Proposes cancelling the newer one; the rationale names both."""
    subs = recurring_monthly(transactions)
    by_cat = defaultdict(list)
    for sub in subs:
        name = sub["merchant"].lower()
        for cat, keywords in SERVICE_CATEGORIES.items():
            if any(k in name for k in keywords):
                by_cat[cat].append(sub)
                break

    actions = []
    for cat, members in sorted(by_cat.items()):
        if len(members) < 2:
            continue
        members.sort(key=lambda s: s["transactions"][0].date)  # oldest first
        keeper, newer = members[0], members[-1]
        cur = newer["currency"]
        names = ", ".join(m["merchant"] for m in members)
        actions.append(
            ProposedAction(
                kind="duplicate_service",
                merchant=newer["merchant"],
                description=(
                    f"You pay for {len(members)} {cat} services ({names}) — "
                    f"dropping {newer['merchant']} saves {cur}{newer['monthly']:.2f}/mo."
                ),
                rationale=(f"All charge monthly; {keeper['merchant']} is the "
                           f"longest-standing. Rotating services month-to-month "
                           "is the cheap way to keep both."),
                amount=newer["monthly"],
                est_savings=round(newer["monthly"] * 12, 2),
                draft=cancel_draft(newer["merchant"]),
                currency=cur,
            )
        )
    return actions


def _fee_actions(transactions, pattern, merchant_label, subject, ask,
                 kind="negotiate") -> "list[ProposedAction]":
    hits = [t for t in transactions
            if t.amount > 0 and (pattern.search(t.merchant or "")
                                 or pattern.search(t.description or ""))]
    if not hits:
        return []
    cur = _cur(hits)
    total = round(sum(t.amount for t in hits), 2)
    dates = ", ".join(sorted({t.date for t in hits})[:6])
    return [ProposedAction(
        kind=kind,
        merchant=merchant_label,
        description=f"{cur}{total:.2f} of {merchant_label.lower()} across "
                    f"{len(hits)} charge(s) — {ask}",
        rationale=f"Charged on {dates}.",
        amount=total,
        est_savings=total,   # what's already been paid; honest, not annualised
        draft=(
            "To: your bank (use in-app secure message)\n"
            f"Subject: {subject}\n\n"
            "Hello,\n\n"
            f"I've been charged {cur}{total:.2f} in {merchant_label.lower()} "
            f"({dates}). As a long-standing customer I'd like these reviewed "
            "and refunded as a gesture of goodwill, and to hear my options for "
            "avoiding them in future.\n\nThank you."
        ),
        currency=cur,
    )]


def find_bank_fees(transactions) -> "list[ProposedAction]":
    """Overdraft / late / unpaid-item fees — banks routinely waive these when
    asked. The draft is the ask."""
    return _fee_actions(
        transactions, FEE_KEYWORDS, "Bank fees",
        "Fee refund request", "banks often waive these if you ask.")


def find_fx_fees(transactions) -> "list[ProposedAction]":
    """Non-sterling transaction fees — the fix is a no-FX-fee card, and the
    figure shows what it's worth."""
    return _fee_actions(
        transactions, FX_KEYWORDS, "Non-sterling fees",
        "Foreign transaction fees",
        "a no-FX-fee card would have cost £0.")


def find_partial_refunds(transactions) -> "list[ProposedAction]":
    """Paid X, refunded only Y: worth asking where the rest went. Conservative:
    the gap must be ≥£10 and the credit must not exactly match another charge."""
    charge_amounts = {round(t.amount, 2) for t in transactions if t.amount > 0}
    actions, seen = [], set()
    for t in transactions:
        if t.amount <= 0 or t.merchant in seen:
            continue
        try:
            day = dt.date.fromisoformat(t.date)
        except ValueError:
            continue
        for c in transactions:
            if c.merchant != t.merchant or c.amount >= 0:
                continue
            refund = round(-c.amount, 2)
            if refund in charge_amounts:
                continue  # full refund of some other charge, not a shortfall
            gap = round(t.amount - refund, 2)
            if gap < PARTIAL_REFUND_MIN_GAP:
                continue
            try:
                delta = (dt.date.fromisoformat(c.date) - day).days
            except ValueError:
                continue
            if not (0 <= delta <= PARTIAL_REFUND_WINDOW):
                continue
            cur = getattr(t, "currency", "£")
            seen.add(t.merchant)
            actions.append(ProposedAction(
                kind="chase_refund",
                merchant=t.merchant,
                description=(
                    f"{t.merchant} refunded {cur}{refund:.2f} of a {cur}{t.amount:.2f} "
                    f"charge — {cur}{gap:.2f} short. If the whole thing was "
                    "returned, chase the rest."
                ),
                rationale=(f"Charge {t.date}, partial credit {c.date}. If the "
                           "refund was meant to be partial, skip me."),
                amount=gap,
                est_savings=gap,
                draft=(
                    f"To: support@{_slug(t.merchant)}.com\n"
                    "Subject: Refund shortfall\n\n"
                    "Hello,\n\n"
                    f"On {t.date} I was charged {cur}{t.amount:.2f}; on {c.date} "
                    f"you refunded {cur}{refund:.2f}, leaving {cur}{gap:.2f} "
                    "outstanding. Please refund the remainder or explain the "
                    "difference.\n\nThank you."
                ),
                currency=cur,
            ))
            break
    return actions


def detect_all(transactions) -> "list[ProposedAction]":
    return (find_duplicate_charges(transactions)
            + find_zombie_subscriptions(transactions)
            + find_new_subscriptions(transactions)
            + find_price_hikes(transactions)
            + find_duplicate_services(transactions)
            + find_bank_fees(transactions)
            + find_fx_fees(transactions)
            + find_partial_refunds(transactions))
