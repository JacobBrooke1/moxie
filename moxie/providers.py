"""Pluggable read-only bank providers (AIS) — the user's choice. Stdlib only.

    moxie connect truelayer     # consent in the provider's own UI, read-only
    moxie sync                  # pull fresh transactions + balances

Providers (pick in the dashboard or CLI; all bring-your-own credentials):

  * truelayer   — UK default (great coverage incl. NatWest; free sandbox)
  * gocardless  — GoCardless Bank Account Data (ex-Nordigen); generous free tier
  * plaid       — strong US coverage (also UK)
  * csv/pdf     — the existing no-cloud fallback (moxie scan --csv/--pdf)

Honesty note: every aggregator is a cloud third party. Because Moxie is
self-hosted and BYO-key, *you* hold the provider account — the Moxie project
runs no servers and never sees your data. CSV/PDF remains the no-cloud path.

Security posture:
  * AIS (read-only) scopes only — Moxie cannot move money by construction.
  * You authenticate in the provider/bank's own UI; bank credentials never
    touch Moxie. Moxie stores only the provider's short-lived tokens, in
    ~/.moxie/bank.json (0600 best-effort; OS-keychain planned — SECURITY.md).
  * UK consents lapse ~90 days; `needs_reauth` surfaces that in the
    dashboard and `moxie doctor`.

Every HTTP call goes through an injectable transport so tests never touch
the network (the same pattern as the brain and Telegram).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.parse
import urllib.request

from .connectors import normalize_merchant
from .models import Transaction

CONSENT_DAYS = 90  # UK open-banking consent window (TrueLayer / GoCardless)


def _http(method: str, url: str, headers=None, data=None) -> dict:
    """Default transport: JSON over urllib. Injectable everywhere."""
    body = None
    headers = dict(headers or {})
    if data is not None:
        if isinstance(data, dict):
            if headers.get("Content-Type") == "application/x-www-form-urlencoded":
                body = urllib.parse.urlencode(data).encode("utf-8")
            else:
                headers.setdefault("Content-Type", "application/json")
                body = json.dumps(data).encode("utf-8")
        else:
            body = data
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


# --------------------------------------------------------------------------- #
# link state — which provider is connected, its tokens, when consent started
# --------------------------------------------------------------------------- #
class BankLink:
    """Persisted link state at ~/.moxie/bank.json (tokens are secrets: 0600,
    and Fernet-encrypted when `moxie encrypt on` has been run)."""

    def __init__(self, config, cipher=None):
        from .secure import Cipher
        self.config = config
        self.path = config.home / "bank.json"
        self.cipher = cipher if cipher is not None else Cipher.from_env()

    def load(self) -> "dict | None":
        from .secure import maybe_decrypt
        try:
            raw = self.path.read_text(encoding="utf-8")
            return json.loads(maybe_decrypt(raw.strip(), self.cipher))
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(state, indent=2)
        if self.cipher:
            blob = self.cipher.encrypt(blob)
        self.path.write_text(blob, encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass  # Windows: chmod is advisory; encryption is the real cover

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def status(self) -> dict:
        state = self.load()
        if not state:
            return {"linked": False, "provider": None}
        days_left = None
        consented = state.get("consented_at")
        if consented:
            try:
                start = dt.date.fromisoformat(consented[:10])
                days_left = CONSENT_DAYS - (dt.date.today() - start).days
            except ValueError:
                pass
        return {
            "linked": True,
            "provider": state.get("provider"),
            "consented_at": consented,
            "consent_days_left": days_left,
            "needs_reauth": days_left is not None and days_left <= 0,
            "last_sync": state.get("last_sync"),
            "accounts": len(state.get("accounts", [])),
        }


# --------------------------------------------------------------------------- #
# the provider interface
# --------------------------------------------------------------------------- #
class AccountProvider:
    """Read-only bank data. Three calls matter:

    start_link()             -> {url, state, hint}   open url, consent at the bank
    complete_link(code, st)  -> link state dict      exchange the redirect result
    fetch(link_state)        -> (transactions, balances, refreshed_state)
    """

    name = "base"
    consent_days: "int | None" = CONSENT_DAYS

    def __init__(self, config, transport=None):
        self.config = config
        self.transport = transport or _http

    # subclasses implement:
    def start_link(self) -> dict:
        raise NotImplementedError

    def complete_link(self, code: str, state: dict) -> dict:
        raise NotImplementedError

    def fetch(self, link_state: dict):
        raise NotImplementedError

    # shared helpers
    @staticmethod
    def _missing(names: "list[str]") -> "list[str]":
        return [n for n in names if not os.environ.get(n)]

    def credentials_hint(self) -> str:
        return ""


class TrueLayerProvider(AccountProvider):
    """UK default. BYO app from console.truelayer.com (free sandbox).

    Env: TRUELAYER_CLIENT_ID, TRUELAYER_CLIENT_SECRET,
         MOXIE_TRUELAYER_ENV=sandbox|live (default sandbox),
         MOXIE_REDIRECT_URI (default http://localhost:8484/callback —
         register the same URI on your TrueLayer app).
    """

    name = "truelayer"

    @property
    def _env(self) -> str:
        return os.environ.get("MOXIE_TRUELAYER_ENV", "sandbox").lower()

    @property
    def _auth_base(self) -> str:
        return ("https://auth.truelayer-sandbox.com" if self._env == "sandbox"
                else "https://auth.truelayer.com")

    @property
    def _api_base(self) -> str:
        return ("https://api.truelayer-sandbox.com" if self._env == "sandbox"
                else "https://api.truelayer.com")

    @property
    def _redirect(self) -> str:
        return os.environ.get("MOXIE_REDIRECT_URI", "http://localhost:8484/callback")

    def credentials_hint(self) -> str:
        return ("create a free app at console.truelayer.com, add "
                f"{self._redirect} as a redirect URI, then set "
                "TRUELAYER_CLIENT_ID and TRUELAYER_CLIENT_SECRET in .env")

    def start_link(self) -> dict:
        missing = self._missing(["TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET"])
        if missing:
            return {"error": f"missing {', '.join(missing)} — {self.credentials_hint()}"}
        params = {
            "response_type": "code",
            "client_id": os.environ["TRUELAYER_CLIENT_ID"],
            "scope": "info accounts balance transactions offline_access",
            "redirect_uri": self._redirect,
            "providers": ("uk-cs-mock uk-ob-all uk-oauth-all" if self._env == "sandbox"
                          else "uk-ob-all uk-oauth-all"),
        }
        return {"url": self._auth_base + "/?" + urllib.parse.urlencode(params),
                "state": {},
                "hint": "consent read-only access at your bank, then paste the "
                        "?code=… value from the redirect URL"}

    def complete_link(self, code: str, state: dict) -> dict:
        data = {
            "grant_type": "authorization_code",
            "client_id": os.environ["TRUELAYER_CLIENT_ID"],
            "client_secret": os.environ["TRUELAYER_CLIENT_SECRET"],
            "redirect_uri": self._redirect,
            "code": code.strip(),
        }
        tokens = self.transport(
            "POST", self._auth_base + "/connect/token",
            {"Content-Type": "application/x-www-form-urlencoded"}, data)
        if "access_token" not in tokens:
            raise RuntimeError(f"TrueLayer token exchange failed: {tokens}")
        accounts = self._accounts(tokens["access_token"])
        return {
            "provider": self.name,
            "tokens": tokens,
            "accounts": accounts,
            "consented_at": dt.datetime.now().isoformat(timespec="seconds"),
        }

    def _refresh(self, tokens: dict) -> dict:
        if not tokens.get("refresh_token"):
            return tokens
        data = {
            "grant_type": "refresh_token",
            "client_id": os.environ["TRUELAYER_CLIENT_ID"],
            "client_secret": os.environ["TRUELAYER_CLIENT_SECRET"],
            "refresh_token": tokens["refresh_token"],
        }
        fresh = self.transport(
            "POST", self._auth_base + "/connect/token",
            {"Content-Type": "application/x-www-form-urlencoded"}, data)
        if "access_token" in fresh:
            fresh.setdefault("refresh_token", tokens["refresh_token"])
            return fresh
        return tokens

    def _accounts(self, access: str) -> "list[dict]":
        out = self.transport("GET", self._api_base + "/data/v1/accounts",
                             {"Authorization": f"Bearer {access}"})
        return [{"id": a["account_id"],
                 "name": a.get("display_name") or a.get("account_type", "account"),
                 "currency": a.get("currency", "GBP")}
                for a in out.get("results", [])]

    def fetch(self, link_state: dict):
        tokens = self._refresh(link_state.get("tokens", {}))
        access = tokens.get("access_token", "")
        headers = {"Authorization": f"Bearer {access}"}
        accounts = link_state.get("accounts") or self._accounts(access)

        txns, balances = [], []
        for acct in accounts:
            cur = "£" if acct.get("currency") == "GBP" else acct.get("currency", "£")
            data = self.transport(
                "GET", f"{self._api_base}/data/v1/accounts/{acct['id']}/transactions", headers)
            for t in data.get("results", []):
                amount = float(t.get("amount", 0))
                # TrueLayer: CREDIT/DEBIT marker; amounts arrive signed.
                # Moxie's convention: spend positive, credits negative.
                kind = (t.get("transaction_type") or "").upper()
                if kind == "DEBIT":
                    amount = abs(amount)
                elif kind == "CREDIT":
                    amount = -abs(amount)
                else:
                    amount = -amount  # signed fallback: negative = debit
                desc = t.get("description", "") or t.get("merchant_name", "")
                txns.append(Transaction(
                    date=(t.get("timestamp", "") or "")[:10],
                    merchant=normalize_merchant(t.get("merchant_name") or desc),
                    amount=round(amount, 2),
                    description=desc,
                    currency=cur,
                ))
            bal = self.transport(
                "GET", f"{self._api_base}/data/v1/accounts/{acct['id']}/balance", headers)
            for b in bal.get("results", []):
                balances.append({"account": acct["name"], "currency": cur,
                                 "available": b.get("available"),
                                 "current": b.get("current")})

        refreshed = dict(link_state)
        refreshed["tokens"] = tokens
        refreshed["accounts"] = accounts
        return txns, balances, refreshed


class GoCardlessProvider(AccountProvider):
    """GoCardless Bank Account Data (ex-Nordigen) — the generous free tier.

    Env: GOCARDLESS_SECRET_ID, GOCARDLESS_SECRET_KEY (from
    bankaccountdata.gocardless.com), MOXIE_GC_INSTITUTION (e.g.
    NATWEST_NWBKGB2L; the CLI lists options), MOXIE_REDIRECT_URI.
    """

    name = "gocardless"
    BASE = "https://bankaccountdata.gocardless.com/api/v2"

    def credentials_hint(self) -> str:
        return ("create free 'user secrets' at bankaccountdata.gocardless.com "
                "and set GOCARDLESS_SECRET_ID / GOCARDLESS_SECRET_KEY in .env; "
                "set MOXIE_GC_INSTITUTION to your bank's id "
                "(e.g. NATWEST_NWBKGB2L — see `moxie connect gocardless --banks`)")

    def _token(self) -> str:
        out = self.transport("POST", self.BASE + "/token/new/", {}, {
            "secret_id": os.environ["GOCARDLESS_SECRET_ID"],
            "secret_key": os.environ["GOCARDLESS_SECRET_KEY"],
        })
        if "access" not in out:
            raise RuntimeError(f"GoCardless auth failed: {out}")
        return out["access"]

    def list_banks(self, country: str = "gb") -> "list[dict]":
        token = self._token()
        out = self.transport("GET", self.BASE + f"/institutions/?country={country}",
                             {"Authorization": f"Bearer {token}"})
        return [{"id": b["id"], "name": b["name"]} for b in out] if isinstance(out, list) else []

    def start_link(self) -> dict:
        missing = self._missing(["GOCARDLESS_SECRET_ID", "GOCARDLESS_SECRET_KEY"])
        if missing:
            return {"error": f"missing {', '.join(missing)} — {self.credentials_hint()}"}
        institution = os.environ.get("MOXIE_GC_INSTITUTION")
        if not institution:
            return {"error": "set MOXIE_GC_INSTITUTION first — " + self.credentials_hint()}
        token = self._token()
        redirect = os.environ.get("MOXIE_REDIRECT_URI", "http://localhost:8484/callback")
        req = self.transport("POST", self.BASE + "/requisitions/",
                             {"Authorization": f"Bearer {token}"},
                             {"redirect": redirect, "institution_id": institution})
        if "id" not in req:
            raise RuntimeError(f"GoCardless requisition failed: {req}")
        return {"url": req.get("link", ""),
                "state": {"requisition_id": req["id"]},
                "hint": "consent at your bank; when you land back, run the "
                        "complete step (no code needed — press Enter)"}

    def complete_link(self, code: str, state: dict) -> dict:
        # No code to exchange: completion = the requisition now lists accounts.
        token = self._token()
        rid = state.get("requisition_id", code.strip())
        req = self.transport("GET", self.BASE + f"/requisitions/{rid}/",
                             {"Authorization": f"Bearer {token}"})
        accounts = req.get("accounts") or []
        if not accounts:
            raise RuntimeError(
                "no accounts on the requisition yet — finish the consent flow "
                "at your bank, then retry")
        return {
            "provider": self.name,
            "requisition_id": rid,
            "accounts": [{"id": a, "name": f"account-{i+1}", "currency": "GBP"}
                         for i, a in enumerate(accounts)],
            "consented_at": dt.datetime.now().isoformat(timespec="seconds"),
        }

    def fetch(self, link_state: dict):
        token = self._token()
        headers = {"Authorization": f"Bearer {token}"}
        txns, balances = [], []
        for acct in link_state.get("accounts", []):
            data = self.transport(
                "GET", self.BASE + f"/accounts/{acct['id']}/transactions/", headers)
            for t in (data.get("transactions", {}) or {}).get("booked", []):
                ta = t.get("transactionAmount", {}) or {}
                raw = float(ta.get("amount", "0") or 0)
                cur = "£" if ta.get("currency") == "GBP" else ta.get("currency", "£")
                desc = (t.get("remittanceInformationUnstructured")
                        or t.get("creditorName") or t.get("debtorName") or "")
                merchant = t.get("creditorName") if raw < 0 else (t.get("debtorName") or desc)
                txns.append(Transaction(
                    date=t.get("bookingDate", ""),
                    merchant=normalize_merchant(merchant or desc),
                    amount=round(-raw, 2),   # GC: negative = debit -> Moxie: spend positive
                    description=desc,
                    currency=cur,
                ))
            bal = self.transport(
                "GET", self.BASE + f"/accounts/{acct['id']}/balances/", headers)
            for b in bal.get("balances", []):
                amt = (b.get("balanceAmount", {}) or {})
                balances.append({"account": acct.get("name", acct["id"]),
                                 "currency": "£" if amt.get("currency") == "GBP"
                                             else amt.get("currency", "£"),
                                 "available": float(amt.get("amount", 0) or 0),
                                 "current": float(amt.get("amount", 0) or 0)})
        return txns, balances, dict(link_state)


class PlaidProvider(AccountProvider):
    """Plaid — strong US coverage (also UK). BYO keys from dashboard.plaid.com.

    Env: PLAID_CLIENT_ID, PLAID_SECRET, MOXIE_PLAID_ENV=sandbox|production.
    Uses Hosted Link so no widget embedding is needed: start_link returns a
    plaid.com URL to finish in the browser.
    """

    name = "plaid"
    consent_days = None  # Plaid items don't hard-expire on a 90-day clock

    @property
    def _base(self) -> str:
        env = os.environ.get("MOXIE_PLAID_ENV", "sandbox").lower()
        return f"https://{env}.plaid.com"

    def credentials_hint(self) -> str:
        return ("get free sandbox keys at dashboard.plaid.com and set "
                "PLAID_CLIENT_ID / PLAID_SECRET in .env")

    def _creds(self) -> dict:
        return {"client_id": os.environ["PLAID_CLIENT_ID"],
                "secret": os.environ["PLAID_SECRET"]}

    def start_link(self) -> dict:
        missing = self._missing(["PLAID_CLIENT_ID", "PLAID_SECRET"])
        if missing:
            return {"error": f"missing {', '.join(missing)} — {self.credentials_hint()}"}
        out = self.transport("POST", self._base + "/link/token/create", {}, {
            **self._creds(),
            "client_name": "Moxie (self-hosted)",
            "language": "en",
            "country_codes": ["GB", "US"],
            "user": {"client_user_id": "moxie-local"},
            "products": ["transactions"],
            "hosted_link": {},
        })
        if "link_token" not in out:
            raise RuntimeError(f"Plaid link-token failed: {out}")
        return {"url": out.get("hosted_link_url", ""),
                "state": {"link_token": out["link_token"]},
                "hint": "finish linking in the browser, then run the complete "
                        "step (no code needed — press Enter)"}

    def complete_link(self, code: str, state: dict) -> dict:
        got = self.transport("POST", self._base + "/link/token/get", {}, {
            **self._creds(), "link_token": state.get("link_token", code.strip()),
        })
        sessions = got.get("link_sessions") or []
        public_token = None
        for s in sessions:
            public_token = ((s.get("results", {}).get("item_add_results") or [{}])[0]
                            .get("public_token"))
            if public_token:
                break
        if not public_token:
            raise RuntimeError("link not completed yet — finish the Plaid flow, then retry")
        ex = self.transport("POST", self._base + "/item/public_token/exchange", {}, {
            **self._creds(), "public_token": public_token,
        })
        if "access_token" not in ex:
            raise RuntimeError(f"Plaid exchange failed: {ex}")
        return {
            "provider": self.name,
            "tokens": {"access_token": ex["access_token"]},
            "accounts": [],
            "consented_at": dt.datetime.now().isoformat(timespec="seconds"),
        }

    def fetch(self, link_state: dict):
        access = link_state.get("tokens", {}).get("access_token", "")
        out = self.transport("POST", self._base + "/transactions/get", {}, {
            **self._creds(), "access_token": access,
            "start_date": (dt.date.today() - dt.timedelta(days=365)).isoformat(),
            "end_date": dt.date.today().isoformat(),
            "options": {"count": 500},
        })
        txns = []
        for t in out.get("transactions", []):
            cur = "$" if t.get("iso_currency_code") == "USD" else (
                "£" if t.get("iso_currency_code") == "GBP" else "£")
            txns.append(Transaction(
                date=t.get("date", ""),
                merchant=normalize_merchant(t.get("merchant_name") or t.get("name", "")),
                amount=round(float(t.get("amount", 0)), 2),  # Plaid: positive = money out
                description=t.get("name", ""),
                currency=cur,
            ))
        balances = []
        for a in out.get("accounts", []):
            b = a.get("balances", {}) or {}
            cur = "$" if b.get("iso_currency_code") == "USD" else "£"
            balances.append({"account": a.get("name", "account"), "currency": cur,
                             "available": b.get("available"), "current": b.get("current")})
        refreshed = dict(link_state)
        refreshed["accounts"] = [{"id": a.get("account_id"), "name": a.get("name"),
                                  "currency": "GBP"} for a in out.get("accounts", [])]
        return txns, balances, refreshed


PROVIDERS = {
    "truelayer": TrueLayerProvider,
    "gocardless": GoCardlessProvider,
    "plaid": PlaidProvider,
}


def get_provider(name: str, config, transport=None) -> AccountProvider:
    try:
        cls = PROVIDERS[name.lower().strip()]
    except KeyError:
        raise ValueError(
            f"unknown provider {name!r} — choose from: {', '.join(sorted(PROVIDERS))}, "
            "or stay no-cloud with `moxie scan --csv/--pdf`")
    return cls(config, transport=transport)


# --------------------------------------------------------------------------- #
# the sync entry point (CLI + dashboard both call this)
# --------------------------------------------------------------------------- #
def sync(config, store, audit, transport=None) -> dict:
    """Pull transactions + balances from the linked provider into the same
    store the CSV/PDF path uses. Everything downstream is unchanged."""
    link = BankLink(config)
    state = link.load()
    if not state:
        return {"error": "no bank linked — run `moxie connect <provider>` first "
                         "(or stay no-cloud with `moxie scan --csv/--pdf`)"}
    status = link.status()
    if status.get("needs_reauth"):
        return {"error": f"consent expired (~{CONSENT_DAYS} days, UK rule) — "
                         f"run `moxie connect {state['provider']}` to re-consent",
                "needs_reauth": True}
    provider = get_provider(state["provider"], config, transport=transport)
    txns, balances, refreshed = provider.fetch(state)
    if txns:
        store.save_transactions(txns)
    store.set_meta("balances", json.dumps(balances))
    store.set_meta("last_bank_sync", dt.datetime.now().isoformat(timespec="seconds"))
    refreshed["last_sync"] = dt.datetime.now().isoformat(timespec="seconds")
    link.save(refreshed)
    audit.append("bank_sync", {"provider": state["provider"],
                               "transactions": len(txns), "accounts": len(balances)})
    return {"provider": state["provider"], "transactions": len(txns),
            "balances": balances}
