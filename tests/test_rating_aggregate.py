"""Tests for ensemble rating aggregation (ordinal median + vote counts)."""

import pytest

from tradingagents.agents.utils.rating import (
    aggregate_ratings,
    median_rating,
    rating_ordinal,
)


@pytest.mark.unit
class TestRatingOrdinal:
    def test_scale_order(self):
        assert rating_ordinal("Buy") < rating_ordinal("Overweight")
        assert rating_ordinal("Overweight") < rating_ordinal("Hold")
        assert rating_ordinal("Hold") < rating_ordinal("Underweight")
        assert rating_ordinal("Underweight") < rating_ordinal("Sell")

    def test_case_insensitive(self):
        assert rating_ordinal("buy") == rating_ordinal("Buy")
        assert rating_ordinal("  SELL ") == rating_ordinal("Sell")

    def test_unknown_maps_to_hold(self):
        assert rating_ordinal("Moon") == rating_ordinal("Hold")
        assert rating_ordinal("") == rating_ordinal("Hold")
        assert rating_ordinal(None) == rating_ordinal("Hold")


@pytest.mark.unit
class TestMedianRating:
    def test_odd_median(self):
        assert median_rating(["Buy", "Overweight", "Sell"]) == "Overweight"
        assert median_rating(["Overweight", "Underweight", "Overweight"]) == "Overweight"
        assert median_rating(["Buy", "Buy", "Hold", "Sell", "Sell"]) == "Hold"

    def test_single_run(self):
        assert median_rating(["Underweight"]) == "Underweight"

    def test_even_tie_resolves_toward_hold(self):
        # Middle values Overweight/Hold -> Hold (closer to neutral).
        assert median_rating(["Buy", "Overweight", "Hold", "Sell"]) == "Hold"
        # Middle values Hold/Underweight -> Hold.
        assert median_rating(["Overweight", "Hold", "Underweight", "Sell"]) == "Hold"
        # Equidistant middle values (Overweight/Underweight) -> the lower
        # ordinal (Overweight) wins the tie deterministically.
        assert median_rating(["Buy", "Overweight", "Underweight", "Sell"]) == "Overweight"

    def test_unknown_counts_as_hold(self):
        assert median_rating(["Buy", "???", "Sell"]) == "Hold"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            median_rating([])


@pytest.mark.unit
class TestAggregateRatings:
    def test_votes_and_shape(self):
        result = aggregate_ratings(["Buy", "Overweight", "Overweight"])
        assert result["rating"] == "Overweight"
        assert result["votes"] == {"Buy": 1, "Overweight": 2}
        assert result["n"] == 3
        assert result["method"] == "median"

    def test_unknown_votes_counted_as_hold(self):
        result = aggregate_ratings(["Buy", "???"])
        assert result["votes"] == {"Buy": 1, "Hold": 1}

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            aggregate_ratings([])
