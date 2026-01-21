"""Optional page fetch to improve titles/summaries."""

import html
import re
from typing import Dict, Optional

import httpx


def fetch_metadata(url: str, timeout: float = 2.0, max_chars: int = 120_000) -> Dict[str, Optional[str]]:
    """Fetch a page title/description. Returns {} on any failure."""
    if not url.startswith(("http://", "https://")):
        return {}

    headers = {"User-Agent": "browser-history/0.1 (+local)"}
    try:
        resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError:
        return {}

    ctype = resp.headers.get("content-type", "")
    if "text/html" not in ctype and "text/" not in ctype:
        return {}

    text = resp.text[:max_chars]
    title = _extract_title(text)
    og_title = _extract_meta(text, "og:title")
    desc = _extract_meta(text, "description") or _extract_meta(text, "og:description")

    return {
        "title": og_title or title,
        "description": desc,
        "status": resp.status_code,
    }


def _extract_title(text: str) -> Optional[str]:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return _clean_html(match.group(1))


def _extract_meta(text: str, name: str) -> Optional[str]:
    pattern = rf'<meta[^>]+(?:name|property)\s*=\s*["\']{re.escape(name)}["\'][^>]*>'
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    tag = match.group(0)
    content_match = re.search(r'content\s*=\s*["\'](.*?)["\']', tag, flags=re.IGNORECASE | re.DOTALL)
    if not content_match:
        return None
    return _clean_html(content_match.group(1))


def _clean_html(value: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", value)).strip()
