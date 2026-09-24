"""The metadata store is shared by the request threads.

Seen live: opening a dataset makes the browser ask for its recipe, lineage
and profile at the same time, and the lineage request failed with
``sqlite3.InterfaceError: bad parameter or other API misuse`` because reads
used the shared sqlite connection without the lock the writes took.
"""
import threading

from nightingale import db


def test_concurrent_reads_and_writes_do_not_misuse_the_connection(tmp_path):
    meta = db.Meta(db.connect(tmp_path))
    for i in range(20):
        meta.conn.execute(
            "INSERT INTO datasets (name, source_id, current_version, created_at) VALUES (?,?,0,?)",
            (f"d{i}", None, db.now_iso()),
        )
    meta.conn.commit()
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
                meta.conn.execute(
                    "INSERT INTO datasets (name, source_id, current_version, created_at) VALUES (?,?,0,?)",
                    (f"w{threading.get_ident()}_{j}", None, db.now_iso()),
                )
                meta.conn.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(6)] + [threading.Thread(target=writer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]


def test_rows_behave_like_a_cursor(tmp_path):
    meta = db.Meta(db.connect(tmp_path))
    cur = meta.conn.execute(
        "INSERT INTO datasets (name, source_id, current_version, created_at) VALUES (?,?,0,?)",
        ("x", None, db.now_iso()),
    )
    assert cur.lastrowid
    assert meta.conn.execute("SELECT name FROM datasets").fetchone()["name"] == "x"
    assert [r["name"] for r in meta.conn.execute("SELECT name FROM datasets")] == ["x"]
    assert meta.conn.execute("SELECT name FROM datasets WHERE name='nope'").fetchone() is None
