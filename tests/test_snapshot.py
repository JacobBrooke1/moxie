"""The money picture: derived figures, honestly labelled, grounding the brain."""
import datetime as dt
import json

from moxie.brain import Brain
from moxie.config import Config
from moxie.models import Transaction
from moxie.snapshot import compute_snapshot, format_snapshot, snapshot_from_store
from moxie.storage import Store
from moxie.telegram import Bot
from moxie.vault import AuditLog

TODAY = dt.date(2026, 6, 20)  # mid-June: April+May complete, June in progress


def _txns():
    out = []
    for m in (4, 5, 6):
        out.append(Transaction(date=f"2026-{m:02d}-28", merchant="Acme Payroll",
                               amount=-2400.00, currency="£"))       # salary credit
        out.append(Transaction(date=f"2026-{m:02d}-02", merchant="FitClub",
                               amount=29.99, currency="£"))          # sub
        if m < 6:  # StreamMax hasn't charged yet in June
            out.append(Transaction(date=f"2026-{m:02d}-14", merchant="StreamMax",
                                   amount=15.99, currency="£"))
    # one-off spending
    out.append(Transaction(date="2026-04-10", merchant="Corner Grocery", amount=180.00, currency="£"))
    out.append(Transaction(date="2026-05-10", merchant="Corner Grocery", amount=220.00, currency="£"))
    out.append(Transaction(date="2026-06-10", merchant="Corner Grocery", amount=90.00, currency="£"))
    out.append(Transaction(date="2026-06-12", merchant="Daily Coffee", amount=12.50, currency="£"))
    return out


def test_snapshot_core_figures():
    s = compute_snapshot(_txns(), today=TODAY)
    assert s["monthly_income"] == 2400.00
    # complete months: Apr = 29.99+15.99+180 = 225.98 ; May = 29.99+15.99+220 = 265.98
    assert s["monthly_outgoings"] == 245.98            # median of the two
    assert s["committed"] == 45.98                     # FitClub + StreamMax
    assert s["committed_upcoming"] == 15.99            # StreamMax not charged yet in June
    assert s["spent_this_month"] == 132.49             # 29.99 + 90 + 12.50
    assert s["left_this_month"] == round(2400 - 132.49 - 15.99, 2)
    assert s["disposable"] == round(2400 - 245.98, 2)
    assert s["balance"] is None                        # no bank linked
    assert {"merchant": "FitClub", "monthly": 29.99} in s["recurring"]


def test_snapshot_with_balances_and_store(tmp_path):
    store = Store(tmp_path / "m.db")
    store.save_transactions(_txns())
    store.set_meta("balances", json.dumps(
        [{"account": "Current", "currency": "£", "available": 800.0, "current": 812.5}]))
    s = snapshot_from_store(store, today=TODAY)
    assert s["balance"] == 812.5
    text = format_snapshot(s)
    assert "balance (bank):        £812.50" in text
    assert "left this month" in text


def test_snapshot_trend_and_top_merchants():
    s = compute_snapshot(_txns(), today=TODAY)
    assert s["top_merchants_this_month"][0]["merchant"] == "Corner Grocery"
    # June (132.49) vs May (265.98): spending is down
    assert s["spend_trend_vs_last_month"] == round(132.49 - 265.98, 2)


def test_brain_gets_the_money_picture(tmp_path, monkeypatch):
    monkeypatch.setenv("MOXIE_API_KEY", "test-key")
    monkeypatch.delenv("MOXIE_OFFLINE", raising=False)
    config = Config(home=tmp_path / "home")
    payloads = []

    def fake(payload):
        payloads.append(payload)
        return {"content": [{"type": "text", "text": "grounded"}]}

    brain = Brain(config, transport=fake)
    s = compute_snapshot(_txns(), today=TODAY)
    out = brain.ask("can I afford £120 trainers this month?", _txns(), [], snapshot=s)
    assert out == "grounded"
    content = payloads[0]["messages"][0]["content"]
    assert "MONEY PICTURE" in content
    assert "left this month" in content
    # the honesty guardrail rides in the system prompt
    assert "not a regulated financial adviser" in payloads[0]["system"]


def test_telegram_budget_command(tmp_path):
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    bot = Bot(config, store, audit, allow_chat_id=999)
    assert "moxie scan" in bot.handle(999, "/budget")   # no data yet -> guidance
    store.save_transactions(_txns())
    reply = bot.handle(999, "/budget")
    assert "money picture" in reply.lower() and "committed" in reply
