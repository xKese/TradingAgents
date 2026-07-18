"""Tests for the deterministic market-data verification snapshot (#830/#881)."""

from __future__ import annotations

import pandas as pd
import pytest

import tradingagents.dataflows.market_data_validator as validator


def _sample_ohlcv() -> pd.DataFrame:
    dates = pd.bdate_range("2026-04-01", "2026-05-20")
    closes = [100 + i for i in range(len(dates))]
    return pd.DataFrame({
        "Date": dates,
        "Open": [c - 0.5 for c in closes],
        "High": [c + 1.0 for c in closes],
        "Low": [c - 1.0 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i for i in range(len(dates))],
    })


def _av_csv() -> str:
    """Alpha Vantage TIME_SERIES_DAILY_ADJUSTED dialect: lowercase, newest first."""
    dates = pd.bdate_range("2026-04-01", "2026-05-20")
    lines = ["timestamp,open,high,low,close,adjusted_close,volume"]
    for i, d in reversed(list(enumerate(dates))):
        c = 100 + i
        lines.append(f"{d.date()},{c - 0.5},{c + 1.0},{c - 1.0},{c},{c},{1_000_000 + i}")
    return "\n".join(lines) + "\n"


@pytest.fixture(autouse=True)
def _routing_unavailable(monkeypatch):
    # Hermetic unit tests: the routed vendor path must never hit the network.
    # Tests that exercise the routed path override this stub.
    def _raise(*args, **kwargs):
        raise RuntimeError("vendor routing disabled in unit tests")

    monkeypatch.setattr(validator, "route_to_vendor", _raise)


@pytest.mark.unit
class TestVerifiedSnapshot:
    def test_excludes_future_rows(self, monkeypatch):
        data = pd.concat([
            _sample_ohlcv(),
            pd.DataFrame({"Date": [pd.Timestamp("2026-06-01")], "Open": [999.0],
                          "High": [999.0], "Low": [999.0], "Close": [999.0], "Volume": [999]}),
        ], ignore_index=True)
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: data)

        snap = validator.build_verified_market_snapshot("COF", "2026-05-13")
        assert "Verified market data snapshot for COF" in snap
        assert "Requested analysis date: 2026-05-13" in snap
        assert "Latest trading row used: 2026-05-13" in snap
        assert "999.00" not in snap          # future row excluded
        assert "boll_lb" in snap             # indicators present

    def test_uses_previous_trading_day_when_date_is_weekend(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: _sample_ohlcv())
        # 2026-05-16 is a Saturday; latest row should be Fri 2026-05-15
        snap = validator.build_verified_market_snapshot("COF", "2026-05-16")
        assert "Latest trading row used: 2026-05-15" in snap
        assert "Recent verified closes" in snap

    def test_raises_when_no_rows_on_or_before_date(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: _sample_ohlcv())
        with pytest.raises(ValueError):
            validator.build_verified_market_snapshot("COF", "2020-01-01")

    def test_raises_on_empty_data(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: pd.DataFrame())
        with pytest.raises(ValueError):
            validator.build_verified_market_snapshot("COF", "2026-05-13")

    def test_look_back_window_capped_at_30(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: _sample_ohlcv())
        snap = validator.build_verified_market_snapshot("COF", "2026-05-20", look_back_days=999)
        # last-N closes table has at most 30 data rows
        close_rows = [ln for ln in snap.splitlines() if ln.startswith("| 2026-")]
        assert 0 < len(close_rows) <= 30


@pytest.mark.unit
class TestRoutedVendorPath:
    def test_snapshot_uses_routed_vendor_csv(self, monkeypatch):
        # The snapshot must honor the configured data_vendors chain: an
        # Alpha-Vantage CSV (native symbol, lowercase columns, newest-first)
        # feeds the snapshot without ever touching the yfinance loader.
        calls = {}

        def fake_route(method, symbol, start, end):
            calls["args"] = (method, symbol)
            return _av_csv()

        def no_fallback(*args, **kwargs):
            raise AssertionError("must not fall back to the yfinance loader")

        monkeypatch.setattr(validator, "route_to_vendor", fake_route)
        monkeypatch.setattr(validator, "load_ohlcv", no_fallback)

        snap = validator.build_verified_market_snapshot("ADS.DEX", "2026-05-13")
        assert calls["args"] == ("get_stock_data", "ADS.DEX")
        assert "Verified market data snapshot for ADS.DEX" in snap
        assert "Latest trading row used: 2026-05-13" in snap
        assert "boll_lb" in snap

    def test_no_data_sentinel_falls_back_to_loader(self, monkeypatch):
        # The router's NO_DATA sentinel is prose, not data — fall back.
        monkeypatch.setattr(
            validator, "route_to_vendor", lambda *a, **k: "NO_DATA_AVAILABLE: nope"
        )
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: _sample_ohlcv())
        snap = validator.build_verified_market_snapshot("COF", "2026-05-13")
        assert "Latest trading row used: 2026-05-13" in snap

    def test_unparsable_vendor_payload_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            validator, "route_to_vendor", lambda *a, **k: {"not": "a csv string"}
        )
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: _sample_ohlcv())
        snap = validator.build_verified_market_snapshot("COF", "2026-05-13")
        assert "Latest trading row used: 2026-05-13" in snap


@pytest.mark.unit
class TestTool:
    def test_tool_delegates_to_builder(self, monkeypatch):
        from tradingagents.agents.utils.market_data_validation_tools import (
            get_verified_market_snapshot,
        )
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d: _sample_ohlcv())
        out = get_verified_market_snapshot.invoke(
            {"symbol": "COF", "curr_date": "2026-05-20"}
        )
        assert "Verified market data snapshot for COF" in out

    def test_tool_degrades_instead_of_aborting_the_run(self, monkeypatch):
        # No vendor can serve the symbol: the tool must return an explicit
        # sentinel instead of raising (a raised NoMarketDataError previously
        # killed the whole analysis run).
        from tradingagents.agents.utils import market_data_validation_tools as tools

        def boom(*args, **kwargs):
            raise RuntimeError("no vendor could serve OHLCV")

        monkeypatch.setattr(tools, "build_verified_market_snapshot", boom)
        out = tools.get_verified_market_snapshot.invoke(
            {"symbol": "ADS.DEX", "curr_date": "2026-05-20"}
        )
        assert "VERIFIED_SNAPSHOT_UNAVAILABLE" in out
        assert "ADS.DEX" in out
