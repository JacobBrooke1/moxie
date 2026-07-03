"""The front door: one command, browser opens, wizard sets everything up.
Fake brain transports only; no network, no real browser."""
import json
import threading
import urllib.request

import pytest

from moxie.config import Config
from moxie.dashboard import Dash, maybe_open_browser, serve
from moxie.storage import Store
from moxie.vault import AuditLog

SAMPLE_CSV = """Date,Description,Amount
2026-04-02,FITCLUB,-29.99
2026-05-02,FITCLUB,-29.99
2026-06-02,FITCLUB,-29.99
2026-06-09,CORNER GROCERY,-63.20
"""


class FakeBrainTransport:
    def __init__(self, fail=False):
        self.fail = fail
        self.payloads = []

    def __call__(self, payload):
        if self.fail:
            raise ConnectionError("401 bad key")
        self.payloads.append(payload)
        return {"content": [{"type": "text", "text": "ready"}]}


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    for var in ("MOXIE_API_KEY", "MOXIE_OFFLINE", "MOXIE_MODEL",
                "TELEGRAM_BOT_TOKEN", "MOXIE_TELEGRAM_CHAT_ID", "MOXIE_DASH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    return config, store, audit


def _server(ctx, dash=None):
    srv = serve(*ctx, port=0, dash=dash)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"X-Moxie": "1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------- wizard ----
def test_fresh_home_reports_unconfigured_setup(ctx):
    srv, base = _server(ctx)
    try:
        s = _get(base + "/api/status")["setup"]
        assert s == {"brain_ready": False, "has_data": False,
                     "telegram_paired": False, "wizard_done": False}
    finally:
        srv.shutdown()


def test_page_carries_the_wizard(ctx):
    srv, base = _server(ctx)
    try:
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            html = r.read().decode()
        assert "Welcome. Three steps" in html
        assert "Try with sample data" in html
    finally:
        srv.shutdown()


def test_brain_test_without_key_is_honest(ctx):
    srv, base = _server(ctx)
    try:
        out = _post(base + "/api/brain/test", {})
        assert out["ok"] is False and "no key" in out["error"]
    finally:
        srv.shutdown()


def test_brain_test_with_key_and_fake_transport(ctx, monkeypatch):
    monkeypatch.setenv("MOXIE_API_KEY", "sk-test")
    config, store, audit = ctx
    fake = FakeBrainTransport()
    dash = Dash(config, store, audit, brain_transport=fake)
    out = dash.brain_test()
    assert out["ok"] is True and out["reply"] == "ready"
    assert any(e["event"] == "brain_tested" for e in audit.entries())


def test_brain_test_bad_key_reports_the_failure(ctx, monkeypatch):
    monkeypatch.setenv("MOXIE_API_KEY", "sk-broken")
    config, store, audit = ctx
    dash = Dash(config, store, audit, brain_transport=FakeBrainTransport(fail=True))
    out = dash.brain_test()
    assert out["ok"] is False and "didn't work" in out["error"]


def test_csv_import_in_browser(ctx):
    srv, base = _server(ctx)
    try:
        out = _post(base + "/api/import/csv",
                    {"name": "statement.csv", "text": SAMPLE_CSV})
        assert out["transactions"] == 4
        assert out["found"] >= 1                    # FitClub sub detected
        s = _get(base + "/api/status")
        assert s["setup"]["has_data"] is True
    finally:
        srv.shutdown()


def test_csv_import_rejects_garbage_kindly(ctx):
    srv, base = _server(ctx)
    try:
        out = _post(base + "/api/import/csv", {"name": "x.csv", "text": "not,a\nbank,file"})
        assert "error" in out
        out = _post(base + "/api/import/csv", {"name": "x.csv", "text": "  "})
        assert out["error"] == "empty file"
    finally:
        srv.shutdown()


def test_sample_data_button(ctx):
    srv, base = _server(ctx)
    try:
        out = _post(base + "/api/demo", {})
        assert out["transactions"] == 9 and out["found"] == 3 and out["sample"]
    finally:
        srv.shutdown()


def test_wizard_done_persists(ctx):
    config, store, audit = ctx
    srv, base = _server(ctx)
    try:
        assert _get(base + "/api/status")["setup"]["wizard_done"] is False
        assert _post(base + "/api/wizard/done", {})["ok"] is True
        assert _get(base + "/api/status")["setup"]["wizard_done"] is True
        assert store.get_meta("wizard_done") == "1"
    finally:
        srv.shutdown()


def test_favicon_served_locally(ctx):
    srv, base = _server(ctx)
    try:
        with urllib.request.urlopen(base + "/favicon.svg", timeout=10) as r:
            assert r.headers["Content-Type"] == "image/svg+xml"
            assert b"<svg" in r.read()
    finally:
        srv.shutdown()


def test_page_is_mobile_ready(ctx):
    srv, base = _server(ctx)
    try:
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            html = r.read().decode()
        assert 'name="viewport"' in html
        assert "@media (max-width: 640px)" in html
        assert '<link rel="icon" href="/favicon.svg"' in html
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- browser ---
def test_browser_open_respects_no_browser_env(monkeypatch):
    monkeypatch.setenv("MOXIE_NO_BROWSER", "1")
    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u) or True)
    assert maybe_open_browser("http://127.0.0.1:8484") is False
    assert opened == []


def test_browser_open_skips_headless(monkeypatch):
    monkeypatch.delenv("MOXIE_NO_BROWSER", raising=False)
    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u) or True)
    # pytest's stdin/stdout aren't ttys -> never spawn a browser from CI
    assert maybe_open_browser("http://127.0.0.1:8484") is False
    assert opened == []


def test_browser_open_when_forced(monkeypatch):
    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u) or True)
    assert maybe_open_browser("http://127.0.0.1:8484", force=True) is True
    assert opened == ["http://127.0.0.1:8484"]


# ---------------------------------------------------------------- bare moxie
def test_bare_moxie_maps_tty_to_dashboard(monkeypatch):
    import sys
    from moxie.cli import _no_command_action
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert _no_command_action() == "dashboard"
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert _no_command_action() == "help"
