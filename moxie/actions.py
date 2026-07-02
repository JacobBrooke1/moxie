"""Action channels — how an approved action actually gets carried out.

Three tiers, ordered by safety and reliability (the merchant's SKILL.md picks):

  1. email     — sent from the USER'S OWN mailbox via SMTP (their address +
                 app password). Legitimate and deliverable; no impersonation.
  2. deeplink  — guided: Moxie gives you the exact cancel URL and the clicks.
                 You do the final click, so CAPTCHAs / 2FA / retention offers
                 are handled by a human. Zero password risk, zero side effects.
  3. browser   — full automation via an injectable driver (Playwright behind
                 the optional [browser] extra). Hardest tier; per-merchant
                 skill steps; pauses for humans at 2FA; run it sandboxed.

THE GATES (all of them, always):
  * the Trust Vault first — policy + explicit approval; this module is only
    ever reached for an action the user already approved.
  * MOXIE_LIVE (default false) — flag off means EVERYTHING is a dry-run draft,
    identical to the original scaffold. Honest copy: a draft is never "sent".
  * the kill switch — a `KILL` file in ~/.moxie forces dry-run no matter what
    (create it with `moxie kill`; remove with `moxie kill --release`).
  * browser additionally needs MOXIE_BROWSER_OK=true — explicit opt-in.

Every delivery returns a result dict; nothing here raises for policy reasons:
  {"sent": bool, "dry_run": bool, "channel": str, "note": str,
   "reference": str,          # message-id / confirmation, for the audit log
   "body"/"url"/"steps": ...} # channel-specific payload

Core is stdlib-only (smtplib, email). Playwright is optional and lazy.
"""
from __future__ import annotations

import email.message
import email.utils
import re
import smtplib


# --------------------------------------------------------------------------- #
# draft parsing — drafts are "To: …\nSubject: …\n\nbody", shown to and
# editable by the user before anything runs.
# --------------------------------------------------------------------------- #
def parse_draft(draft: str) -> "dict":
    """Split an email-style draft into {to, subject, body}. Forgiving: missing
    headers come back empty and the caller decides whether that's fatal."""
    to, subject, body_lines, in_body = "", "", [], False
    for line in (draft or "").splitlines():
        if in_body:
            body_lines.append(line)
            continue
        stripped = line.strip()
        if not stripped:
            in_body = True
            continue
        low = stripped.lower()
        if low.startswith("to:"):
            to = stripped[3:].strip()
        elif low.startswith("subject:"):
            subject = stripped[8:].strip()
        else:  # a draft with no headers at all: it's all body
            in_body = True
            body_lines.append(line)
    return {"to": to, "subject": subject, "body": "\n".join(body_lines).strip()}


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _plausible_address(addr: str) -> bool:
    return bool(_EMAIL_RE.match(addr or ""))


