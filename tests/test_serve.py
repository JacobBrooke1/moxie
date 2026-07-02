"""`moxie serve` wiring: dashboard up, tick runs, honest audit trail."""
import json
import urllib.request

from moxie.config import Config
from moxie.models import Transaction
from moxie.serve import run_serve
from moxie.storage import Store
from moxie.vault import AuditLog


class FakeTelegramAPI:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))

    def updates(self, offset, timeout=25):
        return []          # one empty poll, then run(once=True) returns


def _ctx(tmp_path, monkeypatch):
    monkeypatch.delenv("MOXIE_DASH_HOST", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MOXIE_TELEGRAM_CHAT_ID", raising=False)
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    return config, store, audit


def test_serve_without_telegram_runs_dash_and_daily_tick(tmp_path, monkeypatch):
    config, store, audit = _ctx(tmp_path, monkeypatch)
    monkeypatch.setenv("MOXIE_SCAN_HOUR", "0")     # any hour counts as 'after'
    store.save_transactions(
        [Transaction(date=f"2026-0{m}-02", merchant="FitClub", amount=29.99,
                     currency="£") for m in (4, 5, 6)])
    out = run_serve(config, store, audit, port=0, once=True)
    assert out["telegram"] is False
    # the daily tick really scanned
    assert any(e["event"] == "daily_scan" for e in audit.entries())
    assert any(e["event"] == "serve_started" for e in audit.entries())


def test_serve_with_telegram_polls_the_bot(tmp_path, monkeypatch):
    config, store, audit = _ctx(tmp_path, monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("MOXIE_TELEGRAM_CHAT_ID", "999")
    api = FakeTelegramAPI()
    out = run_serve(config, store, audit, port=0, once=True, bot_api=api)
    assert out["telegram"] is True


def test_serve_dashboard_actually_answers(tmp_path, monkeypatch):
    """While serve runs (once=True finishes fast), the dash port is real —
    check by starting it and hitting /api/status before shutdown."""
    import threading

    from moxie.dashboard import serve as dash_serve
    config, store, audit = _ctx(tmp_path, monkeypatch)
    srv = dash_serve(config, store, audit, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{srv.server_address[1]}/api/status",
                timeout=10) as r:
            assert json.loads(r.read())["heartbeat"]["alive"] is True
    finally:
        srv.shutdown()
