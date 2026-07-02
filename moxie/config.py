"""Configuration and the local Moxie home directory. Stdlib only.

Env vars (a `.env` file in the current directory or ~/.moxie is auto-loaded,
never overriding real environment variables):

  MOXIE_API_KEY           bring-your-own LLM key (Anthropic) -- enables the brain
  MOXIE_MODEL             model id (default: claude-sonnet-5)
  MOXIE_OFFLINE           "true" to never call any LLM
  TELEGRAM_BOT_TOKEN      from @BotFather -- enables the Telegram channel
  MOXIE_TELEGRAM_CHAT_ID  your chat id; the bot answers this chat ONLY
  MOXIE_SCAN_HOUR         daily scan hour for `moxie telegram` (default: 9)
  MOXIE_HOME              data directory (default: ~/.moxie)

Live-action gates (see moxie/actions.py):

  MOXIE_LIVE              "true" = approved actions really send; default false
                          (drafts only). A `KILL` file in ~/.moxie overrides
                          this to drafts no matter what (`moxie kill`).
  MOXIE_SMTP_HOST/PORT/USER/PASSWORD/FROM  your own mailbox for the email
                          channel (use an app password)
  MOXIE_SMTP_SSL          "true" = implicit TLS; default STARTTLS
  MOXIE_SMTP_BCC_SELF     default true -- keep a copy of every send
  MOXIE_EMAIL_OVERRIDE_TO reroute all sends to this address (testing)
  MOXIE_BROWSER_OK        "true" = allow the browser-automation tier
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    "llm": {
        "provider": "env",
        "note": "Bring your own API key via MOXIE_API_KEY, or run offline with a local model.",
    },
    "policy": {
        # Hard-denied in v1 -- moving money needs licensing we don't have.
        "denied_kinds": ["move_money", "transfer", "pay_bill", "trade"],
        # Nothing auto-executes by default; everything that acts needs your approval.
        "auto_allow_kinds": [],
    },
    "offline_only": False,
}


def get_home() -> Path:
    """Where Moxie keeps its local data (override with MOXIE_HOME)."""
    return Path(os.environ.get("MOXIE_HOME", str(Path.home() / ".moxie")))


def _load_dotenv(paths) -> None:
    """Tiny .env loader: KEY=value lines; never overrides the real environment."""
    for p in paths:
        try:
            text = Path(p).read_text()
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Config:
    def __init__(self, home: "Path | str | None" = None):
        self.home = Path(home) if home else get_home()
        _load_dotenv([Path.cwd() / ".env", self.home / ".env"])
        self.path = self.home / "config.json"
        self.data = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except json.JSONDecodeError:
                pass

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2))

    @property
    def api_key(self) -> "str | None":
        from .secure import get_secret
        return get_secret("MOXIE_API_KEY")

    @property
    def offline(self) -> bool:
        return os.environ.get("MOXIE_OFFLINE", "").lower() in ("1", "true", "yes") or bool(
            self.data.get("offline_only")
        )

    @property
    def model(self) -> str:
        return os.environ.get("MOXIE_MODEL", "claude-sonnet-5")

    @property
    def telegram_token(self) -> "str | None":
        from .secure import get_secret
        return get_secret("TELEGRAM_BOT_TOKEN") or get_secret("MOXIE_TELEGRAM_TOKEN")

    @property
    def telegram_chat_id(self) -> "str | None":
        return os.environ.get("MOXIE_TELEGRAM_CHAT_ID")

    @property
    def scan_hour(self) -> int:
        try:
            return int(os.environ.get("MOXIE_SCAN_HOUR", "9"))
        except ValueError:
            return 9

    # --- live-action gates (Phase 1) -------------------------------------
    @staticmethod
    def _truthy(name: str) -> bool:
        return os.environ.get(name, "").lower() in ("1", "true", "yes")

    @property
    def live(self) -> bool:
        """MOXIE_LIVE=true means approved actions really send. Default: drafts."""
        return self._truthy("MOXIE_LIVE")

    @property
    def kill_path(self) -> Path:
        return self.home / "KILL"

    @property
    def kill_engaged(self) -> bool:
        """The kill switch: a KILL file in ~/.moxie forces drafts-only."""
        return self.kill_path.exists()

    @property
    def browser_ok(self) -> bool:
        """Extra explicit opt-in for the browser-automation tier."""
        return self._truthy("MOXIE_BROWSER_OK")

    @property
    def smtp(self) -> dict:
        """The user's own mailbox for the email channel (app password!)."""
        return {
            "host": os.environ.get("MOXIE_SMTP_HOST", ""),
            "port": os.environ.get("MOXIE_SMTP_PORT", "587"),
            "user": os.environ.get("MOXIE_SMTP_USER", ""),
            "password": os.environ.get("MOXIE_SMTP_PASSWORD", ""),
            "from": os.environ.get("MOXIE_SMTP_FROM", ""),
            "ssl": self._truthy("MOXIE_SMTP_SSL"),
            "bcc_self": os.environ.get("MOXIE_SMTP_BCC_SELF", "true").lower()
                        not in ("0", "false", "no"),
            "override_to": os.environ.get("MOXIE_EMAIL_OVERRIDE_TO", ""),
        }
