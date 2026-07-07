"""The document vault: one folder Moxie owns for your money papers.

    ~/.moxie/vault/{receipts,statements,bills,confirmations}/

Receipts, past bank statements, bills, cancellation confirmations — filed,
listed, downloadable from the dashboard's Documents section, and encrypted
at rest when `moxie encrypt on` is active.

Security posture (this folder accepts USER-SUPPLIED FILENAMES AND BYTES,
so it is written defensively):

  * Path traversal is hard-blocked: filenames are sanitised to a strict
    character set, category names come from a fixed tuple, and the resolved
    path must still live under the vault root — belt, braces, and a test
    for every hostile name we could think of.
  * Extension whitelist (pdf/png/jpg/jpeg/csv/txt/eml) and a size cap —
    an uploaded .html or .svg is refused outright, because anything the
    browser could execute must never enter the dashboard's origin.
  * Downloads are served as attachments with nosniff — never rendered
    inline, whatever the file claims to be.
  * Every add/remove is audited by name (never contents).

Stdlib only.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .secure import open_bytes, seal_bytes

CATEGORIES = ("receipts", "statements", "bills", "confirmations")
ALLOWED_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".csv", ".txt", ".eml")
MAX_FILE_BYTES = 10 * 1024 * 1024   # 10 MB — a statement PDF is ~200 KB
MAX_NAME_LEN = 80

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")


def sanitize_name(name: str) -> "str | None":
    """A filename we are willing to write to disk — or None.

    Rejects anything with path separators, dot-dot, leading dots, control
    characters, or an extension outside the whitelist. What survives is a
    plain `name.ext` that cannot navigate anywhere."""
    name = (name or "").strip()
    if not name or len(name) > MAX_NAME_LEN:
        return None
    if "/" in name or "\\" in name or ".." in name:
        return None
    if not _SAFE_NAME.match(name):
        return None
    dot = name.rfind(".")
    if dot <= 0:
        return None
    if name[dot:].lower() not in ALLOWED_EXTENSIONS:
        return None
    return name


class DocumentVault:
    def __init__(self, config, cipher=None):
        from .secure import Cipher
        self.root = Path(config.home) / "vault"
        self.cipher = cipher if cipher is not None else Cipher.from_env()

    def ensure(self) -> Path:
        for category in CATEGORIES:
            (self.root / category).mkdir(parents=True, exist_ok=True)
        return self.root

    def _resolve(self, category: str, name: str) -> "Path | None":
        """The one gate every file operation passes: fixed category, sanitised
        name, and the final resolved path MUST stay under the vault root."""
        if category not in CATEGORIES:
            return None
        safe = sanitize_name(name)
        if safe is None:
            return None
        path = (self.root / category / safe).resolve()
        try:
            if not path.is_relative_to(self.root.resolve()):
                return None
        except AttributeError:      # pragma: no cover (py<3.9 has no such attr)
            return None
        return path

    def add(self, category: str, name: str, data: bytes) -> dict:
        if not isinstance(data, (bytes, bytearray)) or len(data) == 0:
            return {"error": "empty file"}
        if len(data) > MAX_FILE_BYTES:
            return {"error": f"file too big (max {MAX_FILE_BYTES // (1024 * 1024)} MB)"}
        path = self._resolve(category, name)
        if path is None:
            return {"error": "refused: bad category, unsafe filename, or "
                             f"extension outside {', '.join(ALLOWED_EXTENSIONS)}"}
        self.ensure()
        # collisions get a numbered suffix rather than overwriting evidence
        base, ext = path.stem, path.suffix
        counter = 1
        while path.exists():
            path = path.with_name(f"{base}-{counter}{ext}")
            counter += 1
        path.write_bytes(seal_bytes(bytes(data), self.cipher))
        return {"ok": True, "category": category, "name": path.name,
                "sealed": self.cipher is not None}

    def read(self, category: str, name: str) -> "bytes | None":
        path = self._resolve(category, name)
        if path is None or not path.exists():
            return None
        return open_bytes(path.read_bytes(), self.cipher)

    def delete(self, category: str, name: str) -> bool:
        path = self._resolve(category, name)
        if path is None or not path.exists():
            return False
        path.unlink()
        return True

    def list(self, category: "str | None" = None) -> "list[dict]":
        out = []
        for cat in CATEGORIES if category is None else (category,):
            if cat not in CATEGORIES:
                continue
            folder = self.root / cat
            if not folder.exists():
                continue
            for f in sorted(folder.iterdir()):
                if not f.is_file():
                    continue
                stat = f.stat()
                out.append({
                    "category": cat,
                    "name": f.name,
                    "size": stat.st_size,
                    "modified": dt.datetime.fromtimestamp(
                        stat.st_mtime).isoformat(timespec="minutes"),
                })
        return out

    def archive_csv(self, name: str, text: str) -> "dict | None":
        """Auto-file an imported statement CSV (dated copy) — best-effort;
        an archive failure must never fail the import itself."""
        stamp = dt.date.today().isoformat()
        base = sanitize_name(name or "statement.csv") or "statement.csv"
        try:
            return self.add("statements", f"{stamp}-{base}", text.encode("utf-8"))
        except Exception:
            return None
