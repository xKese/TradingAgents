from unittest.mock import MagicMock

import pytest

from tradingagents.portfolio_review import (
    PortfolioAction,
    PortfolioReview,
    build_portfolio_review,
    normalize_portfolio_review,
    write_portfolio_review,
)


def _snapshot():
    return {
        "base_currency": "AUD",
        "net_liquidation": 7436.9,
        "cash": 2582.97,
        "position_fetch_complete": True,
        "weights_reconciled_to_base_nav": True,
        "positions": [
            {
                "symbol": "OUST",
                "quantity": 10,
                "market_value": 700,
                "portfolio_weight_pct": 9.4,
                "currency": "USD",
            },
            {
                "symbol": "HIMS",
                "quantity": 10,
                "market_value": 350,
                "portfolio_weight_pct": 4.7,
                "currency": "USD",
            },
        ],
    }


def _review():
    return PortfolioReview(
        executive_assessment="Avoid adding to the largest position.",
        conflicts_and_overrides=["OUST is already the largest holding."],
        risk_triggers=["Review OUST above 10% of NAV."],
        data_quality_warnings=[],
        actions=[
            PortfolioAction(
                ticker="OUST",
                action="Hold existing",
                priority="High",
                current_shares=10,
                proposed_shares=10,
                share_change=0,
                current_weight_pct=9.4,
                proposed_weight_pct=9.4,
                rationale="Standalone Buy conflicts with concentration.",
            )
        ],
    )


def test_review_prompt_flags_buy_conflict_for_largest_holding():
    captured = {}
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or _review()
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    result = build_portfolio_review(
        _snapshot(),
        [{"ticker": "OUST", "status": "success", "signal": "Buy"}],
        {"OUST": "Rating: Buy"},
        llm,
    )

    assert result.actions[0].ticker == "OUST"
    assert "OUST (largest holding)" in captured["prompt"]
    assert "Soft concentration warning: 10% of NAV" in captured["prompt"]
    assert "advisory" in captured["prompt"].lower()


def test_review_prompt_lists_failed_tickers_as_coverage_warnings():
    captured = {}
    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or _review()
    )
    build_portfolio_review(
        _snapshot(),
        [{"ticker": "CCXI", "status": "failed", "error": "timeout"}],
        {},
        llm,
    )
    assert "CCXI: timeout" in captured["prompt"]


def test_review_prompt_preserves_currency_labels_without_inventing_conversion():
    snapshot = _snapshot()
    snapshot["positions"][1]["currency"] = "EUR"
    captured = {}
    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or _review()
    )
    build_portfolio_review(snapshot, [], {}, llm)
    assert '"currency": "USD"' in captured["prompt"]
    assert '"currency": "EUR"' in captured["prompt"]
    assert "Never sum values across currencies" in captured["prompt"]


def test_writers_create_deterministic_markdown_and_csv(tmp_path):
    markdown_path, csv_path = write_portfolio_review(_review(), tmp_path)

    markdown = markdown_path.read_text(encoding="utf-8")
    csv_text = csv_path.read_text(encoding="utf-8")
    assert markdown_path.name == "portfolio_review.md"
    assert csv_path.name == "portfolio_actions.csv"
    assert "## Prioritized Actions" in markdown
    assert "OUST" in markdown
    assert "current_shares" in csv_text
    assert "Hold existing" in csv_text


def _empty_review(**overrides):
    values = {
        "executive_assessment": "Review account-aware actions.",
        "conflicts_and_overrides": [],
        "risk_triggers": [],
        "data_quality_warnings": [],
        "actions": [],
    }
    values.update(overrides)
    return PortfolioReview(**values)


def test_missing_oust_action_is_derived_from_underweight_decision():
    decision = """**Rating**: Underweight

**Executive Summary**: Sell 2 of 10 OUST shares, reducing weight from 9.4% to 7.52%."""

    normalized = normalize_portfolio_review(
        _empty_review(), _snapshot(), {"OUST": decision}
    )

    action = normalized.actions[0]
    assert (action.action, action.current_shares, action.proposed_shares) == (
        "Trim",
        10,
        8,
    )
    assert action.share_change == -2
    assert action.current_weight_pct == 9.4
    assert action.proposed_weight_pct == pytest.approx(7.52)


def test_missing_rklb_hold_action_keeps_current_shares():
    snapshot = _snapshot()
    snapshot["positions"].append(
        {
            "symbol": "RKLB",
            "quantity": 2,
            "market_value": 166,
            "portfolio_weight_pct": 3.23,
            "currency": "USD",
        }
    )
    decision = """**Rating**: Hold

**Executive Summary**: Maintain the current 2-share RKLB position."""

    normalized = normalize_portfolio_review(
        _empty_review(), snapshot, {"RKLB": decision}
    )

    action = normalized.actions[0]
    assert action.action == "Hold existing"
    assert action.current_shares == 2
    assert action.proposed_shares == 2
    assert action.share_change == 0


def test_existing_model_action_is_preserved():
    existing = _review().actions[0]
    normalized = normalize_portfolio_review(
        _review(), _snapshot(), {"OUST": "**Rating**: Sell"}
    )
    assert normalized.actions == [existing]


def test_below_threshold_exceeds_claim_is_removed():
    review = _empty_review(
        conflicts_and_overrides=["OUST at 9.4% exceeds the 10% threshold."]
    )

    normalized = normalize_portfolio_review(review, _snapshot(), {})

    assert normalized.conflicts_and_overrides == []


def test_false_fx_warning_removed_but_fx_risk_preserved():
    review = _empty_review(
        data_quality_warnings=[
            "Weights cannot be reconciled without an FX conversion.",
            "AUD/USD currency exposure remains unhedged.",
        ]
    )

    normalized = normalize_portfolio_review(review, _snapshot(), {})

    assert normalized.data_quality_warnings == [
        "AUD/USD currency exposure remains unhedged."
    ]
