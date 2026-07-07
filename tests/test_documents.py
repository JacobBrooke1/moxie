"""The document vault: user-supplied filenames and bytes, so the hostile
cases get tested hardest — traversal, executable extensions, inline serving."""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from moxie.config import Config
from moxie.dashboard import Dash, serve
from moxie.documents import MAX_FILE_BYTES, DocumentVault, sanitize_name
from moxie.storage import Store
from moxie.vault import AuditLog


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.delenv("MOXIE_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("MOXIE_DASH_TOKEN", raising=False)
    config = Config(home=tmp_path / "home")
    store = Store(tmp_path / "home" / "moxie.db")
    audit = AuditLog(tmp_path / "home" / "audit.log")
    return config, store, audit


# ------------------------------------------------------------- hostile ------
HOSTILE_NAMES = [
    "../../evil.pdf",           # classic traversal
    "..\\..\\evil.pdf",         # windows traversal
    "a/b.pdf",                  # separator smuggling
    "a\\b.pdf",
    "..",
    ".hidden.pdf",              # leading dot
    "x.html",                   # browser-executable
    "x.svg",                    # scriptable image
    "x.exe",
    "x.pdf.exe",                # double extension
    "x",                        # no extension
    "x.",                       # empty extension
    "con.pdf|whoami",           # shell chars
    "x" * 100 + ".pdf",         # oversized
    "",                         # empty
    "évil‮.pdf",           # RTL/unicode trickery outside the charset
]


@pytest.mark.parametrize("name", HOSTILE_NAMES, ids=[repr(n)[:24] for n in HOSTILE_NAMES])
def test_hostile_filenames_are_refused(ctx, name):
    config, store, audit = ctx
    vault = DocumentVault(config, cipher=None)
    assert sanitize_name(name) is None
    out = vault.add("receipts", name, b"data")
    assert "error" in out
    # and absolutely nothing appeared outside (or inside) the vault
    assert vault.list() == []


def test_bad_category_is_refused(ctx):
    config, store, audit = ctx
    vault = DocumentVault(config, cipher=None)
    assert "error" in vault.add("../../etc", "ok.pdf", b"data")
    assert vault.read("nope", "ok.pdf") is None


def test_size_cap(ctx):
    config, store, audit = ctx
    vault = DocumentVault(config, cipher=None)
    out = vault.add("bills", "big.pdf", b"x" * (MAX_FILE_BYTES + 1))
    assert "too big" in out["error"]


# ------------------------------------------------------------- round trips --
def test_add_list_read_delete_round_trip(ctx):
    config, store, audit = ctx
    vault = DocumentVault(config, cipher=None)
    out = vault.add("statements", "jan.csv", b"Date,Amount\n2026-01-01,1.00\n")
    assert out["ok"] and out["name"] == "jan.csv"
    listed = vault.list()
    assert listed[0]["category"] == "statements" and listed[0]["name"] == "jan.csv"
    assert vault.read("statements", "jan.csv").startswith(b"Date,Amount")
    assert vault.delete("statements", "jan.csv") is True
    assert vault.list() == []


def test_collisions_get_numbered_never_overwritten(ctx):
    config, store, audit = ctx
    vault = DocumentVault(config, cipher=None)
    vault.add("receipts", "r.txt", b"first")
    out = vault.add("receipts", "r.txt", b"second")
    assert out["name"] == "r-1.txt"
    assert vault.read("receipts", "r.txt") == b"first"     # evidence intact
    assert vault.read("receipts", "r-1.txt") == b"second"


def test_encrypted_at_rest_round_trip(ctx):
    cryptography = pytest.importorskip("cryptography")  # noqa: F841
    from moxie.secure import Cipher, generate_key
    config, store, audit = ctx
    vault = DocumentVault(config, cipher=Cipher(generate_key()))
    vault.add("bills", "secret.txt", b"MERCHANT-NAME-42")
    raw = next((vault.root / "bills").iterdir()).read_bytes()
    assert b"MERCHANT-NAME-42" not in raw                  # ciphertext on disk
    assert raw.startswith(b"encb:")
    assert vault.read("bills", "secret.txt") == b"MERCHANT-NAME-42"


# ------------------------------------------------------------- dashboard ----
def _server(ctx):
    srv = serve(*ctx, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"X-Moxie": "1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_upload_download_delete_over_http_with_attachment_headers(ctx):
    import base64
    srv, base = _server(ctx)
    try:
        out = _post(base, "/api/documents/upload",
                    {"category": "receipts", "name": "till.txt",
                     "content_b64": base64.b64encode(b"COFFEE 4.75").decode()})
        assert out["ok"] and out["name"] == "till.txt"

        url = (base + "/api/documents/get?category=receipts&name="
               + urllib.parse.quote("till.txt"))
        with urllib.request.urlopen(url, timeout=10) as r:
            assert r.read() == b"COFFEE 4.75"
            # never inline, never sniffed — whatever the file claims to be
            assert r.headers["Content-Disposition"].startswith("attachment")
            assert r.headers["X-Content-Type-Options"] == "nosniff"
            assert r.headers["Content-Type"] == "application/octet-stream"

        out = _post(base, "/api/documents/delete",
                    {"category": "receipts", "name": "till.txt"})
        assert out["ok"] is True
        config, store, audit = ctx
        events = [e["event"] for e in audit.entries()]
        for expected in ("document_added", "document_downloaded", "document_removed"):
            assert expected in events
        # audit carries names only, never contents
        assert not any("COFFEE" in json.dumps(e) for e in audit.entries())
    finally:
        srv.shutdown()


def test_hostile_upload_over_http_is_refused(ctx):
    import base64
    srv, base = _server(ctx)
    try:
        out = _post(base, "/api/documents/upload",
                    {"category": "receipts", "name": "../../evil.html",
                     "content_b64": base64.b64encode(b"<script>x</script>").decode()})
        assert "error" in out
        out = _post(base, "/api/documents/upload",
                    {"category": "receipts", "name": "x.txt",
                     "content_b64": "not-base64!!!"})
        assert "error" in out
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(
                base + "/api/documents/get?category=receipts&name=..%2F..%2Fevil.html",
                timeout=10)
        assert e.value.code == 404
    finally:
        srv.shutdown()


def test_csv_import_auto_archives_to_the_vault(ctx):
    config, store, audit = ctx
    dash = Dash(config, store, audit)
    csv_text = ("Date,Description,Amount\n"
                "2026-04-02,FITCLUB,-29.99\n2026-05-02,FITCLUB,-29.99\n")
    out = dash.import_csv_text("statement.csv", csv_text)
    assert out["archived"] is True
    files = DocumentVault(config).list("statements")
    assert len(files) == 1 and files[0]["name"].endswith("statement.csv")
    assert any(e["event"] == "document_added"
               and e["data"].get("via") == "csv_import" for e in audit.entries())
