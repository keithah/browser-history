import sqlite3
from pathlib import Path

from browser_history import enrich, ingest, query, store, utils


def _make_fake_safari_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT)")
    conn.execute(
        "CREATE TABLE history_visits (id INTEGER PRIMARY KEY, history_item INTEGER, visit_time REAL, title TEXT)"
    )
    conn.execute("INSERT INTO history_items (id, url) VALUES (1, 'https://example.com')")
    conn.execute("INSERT INTO history_visits (id, history_item, visit_time, title) VALUES (1, 1, 1.0, 'Example')")
    conn.commit()
    conn.close()


def test_ingest_generic_writes_ingest_state(tmp_path):
    src = tmp_path / "safari.db"
    _make_fake_safari_db(src)
    dest_db = tmp_path / "store.db"
    stats = ingest._ingest_generic(
        source="safari:test",
        db_path=src,
        snapshot_name="safari_snapshot.db",
        dest_db=dest_db,
        sql_base="""
            SELECT hv.visit_time AS ts_raw, hi.url AS url, hv.title AS title
            FROM history_visits hv
            JOIN history_items hi ON hv.history_item = hi.id
        """,
        ts_to_dt=lambda raw: utils.safari_ts_to_datetime(raw),
        cutoff_transform=lambda ts: ts - utils.SAFARI_EPOCH.timestamp(),
        limit=None,
        fetch_metadata=False,
        metadata_max=0,
        dedupe_scope="global",
        state_interval=1,
        progress_every=0,
    )
    assert stats["seen"] == 1
    assert stats["inserted"] == 1

    conn_store = store.connect(dest_db)
    state = store.read_ingest_state(conn_store)
    assert "sources" in state
    assert "safari:test" in state["sources"]
    assert state["sources"]["safari:test"]["last_seen"] == 1

    # Second run should skip due to cutoff.
    stats_again = ingest._ingest_generic(
        source="safari:test",
        db_path=src,
        snapshot_name="safari_snapshot.db",
        dest_db=dest_db,
        sql_base="""
            SELECT hv.visit_time AS ts_raw, hi.url AS url, hv.title AS title
            FROM history_visits hv
            JOIN history_items hi ON hv.history_item = hi.id
        """,
        ts_to_dt=lambda raw: utils.safari_ts_to_datetime(raw),
        cutoff_transform=lambda ts: ts - utils.SAFARI_EPOCH.timestamp(),
        limit=None,
        fetch_metadata=False,
        metadata_max=0,
        dedupe_scope="global",
        state_interval=1,
        progress_every=0,
    )
    assert stats_again["inserted"] == 0


def test_query_filters_by_source(tmp_path):
    db_path = tmp_path / "store.db"
    conn = store.connect(db_path)
    store.insert_visit(
        conn,
        {
            "url": "https://example.com/one",
            "title": "One",
            "domain": "example.com",
            "path": "/one",
            "visited_at": utils.iso(utils.SAFARI_EPOCH),
            "visited_ts": utils.SAFARI_EPOCH.timestamp(),
            "category": "other",
            "labels": ["other"],
            "summary": "one",
            "source": "safari",
        },
    )
    store.insert_visit(
        conn,
        {
            "url": "https://example.com/two",
            "title": "Two",
            "domain": "example.com",
            "path": "/two",
            "visited_at": utils.iso(utils.SAFARI_EPOCH),
            "visited_ts": utils.SAFARI_EPOCH.timestamp() + 1,
            "category": "ai",
            "labels": ["ai"],
            "summary": "two",
            "source": "chrome:Default",
        },
    )

    safari_rows = store.query_visits(conn, source="safari")
    assert len(safari_rows) == 1
    assert safari_rows[0]["source"] == "safari"

    chrome_rows = store.query_visits(conn, source="chrome:Default")
    assert len(chrome_rows) == 1
    assert chrome_rows[0]["source"] == "chrome:Default"

    all_rows = store.query_visits(conn, source=None, limit=10)
    assert len(all_rows) == 2