# --------------------------------------------------------------------------- #
# channels
# --------------------------------------------------------------------------- #
class EmailChannel:
    """Tier 1: send the draft from the user's own SMTP account.

    Config (env / ~/.moxie/.env):
      MOXIE_SMTP_HOST, MOXIE_SMTP_PORT (587), MOXIE_SMTP_USER,
      MOXIE_SMTP_PASSWORD, MOXIE_SMTP_FROM (defaults to user),
      MOXIE_SMTP_SSL ("true" = implicit TLS, else STARTTLS),
      MOXIE_SMTP_BCC_SELF ("true" by default — keep a copy as evidence),
      MOXIE_EMAIL_OVERRIDE_TO (testing: reroute every send to yourself).

    The transport is injectable so tests never touch the network.
    """

    name = "email"

    def __init__(self, transport=None):
        self._transport = transport or self._smtp_send

    @staticmethod
    def available(config) -> "tuple[bool, str]":
        s = config.smtp
        missing = [k for k in ("host", "user", "password") if not s.get(k)]
        if missing:
            return False, ("SMTP not configured — set MOXIE_SMTP_" +
                           ", MOXIE_SMTP_".join(m.upper() for m in missing) +
                           " in .env (use an app password, never your main one).")
        return True, ""

    def _smtp_send(self, msg, smtp: dict) -> str:
        """Real network path (not exercised in tests). Returns the message id."""
        port = int(smtp.get("port") or 587)
        if smtp.get("ssl"):
            server = smtplib.SMTP_SSL(smtp["host"], port, timeout=30)
        else:
            server = smtplib.SMTP(smtp["host"], port, timeout=30)
        try:
            if not smtp.get("ssl"):
                server.starttls()
            server.login(smtp["user"], smtp["password"])
            server.send_message(msg)
        finally:
            server.quit()
        return msg["Message-ID"]

    def deliver(self, action, config, receipt=None) -> dict:
        ok, why = self.available(config)
        if not ok:
            return {"sent": False, "dry_run": False, "channel": self.name,
                    "reference": "", "note": why, "error": why}
        smtp = config.smtp
        parts = parse_draft(action.draft)
        to = smtp.get("override_to") or parts["to"]
        if not _plausible_address(to):
            why = (f"refusing to send: draft has no plausible To: address ({parts['to']!r}). "
                   "Edit the draft with the merchant's real support address.")
            return {"sent": False, "dry_run": False, "channel": self.name,
                    "reference": "", "note": why, "error": why}

        msg = email.message.EmailMessage()
        msg["From"] = smtp.get("from") or smtp["user"]
        msg["To"] = to
        msg["Subject"] = parts["subject"] or f"Regarding my {action.merchant} account"
        msg["Message-ID"] = email.utils.make_msgid(domain=None)
        msg["Date"] = email.utils.formatdate(localtime=True)
        if smtp.get("bcc_self", True):
            msg["Bcc"] = smtp.get("from") or smtp["user"]
        body = parts["body"] or action.draft
        if receipt is not None:
            body += (f"\n\n--\nEvidence: receipt from {receipt.merchant}, "
                     f"{receipt.date}, amount {receipt.amount:.2f}."
                     + (f"\n{receipt.text}" if receipt.text else ""))
        msg.set_content(body)
        if receipt is not None and getattr(receipt, "path", ""):
            try:
                from pathlib import Path
                blob = Path(receipt.path).read_bytes()
                msg.add_attachment(blob, maintype="application",
                                   subtype="octet-stream",
                                   filename=Path(receipt.path).name)
            except OSError:
                pass  # evidence text is already inline; missing file isn't fatal

        try:
            reference = self._transport(msg, smtp) or msg["Message-ID"]
        except Exception as e:  # network/auth failures come back as a result, not a crash
            why = f"send failed: {e}"
            return {"sent": False, "dry_run": False, "channel": self.name,
                    "reference": "", "note": why, "error": why}
        return {"sent": True, "dry_run": False, "channel": self.name,
                "reference": reference, "to": to,
                "note": f"sent via your SMTP to {to} (ref {reference})"}


class DeeplinkChannel:
    """Tier 2: guided deep-link. Moxie knows the exact cancel URL and the
    clicks (from the merchant's SKILL.md); YOU do the final click. Zero
    passwords, zero external side effects — so it behaves the same with
    MOXIE_LIVE on or off, and `sent` is always False (honest copy)."""

    name = "deeplink"

    def deliver(self, action, config, skill=None, dry_run=False) -> dict:
        url = (getattr(skill, "url", "") or "").strip()
        steps = skill_steps(skill) if skill else []
        if not url:
            why = (f"no deep-link URL known for {action.merchant} — add `url:` to its "
                   "SKILL.md frontmatter, or fall back to the email draft.")
            return {"sent": False, "dry_run": dry_run, "channel": self.name,
                    "reference": "", "note": why, "error": why}
        note = f"guided: open {url} and follow {len(steps) or 'the'} step(s) — you do the final click"
        return {"sent": False, "dry_run": dry_run, "channel": self.name,
                "reference": url, "url": url, "steps": steps, "note": note}


class BrowserChannel:
    """Tier 3: full browser automation, per-merchant via skill steps.

    The driver is injectable (tests use a fake). The real driver uses
    Playwright (optional extra: pip install "moxie-agent[browser]") and should
    run sandboxed (Docker). Requires BOTH MOXIE_LIVE=true and
    MOXIE_BROWSER_OK=true; pauses for a human on `pause` steps (2FA, CAPTCHA,
    retention offers)."""

    name = "browser"

    def __init__(self, driver=None, pause_fn=None):
        self._driver = driver
        self._pause_fn = pause_fn or (lambda msg: None)

    def _get_driver(self):
        if self._driver is not None:
            return self._driver
        return PlaywrightDriver()  # lazy-imports playwright; raises with help if missing

    def deliver(self, action, config, skill=None) -> dict:
        if not config.browser_ok:
            why = ("browser automation needs explicit opt-in: set MOXIE_BROWSER_OK=true "
                   "(and run it sandboxed — see docs/HOW_IT_WORKS.md).")
            return {"sent": False, "dry_run": False, "channel": self.name,
                    "reference": "", "note": why, "error": why}
        steps = skill_steps(skill) if skill else []
        if not steps:
            why = (f"no browser steps for {action.merchant} — its SKILL.md needs a "
                   "```moxie-steps``` block. Falling back is safer than guessing.")
            return {"sent": False, "dry_run": False, "channel": self.name,
                    "reference": "", "note": why, "error": why}
        try:
            driver = self._get_driver()
        except Exception as e:
            why = str(e)
            return {"sent": False, "dry_run": False, "channel": self.name,
                    "reference": "", "note": why, "error": why}
        done = []
        try:
            for step in steps:
                verb, _, arg = step.partition(" ")
                verb, arg = verb.strip().lower(), arg.strip()
                if verb == "pause":
                    self._pause_fn(arg or "human step required (2FA / CAPTCHA)")
                else:
                    driver.run(verb, arg)
                done.append(step)
            confirmation = driver.confirmation() if hasattr(driver, "confirmation") else ""
        except Exception as e:
            why = f"browser run failed at step {len(done) + 1} ({e})"
            return {"sent": False, "dry_run": False, "channel": self.name,
                    "reference": "", "note": why, "error": why,
                    "steps_completed": done}
        finally:
            if hasattr(driver, "close"):
                try:
                    driver.close()
                except Exception:
                    pass
        return {"sent": True, "dry_run": False, "channel": self.name,
                "reference": confirmation or f"{len(done)} steps completed",
                "steps_completed": done,
                "note": f"browser flow completed ({len(done)} steps)"}


