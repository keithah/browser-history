# Browser History Query

Local CLI + MCP server to ingest Safari/Chrome/Firefox/Edge/Brave history, auto-label visits, and answer quick questions (e.g., “list all the GitHub projects I visited”, “what AI sites have I hit recently”) without sending data anywhere.

## Features
- Copies browser history DBs (Safari, Chrome, Firefox, Edge, Brave), converts timestamps, and stores into `data/history_store.db`.
- Deduping: by default, ingestion dedupes globally on `url+timestamp` (enforced with a unique index) and also tracks per-source occurrences; you can switch to per-source dedupe with `--dedupe-scope source`.
- Automatic labeling heuristics: `github_project`, `ai`, `personal_site`, `docs`, `news`, `social`, `shopping`, `video`, `other`.
- Summaries derived from title/path (e.g., GitHub repos become `owner/repo`), optional page metadata fetch to improve titles/descriptions, plus full-text search over URL/title/summary/domain/path/labels.
- Natural-language-ish `ask` command routes questions to filters; `reclassify` lets you refresh labels after tweaking heuristics.
- MCP server exposes tools/resources so agents can call the same ingestion/query/ask logic.

## Setup
```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e .
# for tests/dev extras
pip install -e .[dev]
```

## CLI
- Ingest new visits (deduped): `browser-history ingest --browser safari|chrome|firefox|edge|brave|all` (auto-detects Chrome/Edge/Brave profiles and Firefox release/beta/dev/ESR profiles; add `--limit 200` to sample; add `--fetch-metadata` to pull page titles/descriptions over the network; use `--metadata-max` to cap how many pages are fetched per run, default 500; `--progress-every` to log progress; `--state-interval` to flush ingest_state more frequently; `--dedupe-scope global|source` to choose global dedupe (default) or per-browser/profile; `--analyze` to run SQLite ANALYZE after ingest; `--vacuum` for an offline VACUUM). Warnings are printed if a requested browser DB cannot be found.
- Query by filters: `browser-history query --category ai --since 30d --limit 20 [--source safari|chrome:Profile|firefox:Profile]`
- Full-text search: `--search foo` uses SQLite FTS5 over URL/title/summary/domain/path/labels (falls back to LIKE if FTS5 unavailable).
- Ask in plain-ish text: `browser-history ask "list all the github projects"`
- Re-run classifiers on stored rows (after heuristic changes): `browser-history reclassify`
- Backfill legacy rows into `visit_occurrences`: `browser-history backfill-occurrences [--source foo] [--limit 1000]`
- Run maintenance: `browser-history maintain` (defaults to ANALYZE + VACUUM; pass `--analyze` or `--vacuum` to limit)
- Stats: `browser-history stats [--since 30d] [--source ...]` shows coverage, counts by category, counts by source, and per-source ingest_state (including profile names).
- Daemon: `browser-history daemon --browser all --fetch-metadata --metadata-max 200 --interval 1800 --progress-every 1000` runs continuous ingest on a timer; press Ctrl+C to stop.
- Common `--since` formats: `7d`, `12h`, `2024-12-01`.

Data lives in `data/history_store.db`; snapshots of browser DBs are kept in `data/*_snapshot.db` for ingestion safety.

## MCP server
- Run: `browser-history-mcp` (stdio transport). Requires the `mcp` Python package (installed via `pip install -e .`).
- Tools: `ingest_history(browser?, limit?, fetch_metadata=false, metadata_max=500?, dedupe_scope=global|source)` (browsers include safari/chrome/firefox/edge/brave/all), `query_history(category?, search?, domain?, source?, since?, limit)`, `ask_history(question, limit)`, `stats_history(since?, source?)`, `backfill_occurrences(source?, limit?)`.
- Resource template: `resource://browser/history/recent/{limit}` returns recent visits as JSON.
- Any MCP-capable client can connect over stdio; you don’t need extra configuration beyond pointing the client to the `browser-history-mcp` command.

## Notes
- Classification is heuristic-only (no network/LLM calls). Add more patterns in `browser_history/classifier.py` if you want finer buckets.
- To start fresh, remove `data/history_store.db` and re-run `ingest`.
- Ingest state (progress, last seen timestamps, metadata count) is stored in `ingest_state` inside `data/history_store.db`; `browser-history stats` prints it per source so you can resume/monitor long runs. Each visit also stores occurrences (sources + raw timestamps) so you can see all browsers/profiles that hit the same page when using global dedupe.
