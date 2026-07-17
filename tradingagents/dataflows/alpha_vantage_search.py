import json

from .alpha_vantage_common import _make_api_request

# Alpha Vantage SYMBOL_SEARCH returns matches keyed with numbered field names
# ("1. symbol", "2. name", ...). Map them to plain keys for callers.
_FIELD_MAP = {
    "symbol": "1. symbol",
    "name": "2. name",
    "type": "3. type",
    "region": "4. region",
    "currency": "8. currency",
    "score": "9. matchScore",
}


def get_symbol_search(keywords: str) -> list[dict]:
    """Search Alpha Vantage for ticker symbols matching a free-text query.

    Uses the SYMBOL_SEARCH endpoint (keyword -> best-matching tickers), the only
    vendor endpoint offering symbol lookup. Key injection, timeout and
    rate-limit/bad-key classification are handled by ``_make_api_request``; a
    missing key raises ``AlphaVantageNotConfiguredError`` (caller decides how to
    degrade).

    Args:
        keywords: Free-text search term, e.g. "tesla" or "tesco".

    Returns:
        List of ``{symbol, name, type, region, currency, score}`` dicts, best
        match first. Empty list for a blank query or a malformed/empty response.
    """
    keywords = (keywords or "").strip()
    if not keywords:
        return []

    response = _make_api_request("SYMBOL_SEARCH", {"keywords": keywords})

    # SYMBOL_SEARCH replies with JSON; _make_api_request returns it as raw text.
    if isinstance(response, str):
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            return []
    else:
        payload = response

    if not isinstance(payload, dict):
        return []

    matches = payload.get("bestMatches") or []
    return [
        {key: match.get(av_key, "") for key, av_key in _FIELD_MAP.items()}
        for match in matches
        if isinstance(match, dict)
    ]
