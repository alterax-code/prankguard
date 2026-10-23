"""EventStore : SQLite + snapshots, dans un dossier temporaire."""
import time

import numpy as np
import pytest

from prankguard.events import EventStore


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "ev.sqlite3", tmp_path / "snaps")
    yield s
    s.close()


def frame():
    return np.random.default_rng(0).integers(0, 255, (48, 64, 3), dtype=np.uint8)


def test_add_and_recent_newest_first(store):
    ids = [store.add("lock", "critical", f"t{i}", f"d{i}")["id"] for i in range(5)]
    got = store.recent()
    assert [e["id"] for e in got] == ids[::-1]
    assert got[0]["title"] == "t4" and got[0]["detail"] == "d4"
    assert set(got[0]) == {"id", "ts", "kind", "level", "title", "detail", "snapshot"}
    assert len(store.recent(limit=2)) == 2


def test_kind_filter(store):
    store.add("lock", "critical", "a")
    store.add("device", "warning", "b")
    store.add("lock", "critical", "c")
    assert [e["title"] for e in store.recent(kind="lock")] == ["c", "a"]
    assert [e["title"] for e in store.recent(kind="device")] == ["b"]
    assert store.recent(kind="nothing") == []


def test_before_id_pagination(store):
    ids = [store.add("system", "info", f"e{i}")["id"] for i in range(10)]
    page1 = store.recent(limit=4)
    assert [e["id"] for e in page1] == ids[-1:-5:-1]
    page2 = store.recent(limit=4, before_id=page1[-1]["id"])
    assert [e["id"] for e in page2] == ids[5:1:-1]
    page3 = store.recent(limit=4, before_id=page2[-1]["id"])
    assert [e["id"] for e in page3] == ids[1::-1]
    assert store.recent(limit=4, before_id=page3[-1]["id"]) == []


def test_before_id_combined_with_kind(store):
    a = store.add("lock", "info", "1")["id"]
    store.add("warn", "info", "2")
    c = store.add("lock", "info", "3")["id"]
    assert [e["id"] for e in store.recent(before_id=c, kind="lock")] == [a]


def test_limit_is_clamped(store):
    store.add("system", "info", "x")
    assert len(store.recent(limit=0)) == 1            # min 1
    assert len(store.recent(limit=-5)) == 1
    assert len(store.recent(limit=10_000)) == 1


def test_stats_counts_by_kind_since(store):
    store.add("lock", "critical", "a")
    store.add("lock", "critical", "b")
    store.add("warn", "warning", "c")
    assert store.stats(time.time() - 60) == {"lock": 2, "warn": 1}
    assert store.stats(time.time() + 60) == {}


def test_snapshot_written_and_served(store):
    ev = store.add("lock", "critical", "intrus", frame=frame())
    assert ev["snapshot"].endswith(".jpg")
    p = store.snapshot_path(ev["snapshot"])
    assert p is not None and p.is_file() and p.stat().st_size > 0
    assert p.read_bytes()[:2] == b"\xff\xd8"          # JPEG
    assert store.add("system", "info", "no frame")["snapshot"] == ""


def test_snapshot_path_rejects_traversal_and_missing(store, tmp_path):
    (tmp_path / "x.jpg").write_bytes(b"secret")        # existe, mais hors du dossier snapshots
    ev = store.add("lock", "critical", "a", frame=frame())
    assert store.snapshot_path("../x.jpg") is None
    assert store.snapshot_path("..\\x.jpg") is None
    assert store.snapshot_path("sub/../../x.jpg") is None
    assert store.snapshot_path(str(tmp_path / "x.jpg")) is None
    assert store.snapshot_path("missing.jpg") is None
    assert store.snapshot_path("") is None
    assert store.snapshot_path(ev["snapshot"]) is not None


def test_listeners_notified_and_errors_swallowed(store):
    seen = []
    store.subscribe(lambda e: 1 / 0)
    store.subscribe(seen.append)
    ev = store.add("system", "info", "hello")
    assert seen == [ev]


def test_clear_removes_events_and_snapshots(store):
    ev = store.add("lock", "critical", "a", frame=frame())
    store.add("system", "info", "b")
    snap = store.snapshot_path(ev["snapshot"])
    assert snap.exists()
    store.clear()
    assert store.recent() == []
    assert not snap.exists()
    assert list(store.snapshots.glob("*.jpg")) == []


def test_prune_removes_only_old_events_and_their_snapshots(store):
    old = store.add("lock", "critical", "old", frame=frame())
    new = store.add("lock", "critical", "new")
    old_snap = store.snapshot_path(old["snapshot"])
    store._db.execute("UPDATE events SET ts = ? WHERE id = ?", (time.time() - 90 * 86400, old["id"]))
    store.prune(60)
    assert [e["id"] for e in store.recent()] == [new["id"]]
    assert not old_snap.exists()


def test_prune_runs_on_open(tmp_path):
    s = EventStore(tmp_path / "e.db", tmp_path / "s")
    ev = s.add("lock", "info", "ancien")
    s._db.execute("UPDATE events SET ts = ? WHERE id = ?", (time.time() - 100 * 86400, ev["id"]))
    s.close()
    s2 = EventStore(tmp_path / "e.db", tmp_path / "s", retention_days=60)
    try:
        assert s2.recent() == []
    finally:
        s2.close()
