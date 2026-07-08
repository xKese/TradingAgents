"""Tests for the evaluation harness.

These tests validate the metrics computation (consistency, directional
accuracy, score distribution) without making LLM calls.
"""

import pytest

from tradingagents.evaluation.benchmark import (
    RATINGS_5_TIER,
    _compute_consistency,
    _compute_directional_metrics,
    _score_distribution,
    _score_from_rating,
)


class TestScoreMapping:
    def test_all_five_tiers_map_to_expected_scores(self):
        assert _score_from_rating("Buy") == 2
        assert _score_from_rating("Overweight") == 1
        assert _score_from_rating("Hold") == 0
        assert _score_from_rating("Underweight") == -1
        assert _score_from_rating("Sell") == -2

    def test_case_insensitive(self):
        assert _score_from_rating("buy") == 2
        assert _score_from_rating("BUY") == 2
        assert _score_from_rating("sElL") == -2

    def test_unknown_rating_defaults_to_zero(self):
        assert _score_from_rating("garbage") == 0
        assert _score_from_rating("") == 0


class TestConsistency:
    def test_all_same_rating_perfect_consistency(self):
        runs = [
            {"ticker": "AAPL", "date": "2024-01-01", "run": 0, "score": 2, "rating": "Buy"},
            {"ticker": "AAPL", "date": "2024-01-01", "run": 1, "score": 2, "rating": "Buy"},
            {"ticker": "AAPL", "date": "2024-01-01", "run": 2, "score": 2, "rating": "Buy"},
        ]
        result = _compute_consistency(runs)
        assert result["n_runs"] == 3
        assert result["mean_score"] == 2.0
        assert result["stdev"] == 0.0
        assert result["mean_absolute_deviation"] == 0.0
        assert result["all_same_rating"] is True

    def test_different_ratings(self):
        runs = [
            {"ticker": "AAPL", "date": "2024-01-01", "run": 0, "score": 2, "rating": "Buy"},
            {"ticker": "AAPL", "date": "2024-01-01", "run": 1, "score": 0, "rating": "Hold"},
        ]
        result = _compute_consistency(runs)
        assert result["mean_absolute_deviation"] == 1.0
        assert result["all_same_rating"] is False

    def test_single_run_returns_none_deviation(self):
        runs = [{"ticker": "AAPL", "date": "2024-01-01", "run": 0, "score": 1, "rating": "Overweight"}]
        result = _compute_consistency(runs)
        assert result["n_runs"] == 1
        assert result["mean_score"] == 1.0
        assert result["stdev"] is None
        assert result["mean_absolute_deviation"] is None
        assert result["all_same_rating"] is None

    def test_some_failed_runs(self):
        runs = [
            {"ticker": "AAPL", "date": "2024-01-01", "run": 0, "score": 2, "rating": "Buy"},
            {"ticker": "AAPL", "date": "2024-01-01", "run": 1, "score": None, "rating": None, "error": "timeout"},
        ]
        result = _compute_consistency(runs)
        assert result["n_runs"] == 1
        assert result["mean_score"] == 2.0


class TestDirectionalMetrics:
    def test_perfect_accuracy(self):
        results = [
            {"ticker": "AAPL", "score": 2, "forward_returns": {"ret_20d": 0.05, "ret_60d": 0.10}},
            {"ticker": "MSFT", "score": -1, "forward_returns": {"ret_20d": -0.03, "ret_60d": -0.05}},
        ]
        metrics = _compute_directional_metrics(results)
        assert metrics["hit_rate_20d"] == 1.0
        assert metrics["hit_rate_60d"] == 1.0
        assert metrics["false_positive_rate_20d"] == 0.0

    def test_false_positive_buy_signal(self):
        results = [
            {"ticker": "AAPL", "score": 2, "forward_returns": {"ret_20d": -0.05, "ret_60d": 0.02}},
        ]
        metrics = _compute_directional_metrics(results)
        assert metrics["hit_rate_20d"] == 0.0
        assert metrics["hit_rate_60d"] == 1.0
        assert metrics["false_positive_rate_20d"] == 1.0
        assert metrics["false_positive_rate_60d"] == 0.0

    def test_missing_forward_returns(self):
        results = [
            {"ticker": "AAPL", "score": 1, "forward_returns": {"ret_20d": None, "ret_60d": None}},
        ]
        metrics = _compute_directional_metrics(results)
        assert metrics["hit_rate_20d"] is None
        assert metrics["hit_rate_60d"] is None
        assert metrics["total_predictions_20d"] == 0

    def test_hold_signal_not_counted(self):
        results = [
            {"ticker": "AAPL", "score": 0, "forward_returns": {"ret_20d": 0.05, "ret_60d": 0.10}},
        ]
        metrics = _compute_directional_metrics(results)
        assert metrics["hit_rate_20d"] is None  # no directional predictions made
        assert metrics["bullish_predictions"] == 0

    def test_no_bullish_predictions_means_null_fpr(self):
        results = [
            {"ticker": "AAPL", "score": -1, "forward_returns": {"ret_20d": -0.03}},
            {"ticker": "MSFT", "score": -2, "forward_returns": {"ret_20d": -0.10}},
        ]
        metrics = _compute_directional_metrics(results)
        assert metrics["bullish_predictions"] == 0
        assert metrics["false_positive_rate_20d"] is None


class TestScoreDistribution:
    def test_counts_by_rating(self):
        runs = [
            {"rating": "Buy"}, {"rating": "Buy"}, {"rating": "Hold"},
            {"rating": "Sell"}, {"rating": None},
        ]
        dist = _score_distribution(runs)
        assert dist == {"Buy": 2, "Hold": 1, "Sell": 1, "error": 1}

    def test_empty(self):
        assert _score_distribution([]) == {}
