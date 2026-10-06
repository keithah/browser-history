import datetime as dt

from browser_history import ask


def test_ask_filters_are_json_safe():
    result = ask.ask("ai sites last week", limit=5)
    filters = result["filters"]
    assert isinstance(filters.get("since"), str)
    # ensure it parses back to a datetime
    dt.datetime.fromisoformat(filters["since"])
