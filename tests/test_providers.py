"""Bank provider tests — canned API responses via injectable transports.
No network. The convention under test everywhere: spend positive, credits
negative, ISO dates, £ for GBP — identical to what the CSV/PDF path produces,
so everything downstream (detectors, brain, dashboard) is unchanged."""
import datetime as dt
import json

import pytest

from moxie.config import Config
from moxie.providers import (BankLink, GoCardlessProvider, PlaidProvider,
                             TrueLayerProvider, get_provider, sync)
from moxie.storage import Store
from moxie.vault import AuditLog


class FakeTransport:
    """Routes by URL substring; records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers=None, data=None):
        self.calls.append((method, url, headers, data))
        for key, resp in self.routes.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected URL in test: {url}")


def _config(tmp_path, monkeypatch, **env):
    for k in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET",
              "GOCARDLESS_SECRET_ID", "GOCARDLESS_SECRET_KEY",
              "PLAID_CLIENT_ID", "PLAID_SECRET", "MOXIE_GC_INSTITUTION"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Config(home=tmp_path / "home")


# --------------------------------------------------------------------------- #
# TrueLayer
# --------------------------------------------------------------------------- #
TL_ROUTES = {
    "/connect/token": {"access_token": "at-1", "refresh_token": "rt-1"},
    "/data/v1/accounts/acc-1/transactions": {"results": [
        {"timestamp": "2026-06-02T09:00:00Z", "description": "FITCLUB",
         "merchant_name": "FitClub", "amount": -29.99, "transaction_type": "DEBIT"},
        {"timestamp": "2026-06-05T09:00:00Z", "description": "REFUND OMAZE",
         "merchant_name": "Omaze", "amount": 15.00, "transaction_type": "CREDIT"},
    ]},
    "/data/v1/accounts/acc-1/balance": {"results": [
        {"available": 812.55, "current": 830.00}]},
    "/data/v1/accounts": {"results": [
        {"account_id": "acc-1", "display_name": "Current Account", "currency": "GBP"}]},
}


def test_truelayer_link_and_fetch(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch,
                     TRUELAYER_CLIENT_ID="cid", TRUELAYER_CLIENT_SECRET="sec")
    fake = FakeTransport(TL_ROUTES)
    provider = TrueLayerProvider(config, transport=fake)

    started = provider.start_link()
    assert "truelayer-sandbox" in started["url"] and "code" in started["hint"]

    state = provider.complete_link("auth-code-123", started["state"])
    assert state["provider"] == "truelayer"
    assert state["accounts"][0]["id"] == "acc-1"
    assert state["consented_at"]

    txns, balances, refreshed = provider.fetch(state)
    # DEBIT -29.99 -> spend +29.99; CREDIT +15.00 -> -15.00 (Moxie convention)
    fit = next(t for t in txns if t.merchant == "FitClub")
    ref = next(t for t in txns if t.merchant == "Omaze")
    assert fit.amount == 29.99 and ref.amount == -15.00
    assert fit.date == "2026-06-02" and fit.currency == "£"
    assert balances[0]["current"] == 830.00
    assert refreshed["tokens"]["access_token"] == "at-1"


def test_truelayer_needs_credentials(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    out = TrueLayerProvider(config, transport=FakeTransport({})).start_link()
    assert "TRUELAYER_CLIENT_ID" in out["error"]


# --------------------------------------------------------------------------- #
# GoCardless (ex-Nordigen)
# --------------------------------------------------------------------------- #
GC_ROUTES = {
    "/token/new/": {"access": "gc-token"},
    "/requisitions/req-9/": {"id": "req-9", "accounts": ["a-1"]},
    "/requisitions/": {"id": "req-9", "link": "https://ob.gocardless.com/psd2/start/req-9"},
    "/accounts/a-1/transactions/": {"transactions": {"booked": [
        {"bookingDate": "2026-06-03", "transactionAmount": {"amount": "-40.00", "currency": "GBP"},
         "creditorName": "CloudHost", "remittanceInformationUnstructured": "CLOUDHOST LTD"},
        {"bookingDate": "2026-06-28", "transactionAmount": {"amount": "2100.00", "currency": "GBP"},
         "debtorName": "ACME PAYROLL", "remittanceInformationUnstructured": "SALARY"},
    ]}},
    "/accounts/a-1/balances/": {"balances": [
        {"balanceAmount": {"amount": "512.10", "currency": "GBP"}}]},
}


def test_gocardless_link_and_fetch(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch,
                     GOCARDLESS_SECRET_ID="sid", GOCARDLESS_SECRET_KEY="skey",
                     MOXIE_GC_INSTITUTION="NATWEST_NWBKGB2L")
    fake = FakeTransport(GC_ROUTES)
    provider = GoCardlessProvider(config, transport=fake)

    started = provider.start_link()
    assert started["url"].startswith("https://ob.gocardless.com")
    state = provider.complete_link("", started["state"])
    assert state["accounts"][0]["id"] == "a-1"

    txns, balances, _ = provider.fetch(state)
    spend = next(t for t in txns if t.merchant == "CloudHost")
    salary = next(t for t in txns if "Acme" in t.merchant or "SALARY" in t.description)
    assert spend.amount == 40.00          # -40.00 GC debit -> +40 spend
    assert salary.amount == -2100.00      # credit -> negative
    assert balances[0]["current"] == 512.10


def test_gocardless_requires_institution(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch,
                     GOCARDLESS_SECRET_ID="sid", GOCARDLESS_SECRET_KEY="skey")
    out = GoCardlessProvider(config, transport=FakeTransport(GC_ROUTES)).start_link()
    assert "MOXIE_GC_INSTITUTION" in out["error"]


# --------------------------------------------------------------------------- #
# Plaid
# --------------------------------------------------------------------------- #
PLAID_ROUTES = {
    "/link/token/create": {"link_token": "lt-1", "hosted_link_url": "https://secure.plaid.com/hl/1"},
    "/link/token/get": {"link_sessions": [
        {"results": {"item_add_results": [{"public_token": "pub-1"}]}}]},
    "/item/public_token/exchange": {"access_token": "plaid-at"},
    "/transactions/get": {
        "transactions": [
            {"date": "2026-06-14", "name": "STREAMMAX", "merchant_name": "StreamMax",
             "amount": 15.99, "iso_currency_code": "GBP"},
            {"date": "2026-06-20", "name": "REFUND", "merchant_name": "CloudHost",
             "amount": -40.00, "iso_currency_code": "GBP"},
        ],
        "accounts": [{"account_id": "pa-1", "name": "Checking",
                      "balances": {"available": 900.0, "current": 950.0,
                                   "iso_currency_code": "GBP"}}],
    },
}


def test_plaid_link_and_fetch(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, PLAID_CLIENT_ID="pc", PLAID_SECRET="ps")
    fake = FakeTransport(PLAID_ROUTES)
    provider = PlaidProvider(config, transport=fake)

    started = provider.start_link()
    assert started["url"].startswith("https://secure.plaid.com")
    state = provider.complete_link("", started["state"])
    assert state["tokens"]["access_token"] == "plaid-at"

    txns, balances, _ = provider.fetch(state)
    assert txns[0].amount == 15.99        # Plaid positive = money out already
    assert txns[1].amount == -40.00
    assert balances[0]["current"] == 950.0


# --------------------------------------------------------------------------- #
# link state, consent expiry, and the sync entry point
# --------------------------------------------------------------------------- #
def test_get_provider_unknown_is_helpful(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="truelayer"):
        get_provider("monzo-magic", config)


def test_consent_expiry_flags_reauth(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    link = BankLink(config)
    old = (dt.date.today() - dt.timedelta(days=91)).isoformat()
    link.save({"provider": "truelayer", "tokens": {}, "accounts": [],
               "consented_at": old + "T09:00:00"})
    status = link.status()
    assert status["needs_reauth"] is True and status["consent_days_left"] <= 0


def test_sync_happy_path_feeds_the_same_store(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch,
                     TRUELAYER_CLIENT_ID="cid", TRUELAYER_CLIENT_SECRET="sec")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    BankLink(config).save({
        "provider": "truelayer", "tokens": {"access_token": "at-1"},
        "accounts": [{"id": "acc-1", "name": "Current Account", "currency": "GBP"}],
        "consented_at": dt.datetime.now().isoformat(timespec="seconds"),
    })
    out = sync(config, store, audit, transport=FakeTransport(TL_ROUTES))
    assert out["transactions"] == 2
    assert len(store.load_transactions()) == 2
    assert json.loads(store.get_meta("balances"))[0]["current"] == 830.00
    assert any(e["event"] == "bank_sync" for e in audit.entries())


def test_sync_without_link_says_how_to_start(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    out = sync(config, store, audit)
    assert "moxie connect" in out["error"]


def test_sync_expired_consent_asks_for_reauth(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    old = (dt.date.today() - dt.timedelta(days=120)).isoformat()
    BankLink(config).save({"provider": "truelayer", "tokens": {},
                           "accounts": [], "consented_at": old + "T09:00:00"})
    out = sync(config, store, audit)
    assert out.get("needs_reauth") is True and "re-consent" in out["error"]
