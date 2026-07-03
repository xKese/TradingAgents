"""Alpha Vantage request hardening.

Regressions for #990 (no request timeout -> can hang) and #991 (invalid-key
responses mislabeled as rate limits and silently treated as transient).
"""
import pytest

import tradingagents.dataflows.alpha_vantage_common as av


class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def _patched_get(body, capture=None):
    def fake_get(url, params=None, **kwargs):
        if capture is not None:
            capture.update(kwargs)
        return _FakeResponse(body)
    return fake_get


@pytest.mark.unit
def test_request_passes_timeout(monkeypatch):
    captured = {}
    monkeypatch.setattr(av.requests, "get", _patched_get("Date,Close\n2025-01-02,1.0", captured))
    av._make_api_request("TIME_SERIES_DAILY", {"symbol": "AAPL"})
    assert captured.get("timeout") == av.REQUEST_TIMEOUT  # #990


@pytest.mark.unit
def test_rate_limit_detected(monkeypatch):
    body = '{"Information": "Our standard API rate limit is 25 requests per day. ... your API key ..."}'
    monkeypatch.setattr(av.requests, "get", _patched_get(body))
    with pytest.raises(av.AlphaVantageRateLimitError):
        av._make_api_request("TIME_SERIES_DAILY", {"symbol": "AAPL"})


@pytest.mark.unit
def test_invalid_key_not_mislabeled_as_rate_limit(monkeypatch):
    # AV's invalid-key notice mentions "API key"; it must NOT be treated as a
    # (transient) rate limit, but surface as a real configuration error (#991).
    body = ('{"Information": "the parameter apikey is invalid or missing. '
            'Please claim your free API key on (https://www.alphavantage.co/support/#api-key)."}')
    monkeypatch.setattr(av.requests, "get", _patched_get(body))
    with pytest.raises(av.AlphaVantageNotConfiguredError):
        av._make_api_request("TIME_SERIES_DAILY", {"symbol": "AAPL"})
    with pytest.raises(av.AlphaVantageRateLimitError):  # sanity: rate-limit path still distinct
        monkeypatch.setattr(av.requests, "get", _patched_get('{"Note": "API call frequency is 5 calls per minute."}'))
        av._make_api_request("TIME_SERIES_DAILY", {"symbol": "AAPL"})


@pytest.mark.unit
def test_fundamentals_filter_drops_future_fiscal_periods(monkeypatch):
    # The look-ahead filter must actually run. _make_api_request returns a JSON
    # *string*, so a filter guarded on isinstance(result, dict) never fires and
    # future fiscal periods leak into a historical run. Regression for the #475
    # look-ahead fix, which silently no-op'd on the str return.
    import json

    import tradingagents.dataflows.alpha_vantage_fundamentals as avf

    body = json.dumps({
        "symbol": "AAPL",
        "annualReports": [
            {"fiscalDateEnding": "2025-12-31"},  # after curr_date -> must drop
            {"fiscalDateEnding": "2023-12-31"},  # before          -> must keep
        ],
    })
    monkeypatch.setattr(av.requests, "get", _patched_get(body))
    out = avf.get_balance_sheet("AAPL", curr_date="2024-01-01")
    dates = [r["fiscalDateEnding"] for r in json.loads(out)["annualReports"]]
    assert dates == ["2023-12-31"]
