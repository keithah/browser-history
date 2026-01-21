import glob
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import classifier, enrich, store, utils

SAFARI_DB_DEFAULT = Path.home() / "Library" / "Safari" / "History.db"
CHROME_DB_DEFAULT = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "History"
EDGE_DB_DEFAULT = Path.home() / "Library" / "Application Support" / "Microsoft Edge" / "Default" / "History"
BRAVE_DB_DEFAULT = (
    Path.home() / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser" / "Default" / "History"
)
FIREFOX_PROFILE_GLOB = str(
    Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles" / "*.default-release" / "places.sqlite"
)
FIREFOX_PROFILE_GLOBS = [
    str(Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles" / "*.default-release" / "places.sqlite"),
    str(Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles" / "*.default" / "places.sqlite"),
    str(Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles" / "*.beta" / "places.sqlite"),
    str(Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles" / "*.dev-edition-default" / "places.sqlite"),
    str(Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles" / "*.dev-edition" / "places.sqlite"),
    str(Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles" / "*.esr" / "places.sqlite"),
]


def ingest(
    browser: str = "safari",
    safari_db: Path = SAFARI_DB_DEFAULT,
    chrome_db: Path = CHROME_DB_DEFAULT,
    edge_db: Path = EDGE_DB_DEFAULT,
    brave_db: Path = BRAVE_DB_DEFAULT,
    firefox_db: Optional[Path] = None,
    dest_db: Path = store.DEFAULT_DB,
    limit: Optional[int] = None,
    fetch_metadata: bool = False,
    metadata_max: Optional[int] = 500,
    dedupe_scope: str = "global",
    analyze: bool = False,
    vacuum: bool = False,
    state_interval: int = 1000,
    progress_every: int = 0,
) -> Dict[str, object]:
    """
    Copy browser history DB(s), ingest new visits into our store.
    browser: safari|chrome|firefox|all (comma-separated allowed).
    Returns {"browsers": {...}, "totals": {...}}.
    """
    browsers = _normalize_browsers(browser)
    all_stats: Dict[str, Dict[str, object]] = {}
    totals = {"seen": 0, "inserted": 0, "metadata_used": 0}

    for b in browsers:
        if b == "safari":
            stats = _ingest_safari(
                safari_db, dest_db, limit, fetch_metadata, metadata_max, dedupe_scope, state_interval, progress_every
            )
        elif b == "chrome":
            stats = _ingest_chrome(
                chrome_db, dest_db, limit, fetch_metadata, metadata_max, dedupe_scope, state_interval, progress_every
            )
        elif b == "firefox":
            stats = _ingest_firefox(
                firefox_db, dest_db, limit, fetch_metadata, metadata_max, dedupe_scope, state_interval, progress_every
            )
        elif b == "edge":
            stats = _ingest_edge(
                edge_db, dest_db, limit, fetch_metadata, metadata_max, dedupe_scope, state_interval, progress_every
            )
        elif b == "brave":
            stats = _ingest_brave(
                brave_db, dest_db, limit, fetch_metadata, metadata_max, dedupe_scope, state_interval, progress_every
            )
        else:
            continue
        all_stats[b] = stats
        totals["seen"] += stats.get("seen", 0)
        totals["inserted"] += stats.get("inserted", 0)
        totals["metadata_used"] += stats.get("metadata_used", 0)

    if analyze:
        conn = store.connect(dest_db)
        store.run_analyze(conn)
        conn.close()
    if vacuum:
        store.run_vacuum(dest_db)

    return {"browsers": all_stats, "totals": totals}


def reclassify(dest_db: Path = store.DEFAULT_DB) -> int:
    """Re-run classification/summaries for all stored visits. Returns rows updated."""
    conn = store.connect(dest_db)
    rows = conn.execute("SELECT id, url, title FROM visits").fetchall()
    updated = 0
    for row in rows:
        category, labels, summary = classifier.classify(row["url"], row["title"] or "")
        labels_text = "," + ",".join(labels) + "," if labels else ""
        conn.execute(
            "UPDATE visits SET category = ?, labels = ?, summary = ? WHERE id = ?",
            (category, labels_text, summary, row["id"]),
        )
        updated += 1
    conn.commit()
    return updated


def daemon(
    interval_seconds: int = 1800,
    **kwargs,
) -> None:
    """Continuously ingest on a fixed interval until interrupted."""
    try:
        while True:
            stats = ingest(**kwargs)
            totals = stats.get("totals", {})
            print(
                f"[daemon] totals seen={totals.get('seen', 0)} inserted={totals.get('inserted', 0)} "
                f"meta={totals.get('metadata_used', 0)}",
                flush=True,
            )
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("Ingest daemon stopped.")


def _ingest_safari(
    safari_db: Path,
    dest_db: Path,
    limit: Optional[int],
    fetch_metadata: bool,
    metadata_max: Optional[int],
    dedupe_scope: str,
    state_interval: int,
    progress_every: int,
) -> Dict[str, object]:
    return _ingest_generic(
        source="safari",
        db_path=Path(safari_db),
        snapshot_name="safari_snapshot.db",
        dest_db=dest_db,
        sql_base="""
            SELECT hv.visit_time AS ts_raw, hi.url AS url, hv.title AS title
            FROM history_visits hv
            JOIN history_items hi ON hv.history_item = hi.id
        """,
        ts_to_dt=lambda raw: utils.safari_ts_to_datetime(raw),
        cutoff_transform=lambda ts: ts - utils.SAFARI_EPOCH.timestamp(),
        limit=limit,
        fetch_metadata=fetch_metadata,
        metadata_max=metadata_max,
        dedupe_scope=dedupe_scope,
        state_interval=state_interval,
        progress_every=progress_every,
    )


def _ingest_chrome(
    chrome_db: Path,
    dest_db: Path,
    limit: Optional[int],
    fetch_metadata: bool,
    metadata_max: Optional[int],
    dedupe_scope: str,
    state_interval: int,
    progress_every: int,
) -> Dict[str, object]:
    stats: Dict[str, object] = {"seen": 0, "inserted": 0, "metadata_used": 0}
    candidates = _discover_chrome_dbs(chrome_db)
    if not candidates:
        stats["warning"] = f"No Chrome history DBs found under {chrome_db}."
        return stats
    for label, db_path in candidates:
        res = _ingest_generic(
            source=f"chrome:{label}",
            db_path=db_path,
            snapshot_name=f"chrome_snapshot_{label}.db",
            dest_db=dest_db,
            sql_base="""
                SELECT v.visit_time AS ts_raw, u.url AS url, u.title AS title
                FROM visits v
                JOIN urls u ON v.url = u.id
            """,
            ts_to_dt=lambda raw: utils.chrome_ts_to_datetime(raw),
            cutoff_transform=lambda ts: int((ts - utils.CHROME_EPOCH.timestamp()) * 1_000_000),
            limit=limit,
            fetch_metadata=fetch_metadata,
            metadata_max=metadata_max,
            dedupe_scope=dedupe_scope,
            state_interval=state_interval,
            progress_every=progress_every,
        )
        for k in ("seen", "inserted", "metadata_used"):
            stats[k] = stats.get(k, 0) + res.get(k, 0)
        stats[label] = res
    return stats


def _ingest_edge(
    edge_db: Path,
    dest_db: Path,
    limit: Optional[int],
    fetch_metadata: bool,
    metadata_max: Optional[int],
    dedupe_scope: str,
    state_interval: int,
    progress_every: int,
) -> Dict[str, object]:
    stats: Dict[str, object] = {"seen": 0, "inserted": 0, "metadata_used": 0}
    candidates = _discover_chromium_dbs(edge_db)
    if not candidates:
        stats["warning"] = f"No Edge history DBs found under {edge_db}."
        return stats
    for label, db_path in candidates:
        res = _ingest_generic(
            source=f"edge:{label}",
            db_path=db_path,
            snapshot_name=f"edge_snapshot_{label}.db",
            dest_db=dest_db,
            sql_base="""
                SELECT v.visit_time AS ts_raw, u.url AS url, u.title AS title
                FROM visits v
                JOIN urls u ON v.url = u.id
            """,
            ts_to_dt=lambda raw: utils.chrome_ts_to_datetime(raw),
            cutoff_transform=lambda ts: int((ts - utils.CHROME_EPOCH.timestamp()) * 1_000_000),
            limit=limit,
            fetch_metadata=fetch_metadata,
            metadata_max=metadata_max,
            dedupe_scope=dedupe_scope,
            state_interval=state_interval,
            progress_every=progress_every,
        )
        for k in ("seen", "inserted", "metadata_used"):
            stats[k] = stats.get(k, 0) + res.get(k, 0)
        stats[label] = res
    return stats


def _ingest_brave(
    brave_db: Path,
    dest_db: Path,
    limit: Optional[int],
    fetch_metadata: bool,
    metadata_max: Optional[int],
    dedupe_scope: str,
    state_interval: int,
    progress_every: int,
) -> Dict[str, object]:
    stats: Dict[str, object] = {"seen": 0, "inserted": 0, "metadata_used": 0}
    candidates = _discover_chromium_dbs(brave_db)
    if not candidates:
        stats["warning"] = f"No Brave history DBs found under {brave_db}."
        return stats
    for label, db_path in candidates:
        res = _ingest_generic(
            source=f"brave:{label}",
            db_path=db_path,
            snapshot_name=f"brave_snapshot_{label}.db",
            dest_db=dest_db,
            sql_base="""
                SELECT v.visit_time AS ts_raw, u.url AS url, u.title AS title
                FROM visits v
                JOIN urls u ON v.url = u.id
            """,
            ts_to_dt=lambda raw: utils.chrome_ts_to_datetime(raw),
            cutoff_transform=lambda ts: int((ts - utils.CHROME_EPOCH.timestamp()) * 1_000_000),
            limit=limit,
            fetch_metadata=fetch_metadata,
            metadata_max=metadata_max,
            dedupe_scope=dedupe_scope,
            state_interval=state_interval,
            progress_every=progress_every,
        )
        for k in ("seen", "inserted", "metadata_used"):
            stats[k] = stats.get(k, 0) + res.get(k, 0)
        stats[label] = res
    return stats


def _ingest_firefox(
    firefox_db: Optional[Path],
    dest_db: Path,
    limit: Optional[int],
    fetch_metadata: bool,
    metadata_max: Optional[int],
    dedupe_scope: str,
    state_interval: int,
    progress_every: int,
) -> Dict[str, object]:
    stats: Dict[str, object] = {"seen": 0, "inserted": 0, "metadata_used": 0}
    candidates = _resolve_firefox_dbs(firefox_db)
    if not candidates:
        stats["warning"] = "No Firefox places.sqlite found (pass --firefox-db to override)."
        return stats
    for label, places_path in candidates:
        res = _ingest_generic(
            source=f"firefox:{label}",
            db_path=places_path,
            snapshot_name=f"firefox_snapshot_{label}.db",
            dest_db=dest_db,
            sql_base="""
                SELECT hv.visit_date AS ts_raw, p.url AS url, p.title AS title
                FROM moz_historyvisits hv
                JOIN moz_places p ON hv.place_id = p.id
            """,
            ts_to_dt=lambda raw: utils.firefox_ts_to_datetime(raw),
            cutoff_transform=lambda ts: int(ts * 1_000_000),
            limit=limit,
            fetch_metadata=fetch_metadata,
            metadata_max=metadata_max,
            dedupe_scope=dedupe_scope,
            state_interval=state_interval,
            progress_every=progress_every,
        )
        for k in ("seen", "inserted", "metadata_used"):
            stats[k] = stats.get(k, 0) + res.get(k, 0)
        stats[label] = res
    return stats


def _ingest_generic(
    source: str,
    db_path: Path,
    snapshot_name: str,
    dest_db: Path,
    sql_base: str,
    ts_to_dt,
    cutoff_transform,
    limit: Optional[int],
    fetch_metadata: bool,
    metadata_max: Optional[int],
    dedupe_scope: str,
    state_interval: int,
    progress_every: int,
) -> Dict[str, object]:
    started = datetime.now(tz=timezone.utc)
    db_path = Path(db_path).expanduser()
    snapshot = dest_db.parent / snapshot_name
    _copy_sqlite_source(db_path, snapshot)

    conn_src = None
    conn_store = None
    seen = 0
    inserted = 0
    metadata_used = 0
    latest_ts = None
    latest_raw = None
    latest_dt = None

    try:
        conn_src = sqlite3.connect(snapshot)
        conn_src.row_factory = sqlite3.Row
        conn_store = store.connect(dest_db)
        conn_store.execute("BEGIN")
        last_ts = store.last_visit_ts_by_source(conn_store, source)
        cutoff_raw = None
        if last_ts is not None:
            cutoff_raw = cutoff_transform(last_ts)

        sql = sql_base
        params: List[object] = []
        if cutoff_raw is not None:
            sql += " WHERE ts_raw > ?"
            params.append(cutoff_raw)
        sql += " ORDER BY ts_raw ASC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)

        cur = conn_src.execute(sql, params)
        commit_every = 500
        for row in cur:
            seen += 1
            url = row["url"]
            title = row["title"]
            if not url:
                continue

            fetched = None
            if fetch_metadata and (metadata_max is None or metadata_used < metadata_max):
                metadata_used += 1  # count attempt even if no title/HTML
                fetched = enrich.fetch_metadata(url)
                if fetched.get("title"):
                    title = fetched["title"]

            visited_dt = ts_to_dt(row["ts_raw"])
            visited_ts = visited_dt.timestamp()
            latest_ts = visited_ts
            latest_raw = row["ts_raw"]
            latest_dt = visited_dt

            inserted += _insert_visit(
                conn_store,
                url=url,
                title=title,
                visited_dt=visited_dt,
                visited_ts=visited_ts,
                raw_ts=row["ts_raw"],
                fetched=fetched,
                source=source,
                dedupe_scope=dedupe_scope,
            )

            if seen % commit_every == 0:
                conn_store.commit()

            _maybe_emit_progress(
                conn_store,
                source=source,
                seen=seen,
                inserted=inserted,
                metadata_used=metadata_used,
                latest_dt=latest_dt,
            latest_ts=latest_ts,
            latest_raw=latest_raw,
            fetch_metadata=fetch_metadata,
                metadata_max=metadata_max,
                started=started,
                dedupe_scope=dedupe_scope,
                state_interval=state_interval,
                progress_every=progress_every,
                commit_before_state=True,
            )

        finished = datetime.now(tz=timezone.utc)
        if latest_ts is not None and latest_dt is not None and latest_raw is not None:
            conn_store.commit()
            _write_progress_state(
                conn_store,
                latest_ts,
                latest_raw,
                latest_dt,
                fetch_metadata,
                metadata_max,
                dedupe_scope,
                seen,
                inserted,
                metadata_used,
                started,
                finished=finished,
                source=source,
            )
        conn_store.commit()
        return {
            "seen": seen,
            "inserted": inserted,
            "metadata_used": metadata_used,
            "last_visited_at": utils.iso(latest_dt) if latest_dt else None,
            "duration_seconds": (finished - started).total_seconds(),
        }
    except Exception:
        if conn_store:
            conn_store.rollback()
        raise
    finally:
        if conn_src:
            conn_src.close()
        if conn_store:
            try:
                conn_store.close()
            except Exception:
                pass
        _cleanup_snapshot(snapshot)


def _insert_visit(
    conn: sqlite3.Connection,
    url: str,
    title: Optional[str],
    visited_dt: datetime,
    visited_ts: float,
    raw_ts: Optional[float],
    fetched: Optional[dict],
    source: str,
    dedupe_scope: str,
) -> int:
    domain, path = utils.split_url(url)
    category, labels, summary = classifier.classify(url, title or "")
    if fetched and fetched.get("description") and summary == f"{domain}{path}":
        summary = fetched["description"]
    inserted, visit_id = store.insert_visit(
        conn,
        {
            "url": url,
            "title": title,
            "domain": domain,
            "path": path,
            "visited_at": utils.iso(visited_dt),
            "visited_ts": visited_ts,
            "category": category,
            "labels": labels,
            "summary": summary,
            "source": source,
        },
        dedupe_scope=dedupe_scope,
    )
    store.insert_occurrence(conn, visit_id, source, raw_ts, utils.iso(visited_dt))
    return 1 if inserted else 0


def _maybe_emit_progress(
    conn: sqlite3.Connection,
    source: str,
    seen: int,
    inserted: int,
    metadata_used: int,
    latest_dt: Optional[datetime],
    latest_ts: Optional[float],
    latest_raw: Optional[float],
    fetch_metadata: bool,
    metadata_max: Optional[int],
    dedupe_scope: str,
    started: datetime,
    state_interval: int,
    progress_every: int,
    commit_before_state: bool = False,
) -> None:
    if progress_every and seen % progress_every == 0:
        print(
            f"[{source}] seen={seen} inserted={inserted} meta={metadata_used} "
            f"last_at={utils.iso(latest_dt) if latest_dt else None}",
            flush=True,
        )
    if (
        state_interval
        and seen % state_interval == 0
        and latest_ts is not None
        and latest_dt is not None
        and latest_raw is not None
    ):
        if commit_before_state:
            conn.commit()
        _write_progress_state(
            conn,
            latest_ts,
            latest_raw,
            latest_dt,
            fetch_metadata,
            metadata_max,
            dedupe_scope,
            seen,
            inserted,
            metadata_used,
            started,
            source=source,
        )


def _write_progress_state(
    conn: sqlite3.Connection,
    latest_ts: float,
    latest_raw: float,
    latest_dt: datetime,
    fetch_metadata: bool,
    metadata_max: Optional[int],
    dedupe_scope: str,
    seen: int,
    inserted: int,
    metadata_used: int,
    started: datetime,
    source: str,
    finished: Optional[datetime] = None,
) -> None:
    now = finished or datetime.now(tz=timezone.utc)
    store.write_ingest_state(
        conn,
        source,
        {
            "source": source,
            "last_visited_ts": latest_ts,
            "last_visited_at": utils.iso_from_ts(latest_ts),
            "last_raw_visit_time": latest_raw,
            "last_raw_visit_iso": utils.iso(latest_dt) if latest_dt else None,
            "last_ingest_at": utils.iso(now),
            "last_seen": seen,
            "last_inserted": inserted,
            "last_metadata_used": metadata_used,
            "fetch_metadata": fetch_metadata,
            "metadata_max": metadata_max,
            "dedupe_scope": dedupe_scope,
            "duration_seconds": (now - started).total_seconds(),
        },
    )


def _normalize_browsers(spec: str) -> List[str]:
    vals = []
    for part in spec.split(","):
        p = part.strip().lower()
        if not p:
            continue
        if p == "all":
            return ["safari", "chrome", "firefox", "edge", "brave"]
        vals.append(p)
    return vals or ["safari"]


def _resolve_firefox_db(firefox_db: Optional[Path]) -> Path:
    if firefox_db:
        return Path(firefox_db).expanduser()
    matches = glob.glob(FIREFOX_PROFILE_GLOB)
    if matches:
        return Path(matches[0])
    raise FileNotFoundError("Firefox places.sqlite not found; pass --firefox-db")


def _resolve_firefox_dbs(firefox_db: Optional[Path]) -> List[tuple]:
    if firefox_db:
        p = Path(firefox_db).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"Firefox DB not found at {p}")
        return [(p.parent.name, p)]
    matches: List[str] = []
    for pattern in FIREFOX_PROFILE_GLOBS:
        matches.extend(glob.glob(pattern))
    unique = sorted({m for m in matches})
    return [(Path(m).parent.name, Path(m)) for m in unique]


def _discover_chrome_dbs(chrome_db: Path) -> List[tuple]:
    return _discover_chromium_dbs(chrome_db)


def _discover_chromium_dbs(db_root: Path) -> List[tuple]:
    db_path = Path(db_root).expanduser()
    if not db_path.exists():
        return []
    if db_path.is_file():
        return [(db_path.parent.name, db_path)]
    root = db_path
    if root.name.lower() == "history":
        root = root.parent.parent
    candidates = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if child.name.lower() in {"system profile"}:
            continue
        hist = child / "History"
        if hist.is_file():
            candidates.append((child.name, hist))
    return candidates


def _copy_sqlite_source(src: Path, dest: Path) -> None:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    # Best-effort backup to avoid WAL inconsistencies.
    try:
        conn_src = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=5)
        conn_dest = sqlite3.connect(dest)
        conn_src.backup(conn_dest)
        conn_dest.close()
        conn_src.close()
        return
    except Exception:
        # Fallback to file copy.
        pass
    try:
        shutil.copyfile(src, dest)
        for suffix in ("-wal", "-shm"):
            wal_src = Path(str(src) + suffix)
            if wal_src.exists():
                shutil.copyfile(wal_src, Path(str(dest) + suffix))
        return
    except Exception as exc:
        raise RuntimeError(f"Failed to copy {src} for ingest: {exc}") from exc


def _cleanup_snapshot(snapshot: Path) -> None:
    for path in (snapshot, Path(str(snapshot) + "-shm"), Path(str(snapshot) + "-wal")):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
