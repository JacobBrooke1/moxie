"""Telegram bot conversation policy: pairing, allowlist, two-step approvals."""
from moxie.config import Config
from moxie.models import Transaction
from moxie.storage import Store
from moxie.telegram import Bot
from moxie.vault import AuditLog


def _bot(tmp_path, allow=None):
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    return Bot(config, store, audit, allow_chat_id=allow), store, audit


def _seed(bot, store):
    txns = [Transaction(date=f"2026-0{m}-03", merchant="Omaze", amount=15.0, currency="£")
            for m in (4, 5, 6)]
    store.save_transactions(txns)
    bot.agent.scan(txns)


def test_unpaired_chat_gets_pairing_instructions(tmp_path):
    bot, store, audit = _bot(tmp_path, allow=None)
    reply = bot.handle(12345, "hello")
    assert "MOXIE_TELEGRAM_CHAT_ID=12345" in reply


def test_foreign_chat_is_ignored_and_audited(tmp_path):
    bot, store, audit = _bot(tmp_path, allow=999)
    assert bot.handle(12345, "/findings") is None
    assert any(e["event"] == "telegram_denied" for e in audit.entries())


def test_findings_and_two_step_approval(tmp_path):
    bot, store, audit = _bot(tmp_path, allow=999)
    _seed(bot, store)
    listing = bot.handle(999, "/findings")
    assert "Omaze" in listing and "1." in listing

    ask = bot.handle(999, "/approve 1")
    assert "YES" in ask and "cannot be undone" in ask

    done = bot.handle(999, "YES")
    assert "EXECUTED" in done and "drafts only" in done.lower()
    assert any(e["event"] == "action_executed" and e["data"].get("channel") == "telegram"
               for e in audit.entries())


def test_skip_remembers(tmp_path):
    bot, store, audit = _bot(tmp_path, allow=999)
    _seed(bot, store)
    reply = bot.handle(999, "/skip 1")
    assert "remember" in reply.lower()
    assert store.get_decision("Omaze", "cancel_subscription")["status"] == "skipped"


def test_yes_without_pending_falls_through_safely(tmp_path):
    bot, store, audit = _bot(tmp_path, allow=999)
    _seed(bot, store)
    reply = bot.handle(999, "YES")
    # No pending approval: treated as a question; without a key we get the hint.
    assert "MOXIE_API_KEY" in reply
