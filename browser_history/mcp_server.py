"""Minimal MCP server exposing browser history tools and resources."""

import json
from typing import Optional

from mcp.server import fastmcp

from . import ask, ingest, query, store, utils

server = fastmcp.FastMCP(
    name="browser-history",
    instructions="Query and summarize browser history (Safari/Chrome/Firefox/Edge/Brave) stored locally.",
    website_url="https://example.local/browser-history",
)


@server.tool()
def ingest_history(
    browser: str = "safari",
    limit: Optional[int] = None,
    fetch_metadata: bool = False,
    metadata_max: Optional[int] = 500,
    dedupe_scope: str = "global",
    state_interval: int = 1000,
) -> dict:
    """Ingest browser visits into the local store (browser=safari|chrome|firefox|edge|brave|all, dedupe_scope global|source)."""
    stats = ingest.ingest(
        browser=browser,
        limit=limit,
        fetch_metadata=fetch_metadata,
        metadata_max=metadata_max,
        dedupe_scope=dedupe_scope,
        state_interval=state_interval,
    )
    return {"ok": True, "stats": stats}


@server.tool()
def backfill_occurrences(source: Optional[str] = None, limit: Optional[int] = None) -> dict:
    """Backfill visit_occurrences for existing visits (useful after upgrading)."""
    conn = store.connect()
    created = store.backfill_occurrences(conn, source=source, limit=limit)
    conn.close()
    return {"created": created}


@server.tool()
def query_history(
    category: Optional[str] = None,
    search: Optional[str] = None,
    domain: Optional[str] = None,
    source: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """Query visits with filters (category ai/github_project/personal_site, search substring, since like 7d)."""
    since_dt = utils.parse_since(since) if since else None
    rows = query.run_query(
        category=category,
        search=search,
        domain=domain,
        source=source,
        since=since_dt,
        limit=limit,
    )
    return {"rows": rows}


@server.tool()
def ask_history(question: str, limit: int = 20) -> dict:
    """Interpret a natural language question and return matched visits."""
    return ask.ask(question, limit=limit)


@server.tool()
def stats_history(since: Optional[str] = None, source: Optional[str] = None) -> dict:
    """Return totals, oldest/newest visit, and category counts. `since` like 7d/30d/YYYY-MM-DD."""
    since_dt = utils.parse_since(since) if since else None
    return query.stats(since=since_dt, source=source)


@server.resource("resource://browser/history/recent/{limit}", title="Recent browser visits")
def recent_history(limit: int) -> str:
    rows = query.run_query(limit=limit)
    return json.dumps(rows, indent=2)


def run() -> None:
    """Entry point for `browser-history-mcp`."""
    server.run()


if __name__ == "__main__":
    run()
