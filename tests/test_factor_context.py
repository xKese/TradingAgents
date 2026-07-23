"""External factor pre-rating: validation, prompt formatting, and archiving."""

import json as _json

import pytest
from starlette.testclient import TestClient

from tests.test_webapp import _FakeGraph, _parse_sse, _run_payload
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    format_factor_context,
)
from tradingagents.default_config import DEFAULT_CONFIG
from webapp import run_config, server


def _factor_context():
    return {
        "source": "multi_factor",
        "as_of": "2026-07-23",
        "total_score": 78.2,
        "classification": "B+",
        "factor_scores": {"value": 45.1, "quality": 88.0, "growth": 71.3},
        "filter_ok": "JA",
        "recommendation": "BUY",
        "piotroski": 7,
        "altman_z": 4.2,
        "signals": {"sma_signal": "Golden Cross", "trend_phase": "Bulle (etabliert)"},
        "identity": {"name": "Apple Inc.", "sector": "Information Technology"},
        "source_ticker": "AAPL",
    }


# --------------------------------------------------------------------------- #
# run_config validation
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_build_run_passes_factor_context_through():
    spec = run_config.build_run({**_run_payload(), "factor_context": _factor_context()})
    fc = spec["factor_context"]
    assert fc["total_score"] == 78.2
    assert fc["classification"] == "B+"
    assert fc["factor_scores"]["quality"] == 88.0
    assert fc["signals"]["sma_signal"] == "Golden Cross"


@pytest.mark.unit
def test_build_run_without_factor_context_is_none():
    assert run_config.build_run(_run_payload())["factor_context"] is None


@pytest.mark.unit
def test_factor_context_rejects_non_dict():
    with pytest.raises(run_config.RunRequestError):
        run_config.build_run({**_run_payload(), "factor_context": "78.2"})


@pytest.mark.unit
def test_factor_context_rejects_oversize():
    huge = {"total_score": 50, "signals": {"x": "y" * 10_000}}
    with pytest.raises(run_config.RunRequestError):
        run_config.build_run({**_run_payload(), "factor_context": huge})


@pytest.mark.unit
def test_factor_context_drops_unknown_keys_and_bad_numbers():
    raw = {
        "total_score": "78,2",  # decimal comma is coerced
        "piotroski": "not-a-number",  # dropped, not fatal
        "factor_scores": {"value": "45.1", "quality": None},
        "surprise_key": "ignored",
    }
    fc = run_config._validate_factor_context(raw)
    assert fc["total_score"] == 78.2
    assert "piotroski" not in fc
    assert fc["factor_scores"] == {"value": 45.1}
    assert "surprise_key" not in fc


@pytest.mark.unit
def test_factor_context_empty_dict_becomes_none():
    assert run_config._validate_factor_context({}) is None
    assert run_config._validate_factor_context({"unknown": 1}) is None


# --------------------------------------------------------------------------- #
# Prompt formatting
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_format_factor_context_contains_scores_and_prior_framing():
    text = format_factor_context(_factor_context())
    assert "Quantitative multi-factor pre-rating" in text
    assert "total score 78.2/100" in text
    assert "classification B+" in text
    assert "Quality 88" in text
    assert "Piotroski F-Score 7/9" in text
    assert "Golden Cross" in text
    assert "NOT ground truth" in text


@pytest.mark.unit
def test_format_factor_context_handles_missing_fields():
    text = format_factor_context({"total_score": 55.0})
    assert "total score 55/100" in text
    assert "Factor scores" not in text
    assert "Technical signals" not in text


@pytest.mark.unit
def test_format_factor_context_none_is_empty():
    assert format_factor_context(None) == ""
    assert format_factor_context({}) == ""


@pytest.mark.unit
def test_instrument_context_composes_with_factor_block():
    base = build_instrument_context("AAPL", "stock", {"company_name": "Apple Inc."})
    combined = base + format_factor_context(_factor_context())
    assert combined.startswith(base)
    assert "Quantitative multi-factor pre-rating" in combined


# --------------------------------------------------------------------------- #
# Web worker: pass-through + archive
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_run_archives_factor_context_in_sidecar(monkeypatch, tmp_path):
    captured = {}

    class _CapturingGraph(_FakeGraph):
        def prepare_run_context(self, ticker, asset_type, factor_context=None):
            captured["factor_context"] = factor_context
            return "", {}

    monkeypatch.setattr(server, "TradingAgentsGraph", _CapturingGraph)
    monkeypatch.setitem(DEFAULT_CONFIG, "results_dir", str(tmp_path))

    client = TestClient(server.app)
    resp = client.post(
        "/api/run", json={**_run_payload(), "factor_context": _factor_context()}
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert "error" not in [e[0] for e in events]

    # The worker saw the validated pre-rating ...
    assert captured["factor_context"]["total_score"] == 78.2

    # ... and the archive records it for auditability.
    sidecars = list((tmp_path / "reports").glob("AAPL_*/run.json"))
    assert len(sidecars) == 1
    data = _json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert data["factor_context"]["classification"] == "B+"


@pytest.mark.unit
def test_run_without_factor_context_omits_sidecar_key(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "TradingAgentsGraph", _FakeGraph)
    monkeypatch.setitem(DEFAULT_CONFIG, "results_dir", str(tmp_path))

    client = TestClient(server.app)
    resp = client.post("/api/run", json=_run_payload())
    assert resp.status_code == 200

    sidecars = list((tmp_path / "reports").glob("AAPL_*/run.json"))
    assert len(sidecars) == 1
    data = _json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert "factor_context" not in data
