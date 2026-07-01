"""Configuration and the local Moxie home directory. Stdlib only."""
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
        # Hard-denied in v1 — moving money needs licensing we don't have.
        "denied_kinds": ["move_money", "transfer", "pay_bill", "trade"],
        # Nothing auto-executes by default; everything that acts needs your approval.
        "auto_allow_kinds": [],
    },
    "offline_only": False,
}


def get_home() -> Path:
    """Where Moxie keeps its local data (override with MOXIE_HOME)."""
    return Path(os.environ.get("MOXIE_HOME", str(Path.home() / ".moxie")))


class Config:
    def __init__(self, home: "Path | str | None" = None):
        self.home = Path(home) if home else get_home()
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
        return os.environ.get("MOXIE_API_KEY")

    @property
    def offline(self) -> bool:
        return os.environ.get("MOXIE_OFFLINE", "").lower() in ("1", "true", "yes") or bool(
            self.data.get("offline_only")
        )
