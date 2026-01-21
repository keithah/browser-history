import re
from typing import List, Optional, Set, Tuple

from .utils import split_url

# Lightweight domain heuristics. None of this is perfect, but it should be
# good enough for "AI sites", "GitHub projects", "personal sites", etc.

AI_DOMAINS = {
    "openai.com",
    "chat.openai.com",
    "platform.openai.com",
    "chatgpt.com",
    "anthropic.com",
    "claude.ai",
    "character.ai",
    "pi.ai",
    "phind.com",
    "you.com",
    "perplexity.ai",
    "huggingface.co",
    "huggingface.com",
    "stability.ai",
    "runwayml.com",
    "suno.ai",
    "midjourney.com",
    "replicate.com",
    "cursor.sh",
    "notebooklm.google.com",
    "cohere.com",
    "mistral.ai",
    "grok.com",
    "poe.com",
    "jan.ai",
    "blackbox.ai",
    "bolt.new",
    "gemini.google.com",
    "copilot.microsoft.com",
    "lmsys.org",
    "deepseek.com",
    "deepseek.ai",
    "aider.chat",
    "openrouter.ai",
}

NEWS_DOMAINS = {
    "news.ycombinator.com",
    "nytimes.com",
    "theverge.com",
    "wired.com",
    "bloomberg.com",
    "techcrunch.com",
    "arstechnica.com",
    "wsj.com",
    "washingtonpost.com",
    "reuters.com",
    "apnews.com",
    "ft.com",
    "bbc.com",
    "guardian.com",
    "substack.com",
}

SOCIAL_DOMAINS = {
    "twitter.com",
    "x.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "threads.net",
    "reddit.com",
    "www.reddit.com",
    "bsky.app",
}

VIDEO_DOMAINS = {"youtube.com", "youtu.be", "vimeo.com", "loom.com"}

SHOPPING_DOMAINS = {
    "amazon.com",
    "ebay.com",
    "bestbuy.com",
    "target.com",
    "walmart.com",
    "etsy.com",
    "apple.com",
    "store.google.com",
}

DOC_DOMAINS = {
    "developer.apple.com",
    "developers.google.com",
    "developer.android.com",
    "dev.to",
    "learn.microsoft.com",
    "developer.mozilla.org",
    "docs.github.com",
    "readthedocs.io",
    "pkg.go.dev",
    "pypi.org",
    "rubygems.org",
}

PERSONAL_HOSTS = {
    "github.io",
    "notion.site",
    "substack.com",
    "medium.com",
    "blogspot.com",
    "bearblog.dev",
    "hey.com",
    "hey.world",
    "write.as",
    "vercel.app",
}

PERSONAL_TLDS = (".me", ".page", ".dev", ".app")
EXCLUDED_SUBDOMAINS = ("support.", "help.", "account.", "login.", "id.", "auth.", "secure.", "dashboard.")
SEARCH_DOMAINS = {
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "duckduckgo.com",
    "duck.com",
    "search.brave.com",
    "yahoo.com",
}
REGISTRY_DOMAINS = {"pub.dev", "api.flutter.dev", "npmjs.com", "pypi.org", "rubygems.org", "crates.io"}
CORP_BLOG_DOMAINS = {
    "nordstrom.com",
    "kodi.tv",
    "postmarketos.org",
    "memoryplugin.com",
    "poke.com",
    "bright.com",
    "encoreglobal.com",
    "perfectsettings.com",
    "ezo.io",
    "bluebubbles.app",
    "blog.google",
}


def _github_repo(path: str) -> Optional[str]:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and not parts[0].startswith("@"):
        owner, repo = parts[0], parts[1]
        # Ignore GitHub special paths like /features
        if owner.lower() in {"features", "topics", "settings"}:
            return None
        return f"{owner}/{repo}"
    return None


