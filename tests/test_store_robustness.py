"""Storage under fire: concurrent threads and corrupt rows must never brick
the dashboard — reads stay consistent, bad rows are skipped and counted."""
import threading

from moxie.models import Transaction
from moxie.storage import Store


def _txns(n=20):
    return [Transaction(date="2026-06-02", merchant=f"Shop{i}", amount=1.0 + i,
                        currency="£") for i in range(n)]


def test_concurrent_readers_and_writers_never_tear(tmp_path):
    """The dashboard fires several API calls at once, all through one Store.
    Regression: an unlocked shared sqlite connection could tear reads."""
    store = Store(tmp_path / "m.db")
    store.save_transactions(_txns())
    errors = []

    def reader():
        try:
            for _ in range(60):
                rows = store.load_transactions()
                assert all(r.merchant.startswith("Shop") for r in rows)
                store.get_meta("anything")
        except Exception as e:                     # pragma: no cover
            errors.append(e)

    def writer():
        try:
            for i in range(30):
                store.save_transactions(_txns())
                store.set_meta("beat", str(i))
                store.save_chat("user", f"msg {i}")
        except Exception as e:                     # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=reader) for _ in range(4)] + \
              [threading.Thread(target=writer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert store.load_errors == {}                 # nothing torn, nothing skipped
    assert len(store.load_transactions()) == 20


def test_one_corrupt_row_is_skipped_and_counted(tmp_path):
    store = Store(tmp_path / "m.db")
    store.save_transactions(_txns(3))
    # simulate torn/corrupt data written by an older, unlocked version
    store._write("INSERT INTO transactions (id, data) VALUES (?, ?)",
                 ("bad-row", "�torn-garbage-not-json"))
    rows = store.load_transactions()
    assert len(rows) == 3                          # healthy rows still load
    assert store.load_errors == {"transactions": 1}


def test_corrupt_chat_and_widget_rows_are_tolerated(tmp_path):
    store = Store(tmp_path / "m.db")
    store.save_chat("user", "hello")
    store._write("INSERT INTO chat (ts, data) VALUES (?, ?)", ("2026", "not json"))
    store.save_widget("w1", {"type": "stat_card", "title": "ok",
                             "merchants": ["x"], "months": 3})
    store._write("REPLACE INTO widgets (id, data) VALUES (?, ?)", ("w2", "junk"))
    assert [t["text"] for t in store.load_chat()] == ["hello"]
    assert [w["id"] for w in store.load_widgets()] == ["w1"]
    assert store.load_errors.get("chat") == 1
    assert store.load_errors.get("widgets") == 1
