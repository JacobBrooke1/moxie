"""Read-only account connectors. Stdlib only.

`import_csv` handles real bank exports, not just the ideal format:
- header auto-detection for common UK/US layouts (Monzo, Starling, Barclays,
  HSBC, Revolut, Amex, and the generic "Money Out / Money In" style)
- amounts with currency symbols, thousands commas, parentheses negatives,
  and separate debit/credit columns
- bank sign conventions (many banks export spending as negative — Moxie
  normalises so that spend is positive, credits/refunds negative)
- dates in ISO, UK (dd/mm/yyyy), and timestamp formats
- merchant name normalisation ("PAYPAL *NETFLIX 35314369001" → "Netflix")

Open-banking connectors (TrueLayer etc.) are stubbed: bring-your-own provider
credentials is the plan — see docs/HOW_IT_WORKS.md.
"""
from __future__ import annotations

import csv
import datetime as dt
import re

from .models import Transaction

# ---------------------------------------------------------------- header maps
_DATE_COLS = [
    "date", "transaction date", "date of transaction", "created", "created (utc)",
    "timestamp", "settled", "completed date", "started date", "posting date",
]
_MERCHANT_COLS = [
    "merchant", "name", "counterparty", "payee", "counter party",
    "transaction description", "narrative", "details", "description", "memo",
]
_AMOUNT_COLS = ["amount", "value", "amount (gbp)", "amount (usd)", "transaction amount"]
_OUT_COLS = ["money out", "paid out", "debit", "debit amount", "withdrawal", "out", "money out (£)"]
_IN_COLS = ["money in", "paid in", "credit", "credit amount", "deposit", "in", "money in (£)"]
_DESC_COLS = ["description", "notes", "reference", "memo", "details", "category", "subcategory", "type"]

_DATE_FORMATS = [
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d.%m.%Y",
    "%d %b %Y", "%d %B %Y", "%Y/%m/%d", "%m/%d/%Y",
]

_PREFIX_NOISE = re.compile(
    r"^(card payment to|direct debit payment to|direct debit|standing order to|"
    r"standing order|dd|so|pos|contactless payment to|payment to|bill payment to)\s+",
    re.I,
)
_PROCESSOR_NOISE = re.compile(
    r"^(paypal|pp|sq|sumup|zettle|ztl|crv|izettle|google|apple\.com/bill|amznmktplace|amzn mktp)\s*\*\s*",
    re.I,
)
_TRAILING_REF = re.compile(r"[,\s]+(ref|reference)\b.*$", re.I)
_TRAILING_RATE = re.compile(r",\s*[\d.]+\s*(gbp|usd|eur).*$", re.I)
_TRAILING_ON_DATE = re.compile(r"\s+on\s+\d{2}[-/]\d{2}[-/]\d{4}\s*$", re.I)
_TRAILING_NUMS = re.compile(r"[\s*]+[\d*]{4,}$")
_TRAILING_CORP = re.compile(r"[,\s]+(ltd|plc|inc|llc|gb|uk|com)\.?$", re.I)


def normalize_merchant(raw: str) -> str:
    """Turn bank-statement noise into a human merchant name."""
    s = (raw or "").strip()
    s = _PREFIX_NOISE.sub("", s)
    s = _PROCESSOR_NOISE.sub("", s)
    s = _TRAILING_RATE.sub("", s)
    s = _TRAILING_ON_DATE.sub("", s)
    s = _TRAILING_REF.sub("", s)
    s = _TRAILING_NUMS.sub("", s)
    s = _TRAILING_CORP.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -*,.")
    if not s:
        return raw.strip() or "Unknown"
    if s.isupper() or s.islower():
        s = s.title()
    return s


def _parse_amount(raw: str) -> float:
    s = (raw or "").strip().replace(",", "")
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").lstrip("£$€").strip()
    if not s or s in ("-",):
        return 0.0
    value = float(s)
    return -value if neg else value


def _parse_date(raw: str) -> str:
    s = (raw or "").strip()
    # ISO timestamps: "2026-06-03T14:22:01Z" / "2026-06-03 14:22:01"
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ]", s)
    if m:
        return m.group(1)
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(
        f"Unrecognised date {raw!r} — supported: ISO (2026-06-03), UK (03/06/2026), "
        "or '3 Jun 2026'."
    )


def _pick(headers: "list[str]", candidates: "list[str]", *, exclude: "set[str]" = frozenset()):
    lower = {h.lower().strip(): h for h in headers}
    for cand in candidates:
        if cand in lower and lower[cand] not in exclude:
            return lower[cand]
    return None


def import_csv(path: str, currency: "str | None" = None) -> "list[Transaction]":
    """Import a real bank CSV export. Returns transactions with spend positive,
    credits/refunds negative, ISO dates, and normalised merchant names."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        headers = reader.fieldnames or []

        date_col = _pick(headers, _DATE_COLS)
        merch_col = _pick(headers, _MERCHANT_COLS)
        amount_col = _pick(headers, _AMOUNT_COLS)
        out_col = _pick(headers, _OUT_COLS)
        in_col = _pick(headers, _IN_COLS)
        desc_col = _pick(headers, _DESC_COLS, exclude={merch_col} if merch_col else frozenset())

        missing = [
            label for label, col in
            (("date", date_col), ("merchant/description", merch_col))
            if col is None
        ]
        if amount_col is None and out_col is None:
            missing.append("amount (or Money Out / Money In)")
        if missing:
            raise ValueError(
                f"Couldn't find column(s) for: {', '.join(missing)}. "
                f"Headers seen: {headers}. Rename columns or export a different format."
            )

        if currency is None:
            joined = " ".join(h.lower() for h in headers)
            currency = "£" if ("gbp" in joined or "£" in sample) else ("$" if "$" in sample else "£")

        txns = []
        for row in reader:
            raw_merchant = (row.get(merch_col) or "").strip()
            raw_date = (row.get(date_col) or "").strip()
            if not raw_date or not raw_merchant:
                continue
            if amount_col is not None:
                amount = _parse_amount(row.get(amount_col, ""))
            else:
                out_v = _parse_amount(row.get(out_col, "")) if out_col else 0.0
                in_v = _parse_amount(row.get(in_col, "")) if in_col else 0.0
                amount = out_v if out_v else -in_v
            if amount == 0.0:
                continue
            txns.append(
                Transaction(
                    date=_parse_date(raw_date),
                    merchant=normalize_merchant(raw_merchant),
                    amount=round(amount, 2),
                    description=(row.get(desc_col) or "").strip() if desc_col else raw_merchant,
                    currency=currency,
                )
            )

    # Sign convention: many banks export spending as negative. If most nonzero
    # rows are negative, flip so that spend is positive, credits negative.
    if txns and amount_col is not None:
        neg = sum(1 for t in txns if t.amount < 0)
        if neg > len(txns) / 2:
            for t in txns:
                t.amount = round(-t.amount, 2)
    return txns


# Live open-banking providers (TrueLayer / GoCardless / Plaid) moved to
# moxie/providers.py — `moxie connect <provider>` then `moxie sync`.
# This module stays the no-cloud path: CSV (here) and PDF (statements.py).
