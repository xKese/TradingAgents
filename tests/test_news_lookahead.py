"""yfinance news must not leak future-dated (or undated, in a backtest) articles
into a historical window.

Regressions for #992 (flat articles bypassed the date filter), #1007 (global
news injected future articles), #993 (empty-after-filter returned a blank body).
"""
import time
from datetime import datetime, timezone

import pytest

import tradingagents.dataflows.yfinance_news as ynews


def _epoch(date_str):
    return int(time.mktime(datetime.strptime(date_str, "%Y-%m-%d").timetuple()))


@pytest.mark.unit
def test_flat_article_publish_time_is_parsed():
    # #992: flat articles now carry a pub_date (was always None -> unfilterable).
    data = ynews._extract_article_data(
        {"title": "X", "publisher": "P", "link": "l", "providerPublishTime": _epoch("2025-05-09")}
    )
    assert data["pub_date"] is not None
    assert data["pub_date"].strftime("%Y-%m-%d") == "2025-05-09"


@pytest.mark.unit
def test_window_excludes_future_and_undated_in_backtest():
    start = datetime(2025, 5, 1)
    end = datetime(2025, 5, 9)  # historical window (well in the past)
    inside = datetime(2025, 5, 5)
    future = datetime(2025, 6, 1)
    assert ynews._in_news_window(inside, start, end) is True
    assert ynews._in_news_window(future, start, end) is False     # look-ahead blocked
    assert ynews._in_news_window(None, start, end) is False        # undated -> excluded in backtest


@pytest.mark.unit
def test_window_is_end_exclusive_and_accepts_the_full_end_date():
    start = datetime(2025, 5, 1)
    end = datetime(2025, 5, 9)

    assert ynews._in_news_window(datetime(2025, 5, 1), start, end) is True
    assert (
        ynews._in_news_window(
            datetime(2025, 5, 9, 23, 59, 59, 999999), start, end
        )
        is True
    )
    assert ynews._in_news_window(datetime(2025, 5, 10), start, end) is False


@pytest.mark.unit
def test_window_compares_equivalent_aware_instants_in_utc():
    start = datetime(2025, 5, 1, tzinfo=timezone.utc)
    end = datetime(2025, 5, 9, tzinfo=timezone.utc)
    same_instant = datetime.fromisoformat("2025-05-09T19:30:00-04:00")

    assert ynews._in_news_window(same_instant, start, end) is True


@pytest.mark.unit
def test_window_keeps_undated_in_live_window():
    # Live window (reaches today): undated articles can't be "future", so keep them.
    start = datetime.now()
    end = datetime.now()
    assert ynews._in_news_window(None, start, end) is True


@pytest.mark.unit
def test_global_news_future_flat_article_excluded(monkeypatch):
    # #1007: a flat, future-dated global article must not appear in a historical run.
    future_article = {"title": "FUTURE EVENT", "publisher": "P", "link": "l",
                      "providerPublishTime": _epoch("2025-06-01")}
    past_article = {"title": "PAST EVENT", "publisher": "P", "link": "l",
                    "providerPublishTime": _epoch("2025-05-05")}

    class FakeSearch:
        def __init__(self, *a, **k):
            self.news = [future_article, past_article]

    monkeypatch.setattr(ynews.yf, "Search", FakeSearch)
    out = ynews.get_global_news_yfinance("2025-05-09", look_back_days=7, limit=10)
    assert "PAST EVENT" in out
    assert "FUTURE EVENT" not in out  # #1007


@pytest.mark.unit
def test_global_news_excludes_midnight_after_curr_date(monkeypatch):
    inside = {
        "content": {
            "title": "INSIDE WINDOW",
            "provider": {"displayName": "P"},
            "pubDate": "2025-05-09T23:59:59Z",
        }
    }
    next_midnight = {
        "content": {
            "title": "NEXT MIDNIGHT",
            "provider": {"displayName": "P"},
            "pubDate": "2025-05-10T00:00:00Z",
        }
    }

    class FakeSearch:
        def __init__(self, *args, **kwargs):
            self.news = [inside, next_midnight]

    monkeypatch.setattr(ynews.yf, "Search", FakeSearch)
    out = ynews.get_global_news_yfinance(
        "2025-05-09", look_back_days=7, limit=10
    )

    assert "INSIDE WINDOW" in out
    assert "NEXT MIDNIGHT" not in out


@pytest.mark.unit
def test_global_news_empty_after_filter_is_informative(monkeypatch):
    # #993: everything filtered out -> a clear message, not a blank-bodied report.
    only_future = {"title": "FUTURE", "publisher": "P", "link": "l",
                   "providerPublishTime": _epoch("2025-06-01")}

    class FakeSearch:
        def __init__(self, *a, **k):
            self.news = [only_future]

    monkeypatch.setattr(ynews.yf, "Search", FakeSearch)
    out = ynews.get_global_news_yfinance("2025-05-09", look_back_days=7, limit=10)
    assert "No global news found" in out
    assert "###" not in out  # no empty article body
