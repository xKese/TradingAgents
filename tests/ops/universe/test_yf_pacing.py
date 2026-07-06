"""Unit tests for the yfinance pacing/retry choke point (no real sleeping)."""

import pytest

from ops.universe import yf_pacing

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(yf_pacing, "_last_call_at", 0.0)
    yf_pacing.snapshot_and_reset()


def test_retries_transient_failure_then_succeeds():
    sleeps = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise KeyError("['Earnings Date']")
        return "data"

    result = yf_pacing.call_paced(
        flaky, label="earnings", sleep=sleeps.append, monotonic=lambda: 0.0,
    )
    assert result == "data"
    assert calls["n"] == 3
    # Two backoff sleeps happened (5s then 25s); throttle sleeps may interleave.
    assert [s for s in sleeps if s in yf_pacing.BACKOFF_SECONDS] == [5.0, 25.0]
    assert yf_pacing.snapshot_and_reset() == {"earnings": {"ok": 1, "failed": 0}}


def test_exhausted_retries_reraise_and_count_failure():
    def dead():
        raise KeyError("['Earnings Date']")

    with pytest.raises(KeyError):
        yf_pacing.call_paced(dead, label="momentum", sleep=lambda s: None, monotonic=lambda: 0.0)
    assert yf_pacing.snapshot_and_reset() == {"momentum": {"ok": 0, "failed": 1}}


def test_global_min_interval_between_calls():
    sleeps = []
    clock = {"t": 100.0}
    yf_pacing.call_paced(lambda: 1, label="x", sleep=sleeps.append, monotonic=lambda: clock["t"])
    # Second call at the same instant must wait out the interval.
    yf_pacing.call_paced(lambda: 2, label="x", sleep=sleeps.append, monotonic=lambda: clock["t"])
    assert any(0 < s <= yf_pacing.MIN_INTERVAL_SECONDS for s in sleeps)


def test_snapshot_resets():
    yf_pacing.call_paced(lambda: 1, label="adv", sleep=lambda s: None, monotonic=lambda: 0.0)
    assert yf_pacing.snapshot_and_reset() == {"adv": {"ok": 1, "failed": 0}}
    assert yf_pacing.snapshot_and_reset() == {}


def test_earnings_fetcher_survives_one_transient_failure(monkeypatch):
    import pandas as pd

    from ops.universe import earnings

    calls = {"n": 0}

    class FakeTicker:
        def __init__(self, symbol):
            pass

        @property
        def earnings_dates(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyError("['Earnings Date']")
            return pd.DataFrame()  # empty -> fetcher returns None cleanly

    monkeypatch.setattr(earnings.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(yf_pacing, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(yf_pacing, "BACKOFF_SECONDS", (0.0,))
    assert earnings._fetch_from_yfinance("AAPL") is None
    assert calls["n"] == 2  # retried once, then clean empty
