"""Phase 7: encryption at rest, keychain secrets, and the honest failure modes."""
import sys
import types

import pytest

from moxie.config import Config
from moxie.models import Receipt, Transaction
from moxie.secure import (Cipher, generate_key, get_secret, keyring_available,
                          maybe_decrypt, set_secret)
from moxie.storage import Store

cryptography = pytest.importorskip("cryptography")


# ------------------------------------------------------------------ cipher --
def test_cipher_round_trip_and_tagging():
    c = Cipher(generate_key())
    sealed = c.encrypt('{"merchant": "FitClub"}')
    assert sealed.startswith("enc:") and "FitClub" not in sealed
    assert c.decrypt(sealed) == '{"merchant": "FitClub"}'
    # legacy plaintext passes through untouched
    assert c.decrypt('{"merchant": "FitClub"}') == '{"merchant": "FitClub"}'


def test_wrong_key_fails_loudly():
    sealed = Cipher(generate_key()).encrypt("secret")
    other = Cipher(generate_key())
    with pytest.raises(Exception):     # InvalidToken from cryptography
        other.decrypt(sealed)


def test_encrypted_data_without_key_is_a_clear_error():
    sealed = Cipher(generate_key()).encrypt("secret")
    with pytest.raises(RuntimeError, match="MOXIE_ENCRYPTION_KEY"):
        maybe_decrypt(sealed, None)


# ------------------------------------------------------------------ store ---
def test_store_encrypts_payloads_on_disk(tmp_path):
    cipher = Cipher(generate_key())
    store = Store(tmp_path / "m.db", cipher=cipher)
    store.save_transactions([Transaction(date="2026-06-01", merchant="SecretShop",
                                         amount=12.34, currency="£")])
    store.save_receipt(Receipt(merchant="SecretShop", date="2026-06-01", amount=12.34))
    # the raw database file never contains the merchant in plaintext
    raw = (tmp_path / "m.db").read_bytes()
    assert b"SecretShop" not in raw
    # …but reads still work
    assert store.load_transactions()[0].merchant == "SecretShop"
    assert store.load_receipts()[0].merchant == "SecretShop"


def test_reencrypt_all_migrates_plaintext_history(tmp_path):
    plain = Store(tmp_path / "m.db")
    plain.save_transactions([Transaction(date="2026-06-01", merchant="OldRow",
                                         amount=1.00, currency="£")])
    cipher = Cipher(generate_key())
    migrated = plain.reencrypt_all(cipher)
    assert migrated == 1
    assert b"OldRow" not in (tmp_path / "m.db").read_bytes()
    # a fresh handle with the cipher reads the migrated data
    again = Store(tmp_path / "m.db", cipher=cipher)
    assert again.load_transactions()[0].merchant == "OldRow"


def test_bank_link_state_is_sealed(tmp_path, monkeypatch):
    from moxie.providers import BankLink
    monkeypatch.delenv("MOXIE_ENCRYPTION_KEY", raising=False)
    config = Config(home=tmp_path / "home")
    cipher = Cipher(generate_key())
    link = BankLink(config, cipher=cipher)
    link.save({"provider": "truelayer", "tokens": {"access_token": "tl-secret"}})
    assert "tl-secret" not in link.path.read_text(encoding="utf-8")
    assert link.load()["tokens"]["access_token"] == "tl-secret"


# ------------------------------------------------------------------ secrets -
def test_get_secret_prefers_environment(monkeypatch):
    monkeypatch.setenv("MOXIE_TEST_SECRET", "from-env")
    assert get_secret("MOXIE_TEST_SECRET") == "from-env"
    monkeypatch.delenv("MOXIE_TEST_SECRET")


def test_get_secret_falls_back_to_keyring(monkeypatch):
    fake = types.SimpleNamespace(
        get_password=lambda service, name: "from-keyring" if service == "moxie" else None,
        set_password=lambda service, name, value: None,
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.delenv("MOXIE_TEST_SECRET2", raising=False)
    assert get_secret("MOXIE_TEST_SECRET2") == "from-keyring"
    assert keyring_available() is True


def test_set_secret_without_keyring_says_how_to_install(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_keyring(name, *a, **k):
        if name == "keyring":
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.delitem(sys.modules, "keyring", raising=False)
    monkeypatch.setattr(builtins, "__import__", no_keyring)
    with pytest.raises(RuntimeError, match=r"moxie-agent\[secure\]"):
        set_secret("X", "y")


def test_set_secret_stores_in_fake_keyring(monkeypatch):
    saved = {}
    fake = types.SimpleNamespace(
        get_password=lambda service, name: saved.get((service, name)),
        set_password=lambda service, name, value: saved.__setitem__((service, name), value),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    where = set_secret("MOXIE_TEST_SECRET3", "v3")
    assert where == "os-keychain"
    assert saved[("moxie", "MOXIE_TEST_SECRET3")] == "v3"
