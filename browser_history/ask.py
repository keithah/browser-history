import datetime as dt
import re
from typing import Dict, Optional

from . import query, utils


def ask(question: str, limit: int = 20) -> Dict[str, object]:
    """Parse a natural language-ish query and run it."""
    filters = _interpret_question(question)
    results = query.run_query(
        category=filters.get("category"),
        search=filters.get("search"),
        since=filters.get("since"),
        limit=limit,
    )
    return {"filters": filters, "results": results}


def _interpret_question(question: str) -> Dict[str, object]:
    q = question.lower()
    filters: Dict[str, object] = {}

    if any(word in q for word in ["github", "repo", "repository"]):
        filters["category"] = "github_project"
    elif "ai" in q or "llm" in q or "chatgpt" in q or "claude" in q:
        filters["category"] = "ai"
    elif "personal" in q or "portfolio" in q or "blog" in q or "homepage" in q:
        filters["category"] = "personal_site"
    elif "docs" in q or "documentation" in q or "api" in q:
        filters["category"] = "docs"
    elif "news" in q:
        filters["category"] = "news"
    elif "social" in q:
        filters["category"] = "social"

    filters["since"] = _extract_time(q)
    if "containing" in q or "with " in q:
        tokens = re.findall(r"\"([^\"]+)\"", question)
        if tokens:
            filters["search"] = tokens[0]
    return filters


def _extract_time(q: str) -> Optional[dt.datetime]:
    now = dt.datetime.now(tz=dt.timezone.utc)
    if "last week" in q:
        return now - dt.timedelta(days=7)
    if "last month" in q or "past month" in q or "last 30 days" in q:
        return now - dt.timedelta(days=30)
    if "last year" in q or "past year" in q:
        return now - dt.timedelta(days=365)
    if "yesterday" in q:
        return now - dt.timedelta(days=1)
    if "today" in q:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return None
