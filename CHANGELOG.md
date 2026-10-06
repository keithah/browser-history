# Changelog

## v0.1.0 — 2026-01-20
- Initial public release.
- Multi-browser ingest (Safari, Chrome, Firefox, Edge, Brave) with per-profile detection.
- Heuristic labeling (AI, GitHub projects, personal sites, docs, news, social, shopping, video).
- MCP server + CLI with ask/query/stats, optional metadata fetch, streaming ingest, dedupe controls, and backfill/maintenance helpers.
- MCP `ask_history` returns JSON-safe filters (ISO timestamps); Safari ingest now warns and skips when History.db is missing instead of failing.
- Global dedupe guard is backfilled on upgrade; foreign keys enabled to keep dedupe guard in sync.
