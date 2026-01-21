import argparse
import sys
import textwrap
import time
from pathlib import Path

from . import ask, ingest, query, store, utils


def main(argv=None):
    parser = argparse.ArgumentParser(description="Query your browser history locally.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest browser history into local store.")
    p_ingest.add_argument(
        "--browser",
        default="safari",
        help="safari|chrome|firefox|edge|brave|all (comma-separated allowed).",
    )
    p_ingest.add_argument("--safari-db", default=ingest.SAFARI_DB_DEFAULT, help="Path to Safari History.db")
    p_ingest.add_argument("--chrome-db", default=ingest.CHROME_DB_DEFAULT, help="Path to Chrome History SQLite")
    p_ingest.add_argument("--edge-db", default=ingest.EDGE_DB_DEFAULT, help="Path to Edge History SQLite")
    p_ingest.add_argument("--brave-db", default=ingest.BRAVE_DB_DEFAULT, help="Path to Brave History SQLite")
    p_ingest.add_argument("--firefox-db", help="Path to Firefox places.sqlite (optional; autodetect default profile).")
    p_ingest.add_argument("--limit", type=int, help="Limit rows for faster runs.")
    p_ingest.add_argument(
        "--fetch-metadata",
        action="store_true",
        help="Fetch page metadata (title/description) to improve summaries.",
    )
    p_ingest.add_argument(
        "--metadata-max",
        type=int,
        default=500,
        help="Maximum pages to fetch metadata for (skips metadata after this many, default 500).",
    )
    p_ingest.add_argument(
        "--dedupe-scope",
        choices=["global", "source"],
        default="global",
        help="Deduplicate globally (default) or per source/profile.",
    )
    p_ingest.add_argument("--analyze", action="store_true", help="Run ANALYZE after ingest completes.")
    p_ingest.add_argument(
        "--vacuum",
        action="store_true",
        help="Run VACUUM after ingest (offline/full rebuild; slower, not recommended in daemon).",
    )
    p_ingest.add_argument(
        "--state-interval",
        type=int,
        default=1000,
        help="Write ingest_state progress every N rows (default 1000).",
    )
    p_ingest.add_argument(
        "--progress-every",
        type=int,
        default=0,
        help="Print progress every N rows (0 to disable).",
    )

    p_reclass = sub.add_parser("reclassify", help="Re-run classifiers across stored visits.")
    p_backfill = sub.add_parser("backfill-occurrences", help="Backfill visit_occurrences for legacy rows.")
    p_backfill.add_argument("--source", help="Only backfill for this source.")
    p_backfill.add_argument("--limit", type=int, help="Max rows to backfill.")

    p_maint = sub.add_parser("maintain", help="Run VACUUM/ANALYZE on the store.")
    p_maint.add_argument("--vacuum", action="store_true", help="Run VACUUM (offline, can take time).")
    p_maint.add_argument("--analyze", action="store_true", help="Run ANALYZE to refresh query planner stats.")

    p_query = sub.add_parser("query", help="Filter visits.")
    p_query.add_argument("--category", help="Category like ai, github_project, personal_site")
    p_query.add_argument("--domain", help="Exact domain to match")
    p_query.add_argument("--search", help="Substring to search in URL/title/summary")
    p_query.add_argument("--source", help="Filter by source (safari, chrome:Profile, firefox:Profile)")
    p_query.add_argument("--since", help="7d, 12h, or YYYY-MM-DD")
    p_query.add_argument("--limit", type=int, default=20)
    p_query.add_argument("--oldest", action="store_true", help="Sort oldest first")

    p_ask = sub.add_parser("ask", help='Natural-ish query: e.g. "list ai sites"')
    p_ask.add_argument("question")
    p_ask.add_argument("--limit", type=int, default=20)

    p_stats = sub.add_parser("stats", help="Show history coverage and category counts.")
    p_stats.add_argument("--since", help="Limit stats to this window (7d, 30d, YYYY-MM-DD).")
    p_stats.add_argument("--source", help="Filter stats by source (safari, chrome:Profile, firefox:Profile).")

    p_daemon = sub.add_parser("daemon", help="Continuously ingest on a timer.")
    p_daemon.add_argument(
        "--browser",
        default="safari",
        help="safari|chrome|firefox|edge|brave|all (comma-separated allowed).",
    )
    p_daemon.add_argument("--safari-db", default=ingest.SAFARI_DB_DEFAULT, help="Path to Safari History.db")
    p_daemon.add_argument("--chrome-db", default=ingest.CHROME_DB_DEFAULT, help="Path to Chrome History SQLite")
    p_daemon.add_argument("--edge-db", default=ingest.EDGE_DB_DEFAULT, help="Path to Edge History SQLite")
    p_daemon.add_argument("--brave-db", default=ingest.BRAVE_DB_DEFAULT, help="Path to Brave History SQLite")
    p_daemon.add_argument("--firefox-db", help="Path to Firefox places.sqlite (optional; autodetect default profile).")
    p_daemon.add_argument("--limit", type=int, help="Limit rows per cycle (omit for all new visits).")
    p_daemon.add_argument(
        "--fetch-metadata",
        action="store_true",
        help="Fetch page metadata (title/description) each cycle.",
    )
    p_daemon.add_argument(
        "--metadata-max",
        type=int,
        default=500,
        help="Maximum pages to fetch metadata per cycle (default 500).",
    )
    p_daemon.add_argument(
        "--dedupe-scope",
        choices=["global", "source"],
        default="global",
        help="Deduplicate globally (default) or per source/profile.",
    )
    p_daemon.add_argument("--analyze", action="store_true", help="Run ANALYZE after each cycle.")
    p_daemon.add_argument(
        "--state-interval",
        type=int,
        default=1000,
        help="Write ingest_state progress every N rows (default 1000).",
    )
    p_daemon.add_argument(
        "--progress-every",
        type=int,
        default=0,
        help="Print progress every N rows (0 to disable).",
    )
    p_daemon.add_argument(
        "--interval",
        type=int,
        default=1800,
        help="Seconds to sleep between cycles (default 1800).",
    )

    args = parser.parse_args(argv)

    if args.cmd == "ingest":
        stats = ingest.ingest(
            browser=args.browser,
            safari_db=Path(args.safari_db),
            chrome_db=Path(args.chrome_db),
            edge_db=Path(args.edge_db),
            brave_db=Path(args.brave_db),
            firefox_db=Path(args.firefox_db) if args.firefox_db else None,
            limit=args.limit,
            fetch_metadata=args.fetch_metadata,
            metadata_max=args.metadata_max,
            dedupe_scope=args.dedupe_scope,
            analyze=args.analyze,
            vacuum=args.vacuum,
            state_interval=args.state_interval,
            progress_every=args.progress_every,
        )
        totals = stats.get("totals", {})
        print(
            f"Processed {totals.get('seen', 0)} visits, inserted {totals.get('inserted', 0)}, "
            f"metadata fetched for {totals.get('metadata_used', 0)}."
        )
        for name, st in stats.get("browsers", {}).items():
            print(
                f"  {name}: seen={st.get('seen', 0)} inserted={st.get('inserted', 0)} "
                f"meta={st.get('metadata_used', 0)} last_at={st.get('last_visited_at')}"
            )
            if st.get("warning"):
                print(f"    warning: {st['warning']}")
            for sub_name, sub_stats in st.items():
                if not isinstance(sub_stats, dict):
                    continue
                print(
                    f"    {sub_name}: seen={sub_stats.get('seen', 0)} inserted={sub_stats.get('inserted', 0)} "
                    f"meta={sub_stats.get('metadata_used', 0)} last_at={sub_stats.get('last_visited_at')}"
                )
        return 0

    if args.cmd == "query":
        since_dt = utils.parse_since(args.since)
        rows = query.run_query(
            category=args.category,
            search=args.search,
            domain=args.domain,
            source=args.source,
            since=since_dt,
            limit=args.limit,
            newest_first=not args.oldest,
        )
        _print_rows(rows)
        return 0

    if args.cmd == "ask":
        result = ask.ask(args.question, limit=args.limit)
        print(f"Interpreted filters: {result['filters']}")
        _print_rows(result["results"])
        return 0

    if args.cmd == "stats":
        since_dt = utils.parse_since(args.since)
        s = query.stats(since=since_dt, source=args.source)
        print(f"Total visits: {s['total']}")
        if s["oldest"]:
            print(f"Oldest visit: {s['oldest']}")
        if s["newest"]:
            print(f"Newest visit: {s['newest']}")
        print("Categories:")
        for row in s["categories"]:
            print(f"  - {row['category']}: {row['count']}")
        if s.get("sources"):
            print("Sources:")
            for row in s["sources"]:
                print(f"  - {row['source']}: {row['count']}")
        if s.get("ingest_state"):
            st = dict(s["ingest_state"])
            sources = st.pop("sources", {})
            print("Ingest state:")
            for k in sorted(st.keys()):
                print(f"  - {k}: {st[k]}")
            if sources:
                print("Per-source ingest state:")
                for src, vals in sources.items():
                    print(f"  {src}:")
                    for k, v in vals.items():
                        print(f"    - {k}: {v}")
        return 0

    if args.cmd == "reclassify":
        updated = ingest.reclassify()
        print(f"Reclassified {updated} rows.")
        return 0

    if args.cmd == "backfill-occurrences":
        conn = store.connect()
        created = store.backfill_occurrences(conn, source=args.source, limit=args.limit)
        conn.close()
        print(f"Created {created} occurrence rows.")
        return 0

    if args.cmd == "maintain":
        conn = store.connect()
        did_any = False
        if args.analyze or not args.vacuum:
            store.run_analyze(conn)
            print("ANALYZE completed.")
            did_any = True
        if args.vacuum or (not args.analyze and not args.vacuum):
            conn.close()
            store.run_vacuum(store.DEFAULT_DB)
            print("VACUUM completed.")
            did_any = True
            conn = None
        if conn:
            conn.close()
        if not did_any:
            print("Nothing to do.")
        return 0

    if args.cmd == "daemon":
        try:
            while True:
                stats = ingest.ingest(
                    browser=args.browser,
                    safari_db=Path(args.safari_db),
                    chrome_db=Path(args.chrome_db),
                    edge_db=Path(args.edge_db),
                    brave_db=Path(args.brave_db),
                    firefox_db=Path(args.firefox_db) if args.firefox_db else None,
                    limit=args.limit,
                    fetch_metadata=args.fetch_metadata,
                    metadata_max=args.metadata_max,
                    dedupe_scope=args.dedupe_scope,
                    analyze=args.analyze,
                    state_interval=args.state_interval,
                    progress_every=args.progress_every,
                )
                totals = stats.get("totals", {})
                print(
                    f"[daemon] processed {totals.get('seen', 0)} inserted {totals.get('inserted', 0)} "
                    f"meta {totals.get('metadata_used', 0)}"
                )
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("Daemon stopped.")
        return 0

    return 1


def _print_rows(rows):
    if not rows:
        print("No results.")
        return
    for row in rows:
        title = row.get("title") or row.get("summary") or ""
        title = textwrap.shorten(title, width=80)
        print(f"- [{row.get('visited_at')}] {title}")
        print(f"  {row.get('url')} ({row.get('category')}; labels={row.get('labels')})")
        sources = row.get("sources") or []
        if sources:
            unique_sources = sorted({s.get("source") for s in sources if s.get("source")})
            if unique_sources:
                print(f"  sources: {', '.join(unique_sources)} (occurrences={len(sources)})")


if __name__ == "__main__":
    sys.exit(main())
