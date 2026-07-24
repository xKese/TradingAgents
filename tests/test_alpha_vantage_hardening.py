"""Alpha Vantage request hardening.

Regressions for #990 (no request timeout -> can hang), #991 (invalid-key
responses mislabeled as rate limits and silently treated as transient), and
#1115 (fundamentals look-ahead filter never ran because the payload is a JSON
string, not a dict).
"""
import json

import pytest

import tradingagents.dataflows.alpha_vantage_common as av
import tradingagents.dataflows.alpha_vantage_fundamentals as avf


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


_FUNDAMENTALS_JSON = json.dumps({
    "symbol": "AAPL",
    "annualReports": [
        {"fiscalDateEnding": "2025-12-31", "totalAssets": "1"},   # future -> must drop
        {"fiscalDateEnding": "2023-12-31", "totalAssets": "2"},   # past   -> must keep
    ],
    "quarterlyReports": [
        {"fiscalDateEnding": "2024-06-30", "totalAssets": "3"},   # future -> must drop
        {"fiscalDateEnding": "2023-09-30", "totalAssets": "4"},   # past   -> must keep
    ],
})


_BURST_BODY = json.dumps({
    "Information": (
        "Burst pattern detected. Please consider spreading out your API "
        "requests more evenly across a 1-minute window and query no more "
        "than 5 requests per second. Please contact support@alphavantage.co "
        "if you are targeting a higher API request volume."
    )
})


def _sequenced_get(bodies, sleeps=None):
    """requests.get-Fake, der die Bodies der Reihe nach liefert."""
    calls = {"n": 0}

    def fake_get(url, params=None, **kwargs):
        body = bodies[min(calls["n"], len(bodies) - 1)]
        calls["n"] += 1
        return _FakeResponse(body)

    return fake_get, calls


@pytest.mark.unit
def test_burst_throttle_is_retried_then_returns_data(monkeypatch):
    # A transient burst notice used to slip through unclassified and reach the
    # CSV parser as data; now it backs off, retries, and returns the real CSV.
    sleeps = []
    monkeypatch.setattr(av.time, "sleep", lambda s: sleeps.append(s))
    fake_get, calls = _sequenced_get([_BURST_BODY, "Date,Close\n2025-01-02,1.0"])
    monkeypatch.setattr(av.requests, "get", fake_get)

    result = av._make_api_request("TIME_SERIES_DAILY", {"symbol": "AAPL"})
    assert result.startswith("Date,Close")
    assert calls["n"] == 2
    assert av._BURST_BACKOFFS[0] in sleeps


@pytest.mark.unit
def test_burst_throttle_persisting_raises_rate_limit(monkeypatch):
    monkeypatch.setattr(av.time, "sleep", lambda s: None)
    fake_get, calls = _sequenced_get([_BURST_BODY])
    monkeypatch.setattr(av.requests, "get", fake_get)

    with pytest.raises(av.AlphaVantageRateLimitError, match="[Bb]urst"):
        av._make_api_request("TIME_SERIES_DAILY", {"symbol": "AAPL"})
    assert calls["n"] == len(av._BURST_BACKOFFS) + 1


@pytest.mark.unit
def test_daily_cap_still_raises_immediately(monkeypatch):
    # The daily cap is not transient — no burst retry may kick in.
    sleeps = []
    monkeypatch.setattr(av.time, "sleep", lambda s: sleeps.append(s))
    body = '{"Information": "Our standard API rate limit is 25 requests per day."}'
    fake_get, calls = _sequenced_get([body])
    monkeypatch.setattr(av.requests, "get", fake_get)

    with pytest.raises(av.AlphaVantageRateLimitError):
        av._make_api_request("TIME_SERIES_DAILY", {"symbol": "AAPL"})
    assert calls["n"] == 1
    assert not [s for s in sleeps if s in av._BURST_BACKOFFS]


@pytest.mark.unit
def test_requests_are_throttled(monkeypatch):
    # Back-to-back requests must be spaced MIN_REQUEST_INTERVAL apart so agent
    # tool loops don't trip the vendor's burst detector in the first place.
    sleeps = []
    monkeypatch.setattr(av.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(av.requests, "get", _patched_get("Date,Close\n2025-01-02,1.0"))

    av._make_api_request("TIME_SERIES_DAILY", {"symbol": "AAPL"})
    av._make_api_request("TIME_SERIES_DAILY", {"symbol": "MSFT"})
    assert any(0 < s <= av.MIN_REQUEST_INTERVAL for s in sleeps)


@pytest.mark.unit
def test_fundamentals_look_ahead_filter_runs_on_json_string(monkeypatch):
    # #1115: the payload arrives as a JSON *string*; the old dict-only guard let
    # future-dated fiscal periods leak into historical runs.
    monkeypatch.setattr(avf, "_make_api_request", lambda fn, params: _FUNDAMENTALS_JSON)
    out = avf.get_balance_sheet("AAPL", curr_date="2024-01-01")
    assert isinstance(out, str)  # callers still receive a str
    parsed = json.loads(out)
    assert [r["fiscalDateEnding"] for r in parsed["annualReports"]] == ["2023-12-31"]
    assert [r["fiscalDateEnding"] for r in parsed["quarterlyReports"]] == ["2023-09-30"]


@pytest.mark.unit
def test_fundamentals_no_curr_date_passes_through(monkeypatch):
    monkeypatch.setattr(avf, "_make_api_request", lambda fn, params: _FUNDAMENTALS_JSON)
    assert avf.get_income_statement("AAPL") == _FUNDAMENTALS_JSON


@pytest.mark.unit
def test_fundamentals_non_json_body_unchanged(monkeypatch):
    monkeypatch.setattr(avf, "_make_api_request", lambda fn, params: "not-json")
    assert avf.get_cashflow("AAPL", curr_date="2024-01-01") == "not-json"


@pytest.mark.unit
class TestParseDate:
    """LLM-supplied date arguments: tolerate time suffixes and free-text noise.

    The tools receive their date arguments from the LLM, which may append a
    time ("2026-04-18 00:00:00") or free text ("2026-04-18 约3 months back)").
    parse_date extracts the embedded ISO date instead of crashing the vendor
    call — only a string with no recognizable date still raises.
    """

    def test_plain_forms_parse(self):
        from datetime import datetime
        assert av.parse_date("2026-04-18") == datetime(2026, 4, 18)
        assert av.parse_date("2026-04-18T09:30:00") == datetime(2026, 4, 18, 9, 30)
        assert av.parse_date("2026-04-18 00:00:00") == datetime(2026, 4, 18)
        assert av.parse_date(datetime(2026, 4, 18)) == datetime(2026, 4, 18)

    def test_free_text_noise_around_date(self):
        from datetime import datetime
        # The exact string from the failed run (Chinese "approx. 3 months back").
        assert av.parse_date("2026-04-18 约3 months back)") == datetime(2026, 4, 18)
        assert av.parse_date("from 2026-04-18") == datetime(2026, 4, 18)
        assert av.parse_date("2026-04-18 (approx)") == datetime(2026, 4, 18)

    def test_pure_garbage_still_raises(self):
        with pytest.raises((ValueError, OverflowError)):
            av.parse_date("next quarter")

    def test_format_datetime_for_api_survives_noise(self):
        assert av.format_datetime_for_api("2026-04-18 约3 months back)") == "20260418T0000"
