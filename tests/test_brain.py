"""Brain tests: offline, with a fake transport."""
from moxie.brain import Brain, ensure_instructions
from moxie.config import Config
from moxie.models import ProposedAction, Transaction


class FakeTransport:
    def __init__(self):
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)
        return {"content": [{"type": "text", "text": "badger says ok"}]}


def _config(tmp_path, monkeypatch, key="test-key"):
    if key:
        monkeypatch.setenv("MOXIE_API_KEY", key)
    else:
        monkeypatch.delenv("MOXIE_API_KEY", raising=False)
    monkeypatch.delenv("MOXIE_OFFLINE", raising=False)
    return Config(home=tmp_path / "home")


def test_unavailable_without_key(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, key=None)
    assert Brain(config).available is False


def test_ask_grounds_in_data_and_guardrails(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    fake = FakeTransport()
    brain = Brain(config, transport=fake)
    txns = [Transaction(date="2026-06-01", merchant="Omaze", amount=15.0, currency="£")]
    acts = [ProposedAction(kind="cancel_subscription", merchant="Omaze",
                           description="Recurring £15.00/mo at Omaze", currency="£")]
    out = brain.ask("should I cancel Omaze?", txns, acts)
    assert out == "badger says ok"
    payload = fake.payloads[0]
    assert "Omaze" in payload["messages"][0]["content"]
    assert "cannot" in payload["system"].lower()        # can't-move-money rule
    assert "untrusted" in payload["system"].lower()     # injection guardrail


def test_instructions_created_and_editable(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    path = ensure_instructions(config)
    assert path.exists() and "honey" in path.read_text().lower()
    path.write_text("Only ever answer in haiku.")
    fake = FakeTransport()
    Brain(config, transport=fake).ask("hi", [], [])
    assert "haiku" in fake.payloads[0]["system"]        # user's edits are honored
