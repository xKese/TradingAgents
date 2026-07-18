"""Name-based news fallback for exchange-suffixed (European) tickers.

Yahoo's news endpoint rejects dotted tickers ("Invalid ticker format:
ENR.DEX") and Alpha Vantage's NEWS_SENTIMENT feed is US-centric, so European
symbols got no institutional news at all. The fallback resolves the company
name via the Alpha Vantage symbol search and queries Yahoo News by name.
"""

from __future__ import annotations

import json

import pytest

import tradingagents.dataflows.news_fallback as fb


def _article(title: str, pub_date: str) -> dict:
    return {
        "content": {
            "title": title,
            "summary": f"summary of {title}",
            "provider": {"displayName": "TestWire"},
            "canonicalUrl": {"url": "https://example.com/a"},
            "pubDate": pub_date,
        }
    }


@pytest.mark.unit
class TestEmptyDetection:
    def test_failure_shapes_are_empty(self):
        assert fb.news_result_is_empty(
            "Error fetching news for ENR.DEX: Invalid ticker format: ENR.DEX. "
            "Ticker can only contain alphanumeric characters, colons, "
            "underscores, and hyphens"
        )
        assert fb.news_result_is_empty("No news found for ENR.DEX")
        assert fb.news_result_is_empty("NO_DATA_AVAILABLE: nothing anywhere")
        assert fb.news_result_is_empty("")
        # Alpha Vantage: empty feed, and error bodies without a feed at all
        assert fb.news_result_is_empty(json.dumps({"items": "0", "feed": []}))
        assert fb.news_result_is_empty(json.dumps({"Information": "Invalid inputs"}))

    def test_usable_results_are_not_empty(self):
        assert not fb.news_result_is_empty(
            "## AAPL News, from 2026-07-01 to 2026-07-18:\n\n### headline\n"
        )
        assert not fb.news_result_is_empty(
            json.dumps({"feed": [{"title": "real article"}]})
        )
        assert not fb.news_result_is_empty({"not": "a string"})


@pytest.mark.unit
class TestResolveCompanyName:
    def test_resolves_best_match_name(self, monkeypatch):
        monkeypatch.setattr(
            fb, "get_symbol_search",
            lambda q: [{"symbol": "ENR.DEX", "name": "Siemens Energy AG"}],
        )
        assert fb._resolve_company_name("ENR.DEX") == "Siemens Energy AG"

    def test_no_match_or_error_gives_none(self, monkeypatch):
        monkeypatch.setattr(fb, "get_symbol_search", lambda q: [])
        assert fb._resolve_company_name("ENR.DEX") is None

        def boom(q):
            raise RuntimeError("no key")

        monkeypatch.setattr(fb, "get_symbol_search", boom)
        assert fb._resolve_company_name("ENR.DEX") is None


@pytest.mark.unit
class TestNameBasedNews:
    def test_returns_formatted_block_with_windowed_articles(self, monkeypatch):
        monkeypatch.setattr(
            fb, "_resolve_company_name", lambda t: "Siemens Energy AG"
        )
        monkeypatch.setattr(
            fb, "_search_news",
            lambda q, n: [
                _article("in window", "2026-07-15T10:00:00Z"),
                _article("out of window", "2026-01-01T10:00:00Z"),
            ],
        )
        out = fb.get_news_by_company_name("ENR.DEX", "2026-07-11", "2026-07-18")
        assert out is not None
        assert "Siemens Energy AG" in out
        assert "in window" in out
        assert "out of window" not in out
        assert "ENR.DEX" in out

    def test_none_when_name_unresolvable_or_no_articles(self, monkeypatch):
        monkeypatch.setattr(fb, "_resolve_company_name", lambda t: None)
        assert fb.get_news_by_company_name("ENR.DEX", "2026-07-11", "2026-07-18") is None

        monkeypatch.setattr(fb, "_resolve_company_name", lambda t: "Siemens Energy AG")
        monkeypatch.setattr(fb, "_search_news", lambda q, n: [])
        assert fb.get_news_by_company_name("ENR.DEX", "2026-07-11", "2026-07-18") is None

    def test_search_failure_gives_none(self, monkeypatch):
        monkeypatch.setattr(fb, "_resolve_company_name", lambda t: "Siemens Energy AG")

        def boom(q, n):
            raise RuntimeError("network down")

        monkeypatch.setattr(fb, "_search_news", boom)
        assert fb.get_news_by_company_name("ENR.DEX", "2026-07-11", "2026-07-18") is None


@pytest.mark.unit
class TestToolLevelFallback:
    def test_empty_vendor_result_triggers_fallback(self, monkeypatch):
        from tradingagents.agents.utils import news_data_tools as tools

        monkeypatch.setattr(
            tools, "route_to_vendor",
            lambda *a, **k: "Error fetching news for ENR.DEX: Invalid ticker format",
        )
        monkeypatch.setattr(
            tools, "get_news_by_company_name",
            lambda t, s, e: "## ENR.DEX News (name-based)\n\n### headline\n",
        )
        out = tools.get_news.func("ENR.DEX", "2026-07-11", "2026-07-18")
        assert "name-based" in out

    def test_usable_vendor_result_is_untouched(self, monkeypatch):
        from tradingagents.agents.utils import news_data_tools as tools

        monkeypatch.setattr(
            tools, "route_to_vendor", lambda *a, **k: "## AAPL News\n\n### real\n"
        )

        def must_not_run(*a, **k):
            raise AssertionError("fallback must not run for usable results")

        monkeypatch.setattr(tools, "get_news_by_company_name", must_not_run)
        out = tools.get_news.func("AAPL", "2026-07-11", "2026-07-18")
        assert out == "## AAPL News\n\n### real\n"

    def test_fallback_failure_returns_original_result(self, monkeypatch):
        from tradingagents.agents.utils import news_data_tools as tools

        original = "No news found for ENR.DEX"
        monkeypatch.setattr(tools, "route_to_vendor", lambda *a, **k: original)

        def boom(*a, **k):
            raise RuntimeError("fallback broke")

        monkeypatch.setattr(tools, "get_news_by_company_name", boom)
        out = tools.get_news.func("ENR.DEX", "2026-07-11", "2026-07-18")
        assert out == original
