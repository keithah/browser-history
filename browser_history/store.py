import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "history_store.db"


def _maybe_migrate_visit_unique(conn: sqlite3.Connection) -> None:
    """
    Ensure the visits unique constraint includes source.
    Older DBs may have UNIQUE(url, visited_ts); rebuild if needed.
    """
    cols = _visit_unique_columns(conn)
    if not cols:
        return
    if cols == ["url", "visited_ts", "source"]:
        return
    if cols == ["url", "visited_ts"]:
        _drop_fts(conn)
        conn.execute("ALTER TABLE visits RENAME TO visits_old;")
        conn.execute(
            """
            CREATE TABLE visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT,
                domain TEXT,
                path TEXT,
                visited_at TEXT NOT NULL,
                visited_ts REAL NOT NULL,
                category TEXT,
                labels TEXT,
                summary TEXT,
                source TEXT NOT NULL DEFAULT 'safari',
                UNIQUE(url, visited_ts, source)
            );
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO visits (id, url, title, domain, path, visited_at, visited_ts, category, labels, summary, source)
            SELECT id, url, title, domain, path, visited_at, visited_ts, category, labels, summary, source FROM visits_old;
            """
        )
        conn.execute("DROP TABLE visits_old;")
        conn.commit()


def _visit_unique_columns(conn: sqlite3.Connection) -> List[str]:
    try:
        cur = conn.execute("PRAGMA index_list('visits')")
        indexes = cur.fetchall()
        for idx in indexes:
            idx_name = idx[1]
            if "autoindex" not in idx_name.lower():
                continue
            cols = [row[2] for row in conn.execute(f"PRAGMA index_info('{idx_name}')").fetchall()]
            if cols:
                return cols
    except sqlite3.OperationalError:
        return []
    return []


