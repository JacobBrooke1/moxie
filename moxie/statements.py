"""Bank statement PDF import.

Banks love handing you a PDF instead of a CSV (NatWest, we see you). This module
turns a statement PDF into Transactions:

    moxie scan --pdf TXN_..._NatWest.pdf

Layout support: the "transaction table" style used by NatWest exports —
    Date        Description        Type        Paid in (£)    Paid out (£)
    29 Jun      OMAZE              Debit Card Transaction        -£15.00
Other banks' PDFs that follow a date / description / signed-amount line shape
will often parse too; if yours doesn't, open an issue with (a redacted copy of)
the layout.

Design notes:
- The *text parser* (`parse_statement_text`) is pure stdlib and unit-tested.
- Only the PDF→text step needs `pypdf` (install with:  pip install pypdf
  or  pip install "moxie-agent[pdf]").
- Dates without years ("29 Jun") get their year from the statement's own
  dd/mm/yyyy header dates, handling year-end wrap correctly.
"""
from __future__ import annotations

import re

from .connectors import normalize_merchant
from .models import Transaction

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

_TXN_LINE = re.compile(r"^\s*(\d{1,2}) ([A-Z][a-z]{2})\s{2,}(.+)$")
_AMOUNT = re.compile(r"(-?)£\s?([\d,]+\.\d{2})")
_HEADER_DATE = re.compile(r"\b\d{2}/\d{2}/(\d{4})\b")


def _infer_years(text: str) -> "list[int]":
    """Statement PDFs print their date window as dd/mm/yyyy somewhere in the
    header — collect those years so day-month rows can be pinned to a year."""
    years = sorted({int(y) for y in _HEADER_DATE.findall(text)})
    return years or []


def parse_statement_text(text: str, years: "list[int] | None" = None) -> "list[Transaction]":
    """Parse extracted statement text into Transactions (spend positive,
    credits negative — Moxie's convention). Pure stdlib; unit-testable."""
    years = years if years is not None else _infer_years(text)

    raw = []
    for line in text.splitlines():
        m = _TXN_LINE.match(line.rstrip())
        if not m:
            continue
        day, mon, rest = m.groups()
        if mon not in _MONTHS:
            continue
        am = None
        for match in _AMOUNT.finditer(rest):
            am = match          # last £amount on the line is the money column
        if am is None:
            continue
        value = float(am.group(2).replace(",", ""))
        # Statement: paid-out is -£x (spend), paid-in is £x (credit).
        # Moxie: spend positive, credits negative — so flip.
        amount = value if am.group(1) else -value
        description = re.split(r"\s{2,}", rest.strip())[0]
        raw.append((int(day), _MONTHS[mon], description, round(amount, 2)))

    if not raw:
        return []

    # Assign years: pick, for each row's month, the candidate year that keeps
    # the date inside the statement window (handles Dec→Jan statements).
    candidates = years or []
    txns = []
    for day, month, description, amount in raw:
        year = None
        if len(candidates) == 1 or (candidates and candidates[0] == candidates[-1]):
            year = candidates[0]
        elif candidates:
            # multi-year window (e.g. Dec 2026 – Jan 2027): late months belong
            # to the earlier year, early months to the later year.
            year = candidates[0] if month >= 7 else candidates[-1]
        if year is None:
            raise ValueError(
                "Couldn't infer the statement's year (no dd/mm/yyyy dates found "
                "in the PDF header). Convert to CSV or open an issue with the layout."
            )
        txns.append(
            Transaction(
                date=f"{year}-{month:02d}-{day:02d}",
                merchant=normalize_merchant(description),
                amount=amount,
                description=description,
                currency="£",
            )
        )
    return txns


def import_pdf(path: str) -> "list[Transaction]":
    """Extract text from a statement PDF and parse it. Needs pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise SystemExit(
            "PDF import needs the optional pypdf package:\n"
            "  pip install pypdf     (or: pip install 'moxie-agent[pdf]')"
        ) from e

    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text(extraction_mode="layout"))
        except TypeError:          # older pypdf without layout mode
            pages.append(page.extract_text())
    text = "\n".join(pages)

    txns = parse_statement_text(text)
    if not txns:
        raise SystemExit(
            f"No transactions recognised in {path!r}. Moxie currently understands "
            "NatWest-style statement tables (Date / Description / ±£amount). "
            "Export a CSV instead, or open an issue with your bank's layout."
        )
    return txns
