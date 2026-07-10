from unittest.mock import MagicMock

import pytest

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
from tradingagents.agents.risk_mgmt.conservative_debator import create_conservative_debator
from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator
from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    TraderAction,
    TraderProposal,
)
from tradingagents.agents.trader.trader import create_trader


def _snapshot():
    return {
        "base_currency": "AUD",
        "net_liquidation": 7436.9,
        "cash": 2582.97,
        "available_funds": 5791.39,
        "position_fetch_complete": True,
        "positions": [
            {
                "symbol": "OUST",
                "contract_symbol": "OUST",
                "quantity": 10.0,
                "currency": "USD",
                "market_price": 47.82,
                "market_value": 478.2,
                "average_cost": 51.653,
                "unrealized_pnl": -38.33,
                "unrealized_return_pct": -7.42,
                "portfolio_weight_pct": 6.43,
            }
        ],
    }


def _state():
    return {
        "company_of_interest": "OUST",
        "instrument_context": "Instrument: OUST",
        "portfolio_context": _snapshot(),
        "investment_plan": "Buy plan",
        "trader_investment_plan": "Buy 5%",
        "past_context": "",
        "market_report": "market",
        "sentiment_report": "sentiment",
        "news_report": "news",
        "fundamentals_report": "fundamentals",
        "risk_debate_state": {
            "history": "",
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "latest_speaker": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "count": 0,
        },
    }


def _structured_llm(result):
    captured = {}
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or result
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm, captured


def _prompt_text(prompt):
    if isinstance(prompt, list):
        return "\n".join(message["content"] for message in prompt)
    return str(prompt)


def test_trader_prompt_contains_owned_position_context():
    llm, captured = _structured_llm(
        TraderProposal(action=TraderAction.HOLD, reasoning="Already owned")
    )
    create_trader(llm)(_state())
    text = _prompt_text(captured["prompt"])
    assert "LIVE PORTFOLIO CONTEXT - READ ONLY" in text
    assert "Owned: yes" in text
    assert "Quantity: 10" in text
    assert "Never say \"initiate\" for an owned ticker" in text


@pytest.mark.parametrize(
    "factory",
    [create_aggressive_debator, create_conservative_debator, create_neutral_debator],
)
def test_each_risk_prompt_contains_portfolio_context(factory):
    captured = {}
    llm = MagicMock()
    llm.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or MagicMock(content="argument")
    )
    factory(llm)(_state())
    text = _prompt_text(captured["prompt"])
    assert "LIVE PORTFOLIO CONTEXT - READ ONLY" in text
    assert "Current portfolio weight: 6.43%" in text


def test_portfolio_manager_prompt_contains_account_aware_rules():
    llm, captured = _structured_llm(
        PortfolioDecision(
            rating=PortfolioRating.HOLD,
            executive_summary="Hold existing.",
            investment_thesis="Position is already concentrated.",
        )
    )
    create_portfolio_manager(llm)(_state())
    text = _prompt_text(captured["prompt"])
    assert "LIVE PORTFOLIO CONTEXT - READ ONLY" in text
    assert "Distinguish Hold existing, Add, Trim, Exit, and Avoid" in text


def test_empty_context_does_not_add_live_portfolio_block():
    state = _state()
    state["portfolio_context"] = {}
    llm, captured = _structured_llm(
        TraderProposal(action=TraderAction.HOLD, reasoning="No account context")
    )
    create_trader(llm)(state)
    assert "LIVE PORTFOLIO CONTEXT" not in _prompt_text(captured["prompt"])