def _ensure_global_unique(conn: sqlite3.Connection) -> None:
    """Ensure helper structures for global dedupe exist."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_global_dedupe_url_ts ON global_dedupe(url, visited_ts);")
    conn.commit()


def _maybe_backfill_global_guard(conn: sqlite3.Connection) -> None:
    """Populate global_dedupe for existing visits if empty to enforce global dedupe after upgrade."""
    cur = conn.execute("SELECT COUNT(*) as c FROM global_dedupe")
    count = cur.fetchone()["c"]
    if count > 0:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO global_dedupe (url, visited_ts, visit_id)
        SELECT url, visited_ts, id FROM visits
        """
    )
    conn.commit()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT,
            domain TEXT,
            path TEXT,
            visited_at TEXT NOT NULL,
            visited_ts REAL NOT NULL,
            category TEXT,
            labels TEXT,
            summary TEXT,
            source TEXT NOT NULL DEFAULT 'safari',
            UNIQUE(url, visited_ts, source)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS global_dedupe (
            url TEXT NOT NULL,
            visited_ts REAL NOT NULL,
            visit_id INTEGER,
            UNIQUE(url, visited_ts),
            FOREIGN KEY (visit_id) REFERENCES visits(id) ON DELETE CASCADE
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS visit_occurrences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            raw_visit_time REAL,
            raw_visit_iso TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(visit_id, source, raw_visit_time),
            FOREIGN KEY (visit_id) REFERENCES visits(id) ON DELETE CASCADE
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    _ensure_global_unique(conn)
    _maybe_migrate_visit_unique(conn)
    _maybe_backfill_global_guard(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_domain ON visits(domain);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_category ON visits(category);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(visited_ts);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_source_ts ON visits(source, visited_ts);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_occurrences_visit ON visit_occurrences(visit_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_occurrences_source ON visit_occurrences(source);")
    conn.commit()


def _ensure_fts(conn: sqlite3.Connection) -> None:
    """Create FTS5 table/triggers and rebuild if out of sync."""
    try:
        expected = {"url", "title", "summary", "domain", "path", "labels"}
        existing = _fts_columns(conn)
        if existing and set(existing) != expected:
            _drop_fts(conn)

        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS visits_fts USING fts5(
                url, title, summary, domain, path, labels,
                content='visits', content_rowid='id'
            );
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS visits_ai AFTER INSERT ON visits BEGIN
                INSERT INTO visits_fts(rowid, url, title, summary, domain, path, labels)
                VALUES (new.id, new.url, new.title, new.summary, new.domain, new.path, new.labels);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS visits_ad AFTER DELETE ON visits BEGIN
                INSERT INTO visits_fts(visits_fts, rowid, url, title, summary, domain, path, labels)
                VALUES ('delete', old.id, old.url, old.title, old.summary, old.domain, old.path, old.labels);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS visits_au AFTER UPDATE ON visits BEGIN
                INSERT INTO visits_fts(visits_fts, rowid, url, title, summary, domain, path, labels)
                VALUES ('delete', old.id, old.url, old.title, old.summary, old.domain, old.path, old.labels);
                INSERT INTO visits_fts(rowid, url, title, summary, domain, path, labels)
                VALUES (new.id, new.url, new.title, new.summary, new.domain, new.path, new.labels);
            END;
            """
        )
        _maybe_rebuild_fts(conn)
    except sqlite3.OperationalError as exc:
        # FTS5 not available or other issue; skip silently to preserve base functionality.
        if "fts5" in str(exc).lower():
            return
        raise


def _drop_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TRIGGER IF EXISTS visits_ai;")
    conn.execute("DROP TRIGGER IF EXISTS visits_ad;")
    conn.execute("DROP TRIGGER IF EXISTS visits_au;")
    conn.execute("DROP TABLE IF EXISTS visits_fts;")


def _fts_columns(conn: sqlite3.Connection) -> List[str]:
    try:
        cur = conn.execute("PRAGMA table_info(visits_fts)")
        return [row[1] for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


def _maybe_rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild FTS index if counts are out of sync."""
    try:
        cur = conn.execute("SELECT count(*) FROM visits")
        total = cur.fetchone()[0]
        cur = conn.execute("SELECT count(*) FROM visits_fts")
        fts_total = cur.fetchone()[0]
        if total != fts_total:
            conn.execute("INSERT INTO visits_fts(visits_fts) VALUES ('rebuild');")
            conn.commit()
    except sqlite3.OperationalError:
        # visits_fts missing or other FTS issue; ignore.
        return


def _has_fts(conn: sqlite3.Connection) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='visits_fts'"
    )
    return cur.fetchone() is not None


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    _ensure_schema(conn)
    _ensure_fts(conn)
    return conn


def insert_visit(
    conn: sqlite3.Connection, record: Dict[str, object], dedupe_scope: str = "global"
) -> tuple[bool, int]:
    """
    Insert a visit. Returns (inserted, visit_id).
    dedupe_scope: "global" dedupes across all sources; "source" dedupes per source/profile.
    """
    labels = record.get("labels") or []
    labels_text = "," + ",".join(labels) + "," if labels else ""
    url = record.get("url")
    visited_ts = record.get("visited_ts")
    source = record.get("source", "safari")

    existing_id = None
    if dedupe_scope == "global":
        guard = conn.execute(
            "SELECT visit_id FROM global_dedupe WHERE url = ? AND visited_ts = ?", (url, visited_ts)
        ).fetchone()
        if guard and guard["visit_id"]:
            return False, guard["visit_id"]
    elif dedupe_scope == "source":
        row = conn.execute(
            "SELECT id FROM visits WHERE url = ? AND visited_ts = ? AND source = ?", (url, visited_ts, source)
        ).fetchone()
        if row:
            existing_id = row["id"]
    else:
        raise ValueError(f"Unknown dedupe_scope {dedupe_scope}")

    if existing_id is not None:
        return False, existing_id

    cur = conn.execute(
        """
        INSERT OR IGNORE INTO visits (
            url, title, domain, path, visited_at, visited_ts, category, labels, summary, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            url,
            record.get("title"),
            record.get("domain"),
            record.get("path"),
            record.get("visited_at"),
            visited_ts,
            record.get("category"),
            labels_text,
            record.get("summary"),
            source,
        ),
    )
    visit_id = cur.lastrowid
    inserted = bool(cur.rowcount)
    if not visit_id:
        # In case another process inserted between our check and insert.
        row = conn.execute("SELECT id FROM visits WHERE url = ? AND visited_ts = ?", (url, visited_ts)).fetchone()
        if row:
            visit_id = row["id"]
            inserted = False
        else:
            raise RuntimeError("Failed to insert or find visit record")

    if dedupe_scope == "global":
        try:
            conn.execute(
                "INSERT INTO global_dedupe (url, visited_ts, visit_id) VALUES (?, ?, ?)",
                (url, visited_ts, visit_id),
            )
        except sqlite3.IntegrityError:
            # Another process won; reuse canonical visit_id and clean up the extra row.
            row = conn.execute(
                "SELECT visit_id FROM global_dedupe WHERE url = ? AND visited_ts = ?", (url, visited_ts)
            ).fetchone()
            canonical = row["visit_id"] if row else visit_id
            if inserted and canonical != visit_id:
                conn.execute("DELETE FROM visits WHERE id = ?", (visit_id,))
                inserted = False
                visit_id = canonical
    return inserted, visit_id


def insert_occurrence(
    conn: sqlite3.Connection, visit_id: int, source: str, raw_visit_time: Optional[float], raw_visit_iso: Optional[str]
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO visit_occurrences (visit_id, source, raw_visit_time, raw_visit_iso)
        VALUES (?, ?, ?, ?)
        """,
        (visit_id, source, raw_visit_time, raw_visit_iso),
    )


def query_visits(
    conn: sqlite3.Connection,
    category: Optional[str] = None,
    domain: Optional[str] = None,
    source: Optional[str] = None,
    search: Optional[str] = None,
    since_ts: Optional[float] = None,
    limit: int = 50,
    newest_first: bool = True,
) -> List[sqlite3.Row]:
    use_fts = bool(search) and _has_fts(conn)
    base_select = (
        "SELECT v.id, v.url, v.title, v.domain, v.path, v.visited_at, v.category, v.labels, v.summary, v.source"
    )
    if use_fts:
        sql = f"{base_select} FROM visits v JOIN visits_fts ON visits_fts.rowid = v.id WHERE 1=1"
    else:
        sql = f"{base_select} FROM visits v WHERE 1=1"
    params: List[object] = []
    if category:
        sql += " AND (category = ? OR labels LIKE ?)"
        params.extend([category, f"%,{category},%"])
    if domain:
        sql += " AND domain = ?"
        params.append(domain)
    if source:
        sql += " AND source = ?"
        params.append(source)
    if search:
        if use_fts:
            sql += " AND visits_fts MATCH ?"
            params.append(search)
        else:
            sql += " AND (url LIKE ? OR title LIKE ? OR summary LIKE ?)"
            like = f"%{search}%"
            params.extend([like, like, like])
    if since_ts is not None:
        sql += " AND visited_ts >= ?"
        params.append(since_ts)
    order = "DESC" if newest_first else "ASC"
    sql += f" ORDER BY visited_ts {order}"
    sql += " LIMIT ?"
    params.append(limit)
    try:
        cur = conn.execute(sql, params)
        return list(cur.fetchall())
    except sqlite3.OperationalError as exc:
        if use_fts:
            # Fallback to LIKE search if FTS query is malformed.
            use_fts = False
            sql = f"{base_select} FROM visits WHERE 1=1"
            params = []
            if category:
                sql += " AND (category = ? OR labels LIKE ?)"
                params.extend([category, f"%,{category},%"])
            if domain:
                sql += " AND domain = ?"
                params.append(domain)
            if source:
                sql += " AND source = ?"
                params.append(source)
            if search:
                sql += " AND (url LIKE ? OR title LIKE ? OR summary LIKE ?)"
                like = f"%{search}%"
                params.extend([like, like, like])
            if since_ts is not None:
                sql += " AND visited_ts >= ?"
                params.append(since_ts)
            order = "DESC" if newest_first else "ASC"
            sql += f" ORDER BY visited_ts {order}"
            sql += " LIMIT ?"
            params.append(limit)
            cur = conn.execute(sql, params)
            return list(cur.fetchall())
        raise exc


def last_visit_ts(conn: sqlite3.Connection) -> Optional[float]:
    return last_visit_ts_by_source(conn, None)


def last_visit_ts_by_source(conn: sqlite3.Connection, source: Optional[str]) -> Optional[float]:
    sql = "SELECT MAX(visited_ts) FROM visits"
    params: list[object] = []
    if source:
        sql += " WHERE source = ?"
        params.append(source)
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else None


def read_ingest_state(conn: sqlite3.Connection) -> Dict[str, object]:
    cur = conn.execute("SELECT key, value FROM ingest_state")
    out: Dict[str, object] = {}
    for row in cur.fetchall():
        key = row["key"]
        try:
            val = json.loads(row["value"])
        except Exception:
            val = row["value"]
        out[key] = val
    sources: Dict[str, object] = {}
    for key, val in out.items():
        if key.startswith("ingest_state."):
            sources[key.split(".", 1)[1]] = val
    if sources:
        out["sources"] = sources
    return out


def fetch_occurrences(conn: sqlite3.Connection, visit_ids: List[int]) -> Dict[int, List[Dict[str, object]]]:
    if not visit_ids:
        return {}
    placeholders = ",".join(["?"] * len(visit_ids))
    cur = conn.execute(
        f"""
        SELECT visit_id, source, raw_visit_time, raw_visit_iso
        FROM visit_occurrences
        WHERE visit_id IN ({placeholders})
        """,
        visit_ids,
    )
    out: Dict[int, List[Dict[str, object]]] = {}
    for row in cur.fetchall():
        out.setdefault(row["visit_id"], []).append(
            {
                "source": row["source"],
                "raw_visit_time": row["raw_visit_time"],
                "raw_visit_iso": row["raw_visit_iso"],
            }
        )
    return out


def run_analyze(conn: sqlite3.Connection) -> None:
    conn.execute("ANALYZE;")
    conn.commit()


def run_vacuum(db_path: Path = DEFAULT_DB) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("VACUUM;")
    conn.commit()
    conn.close()


def backfill_occurrences(conn: sqlite3.Connection, source: Optional[str] = None, limit: Optional[int] = None) -> int:
    """
    Create occurrence rows for visits that are missing them.
    Uses visited_ts/visited_at as raw values when raw visit time is unknown.
    """
    sql = """
        SELECT v.id, v.source, v.visited_ts, v.visited_at
        FROM visits v
        LEFT JOIN visit_occurrences o ON v.id = o.visit_id
        WHERE o.visit_id IS NULL
    """
    params: list[object] = []
    if source:
        sql += " AND v.source = ?"
        params.append(source)
    sql += " ORDER BY v.id ASC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    created = 0
    for row in rows:
        insert_occurrence(
            conn,
            visit_id=row["id"],
            source=row["source"],
            raw_visit_time=row["visited_ts"],
            raw_visit_iso=row["visited_at"],
        )
        created += 1
    conn.commit()
    return created


def write_ingest_state(conn: sqlite3.Connection, source: str, state: Dict[str, object]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ingest_state (key, value) VALUES (?, ?)",
        (f"ingest_state.{source}", json.dumps(state)),
    )
    conn.commit()
