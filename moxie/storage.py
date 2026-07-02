"""Local SQLite store for actions, receipts, transactions, and decisions.

Decisions are Moxie's memory: once you skip or act on a finding, it is
remembered and not re-proposed while the snooze window lasts -- an agent
that nags you daily with the same question gets uninstalled.

Encryption at rest (Phase 7): pass a Cipher (moxie/secure.py) and the JSON
payload columns — actions, receipts, transactions — are Fernet-encrypted on
disk (`moxie encrypt on`). The decisions table's merchant/kind keys stay
plaintext (they're SQL primary keys); SECURITY.md says so out loud.
Stdlib only; the cipher itself is an optional extra.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3
from pathlib import Path

from .models import ProposedAction, Receipt, Transaction
from .secure import maybe_decrypt


class Store:
    def __init__(self, path: "Path | str", cipher=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the dashboard serves from worker threads;
        # every write commits immediately, so cross-thread reuse is safe here.
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.cipher = cipher
        self._init()

    # --- encryption plumbing ---
    def _seal(self, text: str) -> str:
        return self.cipher.encrypt(text) if self.cipher else text

    def _open(self, text: str) -> str:
        return maybe_decrypt(text, self.cipher)

    def _init(self) -> None:
        self.db.execute("CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, data TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, data TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS transactions (id TEXT PRIMARY KEY, data TEXT)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS decisions "
            "(merchant TEXT, kind TEXT, status TEXT, date TEXT, PRIMARY KEY (merchant, kind))"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        self.db.commit()

    # --- actions ---
    def save_action(self, action: ProposedAction) -> None:
        self.db.execute(
            "REPLACE INTO actions (id, data) VALUES (?, ?)",
            (action.id, self._seal(json.dumps(dataclasses.asdict(action)))),
        )
        self.db.commit()

    def load_actions(self) -> "list[ProposedAction]":
        rows = self.db.execute("SELECT data FROM actions").fetchall()
        return [ProposedAction(**json.loads(self._open(r[0]))) for r in rows]

    def clear_actions(self) -> None:
        self.db.execute("DELETE FROM actions")
        self.db.commit()

    # --- receipts ---
    def save_receipt(self, receipt: Receipt) -> None:
        self.db.execute(
            "REPLACE INTO receipts (id, data) VALUES (?, ?)",
            (receipt.id, self._seal(json.dumps(dataclasses.asdict(receipt)))),
        )
        self.db.commit()

    def load_receipts(self) -> "list[Receipt]":
        rows = self.db.execute("SELECT data FROM receipts").fetchall()
        return [Receipt(**json.loads(self._open(r[0]))) for r in rows]

    # --- transactions (latest import, so chat channels can reason later) ---
    def save_transactions(self, txns: "list[Transaction]") -> None:
        self.db.execute("DELETE FROM transactions")
        self.db.executemany(
            "INSERT INTO transactions (id, data) VALUES (?, ?)",
            [(t.id, self._seal(json.dumps(dataclasses.asdict(t)))) for t in txns],
        )
        self.db.commit()

    def load_transactions(self) -> "list[Transaction]":
        rows = self.db.execute("SELECT data FROM transactions").fetchall()
        return [Transaction(**json.loads(self._open(r[0]))) for r in rows]

    def reencrypt_all(self, cipher) -> int:
        """Migrate every stored payload to `cipher` (enabling encryption on a
        store with plaintext history). Returns rows rewritten."""
        old_cipher, self.cipher = self.cipher, cipher
        count = 0
        for table in ("actions", "receipts", "transactions"):
            rows = self.db.execute(f"SELECT id, data FROM {table}").fetchall()
            for rid, data in rows:
                plain = maybe_decrypt(data, old_cipher or cipher)
                self.db.execute(f"UPDATE {table} SET data = ? WHERE id = ?",
                                (self._seal(plain), rid))
                count += 1
        self.db.commit()
        return count

    # --- decisions (Moxie's memory) ---
    def save_decision(self, merchant: str, kind: str, status: str, date: "str | None" = None) -> None:
        self.db.execute(
            "REPLACE INTO decisions (merchant, kind, status, date) VALUES (?, ?, ?, ?)",
            (merchant, kind, status, date or dt.date.today().isoformat()),
        )
        self.db.commit()

    def get_decision(self, merchant: str, kind: str) -> "dict | None":
        row = self.db.execute(
            "SELECT status, date FROM decisions WHERE merchant = ? AND kind = ?",
            (merchant, kind),
        ).fetchone()
        return {"status": row[0], "date": row[1]} if row else None

    # --- meta ---
    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self.db.commit()

    def get_meta(self, key: str) -> "str | None":
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    # --- skill stats (how often each SKILL.md was used, and how it went) ---
    def bump_skill(self, name: str, outcome: str) -> None:
        """outcome: 'used' on every execution; plus 'sent' or 'failed'."""
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS skill_stats "
            "(name TEXT PRIMARY KEY, used INTEGER DEFAULT 0, "
            " sent INTEGER DEFAULT 0, failed INTEGER DEFAULT 0)"
        )
        self.db.execute(
            "INSERT INTO skill_stats (name) VALUES (?) "
            "ON CONFLICT(name) DO NOTHING", (name,))
        if outcome in ("used", "sent", "failed"):
            self.db.execute(
                f"UPDATE skill_stats SET {outcome} = {outcome} + 1 WHERE name = ?",
                (name,))
        self.db.commit()

    def skill_stats(self) -> "dict[str, dict]":
        try:
            rows = self.db.execute(
                "SELECT name, used, sent, failed FROM skill_stats").fetchall()
        except Exception:
            return {}
        return {r[0]: {"used": r[1], "sent": r[2], "failed": r[3]} for r in rows}
