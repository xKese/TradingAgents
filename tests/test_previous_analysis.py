"""Tests for the previous-analysis context (archived run.json → decision layer).

Covers the archive helper, the previous_analysis_enabled toggle in
prepare_run_context, the state wiring, and the prompt injection into the
Research Manager and Portfolio Manager (analysts never see this context).
"""

import json
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    ResearchPlan,
)
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.run_archive import (
    build_previous_analysis_context,
    find_latest_run_sidecar,
)

FINAL_DECISION_MD = (
    "**Rating**: Buy\n\n"
    "**Executive Summary**: AI capex cycle intact; enter gradually.\n\n"
    "**Investment Thesis**: Data-center demand outpaces supply."
)


def _write_sidecar(root, dirname, payload):
    run_dir = root / "reports" / dirname
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _sidecar(ticker="AAPL", date="2026-04-20", decision="Buy",
             final_decision=FINAL_DECISION_MD):
    return {
        "schema_version": 1,
        "ticker": ticker,
        "analysis_date": date,
        "decision": decision,
        "reports": {"final_trade_decision": final_decision},
    }


# ---------------------------------------------------------------------------
# Archive helper
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFindLatestRunSidecar:
    def test_newest_matching_ticker_wins(self, tmp_path):
        _write_sidecar(tmp_path, "AAPL_20260101_000000", _sidecar(date="2026-01-01"))
        _write_sidecar(tmp_path, "AAPL_20260315_000000", _sidecar(date="2026-03-15"))
        _write_sidecar(tmp_path, "MSFT_20260401_000000", _sidecar(ticker="MSFT"))
        _write_sidecar(tmp_path, "AAPL_20260420_000000", _sidecar(date="2026-04-20"))
        data = find_latest_run_sidecar("AAPL", tmp_path)
        assert data["analysis_date"] == "2026-04-20"

    def test_corrupted_and_non_dict_sidecars_skipped(self, tmp_path):
        _write_sidecar(tmp_path, "AAPL_20260101_000000", _sidecar(date="2026-01-01"))
        run_dir = tmp_path / "reports" / "AAPL_20260601_000000"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text("{not json", encoding="utf-8")
        _write_sidecar(tmp_path, "AAPL_20260701_000000", ["not", "a", "dict"])
        data = find_latest_run_sidecar("AAPL", tmp_path)
        assert data["analysis_date"] == "2026-01-01"

    def test_no_reports_dir_returns_none(self, tmp_path):
        assert find_latest_run_sidecar("AAPL", tmp_path) is None
        assert find_latest_run_sidecar("AAPL", "") is None

    def test_ticker_mismatch_returns_none(self, tmp_path):
        _write_sidecar(tmp_path, "MSFT_20260401_000000", _sidecar(ticker="MSFT"))
        assert find_latest_run_sidecar("AAPL", tmp_path) is None


@pytest.mark.unit
class TestBuildPreviousAnalysisContext:
    def test_contains_date_rating_summary(self, tmp_path):
        _write_sidecar(tmp_path, "AAPL_20260420_000000", _sidecar())
        ctx = build_previous_analysis_context("AAPL", tmp_path)
        assert "Most recent previous analysis of AAPL" in ctx
        assert "2026-04-20" in ctx
        assert "Buy" in ctx
        assert "AI capex cycle intact" in ctx
        # Only the summary, not the following section
        assert "Investment Thesis" not in ctx

    def test_empty_when_no_archive(self, tmp_path):
        assert build_previous_analysis_context("AAPL", tmp_path) == ""

    def test_falls_back_to_raw_text_without_marker(self, tmp_path):
        _write_sidecar(
            tmp_path, "AAPL_20260420_000000",
            _sidecar(final_decision="Plain prose decision without headers."),
        )
        ctx = build_previous_analysis_context("AAPL", tmp_path)
        assert "Plain prose decision without headers." in ctx

    def test_summary_truncated(self, tmp_path):
        long_summary = "x" * 2000
        _write_sidecar(
            tmp_path, "AAPL_20260420_000000",
            _sidecar(final_decision=f"**Executive Summary**: {long_summary}"),
        )
        ctx = build_previous_analysis_context("AAPL", tmp_path)
        summary_line = next(
            line for line in ctx.splitlines() if line.startswith("- Executive summary:")
        )
        assert summary_line.endswith("…")
        assert len(summary_line) < 800

    def test_missing_reports_section_still_builds(self, tmp_path):
        payload = _sidecar()
        del payload["reports"]
        _write_sidecar(tmp_path, "AAPL_20260420_000000", payload)
        ctx = build_previous_analysis_context("AAPL", tmp_path)
        assert "- Date: 2026-04-20" in ctx
        assert "- Final rating: Buy" in ctx
        assert "Executive summary" not in ctx


