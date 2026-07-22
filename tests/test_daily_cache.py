"""Tests for the opt-in per-day vendor data cache (data_cache_daily)."""

import pytest

from tradingagents.dataflows import interface
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.daily_cache import CACHEABLE_METHODS, cached_vendor_call


def _enable_cache(tmp_path):
    set_config({"data_cache_daily": True, "data_cache_dir": str(tmp_path)})


class _CountingImpl:
    def __init__(self, result="NEWS BODY"):
        self.calls = 0
        self.result = result

    def __call__(self):
        self.calls += 1
        return self.result


@pytest.mark.unit
class TestCachedVendorCall:
    def test_hit_serves_from_disk(self, tmp_path):
        _enable_cache(tmp_path)
        impl = _CountingImpl()
        first = cached_vendor_call("get_news", impl, ("AAPL",), {})
        second = cached_vendor_call("get_news", impl, ("AAPL",), {})
        assert first == second == "NEWS BODY"
        assert impl.calls == 1

    def test_different_args_cache_separately(self, tmp_path):
        _enable_cache(tmp_path)
        impl = _CountingImpl()
        cached_vendor_call("get_news", impl, ("AAPL",), {})
        cached_vendor_call("get_news", impl, ("NVDA",), {})
        assert impl.calls == 2

    def test_non_cacheable_method_always_fetches(self, tmp_path):
        _enable_cache(tmp_path)
        assert "get_stock_data" not in CACHEABLE_METHODS
        impl = _CountingImpl()
        cached_vendor_call("get_stock_data", impl, ("AAPL",), {})
        cached_vendor_call("get_stock_data", impl, ("AAPL",), {})
        assert impl.calls == 2

    def test_disabled_by_default(self, tmp_path):
        set_config({"data_cache_dir": str(tmp_path)})
        impl = _CountingImpl()
        cached_vendor_call("get_news", impl, ("AAPL",), {})
        cached_vendor_call("get_news", impl, ("AAPL",), {})
        assert impl.calls == 2
        assert not (tmp_path / "daily").exists()

    @pytest.mark.parametrize(
        "sentinel",
        [
            "NO_DATA_AVAILABLE: nothing for 'X'",
            "DATA_UNAVAILABLE: optional macro_data could not be retrieved",
        ],
    )
    def test_failure_sentinels_not_persisted(self, tmp_path, sentinel):
        _enable_cache(tmp_path)
        impl = _CountingImpl(result=sentinel)
        cached_vendor_call("get_news", impl, ("AAPL",), {})
        cached_vendor_call("get_news", impl, ("AAPL",), {})
        assert impl.calls == 2  # a transient outage must not poison the day

    def test_non_string_results_not_persisted(self, tmp_path):
        _enable_cache(tmp_path)
        impl = _CountingImpl(result={"not": "a string"})
        cached_vendor_call("get_news", impl, ("AAPL",), {})
        cached_vendor_call("get_news", impl, ("AAPL",), {})
        assert impl.calls == 2


@pytest.mark.unit
def test_route_to_vendor_uses_cache(tmp_path, monkeypatch):
    """Integration: route_to_vendor serves the second call from the day cache."""
    _enable_cache(tmp_path)
    calls = {"n": 0}

    def fake_news(*args, **kwargs):
        calls["n"] += 1
        return "ROUTED NEWS"

    monkeypatch.setitem(interface.VENDOR_METHODS, "get_news", {"yfinance": fake_news})
    first = interface.route_to_vendor("get_news", "AAPL")
    second = interface.route_to_vendor("get_news", "AAPL")
    assert first == second == "ROUTED NEWS"
    assert calls["n"] == 1
