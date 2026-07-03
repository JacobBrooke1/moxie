"""Bank linking from the dashboard: start → consent → complete → auto-sync.
Fake provider transport (canned TrueLayer routes); zero network."""
import json

import pytest

from moxie.config import Config
from moxie.dashboard import Dash
from moxie.providers import BankLink
from moxie.storage import Store
from moxie.vault import AuditLog

TL_ROUTES = {
    "/connect/token": {"access_token": "at-1", "refresh_token": "rt-1"},
    "/data/v1/accounts/acc-1/transactions": {"results": [
        {"timestamp": "2026-06-02T09:00:00Z", "description": "FITCLUB",
         "merchant_name": "FitClub", "amount": -29.99, "transaction_type": "DEBIT"},
    ]},
    "/data/v1/accounts/acc-1/balance": {"results": [
        {"available": 812.55, "current": 830.00}]},
    "/data/v1/accounts": {"results": [
        {"account_id": "acc-1", "display_name": "Current Account", "currency": "GBP"}]},
}


class FakeTransport:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers=None, data=None):
        self.calls.append((method, url))
        for key, resp in self.routes.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected URL in test: {url}")


@pytest.fixture()
def dash(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUELAYER_CLIENT_ID", "cid")
    monkeypatch.setenv("TRUELAYER_CLIENT_SECRET", "sec")
    monkeypatch.delenv("MOXIE_ENCRYPTION_KEY", raising=False)
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    return Dash(config, store, audit, provider_transport=FakeTransport(TL_ROUTES))


def test_start_returns_consent_url_and_hint(dash):
    out = dash.bank_start("truelayer")
    assert out["provider"] == "truelayer"
    assert out["url"].startswith("https://auth.truelayer-sandbox.com")
    assert "code" in out["hint"]


def test_complete_links_saves_and_auto_syncs(dash):
    dash.bank_start("truelayer")
    out = dash.bank_complete("auth-code-123")
    assert out["linked"] is True and out["provider"] == "truelayer"
    assert out["transactions"] == 1
    # persisted link + stored transactions + audit trail
    assert BankLink(dash.config).status()["linked"] is True
    assert dash.store.load_transactions()[0].merchant == "FitClub"
    assert json.loads(dash.store.get_meta("balances"))[0]["current"] == 830.00
    events = [e["event"] for e in dash.audit.entries()]
    assert "bank_linked" in events and "bank_sync" in events


def test_complete_without_start_is_a_clear_error(dash):
    out = dash.bank_complete("code")
    assert "start a bank link first" in out["error"]


def test_missing_credentials_return_the_hint(tmp_path, monkeypatch):
    monkeypatch.delenv("TRUELAYER_CLIENT_ID", raising=False)
    monkeypatch.delenv("TRUELAYER_CLIENT_SECRET", raising=False)
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    out = Dash(config, store, audit).bank_start("truelayer")
    assert "TRUELAYER_CLIENT_ID" in out["error"]
    assert "console.truelayer.com" in out["error"]


def test_unknown_provider_is_helpful(dash):
    out = dash.bank_start("monzo-magic")
    assert "truelayer" in out["error"]


def test_reauth_is_just_linking_again(dash):
    dash.bank_start("truelayer")
    dash.bank_complete("code-1")
    # consent later expires; the fix is the same flow again
    dash.bank_start("truelayer")
    out = dash.bank_complete("code-2")
    assert out["linked"] is True
