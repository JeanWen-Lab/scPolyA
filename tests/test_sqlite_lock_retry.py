from __future__ import annotations

import sqlite3
import threading

import pytest

import task_manage.task_runtime as task_runtime
from task_manage.task_runtime import SQLiteStore, retry_on_sqlite_locked


def _store(tmp_path):
    store = SQLiteStore(tmp_path / "state" / "tasks.sqlite")
    store.init_schema()
    return store


def test_connections_use_two_minute_busy_timeout(tmp_path):
    store = _store(tmp_path)
    with store.connect() as con:
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 120_000


def test_held_lock_is_retried_until_released(tmp_path, monkeypatch):
    store = _store(tmp_path)
    holder = sqlite3.connect(str(store.db_path), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")  # hold the write lock

    monkeypatch.setattr(task_runtime, "SQLITE_BUSY_TIMEOUT_MS", 50)
    sleeps: list[float] = []

    def release_on_second_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            holder.execute("COMMIT;")

    monkeypatch.setattr(task_runtime, "_sqlite_retry_sleep", release_on_second_sleep)

    store.meta_set("k", "v")

    assert sleeps == [1.0, 2.0]
    assert store.meta_get("k") == "v"
    holder.close()


def test_lock_retry_is_bounded(tmp_path, monkeypatch):
    store = _store(tmp_path)
    holder = sqlite3.connect(str(store.db_path), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    monkeypatch.setattr(task_runtime, "SQLITE_BUSY_TIMEOUT_MS", 20)
    monkeypatch.setattr(task_runtime, "SQLITE_LOCKED_RETRY_DELAYS_S", (0.0, 0.0))
    sleeps: list[float] = []
    monkeypatch.setattr(task_runtime, "_sqlite_retry_sleep", sleeps.append)

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        store.meta_set("k", "v")

    assert sleeps == [0.0, 0.0]
    holder.execute("ROLLBACK;")
    holder.close()


def test_lock_released_by_another_thread_succeeds_without_retry(tmp_path, monkeypatch):
    store = _store(tmp_path)
    holder = sqlite3.connect(str(store.db_path), isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE;")
    timer = threading.Timer(0.2, lambda: holder.execute("COMMIT;"))
    timer.start()
    sleeps: list[float] = []
    monkeypatch.setattr(task_runtime, "_sqlite_retry_sleep", sleeps.append)
    try:
        store.meta_set("k", "v")  # busy timeout waits for the holder
    finally:
        timer.join()
        holder.close()
    assert sleeps == []
    assert store.meta_get("k") == "v"


def test_non_lock_errors_are_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(task_runtime, "_sqlite_retry_sleep", lambda s: calls.append(("sleep", s)))

    @retry_on_sqlite_locked
    def broken():
        calls.append("call")
        raise sqlite3.OperationalError("no such table: missing")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        broken()
    assert calls == ["call"]


def _hold_until_first_retry(store, monkeypatch):
    holder = sqlite3.connect(str(store.db_path), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    monkeypatch.setattr(task_runtime, "SQLITE_BUSY_TIMEOUT_MS", 20)
    sleeps: list[float] = []

    def release(seconds: float) -> None:
        sleeps.append(seconds)
        holder.execute("COMMIT;")

    monkeypatch.setattr(task_runtime, "_sqlite_retry_sleep", release)
    return holder, sleeps


def test_generator_samples_survive_a_lock_retry(tmp_path, monkeypatch):
    # Sol review item 3: a retry must not lose items a failed attempt consumed.
    store = _store(tmp_path)
    holder, sleeps = _hold_until_first_retry(store, monkeypatch)
    store.upsert_samples(sid for sid in ["sample-a", "sample-b"])
    holder.close()
    assert sleeps == [1.0]
    with store.connect() as con:
        assert [r[0] for r in con.execute("SELECT sample_id FROM samples ORDER BY sample_id")] == [
            "sample-a", "sample-b",
        ]


def test_nested_generator_contigs_survive_a_lock_retry(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.upsert_samples(["s1", "s2"])
    holder, sleeps = _hold_until_first_retry(store, monkeypatch)
    store.upsert_contigs({"s1": (c for c in ["chr1", "chr2"]), "s2": iter(["chrX"])})
    holder.close()
    assert sleeps == [1.0]
    with store.connect() as con:
        rows = con.execute("SELECT sample_id, contig FROM contigs ORDER BY sample_id, contig").fetchall()
    assert rows == [("s1", "chr1"), ("s1", "chr2"), ("s2", "chrX")]


def test_generator_task_specs_survive_a_lock_retry(tmp_path, monkeypatch):
    from task_manage.task_specs import TASK_SPECS

    store = _store(tmp_path)
    store.init_flex_schema()
    store.init_ledger_schema()
    holder, sleeps = _hold_until_first_retry(store, monkeypatch)
    store.upsert_task_specs(spec for spec in TASK_SPECS)
    holder.close()
    assert sleeps == [1.0]
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM task_spec_registry").fetchone()[0] == len(TASK_SPECS)


def test_retry_wrapper_materialises_iterator_arguments(monkeypatch):
    seen: list[list[int]] = []
    monkeypatch.setattr(task_runtime, "_sqlite_retry_sleep", lambda _s: None)

    @retry_on_sqlite_locked
    def write(items, *, more):
        consumed = [next(iter(items)) for _ in range(1)]  # a partial read, like a failed INSERT
        seen.append(list(items) + list(more))
        if len(seen) == 1:
            raise sqlite3.OperationalError("database is locked")
        return consumed

    write(iter([1, 2]), more=(x for x in [3]))
    assert seen == [[1, 2, 3], [1, 2, 3]]
