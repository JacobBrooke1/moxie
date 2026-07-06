"""The money dashboard: figures you decide on — accounts, trend, upcoming bills."""
import datetime as dt
import json

import pytest

from moxie.config import Config
from moxie.dashboard import Dash
from moxie.models import Transaction
from moxie.snapshot import compute_snapshot, monthly_series, upcoming_bills
from moxie.storage import Store
from moxie.vault import AuditLog

TODAY = dt.date(2026, 6, 20)


def T(date, merchant, amount, cur="£"):
    return Transaction(date=date, merchant=merchant, amount=amount, currency=cur)


def _txns():
    out = []
    for m in (4, 5, 6):
        out.append(T(f"2026-{m:02d}-28", "Acme Payroll", -2400.00))
        out.append(T(f"2026-{m:02d}-02", "FitClub", 29.99))
    for m in (4, 5):   # StreamMax charges on the 25th; June's not hit yet
        out.append(T(f"2026-{m:02d}-25", "StreamMax", 15.99))
    out.append(T("2026-05-10", "Corner Grocery", 220.00))
    out.append(T("2026-06-10", "Corner Grocery", 90.00))
    return out


# ------------------------------------------------------------- snapshot -----
def test_monthly_series_oldest_first_with_income():
    series = monthly_series(_txns())
    assert [p["month"] for p in series] == ["2026-04", "2026-05", "2026-06"]
    assert series[1]["spend"] == round(29.99 + 15.99 + 220.00, 2)
    assert series[0]["income"] == 2400.00


def test_upcoming_bills_expected_day_and_exclusion():
    bills = upcoming_bills(_txns(), today=TODAY)
    # FitClub already charged on June 2nd -> excluded; StreamMax (25th) is due
    assert [b["merchant"] for b in bills] == ["StreamMax"]
    assert bills[0]["expected_day"] == 25
    assert bills[0]["monthly"] == 15.99


def test_snapshot_carries_accounts_series_and_bills():
    balances = [{"account": "Current", "currency": "£", "available": 800.0, "current": 812.5},
                {"account": "Savings", "currency": "£", "available": 1500.0, "current": 1500.0}]
    s = compute_snapshot(_txns(), balances, today=TODAY)
    assert len(s["accounts"]) == 2 and s["accounts"][1]["account"] == "Savings"
    assert s["balance"] == 2312.5
    assert len(s["monthly_series"]) == 3
    assert s["upcoming_bills"][0]["merchant"] == "StreamMax"


# ------------------------------------------------------------- /api/money ---
@pytest.fixture()
def dash(tmp_path, monkeypatch):
    monkeypatch.delenv("MOXIE_DASH_TOKEN", raising=False)
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    return Dash(config, store, audit)


def test_money_empty_state_is_helpful(dash):
    out = dash.money()
    assert out["empty"] is True and "link a bank" in out["note"]


def test_money_full_shape_and_review_routing(dash):
    txns = _txns()
    dash.store.save_transactions(txns)
    dash.store.set_meta("balances", json.dumps(
        [{"account": "Current", "currency": "£", "available": 800.0, "current": 812.5}]))
    dash.agent.scan(txns)
    m = dash.money()
    assert m["accounts"][0]["current"] == 812.5
    assert m["income"] == 2400.00
    assert [p["month"] for p in m["monthly_series"]] == ["2026-04", "2026-05", "2026-06"]
    # recurring subs wire to their live finding for the Vault modal
    fit = next(r for r in m["recurring"] if r["merchant"] == "FitClub")
    proposed_ids = {a.id for a in dash.store.load_actions() if a.status == "proposed"}
    assert fit["finding_id"] in proposed_ids
    # honest framing rides in the page, not advice
    from moxie.dashboard import PAGE
    assert "isn't a financial adviser" in PAGE


def test_money_page_has_no_external_urls(dash):
    """Charts are hand-rolled SVG — the dashboard stays offline."""
    from moxie.dashboard import PAGE
    for marker in ("cdn.", "unpkg", "jsdelivr", "googleapis",
                   "<script src=", "chart.js"):
        assert marker not in PAGE.lower()
