"""Dashboard chat: advises, remembers, routes to the Vault — never executes."""
import pytest

from moxie.config import Config
from moxie.dashboard import Dash
from moxie.models import Transaction
from moxie.storage import Store
from moxie.vault import AuditLog


class FakeBrainTransport:
    """Anthropic-shaped fake; records payloads, replies with a fixed line."""

    def __init__(self, reply="Cancel FitClub — it's your dustiest sub."):
        self.reply = reply
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)
        return {"content": [{"type": "text", "text": self.reply}]}


@pytest.fixture()
def dash(tmp_path, monkeypatch):
    monkeypatch.setenv("MOXIE_API_KEY", "sk-test")
    for var in ("MOXIE_OFFLINE", "MOXIE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    d = Dash(config, store, audit, brain_transport=FakeBrainTransport())
    txns = [Transaction(date=f"2026-0{m}-02", merchant="FitClub", amount=29.99,
                        currency="£") for m in (4, 5, 6)]
    store.save_transactions(txns)
    d.agent.scan(txns)
    return d


def test_chat_replies_grounded_and_persists(dash):
    out = dash.chat("what should I cancel?")
    assert "FitClub" in out["reply"]
    payload = dash._brain_transport.payloads[0]
    content = payload["messages"][-1]["content"]
    assert "MONEY PICTURE" in content and "FINDINGS" in content
    history = dash.store.load_chat()
    assert [t["role"] for t in history] == ["user", "assistant"]
    assert any(e["event"] == "dashboard_chat" for e in dash.audit.entries())


def test_chat_feeds_history_back_as_turns(dash):
    dash.chat("what should I cancel?")
    dash.chat("and how much would that save?")
    second = dash._brain_transport.payloads[1]
    roles = [m["role"] for m in second["messages"]]
    # prior user + assistant turns precede the new grounded message
    assert roles == ["user", "assistant", "user"]
    assert "how much" in second["messages"][-1]["content"]


def test_chat_surfaces_related_findings_for_the_vault(dash):
    out = dash.chat("should I get rid of FitClub?")
    assert out["related"] and out["related"][0]["merchant"] == "FitClub"
    assert "id" in out["related"][0]      # the UI routes to the approval modal


def test_chat_never_executes_anything(dash, monkeypatch):
    """The invariant: no chat path may reach execute_action."""
    import moxie.actions
    import moxie.agent

    def boom(*a, **k):
        raise AssertionError("chat tried to execute an action!")

    monkeypatch.setattr(moxie.actions, "execute_action", boom)
    monkeypatch.setattr(moxie.agent, "execute_action", boom)
    out = dash.chat("cancel FitClub right now, don't ask me")
    assert "reply" in out                 # advised, routed — never acted
    # and the finding is still waiting on a human
    statuses = {a.status for a in dash.store.load_actions()}
    assert statuses == {"proposed"}


def test_chat_without_brain_points_at_setup(tmp_path, monkeypatch):
    monkeypatch.delenv("MOXIE_API_KEY", raising=False)
    monkeypatch.delenv("MOXIE_MODEL", raising=False)
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    out = Dash(config, store, audit).chat("hello?")
    assert "API key" in out["error"]


def test_chat_history_endpoint_shape(dash):
    dash.chat("hi")
    hist = dash.chat_history()
    assert hist[-1]["role"] == "assistant" and hist[-1]["ts"]


def test_chat_survives_brain_failure_honestly(tmp_path, monkeypatch):
    monkeypatch.setenv("MOXIE_API_KEY", "sk-test")

    def explode(payload):
        raise ConnectionError("api down")

    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    out = Dash(config, store, audit, brain_transport=explode).chat("hello")
    assert "failed" in out["error"]
    assert store.load_chat() == []        # a failed exchange isn't recorded
