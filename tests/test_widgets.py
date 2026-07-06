"""Chat-built widgets: specs never code. The validator is the security
boundary, so hostile inputs get the most tests in this file."""
import datetime as dt

import pytest

from moxie.config import Config
from moxie.dashboard import Dash
from moxie.models import Transaction
from moxie.storage import Store
from moxie.vault import AuditLog
from moxie.widgets import compute_widget, validate_widget_spec

TODAY = dt.date(2026, 6, 20)


def T(date, merchant, amount):
    return Transaction(date=date, merchant=merchant, amount=amount, currency="£")


# ----------------------------------------------------------------- hostile --
HOSTILE_SPECS = [
    ({"type": "stat_card", "title": "<script>alert(1)</script>",
      "merchants": ["netflix"]}, "script tag in title"),
    ({"type": "stat_card", "title": "ok", "merchants": ["<img src=x>"]},
     "HTML in merchant"),
    ({"type": "stat_card", "title": "ok", "keywords": ["a" * 41]},
     "oversized keyword"),
    ({"type": "stat_card", "title": "x" * 41, "merchants": ["netflix"]},
     "oversized title"),
    ({"type": "totally_new_type", "title": "ok", "merchants": ["netflix"]},
     "unknown type"),
    ({"type": "stat_card", "title": "ok", "merchants": ["netflix"],
      "onclick": "steal()"}, "unknown key"),
    ({"type": "stat_card", "title": "ok", "merchants": ["netflix"],
      "months": 13}, "months too big"),
    ({"type": "stat_card", "title": "ok", "merchants": ["netflix"],
      "months": 0}, "months too small"),
    ({"type": "stat_card", "title": "ok",
      "merchants": [f"m{i}" for i in range(11)]}, "too many merchants"),
    ({"type": "goal_progress", "title": "ok", "merchants": ["netflix"]},
     "goal without target"),
    ({"type": "goal_progress", "title": "ok", "merchants": ["netflix"],
      "target": -5}, "negative target"),
    ({"type": "goal_progress", "title": "ok", "merchants": ["netflix"],
      "target": 99999999999}, "absurd target"),
    ({"type": "stat_card", "title": "ok"}, "no filter at all"),
    ({"type": "layout", "hide": ["heartbeat"], "pin": [], "extra": 1},
     "layout with extra key"),
    ({"type": "layout", "hide": ["not_a_card"], "pin": []},
     "layout with unknown card id"),
    ("just a string", "not an object"),
    ({"type": "stat_card", "title": "ok`s", "merchants": ["netflix"]},
     "backtick smuggling"),
]


@pytest.mark.parametrize("raw,why", HOSTILE_SPECS, ids=[w for _, w in HOSTILE_SPECS])
def test_hostile_specs_are_rejected(raw, why):
    spec, err = validate_widget_spec(raw)
    assert spec is None and err, why


def test_valid_specs_pass_and_normalize():
    spec, err = validate_widget_spec(
        {"type": "merchant_tracker", "title": "  Netflix spend  ",
         "merchants": ["Netflix"], "months": 6})
    assert err is None
    assert spec == {"type": "merchant_tracker", "title": "Netflix spend",
                    "merchants": ["Netflix"], "months": 6}
    spec, err = validate_widget_spec(
        {"type": "goal_progress", "title": "Eating out budget",
         "keywords": ["pret", "deliveroo"], "target": 150})
    assert err is None and spec["target"] == 150.0 and spec["months"] == 3
    spec, err = validate_widget_spec({"type": "trend_chart", "title": "All spend"})
    assert err is None                                  # trend allows no filter
    spec, err = validate_widget_spec(
        {"type": "layout", "hide": ["telegram"], "pin": ["month"]})
    assert err is None
    spec, err = validate_widget_spec(
        {"type": "remove_widget", "title": "Netflix spend"})
    assert err is None


# ----------------------------------------------------------------- compute --
def _netflix_txns():
    return [T("2026-04-03", "Netflix", 9.99), T("2026-05-03", "Netflix", 9.99),
            T("2026-06-03", "Netflix", 9.99), T("2026-06-10", "Corner Grocery", 60.0),
            T("2026-06-28", "Acme Payroll", -2400.0)]


def test_compute_merchant_tracker_series():
    spec, _ = validate_widget_spec({"type": "merchant_tracker",
                                    "title": "Netflix", "merchants": ["netflix"],
                                    "months": 3})
    data = compute_widget(spec, _netflix_txns(), today=TODAY)
    assert [p["month"] for p in data["series"]] == ["2026-04", "2026-05", "2026-06"]
    assert all(p["amount"] == 9.99 for p in data["series"])


def test_compute_goal_progress_uses_current_month():
    spec, _ = validate_widget_spec({"type": "goal_progress", "title": "Food",
                                    "keywords": ["grocery"], "target": 100})
    data = compute_widget(spec, _netflix_txns(), today=TODAY)
    assert data["actual"] == 60.0 and data["target"] == 100.0 and data["pct"] == 60.0


