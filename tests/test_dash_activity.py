"""Activity feed: the audit log's human face — honest wording included."""
import datetime as dt

import pytest

from moxie.config import Config
from moxie.dashboard import Dash
from moxie.storage import Store
from moxie.vault import AuditLog


@pytest.fixture()
def dash(tmp_path):
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    return Dash(config, store, audit)


def test_feed_is_newest_first_and_humanized(dash):
    dash.audit.append("scan", {"transactions": 9, "found": 3, "suppressed": 1})
    dash.audit.append("action_executed", {"kind": "cancel_subscription",
                                          "merchant": "FitClub", "dry_run": True,
                                          "sent": False, "channel": "dashboard"})
    dash.audit.append("bank_sync", {"provider": "truelayer", "transactions": 42})
    feed = dash.activity()
    assert feed[0]["event"] == "bank_sync"
    assert "42 transactions from truelayer" in feed[0]["summary"]
    assert feed[-1]["summary"].startswith("Scanned 9 transactions")


def test_honest_wording_drafted_vs_sent(dash):
    dash.audit.append("action_executed", {"kind": "cancel_subscription",
                                          "merchant": "FitClub", "sent": False})
    dash.audit.append("action_executed", {"kind": "dispute_charge",
                                          "merchant": "CloudHost", "sent": True,
                                          "channel_used": "email",
                                          "reference": "<mid@x>"})
    feed = dash.activity()
    sent, drafted = feed[0]["summary"], feed[1]["summary"]
    assert sent.startswith("SENT dispute_charge for CloudHost via email")
    assert "Drafted cancel_subscription for FitClub" in drafted
    assert "nothing was sent" in drafted


def test_policy_eval_noise_is_filtered(dash):
    dash.audit.append("policy_eval", {"outcome": "needs_approval"})
    dash.audit.append("scan", {"transactions": 1, "found": 0, "suppressed": 0})
    assert all(e["event"] != "policy_eval" for e in dash.activity())


def test_feed_respects_limit(dash):
    for i in range(40):
        dash.audit.append("ask", {"question": str(i)})
    assert len(dash.activity(limit=10)) == 10


def test_next_daily_scan_is_in_the_future(dash, monkeypatch):
    monkeypatch.setenv("MOXIE_SCAN_HOUR", "9")
    nxt = dash._next_daily_scan()
    assert nxt is not None
    assert dt.datetime.fromisoformat(nxt) > dt.datetime.now()


def test_next_daily_scan_skips_today_if_already_ran(dash, monkeypatch):
    monkeypatch.setenv("MOXIE_SCAN_HOUR", "23")
    dash.store.set_meta("last_auto_scan", dt.date.today().isoformat())
    nxt = dt.datetime.fromisoformat(dash._next_daily_scan())
    assert nxt.date() > dt.date.today()


def test_status_carries_the_new_heartbeat_fields(dash):
    hb = dash.status()["heartbeat"]
    assert "next_daily_scan" in hb and "last_bank_sync" in hb
