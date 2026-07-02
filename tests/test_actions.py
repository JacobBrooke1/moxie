"""Action layer tests — fake transports/drivers only; nothing touches the world.

The invariants under test are the brand:
  * default (no MOXIE_LIVE) = drafts, identical to the original scaffold
  * kill switch beats MOXIE_LIVE
  * live email goes through the injected transport with the draft the user saw
  * failures come back as honest results, never fake "sent"
"""

from moxie.actions import (BrowserChannel, EmailChannel,
                           execute_action, parse_draft, skill_steps)
from moxie.agent import Agent
from moxie.config import Config
from moxie.models import ProposedAction, Transaction
from moxie.skills import Skill
from moxie.storage import Store
from moxie.vault import AuditLog


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #
def _config(tmp_path, monkeypatch, live=False, smtp=True, browser_ok=False):
    for var in ("MOXIE_LIVE", "MOXIE_BROWSER_OK", "MOXIE_SMTP_HOST",
                "MOXIE_SMTP_USER", "MOXIE_SMTP_PASSWORD", "MOXIE_SMTP_FROM",
                "MOXIE_EMAIL_OVERRIDE_TO"):
        monkeypatch.delenv(var, raising=False)
    if live:
        monkeypatch.setenv("MOXIE_LIVE", "true")
    if browser_ok:
        monkeypatch.setenv("MOXIE_BROWSER_OK", "true")
    if smtp:
        monkeypatch.setenv("MOXIE_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("MOXIE_SMTP_USER", "me@example.com")
        monkeypatch.setenv("MOXIE_SMTP_PASSWORD", "app-password")
    return Config(home=tmp_path / "home")


def _action(**kw):
    defaults = dict(
        kind="cancel_subscription", merchant="FitClub",
        description="Recurring £29.99/mo at FitClub",
        draft=("To: support@fitclub.com\nSubject: Cancel my subscription\n\n"
               "Hello,\n\nPlease cancel my FitClub subscription.\n\nThank you."),
        est_savings=359.88, currency="£",
    )
    defaults.update(kw)
    return ProposedAction(**defaults)


class FakeSMTP:
    """Injectable email transport: records the message, returns a message id."""

    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def __call__(self, msg, smtp):
        if self.fail:
            raise ConnectionError("SMTP said no")
        self.messages.append(msg)
        return "<fake-id@moxie>"


class FakeDriver:
    """Injectable browser driver: records steps."""

    def __init__(self, fail_at=None):
        self.ran = []
        self.fail_at = fail_at
        self.closed = False

    def run(self, verb, arg):
        if self.fail_at is not None and len(self.ran) + 1 >= self.fail_at:
            raise RuntimeError("selector not found")
        self.ran.append((verb, arg))

    def confirmation(self):
        return "https://fitclub.example/cancelled"

    def close(self):
        self.closed = True


# --------------------------------------------------------------------------- #
# the gates
# --------------------------------------------------------------------------- #
def test_default_is_dry_run_draft():
    """No config at all (legacy call) -> a draft, never a send."""
    out = execute_action(_action())
    assert out["dry_run"] is True and out["sent"] is False
    assert "draft" in out["note"].lower()
    assert "sent" not in out["note"].lower().replace("nothing was sent", "")


def test_live_flag_off_means_dry_run(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=False)
    out = execute_action(_action(), config)
    assert out["dry_run"] is True and out["sent"] is False
    assert "MOXIE_LIVE" in out["note"]


def test_kill_switch_beats_live_flag(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=True)
    config.home.mkdir(parents=True, exist_ok=True)
    config.kill_path.write_text("engaged\n")
    out = execute_action(_action(), config)
    assert out["dry_run"] is True and out["sent"] is False
    assert "kill switch" in out["note"].lower()


# --------------------------------------------------------------------------- #
# tier 1: email
# --------------------------------------------------------------------------- #
def test_live_email_sends_via_injected_transport(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=True)
    fake = FakeSMTP()
    out = execute_action(_action(), config,
                         channels={"email": EmailChannel(transport=fake)})
    assert out["sent"] is True and out["dry_run"] is False
    assert out["reference"] == "<fake-id@moxie>"
    msg = fake.messages[0]
    assert msg["To"] == "support@fitclub.com"
    assert msg["Subject"] == "Cancel my subscription"
    assert "Please cancel my FitClub subscription" in msg.get_content()
    assert msg["From"] == "me@example.com"


def test_email_refuses_without_smtp_config(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=True, smtp=False)
    out = execute_action(_action(), config,
                         channels={"email": EmailChannel(transport=FakeSMTP())})
    assert out["sent"] is False and "SMTP not configured" in out["error"]


def test_email_refuses_implausible_recipient(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=True)
    bad = _action(draft="Subject: hi\n\nno To header here")
    out = execute_action(bad, config,
                         channels={"email": EmailChannel(transport=FakeSMTP())})
    assert out["sent"] is False and "refusing to send" in out["error"]


def test_email_failure_is_honest(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=True)
    out = execute_action(_action(), config,
                         channels={"email": EmailChannel(transport=FakeSMTP(fail=True))})
    assert out["sent"] is False and "send failed" in out["error"]


def test_override_to_reroutes_for_testing(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=True)
    monkeypatch.setenv("MOXIE_EMAIL_OVERRIDE_TO", "me+test@example.com")
    fake = FakeSMTP()
    execute_action(_action(), config, channels={"email": EmailChannel(transport=fake)})
    assert fake.messages[0]["To"] == "me+test@example.com"


def test_parse_draft_roundtrip():
    parts = parse_draft("To: a@b.co\nSubject: Hi there\n\nline one\nline two")
    assert parts == {"to": "a@b.co", "subject": "Hi there", "body": "line one\nline two"}
    # headerless drafts are all body
    parts = parse_draft("just words")
    assert parts["to"] == "" and parts["body"] == "just words"


# --------------------------------------------------------------------------- #
# tier 2: guided deep-link
# --------------------------------------------------------------------------- #
def _deeplink_skill():
    return Skill(
        name="cancel-fitclub", merchant="FitClub", action_type="cancel_subscription",
        channel="deeplink", url="https://fitclub.example/account/cancel",
        instructions=("## Steps\n\n1. Log in with your usual email.\n"
                      "2. Click 'Membership' then 'Cancel'.\n"
                      "3. Decline the retention offer.\n"),
    )


def test_deeplink_returns_url_and_steps_never_sent(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=True)
    out = execute_action(_action(), config, skill=_deeplink_skill())
    assert out["channel"] == "deeplink"
    assert out["sent"] is False                      # honest: the human clicks
    assert out["url"] == "https://fitclub.example/account/cancel"
    assert len(out["steps"]) == 3 and "retention" in out["steps"][2]


def test_deeplink_same_guidance_in_drafts_mode(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=False)
    out = execute_action(_action(), config, skill=_deeplink_skill())
    assert out["channel"] == "deeplink" and out["url"].startswith("https://")


def test_deeplink_without_url_fails_honestly(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=True)
    skill = _deeplink_skill()
    skill.url = ""
    out = execute_action(_action(), config, skill=skill)
    assert out["sent"] is False and "no deep-link URL" in out["error"]


# --------------------------------------------------------------------------- #
# tier 3: browser automation
# --------------------------------------------------------------------------- #
def _browser_skill():
    return Skill(
        name="cancel-fitclub-auto", merchant="FitClub",
        action_type="cancel_subscription", channel="browser",
        instructions=("```moxie-steps\n"
                      "goto https://fitclub.example/login\n"
                      "fill #email=me@example.com\n"
                      "pause complete 2FA in the window\n"
                      "click text=Cancel membership\n"
                      "```\n"),
    )


def test_browser_runs_skill_steps_with_fake_driver(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=True, browser_ok=True)
    driver = FakeDriver()
    paused = []
    ch = BrowserChannel(driver=driver, pause_fn=paused.append)
    out = execute_action(_action(), config, skill=_browser_skill(),
                         channels={"browser": ch})
    assert out["sent"] is True
    assert out["reference"] == "https://fitclub.example/cancelled"
    assert ("goto", "https://fitclub.example/login") in driver.ran
    assert paused == ["complete 2FA in the window"]
    assert driver.closed


def test_browser_needs_explicit_opt_in(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=True, browser_ok=False)
    out = execute_action(_action(), config, skill=_browser_skill(),
                         channels={"browser": BrowserChannel(driver=FakeDriver())})
    assert out["sent"] is False and "MOXIE_BROWSER_OK" in out["error"]


def test_browser_failure_reports_step(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch, live=True, browser_ok=True)
    ch = BrowserChannel(driver=FakeDriver(fail_at=2), pause_fn=lambda m: None)
    out = execute_action(_action(), config, skill=_browser_skill(),
                         channels={"browser": ch})
    assert out["sent"] is False and "failed at step" in out["error"]


def test_skill_steps_prefers_machine_block():
    assert skill_steps(_browser_skill())[0].startswith("goto ")
    assert skill_steps(_deeplink_skill())[0].startswith("Log in")


# --------------------------------------------------------------------------- #
# through the whole Vault pipeline (Agent)
# --------------------------------------------------------------------------- #
def _agent(tmp_path, monkeypatch, live=False, channels=None):
    config = _config(tmp_path, monkeypatch, live=live)
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    agent = Agent(config, store, audit, channels=channels or {})
    txns = [Transaction(date=f"2026-0{m}-02", merchant="FitClub", amount=29.99,
                        currency="£") for m in (4, 5, 6)]
    store.save_transactions(txns)
    agent.scan(txns)
    return agent, store, audit


def test_agent_dry_run_is_unchanged_from_scaffold(tmp_path, monkeypatch):
    agent, store, audit = _agent(tmp_path, monkeypatch, live=False)
    results = agent.review(approve_fn=lambda a: True)
    action, outcome, note = results[0]
    assert outcome == "executed" and "dry-run" in note
    e = [x for x in audit.entries() if x["event"] == "action_executed"][0]
    assert e["data"]["dry_run"] is True and e["data"]["sent"] is False


def test_agent_live_send_marks_sent_and_audits_reference(tmp_path, monkeypatch):
    fake = FakeSMTP()
    agent, store, audit = _agent(tmp_path, monkeypatch, live=True,
                                 channels={"email": EmailChannel(transport=fake)})
    results = agent.review(approve_fn=lambda a: True)
    action, outcome, note = results[0]
    assert outcome == "sent" and action.status == "sent"
    assert action.reference == "<fake-id@moxie>"
    assert store.get_decision("FitClub", "cancel_subscription")["status"] == "sent"
    e = [x for x in audit.entries() if x["event"] == "action_executed"][0]
    assert e["data"]["sent"] is True and e["data"]["reference"] == "<fake-id@moxie>"


def test_agent_send_failure_marks_failed(tmp_path, monkeypatch):
    agent, store, audit = _agent(
        tmp_path, monkeypatch, live=True,
        channels={"email": EmailChannel(transport=FakeSMTP(fail=True))})
    results = agent.review(approve_fn=lambda a: True)
    action, outcome, note = results[0]
    assert outcome == "failed" and action.status == "failed"
    assert any(e["event"] == "action_failed" for e in audit.entries())
    # a failed send never claims success anywhere
    assert "sent" not in note.lower() or "failed" in note.lower()


def test_agent_resolve_uses_edited_draft(tmp_path, monkeypatch):
    fake = FakeSMTP()
    agent, store, audit = _agent(tmp_path, monkeypatch, live=True,
                                 channels={"email": EmailChannel(transport=fake)})
    target = [a for a in store.load_actions() if a.status == "proposed"][0]
    edited = ("To: cancellations@fitclub.com\nSubject: Cancel — account 991\n\n"
              "Cancel it, please. Confirm in writing.")
    action, outcome, note = agent.resolve(target.id, True, channel="dashboard",
                                          edited_draft=edited)
    assert outcome == "sent"
    assert fake.messages[0]["To"] == "cancellations@fitclub.com"
    assert any(e["event"] == "draft_edited" for e in audit.entries())


def test_approval_prompt_edit_loop(monkeypatch):
    """'e' edits the draft, then 'y' approves the edited version."""
    import sys
    from moxie.vault.approval import request_approval
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    action = _action()
    answers = iter(["e", "y"])
    ok = request_approval(action, prompt_fn=lambda _: next(answers),
                          edit_fn=lambda old: old.replace("fitclub.com", "gym.example"))
    assert ok is True
    assert "gym.example" in action.draft


def test_unattended_still_declines(monkeypatch):
    """The isatty fail-safe survives Phase 1 intact."""
    import sys
    from moxie.vault.approval import request_approval
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert request_approval(_action()) is False
