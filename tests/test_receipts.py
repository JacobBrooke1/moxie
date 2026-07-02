"""Receipt vault tests — fake OCR + fake IMAP; parsing is pure stdlib."""
from email.message import EmailMessage

from moxie.agent import Agent
from moxie.config import Config
from moxie.models import Receipt, Transaction
from moxie.receipts import (attach_evidence, ingest_email_receipts,
                            match_receipt, ocr_receipt, parse_receipt_text)
from moxie.storage import Store
from moxie.vault import AuditLog

RECEIPT_TEXT = """CORNER GROCERY
12 High Street
Tel: 0161 000 0000

2026-06-09  14:31
Bread            2.10
Milk             1.15
Cheese           4.95
TOTAL           £63.20
Card payment    £63.20
Thank you for shopping!
"""


def test_parse_receipt_text_finds_the_fields():
    fields = parse_receipt_text(RECEIPT_TEXT)
    assert fields["merchant"] == "CORNER GROCERY"
    assert fields["date"] == "2026-06-09"
    assert fields["amount"] == 63.20            # the TOTAL line, not the largest item


def test_parse_handles_uk_dates_and_bare_amounts():
    fields = parse_receipt_text("Ye Olde Cafe\n14/06/2026\nAmount due 12.50\n")
    assert fields["date"] == "2026-06-14" and fields["amount"] == 12.50


def test_ocr_receipt_with_injected_engine(tmp_path):
    img = tmp_path / "receipt.jpg"
    img.write_bytes(b"fake-jpeg")
    r = ocr_receipt(str(img), ocr_fn=lambda p: RECEIPT_TEXT)
    assert r.merchant == "CORNER GROCERY" and r.amount == 63.20
    assert r.source == "photo" and r.path.endswith("receipt.jpg")


def test_ocr_without_extra_says_how_to_install(tmp_path, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_pil(name, *a, **k):
        if name in ("PIL", "pytesseract"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pil)
    img = tmp_path / "r.jpg"
    img.write_bytes(b"x")
    try:
        ocr_receipt(str(img))
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "moxie-agent[ocr]" in str(e)


class FakeImap:
    """Injectable IMAP client: canned RFC822 messages, never a socket."""

    def __init__(self, messages):
        self.messages = messages
        self.closed = False

    def fetch_recent(self, folder="INBOX", limit=50):
        return self.messages

    def close(self):
        self.closed = True


def _email(subject, body, sender="billing@cloudhost.com"):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "me@example.com"
    msg.set_content(body)
    return bytes(msg)


def test_email_ingest_keeps_receipts_skips_noise():
    fake = FakeImap([
        _email("Your CloudHost receipt", "CloudHost\nInvoice A-2231\n2026-06-03\nTotal £40.00"),
        _email("Lunch on Friday?", "fancy the pub?"),
        _email("Payment received — order 1189", "StreamMax\n14/06/2026\nAmount paid 15.99"),
    ])
    receipts = ingest_email_receipts(client=fake)
    assert len(receipts) == 2
    cloud = receipts[0]
    assert cloud.merchant == "CloudHost" and cloud.amount == 40.00
    assert cloud.date == "2026-06-03" and cloud.source == "email"


def test_match_receipt_amount_date_merchant():
    txns = [
        Transaction(date="2026-06-03", merchant="CloudHost", amount=40.00, currency="£"),
        Transaction(date="2026-06-03", merchant="Somewhere Else", amount=40.00, currency="£"),
        Transaction(date="2026-06-25", merchant="CloudHost", amount=12.00, currency="£"),
    ]
    r = Receipt(merchant="CloudHost Ltd", date="2026-06-04", amount=40.00)
    match = match_receipt(r, txns)
    assert match is txns[0]                    # name overlap beats the fallback
    assert match_receipt(Receipt(merchant="X", date="2026-01-01", amount=9.99), txns) is None


def test_scan_attaches_evidence_to_disputes(tmp_path):
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    store.save_receipt(Receipt(merchant="CloudHost", date="2026-06-03", amount=40.00,
                               source="email", text="CloudHost invoice A-2231 £40.00"))
    txns = [
        Transaction(date="2026-06-03", merchant="CloudHost", amount=40.00, currency="£"),
        Transaction(date="2026-06-03", merchant="CloudHost", amount=40.00, currency="£"),
    ]
    store.save_transactions(txns)
    actions = Agent(config, store, audit).scan(txns)
    dispute = next(a for a in actions if a.kind == "dispute_charge")
    receipt_id = store.load_receipts()[0].id
    assert dispute.evidence_receipt_id == receipt_id


def test_live_dispute_email_carries_the_receipt(tmp_path, monkeypatch):
    """End to end: scan matches the receipt, approval sends, evidence rides along."""
    from moxie.actions import EmailChannel
    monkeypatch.setenv("MOXIE_LIVE", "true")
    monkeypatch.setenv("MOXIE_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("MOXIE_SMTP_USER", "me@example.com")
    monkeypatch.setenv("MOXIE_SMTP_PASSWORD", "app-pass")
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    store.save_receipt(Receipt(merchant="CloudHost", date="2026-06-03", amount=40.00,
                               source="email", text="CloudHost invoice A-2231 £40.00"))
    txns = [Transaction(date="2026-06-03", merchant="CloudHost", amount=40.00, currency="£")
            for _ in range(2)]
    store.save_transactions(txns)

    sent = []

    def transport(msg, smtp):
        sent.append(msg)
        return "<mid@moxie>"

    agent = Agent(config, store, audit,
                  channels={"email": EmailChannel(transport=transport)})
    agent.scan(txns)
    results = agent.review(approve_fn=lambda a: True)
    assert any(outcome == "sent" for _, outcome, _ in results)
    body = sent[0].get_content() if not sent[0].is_multipart() else (
        sent[0].get_body(("plain",)).get_content())
    assert "Evidence: receipt from CloudHost" in body
    assert "invoice A-2231" in body


def test_attach_evidence_counts_and_respects_existing():
    receipts = [Receipt(id="r1", merchant="CloudHost", date="2026-06-03", amount=40.0)]
    from moxie.models import ProposedAction
    a1 = ProposedAction(kind="dispute_charge", merchant="CloudHost",
                        description="", amount=40.0)
    a2 = ProposedAction(kind="cancel_subscription", merchant="CloudHost",
                        description="", amount=40.0)
    a3 = ProposedAction(kind="dispute_charge", merchant="CloudHost",
                        description="", amount=40.0, evidence_receipt_id="already")
    assert attach_evidence([a1, a2, a3], receipts) == 1
    assert a1.evidence_receipt_id == "r1"
    assert a2.evidence_receipt_id is None      # cancels don't need receipts
    assert a3.evidence_receipt_id == "already"
