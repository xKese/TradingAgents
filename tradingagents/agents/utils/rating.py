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


# Overnight-vs-intraday holding call (see HoldingRecommendation in schemas.py).
# Values are multi-word, so this uses a "rest of line" capture rather than
# _RATING_LABEL_RE's single-word one — matched by prefix against the three
# canonical values rather than exact string equality, tolerant of trailing
# punctuation/qualifiers the model appends after the label.
_HOLDING_LABEL_RE = re.compile(r"holding\s*recommendation.*?[:\-]\s*(.+)", re.IGNORECASE)
HOLDING_RECOMMENDATIONS: tuple[str, ...] = ("Hold Overnight", "Square Off Intraday", "Data-Dependent")


def parse_holding_recommendation(text: str, default: str = "Data-Dependent") -> str:
    """Heuristically extract the overnight-vs-intraday holding call from prose text.

    Same two-pass strategy as parse_rating: an explicit "Holding
    Recommendation: X" label first, then the first canonical value found
    anywhere in the text. Defaults to "Data-Dependent" rather than either
    extreme when nothing matches — an unparseable response should read as
    "needs a human look," not as a confident call either way.
    """
    for line in text.splitlines():
        m = _HOLDING_LABEL_RE.search(line)
        if m:
            candidate = m.group(1).strip(" *")
            for value in HOLDING_RECOMMENDATIONS:
                if candidate.lower().startswith(value.lower()):
                    return value

    lower_text = text.lower()
    for value in HOLDING_RECOMMENDATIONS:
        if value.lower() in lower_text:
            return value

    return default
