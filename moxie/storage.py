"""Local SQLite store for proposed actions and receipts.

NOTE: encryption-at-rest is a TODO before any real-data use (see SECURITY.md).
Stdlib only.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

from .models import ProposedAction, Receipt


class Store:
    def __init__(self, path: "Path | str"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self._init()

    def _init(self) -> None:
        self.db.execute("CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, data TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, data TEXT)")
        self.db.commit()

    # --- actions ---
    def save_action(self, action: ProposedAction) -> None:
        self.db.execute(
            "REPLACE INTO actions (id, data) VALUES (?, ?)",
            (action.id, json.dumps(dataclasses.asdict(action))),
        )
        self.db.commit()

    def load_actions(self) -> "list[ProposedAction]":
        rows = self.db.execute("SELECT data FROM actions").fetchall()
        return [ProposedAction(**json.loads(r[0])) for r in rows]

    def clear_actions(self) -> None:
        self.db.execute("DELETE FROM actions")
        self.db.commit()

    # --- receipts ---
    def save_receipt(self, receipt: Receipt) -> None:
        self.db.execute(
            "REPLACE INTO receipts (id, data) VALUES (?, ?)",
            (receipt.id, json.dumps(dataclasses.asdict(receipt))),
        )
        self.db.commit()

    def load_receipts(self) -> "list[Receipt]":
        rows = self.db.execute("SELECT data FROM receipts").fetchall()
        return [Receipt(**json.loads(r[0])) for r in rows]
