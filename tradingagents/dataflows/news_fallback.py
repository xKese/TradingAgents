"""Name-based news fallback for exchange-suffixed (mostly European) tickers.

Ticker-based news retrieval fails structurally for European symbols: Yahoo's
news endpoint rejects any dotted ticker ("Invalid ticker format: ENR.DEX"),
and Alpha Vantage's NEWS_SENTIMENT coverage is strongly US-centric, so an
all-Alpha-Vantage setup typically gets an empty feed for e.g. XETRA symbols.

This module recovers institutional news for such tickers by searching Yahoo
News with the *company name* instead of the ticker: the name is resolved via
the Alpha Vantage SYMBOL_SEARCH reverse lookup (the same endpoint that powers
the web UI's ticker autocomplete), and the news search reuses the yf.Search
mechanism the global-news path already uses. Purely additive: the tool layer
only consults this fallback when the configured vendor produced no usable
ticker news, and any failure here degrades back to the vendor's original
result.
"""

from __future__ import annotations

import json

import yfinance as yf

from .alpha_vantage_common import parse_date
from .alpha_vantage_search import get_symbol_search
from .config import get_config
from .stockstats_utils import yf_retry

# Shared article helpers from the ticker-news path, so name-based results get
# the identical look-ahead-safe window filtering and formatting.
from .yfinance_news import _extract_article_data, _in_news_window


def news_result_is_empty(result) -> bool:
    """True when a get_news vendor result contains no usable articles.

    Recognizes the failure shapes of both vendors: the yfinance path returns
    prose ("Error fetching news ...", "No news found ..."), the router returns
    the NO_DATA_AVAILABLE sentinel, and the Alpha Vantage path returns raw
    NEWS_SENTIMENT JSON whose ``feed`` is empty (or an error body without one).
    Anything unrecognized counts as usable — the fallback must never shadow
    real articles.
    """
    if not isinstance(result, str):
        return False
    text = result.strip()
    if not text:
        return True
    if text.startswith(("Error fetching news", "No news found", "NO_DATA_AVAILABLE")):
        return True
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return False
        if isinstance(payload, dict):
            return not payload.get("feed")
    return False


def _resolve_company_name(ticker: str) -> str | None:
    """Company name for a ticker via Alpha Vantage SYMBOL_SEARCH, or None.

    Queries the FULL symbol only. Deliberately no retry with the bare base:
    the same base letters often mean a different company on another exchange
    (ENR.DEX is Siemens Energy, but ENR alone is Energizer Holdings on NYSE),
    and wrong-company news is worse than no news.
    """
    try:
        matches = get_symbol_search(ticker)
    except Exception:
        return None  # no key / network / rate limit -> fallback unavailable
    if not matches:
        return None
    name = (matches[0].get("name") or "").strip()
    return name or None


def _search_news(query: str, count: int) -> list:
    """Raw Yahoo news search by free-text query (seam for tests)."""
    search = yf_retry(lambda: yf.Search(
        query=query,
        news_count=count,
        enable_fuzzy_query=True,
    ))
    return search.news or []


def get_news_by_company_name(ticker: str, start_date: str, end_date: str) -> str | None:
    """Institutional news for ``ticker`` via company-name search, or None.

    Returns a formatted block (same shape as the ticker-news path, labeled
    with the resolved name) or None when the name cannot be resolved, the
    search fails, or no article falls into the requested window.
    """
    name = _resolve_company_name(ticker)
    if not name:
        return None

    limit = get_config()["news_article_limit"]
    try:
        articles = _search_news(name, limit)
    except Exception:
        return None
    if not articles:
        return None

    try:
        start_dt = parse_date(start_date)
        end_dt = parse_date(end_date)
    except (ValueError, OverflowError):
        return None

    news_str = ""
    kept = 0
    for article in articles:
        data = _extract_article_data(article)
        if not _in_news_window(data["pub_date"], start_dt, end_dt):
            continue
        news_str += f"### {data['title']} (source: {data['publisher']})\n"
        if data["summary"]:
            news_str += f"{data['summary']}\n"
        if data["link"]:
            news_str += f"Link: {data['link']}\n"
        news_str += "\n"
        kept += 1

    if kept == 0:
        return None

    return (
        f"## {ticker} News, from {start_date} to {end_date} "
        f"(name-based search: \"{name}\" — ticker news was unavailable for this "
        f"symbol):\n\n{news_str}"
    )
