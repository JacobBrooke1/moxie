"""Phase 6: skills actually drive actions — routes steer, advice informs."""
import textwrap

from moxie.actions import EmailChannel
from moxie.agent import Agent
from moxie.config import Config
from moxie.models import Transaction
from moxie.skills import Skill, SkillRegistry, default_registry, render_draft
from moxie.storage import Store
from moxie.vault import AuditLog


def _write_skill(root, folder, text):
    d = root / folder
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(textwrap.dedent(text), encoding="utf-8")


def _agent(tmp_path, monkeypatch, skills_md: dict, live=False, channels=None):
    skills_root = tmp_path / "skills"
    for folder, text in skills_md.items():
        _write_skill(skills_root, folder, text)
    monkeypatch.setenv("MOXIE_SKILLS", str(skills_root))
    for var in ("MOXIE_LIVE", "MOXIE_SMTP_HOST", "MOXIE_SMTP_USER",
                "MOXIE_SMTP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    if live:
        monkeypatch.setenv("MOXIE_LIVE", "true")
        monkeypatch.setenv("MOXIE_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("MOXIE_SMTP_USER", "me@example.com")
        monkeypatch.setenv("MOXIE_SMTP_PASSWORD", "pw")
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    return Agent(config, store, audit, channels=channels or {}), store, audit


NETFLIX_SKILL = """\
    ---
    name: Cancel Netflix
    merchant: Netflix
    action_type: cancel_subscription
    channel: deeplink
    url: https://www.netflix.com/cancelplan
    ---

    ## Steps

    1. Sign in with your usual account.
    2. Confirm on the cancel button.
    """

BANKROUTE_SKILL = """\
    ---
    name: Dispute via TestBank
    merchant: "*"
    action_type: dispute_charge
    channel: deeplink
    url: https://testbank.example/dispute
    ---

    ## Steps

    1. Email the merchant first; allow 14 days.
    2. Then open the bank dispute page.
    """

EMAIL_SKILL = """\
    ---
    name: Cancel FitClub
    merchant: FitClub
    action_type: cancel_subscription
    channel: email
    email: cancellations@fitclub-verified.example
    ---

    ## Steps

    1. Email from the address on the account.
    """


def _sub_txns(merchant="Netflix", amount=9.99):
    return [Transaction(date=f"2026-0{m}-03", merchant=merchant, amount=amount,
                        currency="£") for m in (3, 4, 5, 6)]


def test_registry_exact_beats_wildcard():
    reg = SkillRegistry()
    reg.skills = [
        Skill(name="wild", merchant="*", action_type="dispute_charge"),
        Skill(name="exact", merchant="CloudHost", action_type="dispute_charge"),
    ]
    got = reg.find(merchant="CloudHost", action_type="dispute_charge")
    assert [s.name for s in got] == ["exact", "wild"]
    got = reg.find(merchant="Anyone", action_type="dispute_charge")
    assert [s.name for s in got] == ["wild"]


def test_route_skill_turns_cancel_into_guided_deeplink(tmp_path, monkeypatch):
    agent, store, audit = _agent(tmp_path, monkeypatch,
                                 {"cancel-netflix": NETFLIX_SKILL})
    actions = agent.scan(_sub_txns())
    a = next(x for x in actions if x.merchant == "Netflix")
    assert a.channel == "deeplink"
    assert "netflix.com/cancelplan" in a.draft
    assert "final click" in a.draft            # honest: the human acts
    # approving it executes the deeplink channel, never email
    action, outcome, note = agent.review(approve_fn=lambda x: True)[0]
    assert outcome == "executed" and "cancelplan" in note


def test_wildcard_skill_advises_but_never_hijacks(tmp_path, monkeypatch):
    agent, store, audit = _agent(tmp_path, monkeypatch,
                                 {"dispute-testbank": BANKROUTE_SKILL})
    txns = [Transaction(date="2026-06-03", merchant="CloudHost", amount=40.0,
                        currency="£") for _ in range(2)]
    actions = agent.scan(txns)
    d = next(x for x in actions if x.kind == "dispute_charge")
    assert d.channel == "email"                       # route untouched
    assert d.draft.lower().startswith("to:")          # still the email draft
    assert "Playbook" in d.rationale and "14 days" in d.rationale


def test_email_skill_fixes_the_support_address(tmp_path, monkeypatch):
    agent, store, audit = _agent(tmp_path, monkeypatch,
                                 {"cancel-fitclub": EMAIL_SKILL})
    actions = agent.scan(_sub_txns(merchant="FitClub", amount=29.99))
    a = next(x for x in actions if x.merchant == "FitClub")
    assert a.draft.startswith("To: cancellations@fitclub-verified.example")


def test_draft_template_overrides_and_renders():
    skill = Skill(
        name="t", merchant="X", action_type="cancel_subscription",
        instructions=("```moxie-draft\nTo: hello@x.example\nSubject: Bye\n\n"
                      "Cancel my {merchant} plan ({currency}{amount}). "
                      "Unknown {stays}.\n```"),
    )
    from moxie.models import ProposedAction
    action = ProposedAction(kind="cancel_subscription", merchant="X",
                            description="", amount=9.99, currency="£")
    out = render_draft(skill, action)
    assert "Cancel my X plan (£9.99)" in out
    assert "{stays}" in out                     # unknown placeholders survive


def test_skill_usage_is_tracked(tmp_path, monkeypatch):
    sent = []

    def transport(msg, smtp):
        sent.append(msg)
        return "<mid@x>"

    agent, store, audit = _agent(
        tmp_path, monkeypatch, {"cancel-fitclub": EMAIL_SKILL}, live=True,
        channels={"email": EmailChannel(transport=transport)})
    agent.scan(_sub_txns(merchant="FitClub", amount=29.99))
    agent.review(approve_fn=lambda a: True)
    stats = store.skill_stats()
    assert stats["Cancel FitClub"]["used"] == 1
    assert stats["Cancel FitClub"]["sent"] == 1
    assert sent[0]["To"] == "cancellations@fitclub-verified.example"


def test_seeded_library_parses_and_routes(tmp_path, monkeypatch):
    monkeypatch.delenv("MOXIE_SKILLS", raising=False)
    reg = default_registry(Config(home=tmp_path / "home"))
    names = {s.name for s in reg.skills}
    assert len(reg.skills) >= 7
    assert "Cancel Netflix" in names and "Dispute a card charge via NatWest (UK)" in names
    netflix = reg.find(merchant="Netflix", action_type="cancel_subscription")[0]
    assert netflix.channel == "deeplink" and netflix.url.startswith("https://www.netflix.com")
    # every seeded route skill declares a usable route (channel or address)
    for s in reg.skills:
        if s.merchant != "*":
            assert s.channel in ("email", "deeplink", "browser") or s.email, \
                f"{s.name} declares no route"
    wild = reg.find(merchant="RandomShop", action_type="dispute_charge")
    assert wild and wild[0].merchant == "*"
