"""Hosted-mode security: real login, session cookies, and the refusal to
expose the keys-and-approvals surface unauthenticated."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from moxie.config import Config
from moxie.dashboard import Dash, _check_bind_safety, serve
from moxie.storage import Store
from moxie.vault import AuditLog


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.delenv("MOXIE_DASH_TOKEN", raising=False)
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    return config, store, audit


def _server(ctx):
    srv = serve(*ctx, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _req(url, body=None, headers=None):
    h = {"X-Moxie": "1"} if body is not None else {}
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=h)
    resp = urllib.request.urlopen(req, timeout=10)
    return resp, json.loads(resp.read()) if "json" in resp.headers.get(
        "Content-Type", "") else resp.read().decode()


# ------------------------------------------------------------- bind safety --
def test_loopback_without_token_is_fine():
    _check_bind_safety("127.0.0.1")
    _check_bind_safety("localhost")


def test_non_loopback_without_token_refuses(monkeypatch):
    monkeypatch.delenv("MOXIE_DASH_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="MOXIE_DASH_TOKEN"):
        _check_bind_safety("0.0.0.0")


def test_non_loopback_with_token_is_allowed(monkeypatch):
    monkeypatch.setenv("MOXIE_DASH_TOKEN", "sesame")
    _check_bind_safety("0.0.0.0")   # no raise


def test_serve_applies_the_guard(ctx, monkeypatch):
    monkeypatch.delenv("MOXIE_DASH_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        serve(*ctx, port=0, host="0.0.0.0")


# ------------------------------------------------------------- login flow ---
def test_full_login_session_logout_cycle(ctx, monkeypatch):
    monkeypatch.setenv("MOXIE_DASH_TOKEN", "sesame")
    srv, base = _server(ctx)
    try:
        # unauthenticated: the page is the login gate, the API is 401
        _, html = _req(base + "/")
        assert "sign in" in html.lower()
        assert "Chat with Moxie" not in html      # the real dashboard stays hidden
        with pytest.raises(urllib.error.HTTPError) as e:
            _req(base + "/api/status")
        assert e.value.code == 401

        # wrong token: 401
        with pytest.raises(urllib.error.HTTPError) as e:
            _req(base + "/api/login", {"token": "nope"})
        assert e.value.code == 401

        # right token: session cookie
        resp, out = _req(base + "/api/login", {"token": "sesame"})
        assert out["ok"] is True
        cookie = resp.headers["Set-Cookie"]
        assert "moxie_session=" in cookie and "HttpOnly" in cookie
        session = cookie.split("moxie_session=")[1].split(";")[0]

        # the cookie authorizes both page and API
        _, page = _req(base + "/", headers={"Cookie": f"moxie_session={session}"})
        assert "Chat with Moxie" in page
        _, status = _req(base + "/api/status",
                         headers={"Cookie": f"moxie_session={session}"})
        assert status["heartbeat"]["alive"] is True

        # bearer keeps working for curl/scripts
        _, status = _req(base + "/api/status",
                         headers={"Authorization": "Bearer sesame"})
        assert status["heartbeat"]["alive"] is True

        # logout kills the session
        _req(base + "/api/logout", {}, headers={"Cookie": f"moxie_session={session}"})
        with pytest.raises(urllib.error.HTTPError) as e:
            _req(base + "/api/status", headers={"Cookie": f"moxie_session={session}"})
        assert e.value.code == 401
    finally:
        srv.shutdown()


def test_login_rate_limits_after_five_failures(ctx, monkeypatch):
    monkeypatch.setenv("MOXIE_DASH_TOKEN", "sesame")
    config, store, audit = ctx
    dash = Dash(config, store, audit)
    for _ in range(5):
        assert dash.login("wrong")["error"] == "wrong token"
    out = dash.login("wrong")
    assert out.get("locked") is True
    # even the RIGHT token is locked out during the window — brute force dies
    assert dash.login("sesame").get("locked") is True
    assert any(e["event"] == "login_failed" for e in audit.entries())


def test_no_token_means_open_localhost_as_before(ctx):
    srv, base = _server(ctx)
    try:
        _, html = _req(base + "/")
        assert "Chat with Moxie" in html          # straight to the dashboard
    finally:
        srv.shutdown()
