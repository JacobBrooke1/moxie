"""Decision memory: skip once, and Moxie stays quiet about it."""
import datetime as dt

from moxie.agent import Agent
from moxie.config import Config
from moxie.models import Transaction
from moxie.storage import Store
from moxie.vault import AuditLog


def _ctx(tmp_path):
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    return Agent(config, store, audit), store


def _netflix():
    return [Transaction(date=f"2026-0{m}-03", merchant="Netflix", amount=9.99, currency="£")
            for m in (4, 5, 6)]


def test_skip_suppresses_next_scan(tmp_path):
    agent, store = _ctx(tmp_path)
    assert len(agent.scan(_netflix())) == 1
    agent.review(approve_fn=lambda a: False)          # you said no
    assert agent.scan(_netflix()) == []               # so it stays quiet
    assert agent.last_suppressed == 1


def test_execute_also_suppresses(tmp_path):
    agent, store = _ctx(tmp_path)
    agent.scan(_netflix())
    agent.review(approve_fn=lambda a: True)           # acted on it
    assert agent.scan(_netflix()) == []


def test_snooze_expires(tmp_path):
    agent, store = _ctx(tmp_path)
    agent.scan(_netflix())
    agent.review(approve_fn=lambda a: False)
    old = (dt.date.today() - dt.timedelta(days=61)).isoformat()
    store.save_decision("Netflix", "cancel_subscription", "skipped", date=old)
    assert len(agent.scan(_netflix())) == 1           # 60 days later, fair to re-ask


def test_resolve_single_action(tmp_path):
    agent, store = _ctx(tmp_path)
    actions = agent.scan(_netflix())
    action, outcome, note = agent.resolve(actions[0].id, True, channel="telegram")
    assert outcome == "executed"
    assert agent.resolve(actions[0].id, True) is None  # already handled
