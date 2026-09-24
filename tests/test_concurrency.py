"""Per-dataset write serialization: concurrent version-changing operations on
the SAME dataset (transform apply, undo/redo, refresh/replay, pipeline apply,
delete) used to race on `current_version` -- each was captured once before
being handed to a worker thread, so two concurrent calls could compute the
same "next version" and collide, or one could silently discard the other's
just-written version. These tests hammer the real `Services` object with
real threads (not mocked), the same way two concurrent HTTP requests would.
"""

import threading

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def big_ds(services, tmp_path):
    rng = np.random.default_rng(0)
    n = 2000
    df = pd.DataFrame({
        "x": rng.normal(0, 1, n), "y": rng.normal(0, 1, n),
        "region": rng.choice(["north", "south", "east", "west"], n),
    })
    path = tmp_path / "big.csv"
    df.to_csv(path, index=False)
    services.ingest_file(str(path), "big")
    return "big"


def _run_concurrently(fns):
    """Run each callable in its own thread, starting them as close together as
    possible, and return (results, errors) collected without raising."""
    results: list = [None] * len(fns)
    errors: list = [None] * len(fns)
    barrier = threading.Barrier(len(fns))

    def worker(i, fn):
        barrier.wait()
        try:
            results[i] = fn()
        except Exception as exc:  # noqa: BLE001 - capture, don't let a thread crash the test
            errors[i] = exc

    threads = [threading.Thread(target=worker, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results, errors


def test_concurrent_transform_apply_on_same_dataset_never_collides(services, big_ds):
    n_threads = 8
    fns = [
        (lambda i=i: services.transform_apply(big_ds, "filter", {"expr": f"x > {-5 - i}"}))
        for i in range(n_threads)
    ]
    results, errors = _run_concurrently(fns)

    assert errors == [None] * n_threads, f"unexpected errors: {[e for e in errors if e]}"

    row = services._dataset_row(big_ds)
    versions = services.meta.list_versions(row["id"])
    version_numbers = sorted(v["version"] for v in versions)
    # every one of the n_threads calls appended exactly one version on top of
    # v0, in some order, with none lost and none colliding: v0..v{n_threads}
    assert version_numbers == list(range(n_threads + 1))
    assert row["current_version"] == n_threads
    # each of DuckDB's own tables for those versions must actually exist
    for v in versions:
        assert services.engine.row_count(v["table_name"]) >= 0


def test_concurrent_transform_apply_and_undo_never_corrupt_versions(services, big_ds):
    # a mix of writers (transform_apply) and a pointer-only writer (undo) on
    # the same dataset -- must not interleave into an inconsistent state
    services.transform_apply(big_ds, "filter", {"expr": "x > -100"})  # v1, so undo has somewhere to go
    fns = [
        lambda: services.transform_apply(big_ds, "filter", {"expr": "x > -50"}),
        lambda: services.transform_apply(big_ds, "filter", {"expr": "x > -40"}),
        lambda: services.undo(big_ds, steps=1),
        lambda: services.transform_apply(big_ds, "filter", {"expr": "x > -30"}),
    ]
    results, errors = _run_concurrently(fns)

    # DataError ("no version N to undo to") is an acceptable outcome if undo
    # loses the race to go first; anything else (IntegrityError, KeyError,
    # a stale/duplicate version) is not.
    from nightingale.workbench.engine import DataError

    for e in errors:
        if e is not None:
            assert isinstance(e, DataError), f"unexpected error type: {type(e)}: {e}"

    row = services._dataset_row(big_ds)
    versions = services.meta.list_versions(row["id"])
    version_numbers = [v["version"] for v in versions]
    # no duplicate version numbers (a version-branching undo can legitimately
    # leave a *gap* -- e.g. v3 discarded by a later step branching off v2 --
    # that isn't a concurrency bug; a duplicate or a missing current_version
    # pointer is)
    assert len(version_numbers) == len(set(version_numbers)), f"duplicate version number(s): {version_numbers}"
    assert row["current_version"] in version_numbers


def test_concurrent_transform_apply_and_delete_never_corrupt_state(services, big_ds):
    fns = [
        lambda: services.transform_apply(big_ds, "filter", {"expr": "x > -20"}),
        lambda: services.transform_apply(big_ds, "filter", {"expr": "x > -10"}),
        lambda: services.dataset_delete(big_ds, force=True),
    ]
    results, errors = _run_concurrently(fns)

    from nightingale.services import NotFoundError

    for e in errors:
        if e is not None:
            assert isinstance(e, (NotFoundError, LookupError)), f"unexpected error type: {type(e)}: {e}"

    # whichever order won, the dataset is either gone or in a consistent state
    row = services.meta.get_dataset(big_ds)
    if row is not None:
        versions = services.meta.list_versions(row["id"])
        version_numbers = [v["version"] for v in versions]
        assert len(version_numbers) == len(set(version_numbers)), f"duplicate version number(s): {version_numbers}"
        assert row["current_version"] in version_numbers
