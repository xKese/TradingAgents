import sys
import types
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from ops.universe.filters import apply_deny_list, apply_liquidity_filter


def test_deny_list_strips_excluded_symbols():
    result = apply_deny_list(["AAPL", "SPOT", "MSFT", "TQQQ"], frozenset({"SPOT", "TQQQ"}))
    assert result == ["AAPL", "MSFT"]


def test_liquidity_filter_keeps_above_both_floors():
    metrics = {
        "AAPL": (Decimal("200"), Decimal("60000000")),  # passes
        "PENNY": (Decimal("2"),  Decimal("60000000")),  # price floor
        "ILLIQ": (Decimal("200"), Decimal("10000000")),  # adv floor
        "ZZZZ": None,                                    # no data
    }
    result = apply_liquidity_filter(
        ["AAPL", "PENNY", "ILLIQ", "ZZZZ"],
        min_adv=Decimal("50000000"),
        min_price=Decimal("5"),
        fetch_metrics=lambda s: metrics[s],
    )
    syms = [r[0] for r in result]
    assert syms == ["AAPL"]


def _fake_yf_module(monkeypatch, hist_data):
    """Replace ops.universe.filters.yf with a fake module exposing
    Ticker(symbol).history(...) → DataFrame-like with `.empty`,
    indexable by 'Close' and 'Volume' returning a list-like with .tolist()
    and .iloc[-1]."""
    import pandas as pd
    fake_hist = pd.DataFrame(hist_data)
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = fake_hist
    fake_yf = types.SimpleNamespace(Ticker=MagicMock(return_value=fake_ticker))
    monkeypatch.setattr("ops.universe.filters.yf", fake_yf)
    return fake_hist


def test_fetch_price_and_adv_uses_decimal_arithmetic(monkeypatch):
    from ops.universe.filters import fetch_price_and_adv_from_yfinance
    _fake_yf_module(monkeypatch, {
        "Close":  [100.0, 110.0, 120.0],
        "Volume": [1_000_000.0, 1_000_000.0, 1_000_000.0],
    })
    result = fetch_price_and_adv_from_yfinance("AAPL")
    assert result is not None
    last_price, adv = result
    # All arithmetic should be in Decimal land — verify types
    assert isinstance(last_price, Decimal)
    assert isinstance(adv, Decimal)
    # Mean of (100M, 110M, 120M) = 110M
    assert adv == Decimal("110000000")
    assert last_price == Decimal("120.0")


def test_fetch_returns_none_on_empty_history(monkeypatch):
    from ops.universe.filters import fetch_price_and_adv_from_yfinance
    _fake_yf_module(monkeypatch, {"Close": [], "Volume": []})
    assert fetch_price_and_adv_from_yfinance("AAPL") is None


def test_fetch_normalises_nan_values_via_safe_decimal(monkeypatch):
    """NaN values in the historical data must NOT produce Decimal('NaN')
    (which would silently pass any comparison in the liquidity filter)."""
    import math
    from ops.universe.filters import fetch_price_and_adv_from_yfinance
    _fake_yf_module(monkeypatch, {
        "Close":  [100.0, math.nan, 120.0],
        "Volume": [1_000_000.0, 1_000_000.0, math.nan],
    })
    result = fetch_price_and_adv_from_yfinance("AAPL")
    assert result is not None
    last_price, adv = result
    # last_price is the last close (120)
    assert last_price == Decimal("120.0")
    # NaN entries are coerced to 0; per-bar dollar volumes are:
    #   100 * 1M + 0 * 1M + 120 * 0 = 100_000_000
    # mean over 3 bars = 33_333_333.33...
    assert adv == Decimal("100000000") / Decimal("3")
    # And crucially: adv is a real number, not NaN
    assert not adv.is_nan()


def test_fetch_returns_none_and_logs_on_yfinance_exception(monkeypatch, capsys):
    """A yfinance boundary exception should log to stderr and return None,
    never propagate a raw KeyError/AttributeError to the caller."""
    from ops.universe.filters import fetch_price_and_adv_from_yfinance

    class BoomTicker:
        def history(self, *a, **k):
            raise KeyError("['Adj Close']")

    fake_yf = types.SimpleNamespace(Ticker=MagicMock(return_value=BoomTicker()))
    monkeypatch.setattr("ops.universe.filters.yf", fake_yf)

    result = fetch_price_and_adv_from_yfinance("AAPL")
    assert result is None
    err = capsys.readouterr().err
    assert "AAPL" in err
    assert "KeyError" in err


def test_fetch_returns_none_and_logs_on_missing_columns(monkeypatch, capsys):
    """If the returned DataFrame lacks Close/Volume columns, log + skip."""
    from ops.universe.filters import fetch_price_and_adv_from_yfinance
    _fake_yf_module(monkeypatch, {"Open": [100.0], "High": [110.0]})
    result = fetch_price_and_adv_from_yfinance("AAPL")
    assert result is None
    err = capsys.readouterr().err
    assert "AAPL" in err
