"""Telegram channel: text Moxie like a PA. Stdlib long polling, no dependencies.

Security model (mirrors OpenClaw's channel design):
- token from @BotFather via TELEGRAM_BOT_TOKEN
- PAIRING: until MOXIE_TELEGRAM_CHAT_ID is set, the bot answers any chat with
  pairing instructions (showing that chat's id) and does nothing else
- ALLOWLIST: once paired, messages from any other chat are ignored (and audited)
- approvals are two-step (/approve N, then YES) and drafts-only (dry-run)
- sensitive setup (API keys, bank links) happens on your computer, never over chat

Run it:  moxie telegram
It also runs the daily loop: once a day (MOXIE_SCAN_HOUR) it re-scans your
stored transactions and messages you only if there is something new to decide.
"""
from __future__ import annotations

import datetime as dt
import json
import time
import urllib.parse
import urllib.request

from .agent import Agent
from .brain import Brain

HELP = (
    "🦡 Moxie — your money agent.\n\n"
    "/findings — what I've found\n"
    "/scan — re-check the transactions on file\n"
    "/approve N — approve finding N (I'll ask you to confirm)\n"
    "/skip N — skip & remember (no nagging for 60 days)\n"
    "/help — this message\n\n"
    "Anything else, just ask — e.g. \"can I afford £120 trainers this month?\"\n\n"
    "I never move money. Actions are drafts you approve. Setup (keys, bank "
    "links) stays on your computer — never over chat."
)


class TelegramAPI:
    """Thin stdlib wrapper over the Bot API; injectable for tests."""

    def __init__(self, token: str, transport=None):
        self.base = f"https://api.telegram.org/bot{token}/"
        self._transport = transport or self._http

    def _http(self, method: str, params: dict) -> dict:
        data = urllib.parse.urlencode(params).encode("utf-8")
        with urllib.request.urlopen(self.base + method, data=data, timeout=40) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def call(self, method: str, **params) -> dict:
        return self._transport(method, params)

    def send(self, chat_id, text: str) -> None:
        self.call("sendMessage", chat_id=chat_id, text=text)

    def updates(self, offset: int, timeout: int = 25) -> "list[dict]":
        out = self.call("getUpdates", offset=offset, timeout=timeout)
        return out.get("result", [])


