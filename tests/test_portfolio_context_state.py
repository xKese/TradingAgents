import inspect

from tradingagents.graph.propagation import Propagator
from tradingagents.graph.trading_graph import TradingAgentsGraph


def test_initial_state_defaults_to_empty_portfolio_context():
    state = Propagator().create_initial_state("OUST", "2026-07-09")
    assert state["portfolio_context"] == {}


def test_initial_state_preserves_frozen_portfolio_context():
    snapshot = {"base_currency": "AUD", "positions": [{"symbol": "OUST"}]}
    state = Propagator().create_initial_state(
        "OUST", "2026-07-09", portfolio_context=snapshot
    )
    assert state["portfolio_context"] is snapshot


def test_full_graph_public_entrypoint_accepts_portfolio_context():
    signature = inspect.signature(TradingAgentsGraph.propagate)
    assert signature.parameters["portfolio_context"].default is None


def test_research_only_entrypoint_remains_portfolio_blind():
    signature = inspect.signature(TradingAgentsGraph.propagate_analysts)
    assert "portfolio_context" not in signature.parameters