def test_dedupe_scopes_and_occurrences(tmp_path):
    db_path = tmp_path / "store.db"
    conn = store.connect(db_path)
    inserted, vid1 = store.insert_visit(
        conn,
        {
            "url": "https://example.com/a",
            "title": "A",
            "domain": "example.com",
            "path": "/a",
            "visited_at": utils.iso(utils.SAFARI_EPOCH),
            "visited_ts": utils.SAFARI_EPOCH.timestamp(),
            "category": "other",
            "labels": ["other"],
            "summary": "a",
            "source": "safari",
        },
        dedupe_scope="global",
    )
    assert inserted
    store.insert_occurrence(conn, vid1, "safari", 1.0, utils.iso(utils.SAFARI_EPOCH))

    # Global dedupe: Chrome occurrence should map to same visit_id.
    inserted2, vid2 = store.insert_visit(
        conn,
        {
            "url": "https://example.com/a",
            "title": "A",
            "domain": "example.com",
            "path": "/a",
            "visited_at": utils.iso(utils.SAFARI_EPOCH),
            "visited_ts": utils.SAFARI_EPOCH.timestamp(),
            "category": "other",
            "labels": ["other"],
            "summary": "a",
            "source": "chrome:Default",
        },
        dedupe_scope="global",
    )
    assert not inserted2
    assert vid1 == vid2
    store.insert_occurrence(conn, vid2, "chrome:Default", 2.0, utils.iso(utils.SAFARI_EPOCH))

    occ = store.fetch_occurrences(conn, [vid1])
    assert len(occ[vid1]) == 2
    sources = {o["source"] for o in occ[vid1]}
    assert sources == {"safari", "chrome:Default"}


def test_per_source_dedupe_allows_multiple_rows(tmp_path):
    db_path = tmp_path / "store.db"
    conn = store.connect(db_path)
    inserted_a, vid_a = store.insert_visit(
        conn,
        {
            "url": "https://example.com/a",
            "title": "A",
            "domain": "example.com",
            "path": "/a",
            "visited_at": utils.iso(utils.SAFARI_EPOCH),
            "visited_ts": utils.SAFARI_EPOCH.timestamp(),
            "category": "other",
            "labels": ["other"],
            "summary": "a",
            "source": "safari",
        },
        dedupe_scope="source",
    )
    inserted_b, vid_b = store.insert_visit(
        conn,
        {
            "url": "https://example.com/a",
            "title": "A",
            "domain": "example.com",
            "path": "/a",
            "visited_at": utils.iso(utils.SAFARI_EPOCH),
            "visited_ts": utils.SAFARI_EPOCH.timestamp(),
            "category": "other",
            "labels": ["other"],
            "summary": "a",
            "source": "chrome:Profile",
        },
        dedupe_scope="source",
    )
    assert inserted_a
    assert inserted_b
    assert vid_a != vid_b


def test_backfill_occurrences(tmp_path):
    db_path = tmp_path / "store.db"
    conn = store.connect(db_path)
    inserted, vid = store.insert_visit(
        conn,
        {
            "url": "https://example.com/backfill",
            "title": "Backfill",
            "domain": "example.com",
            "path": "/backfill",
            "visited_at": utils.iso(utils.SAFARI_EPOCH),
            "visited_ts": utils.SAFARI_EPOCH.timestamp(),
            "category": "other",
            "labels": ["other"],
            "summary": "bf",
            "source": "safari",
        },
        dedupe_scope="global",
    )
    assert inserted
    created = store.backfill_occurrences(conn)
    assert created == 1
    occ = store.fetch_occurrences(conn, [vid])
    assert vid in occ
    assert occ[vid][0]["source"] == "safari"
    # Maintenance helpers should be safe to run.
    store.run_analyze(conn)
    conn.close()
    store.run_vacuum(db_path)


