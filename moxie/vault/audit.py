"""Tamper-evident audit log.

Every action Moxie takes is appended here as a hash-chained entry: each record
embeds the hash of the previous one, so altering any past entry breaks the chain
and `verify()` will catch it. This is what lets a user trust the record of what
the agent did on their behalf.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Iterator, Optional, Tuple

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: "Path | str"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def _entries(self) -> Iterator[dict]:
        with self.path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def last_hash(self) -> str:
        last = GENESIS
        for entry in self._entries():
            last = entry["hash"]
        return last

    @staticmethod
    def _hash(prev_hash: str, ts: float, event: str, data: dict) -> str:
        payload = json.dumps(
            {"prev": prev_hash, "ts": ts, "event": event, "data": data},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def append(self, event: str, data: dict) -> dict:
        prev = self.last_hash()
        ts = time.time()
        h = self._hash(prev, ts, event, data)
        entry = {"ts": ts, "event": event, "data": data, "prev_hash": prev, "hash": h}
        with self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def verify(self) -> Tuple[bool, Optional[int]]:
        """Returns (ok, first_bad_index). ok=True means the chain is intact."""
        prev = GENESIS
        for i, entry in enumerate(self._entries()):
            if entry.get("prev_hash") != prev:
                return False, i
            expected = self._hash(entry["prev_hash"], entry["ts"], entry["event"], entry["data"])
            if expected != entry.get("hash"):
                return False, i
            prev = entry["hash"]
        return True, None

    def entries(self) -> "list[dict]":
        return list(self._entries())
