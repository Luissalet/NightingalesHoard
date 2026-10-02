"""The metadata store is shared by the request threads.

Seen live: opening a dataset makes the browser ask for its recipe, lineage
and profile at the same time, and the lineage request failed with
``sqlite3.InterfaceError: bad parameter or other API misuse`` because reads
used the shared sqlite connection without the lock the writes took. The
metadata store is now the shared ``sqlkit.Database`` (every statement under
one lock), and these tests keep guarding that.
"""
import threading

from nightingale import db


def test_concurrent_reads_and_writes_do_not_misuse_the_connection(tmp_path):
    meta = db.Meta(db.connect(tmp_path))
    for i in range(20):
        meta.db.execute(
            "INSERT INTO datasets (name, source_id, current_version, created_at) VALUES (?,?,0,?)",
            (f"d{i}", None, db.now_iso()),
        )
    errors = []

    def reader():
        try:
            for _ in range(300):
                for i in range(20):
                    row = meta.get_dataset(f"d{i}")
                    assert row["name"] == f"d{i}"
                assert len(meta.list_datasets()) >= 20
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def writer():
        try:
            for j in range(200):
                meta.db.execute(
                    "INSERT INTO datasets (name, source_id, current_version, created_at) VALUES (?,?,0,?)",
                    (f"w{threading.get_ident()}_{j}", None, db.now_iso()),
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(6)] + [threading.Thread(target=writer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]


def test_rows_come_back_by_name(tmp_path):
    meta = db.Meta(db.connect(tmp_path))
    cur = meta.db.execute(
        "INSERT INTO datasets (name, source_id, current_version, created_at) VALUES (?,?,0,?)",
        ("x", None, db.now_iso()),
    )
    assert cur.lastrowid
    assert meta.db.one("SELECT name FROM datasets")["name"] == "x"
    assert [r["name"] for r in meta.db.query("SELECT name FROM datasets")] == ["x"]
    assert meta.db.one("SELECT name FROM datasets WHERE name='nope'") is None
