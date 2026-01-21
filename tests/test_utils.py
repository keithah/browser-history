import datetime as dt

from browser_history import utils


def test_parse_since_relative_days():
    now = dt.datetime.now(tz=dt.timezone.utc)
    result = utils.parse_since("2d")
    delta = now - result
    assert 2 * 86400 - 5 <= delta.total_seconds() <= 2 * 86400 + 5


def test_parse_since_date_string():
    result = utils.parse_since("2024-01-02")
    assert result.year == 2024
    assert result.month == 1
    assert result.day == 2
    assert result.tzinfo == dt.timezone.utc


def test_split_url_extracts_domain_and_path():
    domain, path = utils.split_url("https://example.com/foo/bar")
    assert domain == "example.com"
    assert path == "/foo/bar"