def test_stats_by_source(tmp_path, monkeypatch):
    db_path = tmp_path / "store.db"
    orig_connect = store.connect
    monkeypatch.setattr(store, "connect", lambda db_path=db_path: orig_connect(db_path))
    conn = store.connect()
    store.insert_visit(
        conn,
        {
            "url": "https://example.com/one",
            "title": "One",
            "domain": "example.com",
            "path": "/one",
            "visited_at": utils.iso(utils.SAFARI_EPOCH),
            "visited_ts": utils.SAFARI_EPOCH.timestamp(),
            "category": "other",
            "labels": ["other"],
            "summary": "one",
            "source": "safari",
        },
        dedupe_scope="global",
    )
    store.insert_visit(
        conn,
        {
            "url": "https://example.com/two",
            "title": "Two",
            "domain": "example.com",
            "path": "/two",
            "visited_at": utils.iso(utils.SAFARI_EPOCH),
            "visited_ts": utils.SAFARI_EPOCH.timestamp() + 1,
            "category": "ai",
            "labels": ["ai"],
            "summary": "two",
            "source": "chrome:Default",
        },
        dedupe_scope="global",
    )
    conn.commit()
    conn.close()
    stats = query.stats()
    sources = {row["source"]: row["count"] for row in stats["sources"]}
    assert sources.get("safari") == 1
    assert sources.get("chrome:Default") == 1


def test_query_includes_occurrences(tmp_path, monkeypatch):
    db_path = tmp_path / "store.db"
    orig_connect = store.connect
    monkeypatch.setattr(store, "connect", lambda db_path=db_path: orig_connect(db_path))
    conn = store.connect()
    inserted, vid = store.insert_visit(
        conn,
        {
            "url": "https://example.com/x",
            "title": "X",
            "domain": "example.com",
            "path": "/x",
            "visited_at": utils.iso(utils.SAFARI_EPOCH),
            "visited_ts": utils.SAFARI_EPOCH.timestamp(),
            "category": "other",
            "labels": ["other"],
            "summary": "x",
            "source": "safari",
        },
        dedupe_scope="global",
    )
    assert inserted
    store.insert_occurrence(conn, vid, "safari", 1.0, utils.iso(utils.SAFARI_EPOCH))
    store.insert_occurrence(conn, vid, "chrome:Default", 2.0, utils.iso(utils.SAFARI_EPOCH))
    conn.commit()
    conn.close()

    rows = query.run_query(limit=5)
    assert len(rows) == 1
    assert len(rows[0]["sources"]) == 2
    srcs = {s["source"] for s in rows[0]["sources"]}
    assert srcs == {"safari", "chrome:Default"}


def test_metadata_max_counts_attempts(tmp_path, monkeypatch):
    src = tmp_path / "safari_multi.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT)")
    conn.execute(
        "CREATE TABLE history_visits (id INTEGER PRIMARY KEY, history_item INTEGER, visit_time REAL, title TEXT)"
    )
    conn.execute("INSERT INTO history_items (id, url) VALUES (1, 'https://example.com/one')")
    conn.execute("INSERT INTO history_items (id, url) VALUES (2, 'https://example.com/two')")
    conn.execute("INSERT INTO history_visits (id, history_item, visit_time, title) VALUES (1, 1, 1.0, 'One')")
    conn.execute("INSERT INTO history_visits (id, history_item, visit_time, title) VALUES (2, 2, 2.0, 'Two')")
    conn.commit()
    conn.close()

    calls = {"count": 0}

    def fake_fetch(url):
        calls["count"] += 1
        return {}

    monkeypatch.setattr(enrich, "fetch_metadata", fake_fetch)

    dest_db = tmp_path / "store.db"
    stats = ingest._ingest_generic(
        source="safari",
        db_path=src,
        snapshot_name="safari_snapshot.db",
        dest_db=dest_db,
        sql_base="""
            SELECT hv.visit_time AS ts_raw, hi.url AS url, hv.title AS title
            FROM history_visits hv
            JOIN history_items hi ON hv.history_item = hi.id
        """,
        ts_to_dt=lambda raw: utils.safari_ts_to_datetime(raw),
        cutoff_transform=lambda ts: ts - utils.SAFARI_EPOCH.timestamp(),
        limit=None,
        fetch_metadata=True,
        metadata_max=1,
        dedupe_scope="global",
        state_interval=0,
        progress_every=0,
    )
    assert stats["seen"] == 2
    assert stats["metadata_used"] == 1
    assert calls["count"] == 1
