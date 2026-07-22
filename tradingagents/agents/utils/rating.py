"""Shared 5-tier rating vocabulary and a deterministic heuristic parser.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision)
- The signal processor (rating extracted for downstream consumers)
- The memory log (rating tag stored alongside each decision entry)

Centralising it here avoids drift between those call sites.
"""

from __future__ import annotations

import re

# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}

# Matches "Rating: X" / "rating - X" / "Rating: **X**" — tolerates markdown
# bold wrappers and either a colon or hyphen separator.
_RATING_LABEL_RE = re.compile(r"rating.*?[:\-][\s*]*(\w+)", re.IGNORECASE)


def parse_rating(text: str, default: str = "Hold") -> str:
    """Heuristically extract a 5-tier rating from prose text.

    Two-pass strategy:
    1. Look for an explicit "Rating: X" label (tolerant of markdown bold).
    2. Fall back to the first 5-tier rating word found anywhere in the text.

    Returns a Title-cased rating string, or ``default`` if no rating word appears.
    """
    for line in text.splitlines():
        m = _RATING_LABEL_RE.search(line)
        if m and m.group(1).lower() in _RATING_SET:
            return m.group(1).capitalize()

    for line in text.splitlines():
        for word in line.lower().split():
            clean = word.strip("*:.,")
            if clean in _RATING_SET:
                return clean.capitalize()

    return default


def rating_ordinal(rating: str) -> int:
    """Map a rating string to its ordinal on the 5-tier scale (Buy=0 … Sell=4).

    Unknown strings map to Hold — the same neutral default ``parse_rating``
    falls back to.
    """
    try:
        return RATINGS_5_TIER.index(rating.strip().capitalize())
    except (ValueError, AttributeError):
        return RATINGS_5_TIER.index("Hold")


def median_rating(ratings: list[str]) -> str:
    """Median rating across runs, computed on the ordinal scale.

    With an even count, the lower median is nudged toward Hold so a tie
    between adjacent tiers resolves conservatively rather than bullishly.
    """
    if not ratings:
        raise ValueError("median_rating() requires at least one rating")
    ordinals = sorted(rating_ordinal(r) for r in ratings)
    n = len(ordinals)
    if n % 2 == 1:
        return RATINGS_5_TIER[ordinals[n // 2]]
    lo, hi = ordinals[n // 2 - 1], ordinals[n // 2]
    hold = RATINGS_5_TIER.index("Hold")
    # Pick whichever of the two middle values sits closer to Hold (ties → lo).
    pick = lo if abs(lo - hold) <= abs(hi - hold) else hi
    return RATINGS_5_TIER[pick]


def aggregate_ratings(ratings: list[str]) -> dict:
    """Aggregate N per-run ratings into an ensemble result.

    Returns ``{"rating", "votes", "n", "method"}`` where ``votes`` maps each
    5-tier rating to its count (only tiers that received votes).
    """
    result = median_rating(ratings)  # raises on empty input
    votes: dict[str, int] = {}
    for r in ratings:
        tier = RATINGS_5_TIER[rating_ordinal(r)]
        votes[tier] = votes.get(tier, 0) + 1
    return {
        "rating": result,
        "votes": votes,
        "n": len(ratings),
        "method": "median",
    }
