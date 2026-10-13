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


def _factor_context_v2():
    return {
        "source": "multi_factor",
        "as_of": "2026-07-23",
        "scoring_version": "v2",
        "composite_v2": {
            "score": 83.4,
            "pct": 0.834,
            "z": 1.0123,
            "classification": "B+",
            "zone": "KANDIDAT",
            "zone_thresholds": {"entry_pct": 0.8, "exit_pct": 0.667},
            "data_coverage": 0.95,
            "segment": "nonfinancial",
            "factors": {
                "value": {"z": 0.4, "coverage": 1.0, "weight_strategic": 0.3},
                "quality": {"z": 1.2, "coverage": 0.75, "weight_strategic": 0.3},
                "momentum": {"z": 0.9, "coverage": 1.0, "weight_strategic": 0.25},
                "investment": {"coverage": 0.0, "weight_strategic": 0.15},
            },
            "indicators": {
                "ev_ebitda": {
                    "factor": "value", "direction": -1, "z": -0.4, "value": 8.1,
                    "neut_level": "industry_group",
                },
                "fcf_yield": {"factor": "value", "direction": 1, "z": 1.2, "value": 0.065},
                "roic": {"factor": "quality", "direction": 1, "z": 1.0, "value": 0.18},
            },
            "filter": {"pass": False, "reasons": ["liquidity", "coverage"]},
            "trend_warning": True,
        },
        "piotroski": 7,
        "altman_z": 4.2,
        "signals": {"sma_signal": "Golden Cross"},
        "identity": {"name": "Apple Inc."},
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
def test_build_run_passes_composite_v2_block_through():
    spec = run_config.build_run(
        {**_run_payload(), "factor_context": _factor_context_v2()}
    )
    fc = spec["factor_context"]
    assert fc["scoring_version"] == "v2"
    assert fc["composite_v2"]["score"] == 83.4
    assert fc["composite_v2"]["zone"] == "KANDIDAT"
    assert fc["composite_v2"]["indicators"]["ev_ebitda"]["z"] == -0.4
    assert fc["composite_v2"]["filter"]["reasons"] == ["liquidity", "coverage"]
    # The v2 payload carries no v1 score fields — and none are invented.
    assert "total_score" not in fc and "factor_scores" not in fc
    # A non-dict block is dropped like a malformed signals/identity dict.
    bad = run_config._validate_factor_context({"composite_v2": "83.4", "piotroski": 7})
    assert "composite_v2" not in bad and bad["piotroski"] == 7


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
def test_format_factor_context_renders_composite_v2():
    text = format_factor_context(_factor_context_v2())
    assert "Quantitative multi-factor pre-rating" in text
    assert "(scoring v2)" in text
    assert "composite score 83.4/100" in text
    assert "composite z +1.01" in text
    assert "classification B+" in text
    assert "zone KANDIDAT (buy candidate" in text
    assert "entry >= 0.800, exit < 0.667" in text
    assert "segment non-financial, data coverage 0.95" in text
    # Factor z-scores with weight/coverage; a factor without z is marked n/a.
    assert "Quality +1.20 (weight 0.30, coverage 0.75)" in text
    assert "Investment n/a" in text
    # Indicators grouped by factor with direction and neutralisation level.
    assert "ev_ebitda (z -0.40, raw 8.1000, lower is better, vs industry_group)" in text
    assert "Value: ev_ebitda" in text and "Quality: roic" in text
    assert "Universe filter: FAILED (liquidity, coverage)" in text
    assert "Trend warning: death cross" in text
    # Neutral fields and the prior framing are shared with the v1 block.
    assert "Piotroski F-Score 7/9" in text
    assert "Golden Cross" in text
    assert "NOT ground truth" in text
    # No v1 wording leaks into a v2 context.
    assert "total score" not in text
    assert "Factor scores (0-100)" not in text
    assert "Model recommendation" not in text


@pytest.mark.unit
def test_format_factor_context_v2_passed_filter_minimal():
    text = format_factor_context(
        {"composite_v2": {"score": 55.0, "zone": "HALTEN", "filter": {"pass": True}}}
    )
    assert "composite score 55.0/100" in text
    assert "zone HALTEN (hold" in text
    assert "Universe filter: passed." in text
    assert "Factor z-scores" not in text
    assert "Trend warning" not in text


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
            return "", {}, ""

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
