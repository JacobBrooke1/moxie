"""Dashboard API tests over real HTTP on a loopback port."""
import json
import threading
import urllib.request

import pytest

from moxie.config import Config
from moxie.dashboard import _update_env_file, serve
from moxie.models import Transaction
from moxie.storage import Store
from moxie.vault import AuditLog


@pytest.fixture()
def ctx(tmp_path):
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    return config, store, audit


@pytest.fixture()
def server(ctx):
    srv = serve(*ctx, port=0)          # OS-assigned free port
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", ctx
    srv.shutdown()


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _post(url, body, headers=None, with_guard=True):
    h = {"X-Moxie": "1"} if with_guard else {}   # the CSRF header the page JS sends
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _seed(ctx):
    config, store, audit = ctx
    from moxie.agent import Agent
    txns = [Transaction(date=f"2026-0{m}-03", merchant="Omaze", amount=15.0, currency="£")
            for m in (4, 5, 6)]
    store.save_transactions(txns)
    Agent(config, store, audit).scan(txns)


def test_status_shape(server):
    base, ctx = server
    s = _get(base + "/api/status")
    assert s["heartbeat"]["alive"] is True
    assert s["audit"]["intact"] is True
    assert "brain" in s and "telegram" in s


def test_page_serves(server):
    base, ctx = server
    with urllib.request.urlopen(base + "/", timeout=10) as r:
        html = r.read().decode()
    assert "Moxie Dash" in html


def test_findings_and_resolve_via_http(server):
    base, ctx = server
    _seed(ctx)
    findings = _get(base + "/api/findings")
    assert findings and findings[0]["merchant"] == "Omaze"
    out = _post(base + "/api/resolve", {"id": findings[0]["id"], "approved": True})
    assert out["outcome"] == "executed"
    config, store, audit = ctx
    assert any(e["event"] == "action_executed" and e["data"].get("channel") == "dashboard"
               for e in audit.entries())


def test_setup_writes_env_names_not_values_to_audit(server, ctx):
    import os
    base, c = server
    config, store, audit = c
    try:
        out = _post(base + "/api/setup", {"MOXIE_API_KEY": "sk-test-123", "junk": "x"})
        assert out["saved"] == ["MOXIE_API_KEY"]
        env = (config.home / ".env").read_text()
        assert "sk-test-123" in env
        assert not any("sk-test-123" in json.dumps(e) for e in audit.entries())  # never log secrets
    finally:
        os.environ.pop("MOXIE_API_KEY", None)   # don't leak into other tests


def test_post_without_csrf_header_is_403(server):
    import urllib.error
    base, ctx = server
    _seed(ctx)
    try:
        _post(base + "/api/rescan", {}, with_guard=False)
        raise AssertionError("should have been blocked")
    except urllib.error.HTTPError as e:
        assert e.code == 403
        assert "CSRF" in json.loads(e.read())["error"]


def test_dash_token_locks_the_api(server, monkeypatch):
    import urllib.error
    base, ctx = server
    monkeypatch.setenv("MOXIE_DASH_TOKEN", "shh-token")
    try:
        try:
            _get(base + "/api/status")
            raise AssertionError("should need the token")
        except urllib.error.HTTPError as e:
            assert e.code == 401
        s = _get(base + "/api/status",
                 headers={"Authorization": "Bearer shh-token"})
        assert s["heartbeat"]["alive"] is True
        out = _post(base + "/api/rescan", {},
                    headers={"Authorization": "Bearer shh-token"})
        assert "error" in out or "found" in out   # authorized either way
    finally:
        monkeypatch.delenv("MOXIE_DASH_TOKEN", raising=False)


def test_update_env_preserves_other_lines(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\nFOO=bar\nMOXIE_API_KEY=old\n")
    _update_env_file(p, {"MOXIE_API_KEY": "new", "TELEGRAM_BOT_TOKEN": "t"})
    text = p.read_text()
    assert "FOO=bar" in text and "# comment" in text
    assert "MOXIE_API_KEY=new" in text and "MOXIE_API_KEY=old" not in text
    assert "TELEGRAM_BOT_TOKEN=t" in text
