"""Receipt capture — photo OCR + email e-receipts. Local-first by design.

  * Photos are OCR'd LOCALLY via Tesseract (optional extra: pip install
    "moxie-agent[ocr]" and install the tesseract binary) — images never
    leave the machine.
  * Email is scanned READ-ONLY over IMAP (your own mailbox app-password;
    env: MOXIE_IMAP_HOST / MOXIE_IMAP_USER / MOXIE_IMAP_PASSWORD).
    Nothing is moved, marked, or deleted.
  * The text parser is pure stdlib and unit-tested; OCR and IMAP are
    injectable for tests (the usual Moxie pattern).

Receipts are matched to transactions (same amount, close date, similar
merchant) and attached to disputes as evidence — that's their whole job.
"""
from __future__ import annotations

import datetime as dt
import email
import email.header
import imaplib
import os
import re

from .models import Receipt

# ---------------------------------------------------------------- parsing ---
_AMOUNT = re.compile(r"[£$€]\s?(\d[\d,]*\.\d{2})")
_BARE_AMOUNT = re.compile(r"\b(\d[\d,]*\.\d{2})\b")
_TOTAL_LINE = re.compile(r"\b(total|amount due|amount paid|grand total|paid)\b", re.I)
_DATES = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b", re.I), "dMy"),
]
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}
_NOISE_LINE = re.compile(r"^(receipt|invoice|tax invoice|vat|order|thank|www\.|http|tel|\d)", re.I)


def _find_date(text: str) -> str:
    for rx, kind in _DATES:
        m = rx.search(text)
        if not m:
            continue
        try:
            if kind == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "dmy":
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                d, mo, y = int(m.group(1)), _MONTHS[m.group(2).lower()[:3]], int(m.group(3))
            return dt.date(y, mo, d).isoformat()
        except ValueError:
            continue
    return ""


def _find_amount(text: str) -> float:
    """The total: prefer amounts on a 'total/paid' line; else the largest."""
    totals, everything = [], []
    for line in text.splitlines():
        hits = [float(a.replace(",", "")) for a in _AMOUNT.findall(line)]
        if not hits:
            hits = [float(a.replace(",", "")) for a in _BARE_AMOUNT.findall(line)]
        everything += hits
        if hits and _TOTAL_LINE.search(line):
            totals += hits
    if totals:
        return max(totals)
    return max(everything) if everything else 0.0


def _find_merchant(text: str) -> str:
    """First plausible line: receipts put the shop name at the top."""
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 3 or _NOISE_LINE.match(line):
            continue
        return line[:60]
    return "Unknown"


def parse_receipt_text(text: str) -> dict:
    """OCR/email text -> {merchant, date, amount}. Pure stdlib, unit-tested."""
    return {
        "merchant": _find_merchant(text or ""),
        "date": _find_date(text or ""),
        "amount": _find_amount(text or ""),
    }


# ---------------------------------------------------------------- photo -----
def _tesseract(image_path: str) -> str:
    """Default OCR: local Tesseract via pytesseract (optional extra)."""
    try:
        from PIL import Image
        import pytesseract
    except ImportError as e:
        raise RuntimeError(
            "photo OCR needs the optional extra: pip install \"moxie-agent[ocr]\" "
            "plus the tesseract binary (https://tesseract-ocr.github.io) — "
            "all local, images never leave your machine"
        ) from e
    return pytesseract.image_to_string(Image.open(image_path))


def ocr_receipt(image_path: str, ocr_fn=None) -> Receipt:
    """Photo of a paper receipt -> parsed, filed Receipt. Local OCR only."""
    text = (ocr_fn or _tesseract)(image_path)
    fields = parse_receipt_text(text)
    return Receipt(
        merchant=fields["merchant"],
        date=fields["date"] or dt.date.today().isoformat(),
        amount=fields["amount"],
        source="photo",
        path=str(image_path),
        text=text.strip(),
    )


# ---------------------------------------------------------------- email -----
_RECEIPTISH = re.compile(
    r"\b(receipt|invoice|order confirmation|payment (received|confirmation)|"
    r"your (order|purchase)|billing statement)\b", re.I)


