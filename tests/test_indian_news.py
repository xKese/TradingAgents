"""Indian news vendor (ET Markets + Google News RSS): merge, dedup, and
look-ahead-safe date filtering, mirroring the yfinance news vendor's contract
(tests/test_news_lookahead.py) but for the India-scoped source.
"""
from datetime import datetime

import pytest

import tradingagents.dataflows.indian_news as inews


def _article(title, pub_date=None, publisher="P", link="l"):
    return {"title": title, "publisher": publisher, "link": link, "pub_date": pub_date}


@pytest.mark.unit
def test_strip_exchange_suffix():
    assert inews._strip_exchange_suffix("NBCC.NS") == "NBCC"
    assert inews._strip_exchange_suffix("NBCC.BO") == "NBCC"
    assert inews._strip_exchange_suffix("NBCC") == "NBCC"


@pytest.mark.unit
def test_ticker_news_merges_et_and_google_and_dedupes(monkeypatch):
    in_window = datetime(2026, 6, 15)
    et_articles = [
        _article("NBCC wins order worth Rs 800 crore", in_window, publisher="ET"),
        _article("SHARED HEADLINE", in_window, publisher="ET"),
    ]
    google_articles = [
        _article("SHARED HEADLINE", in_window, publisher="Moneycontrol"),
        _article("NBCC share price target raised", in_window, publisher="CNBC"),
    ]
    monkeypatch.setattr(inews, "_fetch_et_markets", lambda limit: et_articles)
    monkeypatch.setattr(inews, "_fetch_google_news", lambda query, limit: google_articles)

    out = inews.get_news_india("NBCC.NS", "2026-06-01", "2026-06-30")
    assert "NBCC wins order worth Rs 800 crore" in out
    assert "NBCC share price target raised" in out
    # deduped: the shared headline appears once, keeping the first (ET) source
    assert out.count("SHARED HEADLINE") == 1
    assert "(source: ET)" in out


@pytest.mark.unit
def test_ticker_news_no_articles_at_all(monkeypatch):
    monkeypatch.setattr(inews, "_fetch_et_markets", lambda limit: [])
    monkeypatch.setattr(inews, "_fetch_google_news", lambda query, limit: [])
    out = inews.get_news_india("NBCC", "2026-06-01", "2026-06-30")
    assert out == "No news found for NBCC"


@pytest.mark.unit
def test_ticker_news_all_outside_window_is_informative(monkeypatch):
    outside = datetime(2020, 1, 1)
    monkeypatch.setattr(inews, "_fetch_et_markets", lambda limit: [_article("NBCC OLD NEWS", outside)])
    monkeypatch.setattr(inews, "_fetch_google_news", lambda query, limit: [])
    out = inews.get_news_india("NBCC", "2026-06-01", "2026-06-30")
    assert "No news found for NBCC between 2026-06-01 and 2026-06-30" in out
    assert "###" not in out


@pytest.mark.unit
def test_global_news_prefers_et_then_tops_up_google(monkeypatch):
    curr = "2026-07-10"
    et_articles = [_article(f"ET {i}", datetime(2026, 7, 9)) for i in range(2)]
    monkeypatch.setattr(inews, "_fetch_et_markets", lambda limit: et_articles)
    monkeypatch.setattr(inews, "_fetch_google_news", lambda query, limit: [_article("GOOGLE TOPUP", datetime(2026, 7, 9))])

    out = inews.get_global_news_india(curr, look_back_days=7, limit=3)
    assert "ET 0" in out and "ET 1" in out
    assert "GOOGLE TOPUP" in out


@pytest.mark.unit
def test_global_news_empty_is_informative(monkeypatch):
    monkeypatch.setattr(inews, "_fetch_et_markets", lambda limit: [])
    monkeypatch.setattr(inews, "_fetch_google_news", lambda query, limit: [])
    out = inews.get_global_news_india("2026-07-10", look_back_days=7, limit=5)
    assert out == "No global news found for 2026-07-10"


@pytest.mark.unit
def test_ticker_news_fetch_error_is_reported_not_raised(monkeypatch):
    def boom(limit):
        raise RuntimeError("network down")
    monkeypatch.setattr(inews, "_fetch_et_markets", boom)
    out = inews.get_news_india("NBCC", "2026-06-01", "2026-06-30")
    assert "Error fetching news for NBCC" in out
