import datetime as dt
from typing import Dict, List, Optional

from . import store, utils


def run_query(
    category: Optional[str] = None,
    search: Optional[str] = None,
    domain: Optional[str] = None,
    source: Optional[str] = None,
    since: Optional[dt.datetime] = None,
    limit: int = 50,
    newest_first: bool = True,
) -> List[Dict[str, str]]:
    conn = store.connect()
    since_ts = since.timestamp() if since else None
    rows = store.query_visits(
        conn,
        category=category,
        domain=domain,
        source=source,
        search=search,
        since_ts=since_ts,
        limit=limit,
        newest_first=newest_first,
    )
    occurrences = store.fetch_occurrences(conn, [r["id"] for r in rows])
    out = [_row_to_dict(r, occurrences.get(r["id"], [])) for r in rows]
    conn.close()
    return out


def _row_to_dict(row, occurrences) -> Dict[str, str]:
    labels = row["labels"] or ""
    labels_clean = [p for p in labels.split(",") if p]
    return {
        "id": row["id"],
        "url": row["url"],
        "title": row["title"],
        "domain": row["domain"],
        "path": row["path"],
        "visited_at": row["visited_at"],
        "category": row["category"],
        "labels": labels_clean,
        "summary": row["summary"],
        "source": row["source"],
        "sources": occurrences,
    }


def stats(since: Optional[dt.datetime] = None, source: Optional[str] = None) -> Dict[str, object]:
    conn = store.connect()
    params = []
    where = ""
    if since:
        where = "WHERE visited_ts >= ?"
        params.append(since.timestamp())
    if source:
        where += (" AND" if where else "WHERE") + " source = ?"
        params.append(source)
    total = conn.execute(f"SELECT COUNT(*) FROM visits {where}", params).fetchone()[0]
    oldest = conn.execute(f"SELECT MIN(visited_ts) FROM visits {where}", params).fetchone()[0]
    newest = conn.execute(f"SELECT MAX(visited_ts) FROM visits {where}", params).fetchone()[0]
    categories = conn.execute(
        f"SELECT category, COUNT(*) as c FROM visits {where} GROUP BY category ORDER BY c DESC",
        params,
    ).fetchall()
    by_source = conn.execute(
        f"SELECT source, COUNT(*) as c FROM visits {where} GROUP BY source ORDER BY c DESC",
        params,
    ).fetchall()
    state = store.read_ingest_state(conn)
    conn.close()
    return {
        "total": total,
        "oldest": utils.iso_from_ts(oldest) if oldest else None,
        "newest": utils.iso_from_ts(newest) if newest else None,
        "categories": [{"category": row["category"], "count": row["c"]} for row in categories],
        "sources": [{"source": row["source"], "count": row["c"]} for row in by_source],
        "ingest_state": state,
    }
