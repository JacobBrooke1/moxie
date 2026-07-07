"""Secrets and encryption-at-rest. Optional extra, honest fallbacks.

Two jobs, one module:

  * get_secret / set_secret — secrets come from (in order) the environment
    (which the tiny .env loader feeds), then the OS keychain via `keyring`.
    `moxie secret set NAME` moves a secret off disk into the keychain.

  * Cipher — Fernet (AES-128-CBC + HMAC) field-level encryption for the
    local store and bank tokens, keyed by MOXIE_ENCRYPTION_KEY. Enable with
    `moxie encrypt on`. Values are tagged `enc:` so plaintext and encrypted
    rows can coexist during migration.

Both need the optional extra:  pip install "moxie-agent[secure]"
(cryptography + keyring). The core stays stdlib-only without them; SECURITY.md
is honest about what's protected in each mode.
"""
from __future__ import annotations

import os

KEYRING_SERVICE = "moxie"
ENC_PREFIX = "enc:"
KEY_NAME = "MOXIE_ENCRYPTION_KEY"


# ------------------------------------------------------------------ secrets -
def keyring_available() -> bool:
    try:
        import keyring  # noqa: F401  (optional extra)
        return True
    except ImportError:
        return False


def get_secret(name: str) -> "str | None":
    """Environment (populated from .env) first, then the OS keychain."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        return None


def set_secret(name: str, value: str) -> str:
    """Store in the OS keychain. Returns where it went (for honest copy)."""
    try:
        import keyring
    except ImportError as e:
        raise RuntimeError(
            "the OS keychain needs the optional extra: "
            "pip install \"moxie-agent[secure]\" — until then, secrets live "
            "in ~/.moxie/.env (readable by anything running as you)"
        ) from e
    keyring.set_password(KEYRING_SERVICE, name, value)
    return "os-keychain"


def delete_secret(name: str) -> None:
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, name)
    except Exception:
        pass


# ------------------------------------------------------------------ cipher --
def generate_key() -> str:
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise RuntimeError(
            "encryption needs the optional extra: pip install \"moxie-agent[secure]\""
        ) from e
    return Fernet.generate_key().decode("ascii")


class Cipher:
    """Field-level Fernet encryption. `encrypt` tags values with `enc:`;
    `decrypt` passes untagged (legacy plaintext) values straight through, so
    turning encryption on never breaks reading old rows."""

    def __init__(self, key: str):
        try:
            from cryptography.fernet import Fernet
        except ImportError as e:
            raise RuntimeError(
                "MOXIE_ENCRYPTION_KEY is set but the cipher isn't installed: "
                "pip install \"moxie-agent[secure]\""
            ) from e
        self._fernet = Fernet(key.encode("ascii"))

    @classmethod
    def from_env(cls) -> "Cipher | None":
        """The active cipher, or None when encryption isn't enabled."""
        key = get_secret(KEY_NAME)
        return cls(key) if key else None

    def encrypt(self, text: str) -> str:
        return ENC_PREFIX + self._fernet.encrypt(text.encode("utf-8")).decode("ascii")

    def decrypt(self, text: str) -> str:
        if not text.startswith(ENC_PREFIX):
            return text  # legacy plaintext row
        return self._fernet.decrypt(text[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")


def maybe_decrypt(text: str, cipher: "Cipher | None") -> str:
    if text.startswith(ENC_PREFIX):
        if cipher is None:
            raise RuntimeError(
                "this data is encrypted but no MOXIE_ENCRYPTION_KEY is available "
                "— set it in the environment or the OS keychain")
        return cipher.decrypt(text)
    return text


# Binary variants — the document vault stores files, not strings.
ENC_BYTES_PREFIX = b"encb:"


def seal_bytes(data: bytes, cipher: "Cipher | None") -> bytes:
    if cipher is None:
        return data
    return ENC_BYTES_PREFIX + cipher._fernet.encrypt(data)


def open_bytes(data: bytes, cipher: "Cipher | None") -> bytes:
    if not data.startswith(ENC_BYTES_PREFIX):
        return data  # legacy/plaintext file
    if cipher is None:
        raise RuntimeError(
            "this file is encrypted but no MOXIE_ENCRYPTION_KEY is available "
            "— set it in the environment or the OS keychain")
    return cipher._fernet.decrypt(data[len(ENC_BYTES_PREFIX):])