class ImapClient:
    """Thin read-only wrapper over imaplib; injectable for tests.

    Env: MOXIE_IMAP_HOST, MOXIE_IMAP_USER, MOXIE_IMAP_PASSWORD,
         MOXIE_IMAP_FOLDER (default INBOX). Use an app password.
    """

    def __init__(self):
        host = os.environ.get("MOXIE_IMAP_HOST", "")
        user = os.environ.get("MOXIE_IMAP_USER", "")
        password = os.environ.get("MOXIE_IMAP_PASSWORD", "")
        if not (host and user and password):
            raise RuntimeError(
                "email receipts need MOXIE_IMAP_HOST / MOXIE_IMAP_USER / "
                "MOXIE_IMAP_PASSWORD in .env (read-only scan; use an app password)")
        self._imap = imaplib.IMAP4_SSL(host)
        self._imap.login(user, password)

    def fetch_recent(self, folder: str = "INBOX", limit: int = 50) -> "list[bytes]":
        self._imap.select(folder, readonly=True)   # readonly: we never mark/move
        _, data = self._imap.search(None, "ALL")
        ids = data[0].split()[-limit:]
        out = []
        for mid in ids:
            _, msg_data = self._imap.fetch(mid, "(RFC822)")
            if msg_data and msg_data[0]:
                out.append(msg_data[0][1])
        return out

    def close(self) -> None:
        try:
            self._imap.logout()
        except Exception:
            pass


def _decode_header(raw) -> str:
    parts = email.header.decode_header(raw or "")
    out = ""
    for text, charset in parts:
        out += text.decode(charset or "utf-8", "replace") if isinstance(text, bytes) else text
    return out


def _plain_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", "replace")
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode(msg.get_content_charset() or "utf-8", "replace") if payload else ""


def ingest_email_receipts(config=None, client=None, folder: str = "INBOX",
                          limit: int = 50) -> "list[Receipt]":
    """Read-only IMAP scan -> Receipts for messages that look like receipts.
    The client is injectable; the default connects with your app password."""
    own = client is None
    client = client or ImapClient()
    try:
        receipts = []
        for raw in client.fetch_recent(folder=folder, limit=limit):
            msg = email.message_from_bytes(raw)
            subject = _decode_header(msg.get("Subject", ""))
            body = _plain_text(msg)
            if not (_RECEIPTISH.search(subject) or _RECEIPTISH.search(body[:500])):
                continue
            fields = parse_receipt_text(body)
            sender = _decode_header(msg.get("From", ""))
            merchant = fields["merchant"]
            if merchant == "Unknown" and "@" in sender:
                merchant = sender.split("@")[-1].split(".")[0].title().strip("> ")
            receipts.append(Receipt(
                merchant=merchant,
                date=fields["date"] or dt.date.today().isoformat(),
                amount=fields["amount"],
                source="email",
                text=(subject + "\n\n" + body)[:2000].strip(),
            ))
        return receipts
    finally:
        if own:
            client.close()


# ---------------------------------------------------------------- matching --
def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def match_receipt(receipt, transactions, window_days: int = 3):
    """The transaction this receipt is evidence for: same amount (±1p),
    dates within the window, merchant names that overlap."""
    try:
        rdate = dt.date.fromisoformat(receipt.date)
    except (ValueError, TypeError):
        rdate = None
    rname = _norm(receipt.merchant)
    best = None
    for t in transactions:
        if t.amount <= 0 or abs(t.amount - receipt.amount) > 0.01:
            continue
        if rdate is not None:
            try:
                delta = abs((dt.date.fromisoformat(t.date) - rdate).days)
            except ValueError:
                continue
            if delta > window_days:
                continue
        tname = _norm(t.merchant)
        if rname and tname and (rname in tname or tname in rname):
            return t
        best = best or t   # amount+date match with unlike names: weak fallback
    return best


def attach_evidence(actions, receipts) -> int:
    """Give dispute-type actions their receipt (by merchant + amount).
    Returns how many were attached. Called on every scan."""
    attached = 0
    for action in actions:
        if action.kind not in ("dispute_charge", "chase_refund") or action.evidence_receipt_id:
            continue
        aname = _norm(action.merchant)
        for r in receipts:
            if abs(r.amount - action.amount) > 0.01:
                continue
            rname = _norm(r.merchant)
            if aname and rname and (aname in rname or rname in aname):
                action.evidence_receipt_id = r.id
                attached += 1
                break
    return attached