class Bot:
    def __init__(self, config, store, audit, api=None, allow_chat_id=None):
        self.config = config
        self.store = store
        self.audit = audit
        self.agent = Agent(config, store, audit)
        self.brain = Brain(config)
        self.api = api
        self.allow = str(allow_chat_id or config.telegram_chat_id or "") or None
        self.pending = {}  # chat_id -> action id awaiting YES

    # ---- helpers ----------------------------------------------------------
    def _proposed(self):
        actions = [a for a in self.store.load_actions() if a.status == "proposed"]
        return sorted(actions, key=lambda a: (-a.est_savings, a.merchant))

    def _findings_text(self) -> str:
        actions = self._proposed()
        if not actions:
            return "Nothing waiting on you. Run /scan or import fresh data with `moxie scan --csv/--pdf` on your computer."
        cur = getattr(actions[0], "currency", "$")
        total = sum(a.est_savings for a in actions)
        lines = [f"{i}. {a.description} (~{cur}{a.est_savings:.2f}/yr)"
                 for i, a in enumerate(actions, 1)]
        return ("Here's what I've found (~{}{:.2f}/yr):\n\n".format(cur, total)
                + "\n".join(lines)
                + "\n\n/approve N to act · /skip N to snooze")

    def _nth(self, text: str):
        try:
            n = int(text.split()[1])
            actions = self._proposed()
            return actions[n - 1] if 1 <= n <= len(actions) else None
        except (IndexError, ValueError):
            return None

    # ---- the whole conversation policy, pure and testable ------------------
    def handle(self, chat_id, text: str) -> "str | None":
        text = (text or "").strip()

        # Pairing: no allowlisted chat yet -> teach, never obey.
        if not self.allow:
            return (
                "👋 I'm Moxie, but we're not paired yet.\n\n"
                f"To make me yours, set  MOXIE_TELEGRAM_CHAT_ID={chat_id}  in your "
                ".env on your computer and restart `moxie telegram`.\n"
                "(I only ever talk to one paired chat — everyone else gets silence.)"
            )

        # Allowlist: everyone who isn't you is ignored, quietly but audibly.
        if str(chat_id) != self.allow:
            self.audit.append("telegram_denied", {"chat_id": str(chat_id)})
            return None

        low = text.lower()

        if low in ("/start", "/help"):
            return HELP

        if low == "/findings":
            return self._findings_text()

        if low == "/scan":
            txns = self.store.load_transactions()
            if not txns:
                return ("No transactions on file. On your computer run:\n"
                        "  moxie scan --csv statement.csv   (or --pdf statement.pdf)")
            self.agent.scan(txns)
            note = (f"\n({self.agent.last_suppressed} old finding(s) stayed snoozed — "
                    "you already decided on them)") if self.agent.last_suppressed else ""
            return f"Re-checked {len(txns)} transactions.{note}\n\n" + self._findings_text()

        if low.startswith("/approve"):
            action = self._nth(text)
            if not action:
                return "Which one? e.g. /approve 1 — see /findings for numbers."
            self.pending[str(chat_id)] = action.id
            cur = getattr(action, "currency", "$")
            draft = f"\n\nDraft:\n{action.draft}" if action.draft else ""
            if self.config.kill_engaged:
                mode = "🛑 Kill switch engaged — approving produces a draft, nothing sends."
            elif self.config.live:
                mode = "🔴 LIVE mode — this WILL really be sent."
            else:
                mode = "📝 Drafts mode — approving finalises the draft; nothing is sent."
            return (f"About to act on: {action.description} "
                    f"(saves ~{cur}{action.est_savings:.2f}/yr){draft}\n\n{mode}\n"
                    "⚠️ This cannot be undone once sent. Reply YES to confirm.\n"
                    "(Want to edit the draft first? Use the dashboard on your computer.)")

        if low == "yes" and str(chat_id) in self.pending:
            action_id = self.pending.pop(str(chat_id))
            result = self.agent.resolve(action_id, True, channel="telegram")
            if not result:
                return "That one's already been handled."
            action, outcome, note = result
            icon = {"sent": "📮", "executed": "✅", "failed": "❌"}.get(outcome, "•")
            msg = (f"{icon} {outcome.upper()}: {action.merchant} — {note}.\n"
                   "Logged in the tamper-evident audit trail (moxie log).")
            if outcome == "executed" and getattr(action, "channel", "email") != "deeplink":
                msg += " Drafts only — nothing was sent (MOXIE_LIVE is off)."
            return msg

        if low.startswith("/skip"):
            action = self._nth(text)
            if not action:
                return "Which one? e.g. /skip 2 — see /findings for numbers."
            self.agent.resolve(action.id, False, channel="telegram")
            return (f"Skipped {action.merchant}. I'll remember — no nagging about "
                    "this for 60 days.")

        # Anything else: a question for the brain.
        if not self.brain.available:
            return ("I can list and act on findings here, but for questions I need "
                    "a brain: set MOXIE_API_KEY in .env on your computer "
                    "(your own Anthropic key), then restart me.")
        txns = self.store.load_transactions()
        return self.brain.ask(text, txns, self.store.load_actions())

    # ---- daily loop --------------------------------------------------------
    def daily_tick(self, now=None) -> "str | None":
        """Once per day after scan_hour: re-scan stored data; report only if
        there's something new to decide."""
        now = now or dt.datetime.now()
        today = now.date().isoformat()
        if now.hour < self.config.scan_hour:
            return None
        if self.store.get_meta("last_auto_scan") == today:
            return None
        self.store.set_meta("last_auto_scan", today)
        txns = self.store.load_transactions()
        if not txns:
            return None
        actions = self.agent.scan(txns)
        self.audit.append("daily_scan", {"found": len(actions),
                                         "suppressed": self.agent.last_suppressed})
        if not actions:
            return None
        if self.brain.available:
            try:
                briefing = self.brain.triage(actions, txns)
                return f"🦡 Morning briefing:\n\n{briefing}\n\n{self._findings_text()}"
            except Exception:
                pass
        return f"🦡 Morning check-in:\n\n{self._findings_text()}"

    def run(self, once: bool = False) -> None:
        offset = 0
        print("🦡 Moxie Telegram bot running. Ctrl-C to stop.")
        if not self.allow:
            print("   Not paired yet — message your bot and it will show your chat id.")
        while True:
            tick = self.daily_tick()
            if tick and self.allow:
                self.api.send(self.allow, tick)
            try:
                updates = self.api.updates(offset)
            except Exception as e:
                print(f"   (network hiccup: {e}; retrying in 5s)")
                time.sleep(5)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat_id = (msg.get("chat") or {}).get("id")
                text = msg.get("text", "")
                if chat_id is None:
                    continue
                reply = self.handle(chat_id, text)
                if reply:
                    self.api.send(chat_id, reply)
            if once:
                return


def run_bot(config, store, audit, once: bool = False) -> None:
    token = config.telegram_token
    if not token:
        raise SystemExit(
            "No Telegram token. Create a bot with @BotFather, then put\n"
            "  TELEGRAM_BOT_TOKEN=123:abc\n"
            "in a .env file (in this folder or ~/.moxie) and rerun `moxie telegram`."
        )
    Bot(config, store, audit, api=TelegramAPI(token)).run(once=once)
