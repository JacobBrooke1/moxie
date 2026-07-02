"""The money picture: what the brain (and you) actually know about the finances.

Everything here is DERIVED FROM THE DATA ON HAND and labelled as such —
median monthly income, median outgoings, committed recurring spend, what's
been spent so far this month, and what's genuinely left. No vibes.

Honest guardrail (also enforced in the brain's system prompt): Moxie states
figures and trade-offs and lets you decide. It is not a regulated financial
adviser and never gives a confident "yes, buy it" verdict.

Stdlib only.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from statistics import median

from .detect import recurring_monthly


def _month(date_str: str) -> str:
    return (date_str or "")[:7]


def compute_snapshot(transactions, balances=None, today=None) -> dict:
    """Derive the month-level picture from transactions (+ balances if a bank
    is linked). Spend positive / credits negative — Moxie's convention."""
    today = today or dt.date.today()
    this_month = today.isoformat()[:7]

    spend_by_month, credit_by_month = defaultdict(float), defaultdict(float)
    for t in transactions:
        m = _month(t.date)
        if not m:
            continue
        if t.amount > 0:
            spend_by_month[m] += t.amount
        else:
            credit_by_month[m] += -t.amount

    # Medians over COMPLETE months only — the current month is in progress
    # and would drag both numbers down.
    past_spend = [v for m, v in spend_by_month.items() if m != this_month]
    past_credit = [v for m, v in credit_by_month.items() if m != this_month]
    monthly_outgoings = round(median(past_spend), 2) if past_spend else round(
        spend_by_month.get(this_month, 0.0), 2)
    monthly_income = round(median(past_credit), 2) if past_credit else round(
        credit_by_month.get(this_month, 0.0), 2)

    subs = recurring_monthly(transactions)
    committed = round(sum(s["monthly"] for s in subs), 2)

    spent_this_month = round(spend_by_month.get(this_month, 0.0), 2)
    # Committed charges that haven't hit yet this month still have to be paid.
    committed_upcoming = round(sum(
        s["monthly"] for s in subs
        if not any(_month(t.date) == this_month for t in s["transactions"])), 2)

    left_this_month = round(monthly_income - spent_this_month - committed_upcoming, 2)
    disposable = round(monthly_income - monthly_outgoings, 2)  # typical net/mo

    # Category-ish breakdown: bank data rarely has categories, so top merchants.
    by_merchant = defaultdict(float)
    for t in transactions:
        if t.amount > 0 and _month(t.date) == this_month:
            by_merchant[t.merchant] += t.amount
    top = sorted(by_merchant.items(), key=lambda kv: -kv[1])[:5]

    months = sorted(set(spend_by_month) | set(credit_by_month))
    prev_month = months[-2] if len(months) >= 2 and months[-1] == this_month else None
    trend = None
    if prev_month is not None:
        trend = round(spend_by_month.get(this_month, 0.0)
                      - spend_by_month.get(prev_month, 0.0), 2)

    balance = None
    currency = transactions[0].currency if transactions else "£"
    if balances:
        try:
            balance = round(sum(float(b.get("current") or 0) for b in balances), 2)
            currency = balances[0].get("currency", currency)
        except (TypeError, ValueError):
            balance = None

    return {
        "currency": currency,
        "balance": balance,                      # None unless a bank is linked
        "monthly_income": monthly_income,        # median of complete months
        "monthly_outgoings": monthly_outgoings,  # median of complete months
        "committed": committed,                  # recurring subs + bills / mo
        "committed_upcoming": committed_upcoming,  # not yet charged this month
        "spent_this_month": spent_this_month,
        "left_this_month": left_this_month,      # income − spent − upcoming committed
        "disposable": disposable,                # typical income − typical outgoings
        "recurring": [{"merchant": s["merchant"], "monthly": s["monthly"]}
                      for s in subs],
        "top_merchants_this_month": [{"merchant": m, "spent": round(v, 2)}
                                     for m, v in top],
        "spend_trend_vs_last_month": trend,
        "months_of_data": len(months),
        "month": this_month,
    }


def snapshot_from_store(store, today=None) -> dict:
    """Snapshot from whatever the store holds (works for CSV/PDF-only users;
    balance appears once a bank is linked via Phase 2)."""
    balances = None
    raw = store.get_meta("balances")
    if raw:
        try:
            balances = json.loads(raw)
        except json.JSONDecodeError:
            balances = None
    return compute_snapshot(store.load_transactions(), balances, today=today)


def format_snapshot(snap: dict) -> str:
    """Compact, honest text block — used in brain prompts, /budget, and the CLI."""
    c = snap["currency"]
    lines = [f"Month {snap['month']} — derived from {snap['months_of_data']} month(s) of data:"]
    if snap["balance"] is not None:
        lines.append(f"  balance (bank):        {c}{snap['balance']:.2f}")
    else:
        lines.append("  balance:               unknown (no bank linked — CSV/PDF import)")
    lines += [
        f"  typical income/mo:     {c}{snap['monthly_income']:.2f}",
        f"  typical outgoings/mo:  {c}{snap['monthly_outgoings']:.2f}",
        f"  committed/mo (recurring): {c}{snap['committed']:.2f}"
        + (f" (of which {c}{snap['committed_upcoming']:.2f} still to come this month)"
           if snap["committed_upcoming"] else ""),
        f"  spent so far this month: {c}{snap['spent_this_month']:.2f}",
        f"  left this month:       {c}{snap['left_this_month']:.2f} "
        "(income − spent − upcoming committed)",
        f"  typical free cash/mo:  {c}{snap['disposable']:.2f} (income − outgoings)",
    ]
    if snap["recurring"]:
        subs = ", ".join(f"{r['merchant']} {c}{r['monthly']:.2f}" for r in snap["recurring"])
        lines.append(f"  recurring: {subs}")
    if snap["top_merchants_this_month"]:
        tops = ", ".join(f"{t['merchant']} {c}{t['spent']:.2f}"
                         for t in snap["top_merchants_this_month"])
        lines.append(f"  top spend this month: {tops}")
    if snap["spend_trend_vs_last_month"] is not None:
        d = snap["spend_trend_vs_last_month"]
        lines.append(f"  vs last month: {'+' if d >= 0 else '-'}{c}{abs(d):.2f} spend")
    return "\n".join(lines)