def classify(url: str, title: str) -> Tuple[str, List[str], str]:
    """Return (primary_label, labels, summary)."""
    domain, path = split_url(url)
    labels: Set[str] = set()
    summary = title.strip() if title else ""
    lt = (title or "").lower()
    path_lower = path.lower()

    if domain == "github.com":
        repo = _github_repo(path)
        if repo:
            labels.add("github_project")
            summary = summary or _summarize_github(repo, path)

    ai_hit = re.search(r"\b(ai|llm|chatgpt|claude|perplexity|gemini|gpt|copilot|mistral|deepseek)\b", lt)
    if domain.endswith(".ai") or domain in AI_DOMAINS or ai_hit or "ai." in domain:
        labels.add("ai")

    if domain in NEWS_DOMAINS or " news" in lt or lt.startswith("news:") or "newsletter" in lt:
        labels.add("news")

    if domain in SOCIAL_DOMAINS:
        labels.add("social")

    if domain in VIDEO_DOMAINS or "youtube.com/watch" in url:
        labels.add("video")

    if domain in SHOPPING_DOMAINS:
        labels.add("shopping")

    if _looks_docs(domain, path_lower, lt):
        labels.add("docs")

    if _looks_personal(domain, path_lower, lt):
        labels.add("personal_site")

    if not labels:
        labels.add("other")

    primary = _pick_primary(labels)
    if not summary:
        summary = f"{domain}{path}"

    return primary, sorted(labels), summary


def _pick_primary(labels: Set[str]) -> str:
    priority = [
        "github_project",
        "ai",
        "personal_site",
        "docs",
        "news",
        "social",
        "shopping",
        "video",
        "other",
    ]
    for label in priority:
        if label in labels:
            return label
    return "other"


def _looks_docs(domain: str, path_lower: str, lt: str) -> bool:
    if domain in DOC_DOMAINS:
        return True
    if domain.startswith("docs.") or domain.startswith("developer.") or domain.startswith("dev."):
        return True
    if "/docs" in path_lower or "/api" in path_lower or "/reference" in path_lower:
        return True
    return any(term in lt for term in ("documentation", "api ", "api:", "sdk", "reference", "guide"))


def _looks_personal(domain: str, path_lower: str, lt: str) -> bool:
    if domain in SEARCH_DOMAINS:
        return False
    if domain in REGISTRY_DOMAINS:
        return False
    if domain in AI_DOMAINS:
        return False
    if domain in NEWS_DOMAINS:
        return False
    if domain in SHOPPING_DOMAINS:
        return False
    if domain in SOCIAL_DOMAINS:
        return False
    if domain.startswith(EXCLUDED_SUBDOMAINS):
        return False
    if "support." in domain or "/support/" in path_lower or "/help/" in path_lower:
        return False
    if _looks_docs(domain, path_lower, lt):
        return False
    if domain in CORP_BLOG_DOMAINS:
        return False
    if domain.endswith(".atlassian.net"):
        return False
    if domain.endswith(".google.com") or domain == "google.com":
        return False
    personal_host = (
        domain.endswith(".github.io")
        or domain.endswith(".notion.site")
        or domain.endswith(".substack.com")
        or domain.endswith(".vercel.app")
        or domain.startswith("blog.")
        or domain in PERSONAL_HOSTS
        or domain.endswith(PERSONAL_TLDS)
    )
    if "/~" in path_lower:
        return True
    blog_hints = ("/blog", "/posts", "/writing", "/notes", "/til", "/cv", "/resume", "/about-me")
    if personal_host and any(hint in path_lower for hint in blog_hints):
        return True
    if personal_host and ("portfolio" in lt or "blog" in lt):
        return True
    if personal_host and ("about me" in lt or "resume" in lt or "cv" in lt or "portfolio" in lt):
        return True
    if personal_host:
        return True
    return False


def _summarize_github(repo: str, path: str) -> str:
    parts = [p for p in path.split("/") if p]
    summary = f"GitHub repository {repo}"
    if len(parts) >= 4:
        section = parts[2]
        if section == "pull" and len(parts) >= 4:
            summary = f"GitHub PR #{parts[3]} for {repo}"
        elif section == "issues" and len(parts) >= 4:
            summary = f"GitHub issue #{parts[3]} for {repo}"
        elif section == "commit" and len(parts) >= 4:
            summary = f"GitHub commit {parts[3][:7]} for {repo}"
        elif section == "tree" and len(parts) >= 4:
            summary = f"GitHub tree {parts[3]} for {repo}"
        elif section == "blob" and len(parts) >= 5:
            summary = f"GitHub file {'/'.join(parts[4:])} in {repo}"
        elif section == "actions":
            summary = f"GitHub Actions for {repo}"
        elif section == "releases":
            summary = f"GitHub releases for {repo}"
    return summary