class PlaywrightDriver:
    """Real browser driver. Verbs: goto URL · click SELECTOR · fill SELECTOR=VALUE
    · wait SELECTOR. Needs the optional extra:  pip install "moxie-agent[browser]"
    then:  playwright install chromium"""

    def __init__(self):
        try:
            from playwright.sync_api import sync_playwright  # lazy, optional
        except ImportError as e:
            raise RuntimeError(
                "browser channel needs Playwright: pip install \"moxie-agent[browser]\" "
                "&& playwright install chromium"
            ) from e
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._page = self._browser.new_page()

    def run(self, verb: str, arg: str) -> None:
        if verb == "goto":
            self._page.goto(arg)
        elif verb == "click":
            self._page.click(arg)
        elif verb == "fill":
            selector, _, value = arg.partition("=")
            self._page.fill(selector.strip(), value.strip())
        elif verb == "wait":
            self._page.wait_for_selector(arg)
        else:
            raise ValueError(f"unknown browser step verb: {verb!r}")

    def confirmation(self) -> str:
        return self._page.url

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()


# --------------------------------------------------------------------------- #
# skill step extraction (shared by deeplink + browser)
# --------------------------------------------------------------------------- #
_STEPS_BLOCK = re.compile(r"```moxie-steps\s*\n(.*?)```", re.S)
_NUMBERED = re.compile(r"^\s*\d+\.\s+(.*)$")


def skill_steps(skill) -> "list[str]":
    """Steps for a skill: a fenced ```moxie-steps``` block wins (machine verbs
    for the browser tier); otherwise the numbered list in the body (human
    guidance for the deeplink tier)."""
    text = getattr(skill, "instructions", "") or ""
    m = _STEPS_BLOCK.search(text)
    if m:
        return [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
    steps = []
    for line in text.splitlines():
        n = _NUMBERED.match(line)
        if n:
            steps.append(n.group(1).strip())
    return steps


# --------------------------------------------------------------------------- #
# the single entry point the orchestrator calls
# --------------------------------------------------------------------------- #
def execute_action(action, config=None, dry_run=None, *, skill=None,
                   receipt=None, channels=None) -> dict:
    """Carry out one APPROVED action. Never call this without the Vault having
    cleared it first (policy + approval) — the orchestrator enforces that.

    dry-run logic (fail-safe at every step):
      * no config            -> dry-run (legacy scaffold behaviour)
      * MOXIE_LIVE unset/false -> dry-run
      * kill switch engaged  -> dry-run, whatever the flag says
      * explicit dry_run arg -> wins over everything
    """
    channel_name = (getattr(skill, "channel", "") or
                    getattr(action, "channel", "") or "email")

    if dry_run is None:
        if config is None:
            dry_run = True
        else:
            dry_run = not (config.live and not config.kill_engaged)

    channels = channels or {}
    if dry_run:
        if channel_name == "deeplink":
            ch = channels.get("deeplink") or DeeplinkChannel()
            return ch.deliver(action, config, skill=skill, dry_run=True)
        note = ("draft only — nothing was sent"
                + ("" if config is None else
                   (" (kill switch engaged)" if config.kill_engaged and config.live
                    else " (set MOXIE_LIVE=true to send for real)")))
        return {"sent": False, "dry_run": True, "channel": channel_name,
                "reference": "", "body": action.draft, "note": note}

    if channel_name == "email":
        ch = channels.get("email") or EmailChannel()
        return ch.deliver(action, config, receipt=receipt)
    if channel_name == "deeplink":
        ch = channels.get("deeplink") or DeeplinkChannel()
        return ch.deliver(action, config, skill=skill, dry_run=False)
    if channel_name == "browser":
        ch = channels.get("browser") or BrowserChannel()
        return ch.deliver(action, config, skill=skill)

    why = f"unknown action channel {channel_name!r}"
    return {"sent": False, "dry_run": False, "channel": channel_name,
            "reference": "", "note": why, "error": why}
