"""Alpha Vantage indicator fetching: rate-limit propagation and request dedup.

Regressions for the burst-throttle incident: (1) ``AlphaVantageRateLimitError``
was swallowed by ``get_indicator``'s generic except and returned as an error
*string*, so the router's yfinance fallback never fired and the LLM received
the error text as market data; (2) ``boll``/``boll_ub``/``boll_lb`` (and the
three MACD series) each fired an identical HTTP request back to back — the
exact pattern AV's burst detector rejects.
"""
import pytest

import tradingagents.dataflows.alpha_vantage_indicator as avi
from tradingagents.dataflows.alpha_vantage_common import (
    AlphaVantageNotConfiguredError,
    AlphaVantageRateLimitError,
)

_BBANDS_CSV = (
    "time,Real Upper Band,Real Middle Band,Real Lower Band\n"
    "2026-06-30,110.0,100.0,90.0\n"
    "2026-06-29,109.0,99.0,89.0\n"
)

_MACD_CSV = (
    "time,MACD,MACD_Signal,MACD_Hist\n"
    "2026-06-30,1.5,1.2,0.3\n"
)


@pytest.fixture(autouse=True)
def _clear_response_cache():
    avi._response_cache.clear()
    yield
    avi._response_cache.clear()


def _counting_fake(body):
    calls = {"n": 0}

    def fake(function_name, params):
        calls["n"] += 1
        return body

    return fake, calls


@pytest.mark.unit
def test_rate_limit_propagates_from_get_indicator(monkeypatch):
    # The router can only fall back to the next vendor if the typed error
    # escapes get_indicator instead of being returned as a string.
    def raise_rl(function_name, params):
        raise AlphaVantageRateLimitError("burst throttle persisted")

    monkeypatch.setattr(avi, "_make_api_request", raise_rl)
    with pytest.raises(AlphaVantageRateLimitError):
        avi.get_indicator("AAPL", "boll_ub", "2026-07-01", 30)


@pytest.mark.unit
def test_not_configured_still_propagates(monkeypatch):
    def raise_nc(function_name, params):
        raise AlphaVantageNotConfiguredError("no key")

    monkeypatch.setattr(avi, "_make_api_request", raise_nc)
    with pytest.raises(AlphaVantageNotConfiguredError):
        avi.get_indicator("AAPL", "rsi", "2026-07-01", 30)


@pytest.mark.unit
def test_generic_error_still_returns_string(monkeypatch):
    # Non-vendor failures (parsing bugs etc.) keep the old degrade-to-string
    # contract so a formatting hiccup doesn't abort a whole analyst run.
    def boom(function_name, params):
        raise RuntimeError("boom")

    monkeypatch.setattr(avi, "_make_api_request", boom)
    out = avi.get_indicator("AAPL", "rsi", "2026-07-01", 30)
    assert out.startswith("Error retrieving")


@pytest.mark.unit
def test_bollinger_triplet_uses_one_request(monkeypatch):
    fake, calls = _counting_fake(_BBANDS_CSV)
    monkeypatch.setattr(avi, "_make_api_request", fake)

    ub = avi.get_indicator("AAPL", "boll_ub", "2026-07-01", 30)
    mid = avi.get_indicator("AAPL", "boll", "2026-07-01", 30)
    lb = avi.get_indicator("AAPL", "boll_lb", "2026-07-01", 30)

    assert calls["n"] == 1  # one BBANDS payload serves all three bands
    assert "110.0" in ub
    assert "100.0" in mid
    assert "90.0" in lb


@pytest.mark.unit
def test_macd_triplet_uses_one_request(monkeypatch):
    fake, calls = _counting_fake(_MACD_CSV)
    monkeypatch.setattr(avi, "_make_api_request", fake)

    macd = avi.get_indicator("AAPL", "macd", "2026-07-01", 30)
    macds = avi.get_indicator("AAPL", "macds", "2026-07-01", 30)
    macdh = avi.get_indicator("AAPL", "macdh", "2026-07-01", 30)

    assert calls["n"] == 1
    assert "1.5" in macd
    assert "1.2" in macds
    assert "0.3" in macdh


@pytest.mark.unit
def test_cache_keyed_by_symbol(monkeypatch):
    fake, calls = _counting_fake(_BBANDS_CSV)
    monkeypatch.setattr(avi, "_make_api_request", fake)

    avi.get_indicator("AAPL", "boll", "2026-07-01", 30)
    avi.get_indicator("MSFT", "boll", "2026-07-01", 30)
    assert calls["n"] == 2  # different symbols never share a payload


@pytest.mark.unit
def test_cache_expires_after_ttl(monkeypatch):
    fake, calls = _counting_fake(_BBANDS_CSV)
    monkeypatch.setattr(avi, "_make_api_request", fake)

    avi.get_indicator("AAPL", "boll", "2026-07-01", 30)
    # Age the single cache entry past the TTL, then refetch.
    key = next(iter(avi._response_cache))
    stored_at, payload = avi._response_cache[key]
    avi._response_cache[key] = (stored_at - avi._RESPONSE_CACHE_TTL - 1.0, payload)
    avi.get_indicator("AAPL", "boll", "2026-07-01", 30)
    assert calls["n"] == 2


@pytest.mark.unit
def test_failures_are_not_cached(monkeypatch):
    state = {"n": 0}

    def flaky(function_name, params):
        state["n"] += 1
        if state["n"] == 1:
            raise AlphaVantageRateLimitError("throttled")
        return _BBANDS_CSV

    monkeypatch.setattr(avi, "_make_api_request", flaky)
    with pytest.raises(AlphaVantageRateLimitError):
        avi.get_indicator("AAPL", "boll", "2026-07-01", 30)
    out = avi.get_indicator("AAPL", "boll", "2026-07-01", 30)
    assert state["n"] == 2  # the failure was retried, not served from cache
    assert "100.0" in out


@pytest.mark.unit
def test_cache_is_capped(monkeypatch):
    fake, _ = _counting_fake(_BBANDS_CSV)
    monkeypatch.setattr(avi, "_make_api_request", fake)
    for i in range(avi._RESPONSE_CACHE_MAX_ENTRIES + 5):
        avi._fetch_indicator_data("BBANDS", {"symbol": f"S{i}", "datatype": "csv"})
    assert len(avi._response_cache) <= avi._RESPONSE_CACHE_MAX_ENTRIES
