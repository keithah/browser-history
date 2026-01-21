import datetime as _dt
import re
from typing import Optional, Tuple
from urllib.parse import urlparse

SAFARI_EPOCH = _dt.datetime(2001, 1, 1, tzinfo=_dt.timezone.utc)
CHROME_EPOCH = _dt.datetime(1601, 1, 1, tzinfo=_dt.timezone.utc)


def safari_ts_to_datetime(raw: float) -> _dt.datetime:
    """Convert Safari's CFAbsoluteTime (seconds since 2001-01-01) to aware UTC datetime."""
    return SAFARI_EPOCH + _dt.timedelta(seconds=raw)


def chrome_ts_to_datetime(raw_micro: int) -> _dt.datetime:
    """Chrome/WebKit timestamps are microseconds since 1601-01-01 UTC."""
    return CHROME_EPOCH + _dt.timedelta(microseconds=raw_micro)


def firefox_ts_to_datetime(raw_micro: int) -> _dt.datetime:
    """Firefox visit_date is microseconds since Unix epoch."""
    return _dt.datetime.fromtimestamp(raw_micro / 1_000_000, tz=_dt.timezone.utc)


def parse_since(value: Optional[str]) -> Optional[_dt.datetime]:
    """Parse --since values like 7d, 12h, 30m, or YYYY-MM-DD. Returns UTC."""
    if not value:
        return None
    value = value.strip()
    now = _dt.datetime.now(tz=_dt.timezone.utc)

    if value.lower() in {"now", "today"}:
        return now

    dur_match = re.fullmatch(r"(?i)(\d+)([dhm])", value)
    if dur_match:
        amount, unit = dur_match.groups()
        amount_int = int(amount)
        if unit.lower() == "d":
            return now - _dt.timedelta(days=amount_int)
        if unit.lower() == "h":
            return now - _dt.timedelta(hours=amount_int)
        return now - _dt.timedelta(minutes=amount_int)

    try:
        dt = _dt.datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.astimezone(_dt.timezone.utc)
    except ValueError:
        raise ValueError(f"Unrecognized --since format: {value!r}")


def split_url(url: str) -> Tuple[str, str]:
    """Return (domain, path) from a URL."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.hostname or ""
    path = parsed.path or "/"
    return host.lower(), path


def iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).isoformat()


def iso_from_ts(ts: float) -> str:
    """Convert POSIX ts to ISO string (UTC)."""
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat()
