"""Telegram rate limiting: even the paired chat gets a breather."""
from moxie.config import Config
from moxie.storage import Store
from moxie.telegram import RATE_MAX, Bot
from moxie.vault import AuditLog


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _bot(tmp_path, clock):
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    return Bot(config, store, audit, allow_chat_id=999, clock=clock), audit


def test_burst_gets_one_warning_then_silence(tmp_path):
    clock = FakeClock()
    bot, audit = _bot(tmp_path, clock)
    for _ in range(RATE_MAX):
        assert bot.handle(999, "/help") is not None
    warn = bot.handle(999, "/help")
    assert warn is not None and "⏳" in warn
    assert bot.handle(999, "/help") is None          # then silence
    assert any(e["event"] == "telegram_rate_limited" for e in audit.entries())


def test_window_expiry_restores_service(tmp_path):
    clock = FakeClock()
    bot, audit = _bot(tmp_path, clock)
    for _ in range(RATE_MAX + 1):
        bot.handle(999, "/help")
    clock.t += 61.0                                   # window rolls over
    assert bot.handle(999, "/help") is not None


def test_foreign_chats_never_consume_the_budget(tmp_path):
    clock = FakeClock()
    bot, audit = _bot(tmp_path, clock)
    for _ in range(RATE_MAX * 2):
        assert bot.handle(12345, "spam") is None      # denied, not rate-limited
    assert bot.handle(999, "/help") is not None       # you're unaffected
