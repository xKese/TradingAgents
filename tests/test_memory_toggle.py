"""Tests for the memory_enabled toggle.

The toggle gates both sides of cross-run memory on every execution path
(propagate, CLI stream, web worker) through the shared helpers
prepare_run_context / record_decision: with memory off, no past context is
injected, pending-outcome resolution is skipped, and nothing is stored.
"""

import functools
from unittest.mock import MagicMock

import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph


def _graph_with_config(config: dict):
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = config
    graph.memory_log = MagicMock()
    graph.memory_log.get_past_context.return_value = "PAST"
    graph._resolve_pending_entries = MagicMock()
    graph.resolve_instrument_context = MagicMock(return_value="INSTRUMENT")
    return graph


@pytest.mark.unit
class TestPrepareRunContext:
    def test_disabled_skips_read_and_resolution(self):
        graph = _graph_with_config({"memory_enabled": False})
        past, instrument = graph.prepare_run_context("NVDA")
        assert past == ""
        assert instrument == "INSTRUMENT"
        graph.memory_log.get_past_context.assert_not_called()
        graph._resolve_pending_entries.assert_not_called()

    def test_enabled_reads_and_resolves(self):
        graph = _graph_with_config({"memory_enabled": True})
        past, instrument = graph.prepare_run_context("NVDA")
        assert past == "PAST"
        assert instrument == "INSTRUMENT"
        graph.memory_log.get_past_context.assert_called_once_with("NVDA")
        graph._resolve_pending_entries.assert_called_once_with("NVDA")

    def test_missing_key_defaults_to_enabled(self):
        graph = _graph_with_config({})
        past, _ = graph.prepare_run_context("NVDA")
        assert past == "PAST"


@pytest.mark.unit
class TestRecordDecision:
    def test_disabled_skips_store(self):
        graph = _graph_with_config({"memory_enabled": False})
        graph.record_decision("NVDA", "2026-01-10", "Rating: Buy")
        graph.memory_log.store_decision.assert_not_called()

    def test_enabled_stores(self):
        graph = _graph_with_config({"memory_enabled": True})
        graph.record_decision("NVDA", "2026-01-10", "Rating: Buy")
        graph.memory_log.store_decision.assert_called_once_with(
            ticker="NVDA",
            trade_date="2026-01-10",
            final_trade_decision="Rating: Buy",
        )


@pytest.mark.unit
def test_run_graph_honors_toggle():
    """The propagate path (_run_graph) neither injects nor stores when off."""
    fake_state = {"final_trade_decision": "Rating: Buy", "messages": []}
    graph = _graph_with_config({"memory_enabled": False, "checkpoint_enabled": False})
    graph.debug = False
    graph.curr_state = None
    graph.graph = MagicMock()
    graph.graph.invoke.return_value = fake_state
    graph.propagator = MagicMock()
    graph.propagator.get_graph_args.return_value = {}
    graph.signal_processor = MagicMock()
    graph._log_state = MagicMock()
    graph.process_signal = MagicMock(return_value="Buy")
    graph.prepare_run_context = functools.partial(
        TradingAgentsGraph.prepare_run_context, graph
    )
    graph.record_decision = functools.partial(
        TradingAgentsGraph.record_decision, graph
    )
    graph._memory_on = functools.partial(TradingAgentsGraph._memory_on, graph)

    TradingAgentsGraph._run_graph(graph, "NVDA", "2026-01-10")

    graph.memory_log.get_past_context.assert_not_called()
    graph.memory_log.store_decision.assert_not_called()
    # The state was still built with empty past context.
    _, kwargs = graph.propagator.create_initial_state.call_args
    assert kwargs["past_context"] == ""