# ---------------------------------------------------------------------------
# prepare_run_context toggle
# ---------------------------------------------------------------------------

def _graph_with_config(config: dict):
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = config
    graph.memory_log = MagicMock()
    graph.memory_log.get_past_context.return_value = ""
    graph._resolve_pending_entries = MagicMock()
    graph.resolve_instrument_context = MagicMock(return_value="INSTRUMENT")
    return graph


@pytest.mark.unit
class TestPreviousAnalysisToggle:
    def test_disabled_returns_empty(self, tmp_path):
        _write_sidecar(tmp_path, "AAPL_20260420_000000", _sidecar())
        graph = _graph_with_config({
            "memory_enabled": False,
            "previous_analysis_enabled": False,
            "results_dir": str(tmp_path),
        })
        _, _, previous = graph.prepare_run_context("AAPL")
        assert previous == ""

    def test_enabled_returns_context(self, tmp_path):
        _write_sidecar(tmp_path, "AAPL_20260420_000000", _sidecar())
        graph = _graph_with_config({
            "memory_enabled": False,
            "previous_analysis_enabled": True,
            "results_dir": str(tmp_path),
        })
        _, _, previous = graph.prepare_run_context("AAPL")
        assert "Most recent previous analysis of AAPL" in previous

    def test_missing_key_defaults_to_enabled(self, tmp_path):
        _write_sidecar(tmp_path, "AAPL_20260420_000000", _sidecar())
        graph = _graph_with_config({
            "memory_enabled": False,
            "results_dir": str(tmp_path),
        })
        _, _, previous = graph.prepare_run_context("AAPL")
        assert previous != ""


# ---------------------------------------------------------------------------
# State wiring
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestStateWiring:
    def test_defaults_to_empty(self):
        state = Propagator().create_initial_state("NVDA", "2026-01-10")
        assert state["previous_analysis_context"] == ""

    def test_round_trips(self):
        state = Propagator().create_initial_state(
            "NVDA", "2026-01-10", previous_analysis_context="PREV"
        )
        assert state["previous_analysis_context"] == "PREV"


# ---------------------------------------------------------------------------
# Prompt injection (managers only)
# ---------------------------------------------------------------------------

PREV_CTX = (
    "Most recent previous analysis of NVDA:\n"
    "- Date: 2026-04-20\n"
    "- Final rating: Buy\n"
    "- Executive summary: AI capex cycle intact."
)


def _structured_llm(captured: dict, result):
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or result
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


def _pm_state(previous_analysis_context=""):
    return {
        "company_of_interest": "NVDA",
        "past_context": "",
        "previous_analysis_context": previous_analysis_context,
        "risk_debate_state": {
            "history": "Risk debate history.",
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "judge_decision": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "count": 1,
        },
        "investment_plan": "Research plan.",
        "trader_investment_plan": "Trader plan.",
    }


def _rm_state(previous_analysis_context=""):
    return {
        "company_of_interest": "NVDA",
        "previous_analysis_context": previous_analysis_context,
        "investment_debate_state": {
            "history": "Bull vs bear debate.",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "judge_decision": "",
            "count": 1,
        },
    }


@pytest.mark.unit
class TestManagerPromptInjection:
    def _pm_llm(self, captured):
        return _structured_llm(captured, PortfolioDecision(
            rating=PortfolioRating.HOLD,
            executive_summary="Hold.",
            investment_thesis="Balanced.",
        ))

    def _rm_llm(self, captured):
        return _structured_llm(captured, ResearchPlan(
            recommendation=PortfolioRating.HOLD,
            rationale="Balanced debate.",
            strategic_actions="Wait.",
        ))

    def test_pm_prompt_includes_previous_analysis(self):
        captured = {}
        node = create_portfolio_manager(self._pm_llm(captured))
        node(_pm_state(previous_analysis_context=PREV_CTX))
        assert "Previous analysis (reference only)" in captured["prompt"]
        assert "2026-04-20" in captured["prompt"]
        assert "Do not anchor" in captured["prompt"]

    def test_pm_prompt_omits_section_when_empty(self):
        captured = {}
        node = create_portfolio_manager(self._pm_llm(captured))
        node(_pm_state())
        assert "Previous analysis" not in captured["prompt"]

    def test_rm_prompt_includes_previous_analysis(self):
        captured = {}
        node = create_research_manager(self._rm_llm(captured))
        node(_rm_state(previous_analysis_context=PREV_CTX))
        assert "Previous analysis (reference only)" in captured["prompt"]
        assert "2026-04-20" in captured["prompt"]
        assert "Do not anchor" in captured["prompt"]

    def test_rm_prompt_omits_section_when_empty(self):
        captured = {}
        node = create_research_manager(self._rm_llm(captured))
        node(_rm_state())
        assert "Previous analysis" not in captured["prompt"]