def test_compute_ignores_credits_and_nonmatching():
    spec, _ = validate_widget_spec({"type": "stat_card", "title": "Netflix",
                                    "merchants": ["netflix"], "months": 12})
    data = compute_widget(spec, _netflix_txns(), today=TODAY)
    assert data["value"] == round(9.99 * 3, 2)   # payroll credit never counted


# ----------------------------------------------------------------- Dash -----
class FakeBrain:
    """Returns a reply containing whatever widget block the test wants."""

    def __init__(self, block=None, text="Here's a card for that."):
        self.text = text
        self.block = block

    def __call__(self, payload):
        reply = self.text
        if self.block:
            reply += "\n```moxie-widget\n" + self.block + "\n```\n"
        return {"content": [{"type": "text", "text": reply}]}


@pytest.fixture()
def dash(tmp_path, monkeypatch):
    monkeypatch.setenv("MOXIE_API_KEY", "sk-test")
    monkeypatch.delenv("MOXIE_DASH_TOKEN", raising=False)
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    d = Dash(config, store, audit,
             brain_transport=FakeBrain(
                 '{"type": "merchant_tracker", "title": "Netflix spend", '
                 '"merchants": ["Netflix"], "months": 6}'))
    store.save_transactions(_netflix_txns())
    return d


def test_chat_proposes_but_never_persists(dash):
    out = dash.chat("track my netflix spend as a card")
    assert out["proposal"]["kind"] == "add"
    assert out["proposal"]["spec"]["title"] == "Netflix spend"
    assert "moxie-widget" not in out["reply"]      # block stripped from display
    assert dash.store.load_widgets() == []          # nothing saved without consent


def test_confirming_adds_computes_and_audits(dash):
    proposal = dash.chat("track netflix")["proposal"]
    out = dash.widget_add(proposal["spec"])
    assert out["ok"] is True
    listed = dash.widgets_list()["widgets"]
    assert listed[0]["spec"]["title"] == "Netflix spend"
    assert len(listed[0]["data"]["series"]) == 6
    assert any(e["event"] == "widget_added" and e["data"]["title"] == "Netflix spend"
               for e in dash.audit.entries())
    # …and remove works, audited too
    assert dash.widget_remove(listed[0]["id"])["ok"] is True
    assert dash.widgets_list()["widgets"] == []
    assert any(e["event"] == "widget_removed" for e in dash.audit.entries())


def test_hostile_block_from_model_is_rejected_not_rendered(tmp_path, monkeypatch):
    monkeypatch.setenv("MOXIE_API_KEY", "sk-test")
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    hostile = ('{"type": "stat_card", "title": "<script>steal()</script>", '
               '"merchants": ["x"]}')
    d = Dash(config, store, audit, brain_transport=FakeBrain(hostile))
    out = d.chat("add a card")
    assert "proposal" not in out
    assert "rejected" in out["proposal_rejected"]
    assert "<script>" not in out["reply"]           # stripped with the block
    assert store.load_widgets() == []


def test_direct_post_is_revalidated_server_side(dash):
    """The confirm chip is consent, not the boundary — the API re-validates."""
    out = dash.widget_add({"type": "stat_card", "title": "<b>x</b>",
                           "merchants": ["netflix"]})
    assert "error" in out
    out = dash.widget_add({"type": "remove_widget", "title": "sneaky"})
    assert "error" in out                           # intents aren't addable cards
    out = dash.layout_set({"type": "layout", "hide": ["heartbeat"], "pin": []})
    assert out["ok"] is True
    assert dash.store.get_layout()["hide"] == ["heartbeat"]


def test_remove_via_chat_maps_title_to_id(dash):
    dash.widget_add({"type": "stat_card", "title": "Netflix spend",
                     "merchants": ["netflix"]})
    dash._brain_transport = FakeBrain(
        '{"type": "remove_widget", "title": "netflix spend"}')
    out = dash.chat("remove the netflix card")
    assert out["proposal"]["kind"] == "remove"
    assert dash.widget_remove(out["proposal"]["id"])["ok"] is True


def test_widgets_cannot_trigger_actions(dash, monkeypatch):
    """The Vault boundary holds: no widget path reaches execute_action."""
    import moxie.actions
    import moxie.agent

    def boom(*a, **k):
        raise AssertionError("widgets reached execute_action!")

    monkeypatch.setattr(moxie.actions, "execute_action", boom)
    monkeypatch.setattr(moxie.agent, "execute_action", boom)
    dash.widget_add({"type": "goal_progress", "title": "Budget",
                     "keywords": ["grocery"], "target": 100})
    dash.widgets_list()
    dash.chat("add another card and cancel everything now")


def test_widget_specs_are_sealed_at_rest(tmp_path, monkeypatch):
    cryptography = pytest.importorskip("cryptography")  # noqa: F841
    from moxie.secure import Cipher, generate_key
    store = Store(tmp_path / "m.db", cipher=Cipher(generate_key()))
    store.save_widget("w1", {"type": "stat_card", "title": "SecretCard",
                             "merchants": ["x"], "months": 3})
    assert b"SecretCard" not in (tmp_path / "m.db").read_bytes()
    assert store.load_widgets()[0]["spec"]["title"] == "SecretCard"
