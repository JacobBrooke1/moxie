"""Local SQLite store for actions, receipts, transactions, and decisions.

Decisions are Moxie's memory: once you skip or act on a finding, it is
remembered and not re-proposed while the snooze window lasts -- an agent
that nags you daily with the same question gets uninstalled.

Thread safety: the dashboard fires several API requests at once from worker
threads, all sharing this one connection — every statement runs under a
process-wide lock, because interleaved cursor use on a shared sqlite3
connection can tear reads. And loading is TOLERANT: one undecodable row is
skipped and counted (see `load_errors`), never allowed to take the whole
dashboard down. `moxie doctor` reports skips honestly.

Encryption at rest (Phase 7): pass a Cipher (moxie/secure.py) and the JSON
payload columns — actions, receipts, transactions, chat, widgets — are
Fernet-encrypted on disk (`moxie encrypt on`). The decisions table's
merchant/kind keys stay plaintext (they're SQL primary keys); SECURITY.md
says so out loud. Stdlib only; the cipher itself is an optional extra.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

from .models import ProposedAction, Receipt, Transaction
from .secure import maybe_decrypt


class Store:
    def __init__(self, path: "Path | str", cipher=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.cipher = cipher
        self._lock = threading.Lock()
        self.load_errors: "dict[str, int]" = {}   # table -> rows skipped
        self._init()

    # --- locked plumbing (every statement goes through here) ---
    def _write(self, sql: str, params=()) -> "sqlite3.Cursor":
        with self._lock:
            cur = self.db.execute(sql, params)
            self.db.commit()
            return cur

    def _write_many(self, sql: str, seq) -> None:
        with self._lock:
            self.db.executemany(sql, seq)
            self.db.commit()

    def _rows(self, sql: str, params=()) -> list:
        with self._lock:
            return self.db.execute(sql, params).fetchall()

    # --- encryption plumbing ---
    def _seal(self, text: str) -> str:
        return self.cipher.encrypt(text) if self.cipher else text

    def _open(self, text: str) -> str:
        return maybe_decrypt(text, self.cipher)

    def _load_objects(self, table: str, factory) -> list:
        """Decode every row we can; skip and COUNT the ones we can't — a
        single bad row must never brick the dashboard. Skips are visible in
        `load_errors` and reported by `moxie doctor`."""
        out, skipped = [], 0
        for (data,) in self._rows(f"SELECT data FROM {table}"):
            try:
                out.append(factory(**json.loads(self._open(data))))
            except Exception:
                skipped += 1
        if skipped:
            self.load_errors[table] = self.load_errors.get(table, 0) + skipped
        return out

    def _init(self) -> None:
        for sql in (
            "CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, data TEXT)",
            "CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, data TEXT)",
            "CREATE TABLE IF NOT EXISTS transactions (id TEXT PRIMARY KEY, data TEXT)",
            "CREATE TABLE IF NOT EXISTS decisions "
            "(merchant TEXT, kind TEXT, status TEXT, date TEXT, PRIMARY KEY (merchant, kind))",
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
            "CREATE TABLE IF NOT EXISTS chat "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, data TEXT)",
            "CREATE TABLE IF NOT EXISTS widgets (id TEXT PRIMARY KEY, data TEXT)",
            "CREATE TABLE IF NOT EXISTS skill_stats "
            "(name TEXT PRIMARY KEY, used INTEGER DEFAULT 0, "
            " sent INTEGER DEFAULT 0, failed INTEGER DEFAULT 0)",
        ):
            self._write(sql)

    # --- actions ---
    def save_action(self, action: ProposedAction) -> None:
        self._write("REPLACE INTO actions (id, data) VALUES (?, ?)",
                    (action.id, self._seal(json.dumps(dataclasses.asdict(action)))))

    def load_actions(self) -> "list[ProposedAction]":
        return self._load_objects("actions", ProposedAction)

    def clear_actions(self) -> None:
        self._write("DELETE FROM actions")

    # --- receipts ---
    def save_receipt(self, receipt: Receipt) -> None:
        self._write("REPLACE INTO receipts (id, data) VALUES (?, ?)",
                    (receipt.id, self._seal(json.dumps(dataclasses.asdict(receipt)))))

    def load_receipts(self) -> "list[Receipt]":
        return self._load_objects("receipts", Receipt)

    # --- transactions (latest import, so chat channels can reason later) ---
    def save_transactions(self, txns: "list[Transaction]") -> None:
        with self._lock:
            self.db.execute("DELETE FROM transactions")
            self.db.executemany(
                "INSERT INTO transactions (id, data) VALUES (?, ?)",
                [(t.id, self._seal(json.dumps(dataclasses.asdict(t)))) for t in txns],
            )
            self.db.commit()

    def load_transactions(self) -> "list[Transaction]":
        return self._load_objects("transactions", Transaction)

    def reencrypt_all(self, cipher) -> int:
        """Migrate every stored payload to `cipher` (enabling encryption on a
        store with plaintext history). Returns rows rewritten."""
        old_cipher, self.cipher = self.cipher, cipher
        count = 0
        for table in ("actions", "receipts", "transactions", "chat", "widgets"):
            rows = self._rows(f"SELECT rowid, data FROM {table}")
            for rid, data in rows:
                plain = maybe_decrypt(data, old_cipher or cipher)
                self._write(f"UPDATE {table} SET data = ? WHERE rowid = ?",
                            (self._seal(plain), rid))
                count += 1
        return count

    # --- decisions (Moxie's memory) ---
    def save_decision(self, merchant: str, kind: str, status: str, date: "str | None" = None) -> None:
        self._write(
            "REPLACE INTO decisions (merchant, kind, status, date) VALUES (?, ?, ?, ?)",
            (merchant, kind, status, date or dt.date.today().isoformat()))

    def get_decision(self, merchant: str, kind: str) -> "dict | None":
        rows = self._rows(
            "SELECT status, date FROM decisions WHERE merchant = ? AND kind = ?",
            (merchant, kind))
        return {"status": rows[0][0], "date": rows[0][1]} if rows else None

    # --- meta ---
    def set_meta(self, key: str, value: str) -> None:
        self._write("REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def get_meta(self, key: str) -> "str | None":
        rows = self._rows("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0][0] if rows else None

    # --- dashboard chat (encrypted like everything else) ---
    def save_chat(self, role: str, text: str) -> None:
        self._write(
            "INSERT INTO chat (ts, data) VALUES (?, ?)",
            (dt.datetime.now().isoformat(timespec="seconds"),
             self._seal(json.dumps({"role": role, "text": text}))))

    def load_chat(self, limit: int = 20) -> "list[dict]":
        """The most recent turns, oldest first (ready to replay as context).
        Undecodable turns are skipped, same tolerance as everything else."""
        rows = self._rows(
            "SELECT ts, data FROM chat ORDER BY id DESC LIMIT ?", (limit,))
        out, skipped = [], 0
        for ts, data in reversed(rows):
            try:
                turn = json.loads(self._open(data))
                turn["ts"] = ts
                out.append(turn)
            except Exception:
                skipped += 1
        if skipped:
            self.load_errors["chat"] = self.load_errors.get("chat", 0) + skipped
        return out

    def clear_chat(self) -> None:
        self._write("DELETE FROM chat")

    # --- chat-built widgets (validated specs only; sealed like the rest) ---
    def save_widget(self, widget_id: str, spec: dict) -> None:
        self._write("REPLACE INTO widgets (id, data) VALUES (?, ?)",
                    (widget_id, self._seal(json.dumps(spec))))

    def load_widgets(self) -> "list[dict]":
        out, skipped = [], 0
        for wid, data in self._rows("SELECT id, data FROM widgets WHERE id != 'layout'"):
            try:
                out.append({"id": wid, "spec": json.loads(self._open(data))})
            except Exception:
                skipped += 1
        if skipped:
            self.load_errors["widgets"] = self.load_errors.get("widgets", 0) + skipped
        return out

    def delete_widget(self, widget_id: str) -> bool:
        return self._write("DELETE FROM widgets WHERE id = ?", (widget_id,)).rowcount > 0

    def get_layout(self) -> "dict | None":
        rows = self._rows("SELECT data FROM widgets WHERE id = 'layout'")
        if not rows:
            return None
        try:
            return json.loads(self._open(rows[0][0]))
        except Exception:
            self.load_errors["widgets"] = self.load_errors.get("widgets", 0) + 1
            return None

    def set_layout(self, spec: dict) -> None:
        self.save_widget("layout", spec)

    # --- skill stats (how often each SKILL.md was used, and how it went) ---
    def bump_skill(self, name: str, outcome: str) -> None:
        """outcome: 'used' on every execution; plus 'sent' or 'failed'."""
        self._write("INSERT INTO skill_stats (name) VALUES (?) "
                    "ON CONFLICT(name) DO NOTHING", (name,))
        if outcome in ("used", "sent", "failed"):
            self._write(
                f"UPDATE skill_stats SET {outcome} = {outcome} + 1 WHERE name = ?",
                (name,))

    def skill_stats(self) -> "dict[str, dict]":
        try:
            rows = self._rows("SELECT name, used, sent, failed FROM skill_stats")
        except Exception:
            return {}
        return {r[0]: {"used": r[1], "sent": r[2], "failed": r[3]} for r in rows}
