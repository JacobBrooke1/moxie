"""Chat-built dashboard widgets — specs, never code. Stdlib only.

"Track my Netflix spend" in the dashboard chat makes the brain propose a
WIDGET SPEC: a tiny JSON object drawn from the whitelist below. The human
confirms it, Moxie validates it (again, server-side), stores it encrypted,
and renders it with Moxie's OWN code, every string escaped.

THE SECURITY LINE (why this file exists): transaction text feeds the model,
so a malicious merchant name could steer what the model says. If the model
could emit HTML/JS into the page that holds API keys and approves money
actions, that's prompt-injection → key exfiltration. So the model's output
is never markup and never executed — it is DATA, validated against the
strictest practical schema, rejected on any surprise.

Spec vocabulary (the entire universe of what chat can build):

  stat_card        one figure: spend matching a filter over the window
  merchant_tracker one merchant's monthly history (mini bar series)
  category_total   sum over keywords, optional monthly target
  goal_progress    this month's actual vs a target amount
  trend_chart      monthly spend series for a filter (empty = everything)
  remove_widget    (chat intent) remove an existing card by title
  layout           (chat intent) pin/hide the built-in status cards

Fields: type · title (≤40 chars, no angle brackets) · merchants/keywords
(≤10 items, each ≤40 chars, plain strings) · months (1–12) · target
(0 < n ≤ 10_000_000). Unknown keys, unknown types, or HTML anywhere → reject.
"""
from __future__ import annotations

import datetime as dt
import uuid

DATA_TYPES = ("stat_card", "merchant_tracker", "category_total",
              "goal_progress", "trend_chart")
INTENT_TYPES = ("remove_widget", "layout")
ALLOWED_KEYS = {"type", "title", "merchants", "keywords", "months", "target"}
LAYOUT_KEYS = {"type", "hide", "pin"}
CARD_IDS = ("heartbeat", "brain", "telegram", "data", "audit", "mode",
            "bank", "month")
MAX_TITLE = 40
MAX_ITEMS = 10
MAX_ITEM_LEN = 40
MAX_TARGET = 10_000_000


def _clean_str(value, max_len) -> "str | None":
    """A plain, bounded string with no markup characters — or None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > max_len:
        return None
    if any(ch in value for ch in "<>{}`\\"):
        return None
    return value


def _clean_list(value) -> "list[str] | None":
    if not isinstance(value, list) or not (1 <= len(value) <= MAX_ITEMS):
        return None
    out = []
    for item in value:
        cleaned = _clean_str(item, MAX_ITEM_LEN)
        if cleaned is None:
            return None
        out.append(cleaned)
    return out


def validate_widget_spec(raw) -> "tuple[dict | None, str | None]":
    """(spec, None) when the spec is exactly what we allow; (None, why) for
    anything else. Deliberately paranoid: unknown = rejected."""
    if not isinstance(raw, dict):
        return None, "spec must be an object"
    wtype = raw.get("type")

    if wtype == "layout":
        if set(raw) - LAYOUT_KEYS:
            return None, f"layout allows only {sorted(LAYOUT_KEYS)}"
        spec = {"type": "layout"}
        for key in ("hide", "pin"):
            items = raw.get(key, [])
            if not isinstance(items, list) or any(i not in CARD_IDS for i in items):
                return None, f"{key} must be a list from {CARD_IDS}"
            spec[key] = items
        if not spec["hide"] and not spec["pin"]:
            return None, "layout needs something to hide or pin"
        return spec, None

    if wtype == "remove_widget":
        if set(raw) - {"type", "title"}:
            return None, "remove_widget allows only type and title"
        title = _clean_str(raw.get("title"), MAX_TITLE)
        if title is None:
            return None, "remove_widget needs the card's title"
        return {"type": "remove_widget", "title": title}, None

    if wtype not in DATA_TYPES:
        return None, f"unknown widget type {wtype!r}"
    extra = set(raw) - ALLOWED_KEYS
    if extra:
        return None, f"unknown keys: {sorted(extra)}"

    title = _clean_str(raw.get("title"), MAX_TITLE)
    if title is None:
        return None, "title is required (≤40 plain characters)"

    spec = {"type": wtype, "title": title}

    merchants = raw.get("merchants")
    keywords = raw.get("keywords")
    if merchants is not None:
        merchants = _clean_list(merchants)
        if merchants is None:
            return None, "merchants must be 1–10 plain strings"
        spec["merchants"] = merchants
    if keywords is not None:
        keywords = _clean_list(keywords)
        if keywords is None:
            return None, "keywords must be 1–10 plain strings"
        spec["keywords"] = keywords
    if wtype != "trend_chart" and not (spec.get("merchants") or spec.get("keywords")):
        return None, f"{wtype} needs merchants or keywords to match"

    months = raw.get("months", 3)
    if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= 12:
        return None, "months must be an integer from 1 to 12"
    spec["months"] = months

    target = raw.get("target")
    if wtype == "goal_progress" and target is None:
        return None, "goal_progress needs a target amount"
    if target is not None:
        if isinstance(target, bool) or not isinstance(target, (int, float)):
            return None, "target must be a number"
        if not 0 < target <= MAX_TARGET:
            return None, "target must be between 0 and 10,000,000"
        spec["target"] = round(float(target), 2)

    return spec, None


# --------------------------------------------------------------- computing --
def _matches(spec: dict, txn) -> bool:
    hay = f"{txn.merchant} {txn.description}".lower()
    merchants = [m.lower() for m in spec.get("merchants", [])]
    keywords = [k.lower() for k in spec.get("keywords", [])]
    if not merchants and not keywords:
        return True  # trend_chart with no filter = all spending
    return (any(m in txn.merchant.lower() for m in merchants)
            or any(k in hay for k in keywords))


def _window_months(months: int, today: dt.date) -> "list[str]":
    out, year, month = [], today.year, today.month
    for _ in range(months):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(out))


def compute_widget(spec: dict, transactions, today=None) -> dict:
    """Turn a validated spec into numbers. Spend only (amount > 0); the
    window is the last `months` calendar months including the current one."""
    today = today or dt.date.today()
    window = _window_months(spec.get("months", 3), today)
    this_month = window[-1]
    cur = transactions[0].currency if transactions else "£"

    by_month = {m: 0.0 for m in window}
    total = 0.0
    for t in transactions:
        if t.amount <= 0 or not _matches(spec, t):
            continue
        m = (t.date or "")[:7]
        if m in by_month:
            by_month[m] += t.amount
            total += t.amount

    series = [{"month": m, "amount": round(by_month[m], 2)} for m in window]
    out = {"currency": cur, "months": len(window)}

    if spec["type"] in ("merchant_tracker", "trend_chart"):
        out["series"] = series
    if spec["type"] in ("stat_card", "category_total"):
        out["value"] = round(total, 2)
        if "target" in spec:
            out["target"] = spec["target"]
    if spec["type"] == "goal_progress":
        actual = round(by_month[this_month], 2)
        out["actual"] = actual
        out["target"] = spec["target"]
        out["pct"] = round(min(100.0, 100.0 * actual / spec["target"]), 1)
    return out


def new_widget_id() -> str:
    return uuid.uuid4().hex[:12]
