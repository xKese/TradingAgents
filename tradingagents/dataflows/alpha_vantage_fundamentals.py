import json

from .alpha_vantage_common import _make_api_request
from .errors import NoMarketDataError


def _raise_if_empty(result, ticker: str, has_reports: bool):
    """Raise NoMarketDataError when AV returned an empty-but-successful payload.

    Alpha Vantage's fundamentals coverage is US-centric; non-US listings
    (e.g. ADS.DEX) get ``{}`` or empty report lists with HTTP 200. Raising
    the typed error lets the vendor router fall back to the next vendor and
    emit the NO_DATA sentinel instead of handing the analyst raw ``{}`` —
    and keeps the daily cache from storing the empty result. Non-JSON and
    non-dict bodies pass through unchanged (fail-open, like
    ``_filter_reports_by_date``).
    """
    if not isinstance(result, str):
        return result
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return result
    if not isinstance(payload, dict):
        return result
    detail = None
    if not payload:
        detail = (
            "Alpha Vantage returned an empty payload "
            "(fundamentals coverage is US-centric)"
        )
    elif "Error Message" in payload:
        detail = f"Alpha Vantage error: {payload['Error Message']}"
    elif (
        has_reports
        and not payload.get("annualReports")
        and not payload.get("quarterlyReports")
    ):
        detail = "Alpha Vantage returned no annual/quarterly reports"
    if detail:
        raise NoMarketDataError(ticker, detail=detail)
    return result


def _filter_reports_by_date(result, curr_date: str):
    """Drop annual/quarterly reports dated after curr_date to prevent look-ahead.

    ``_make_api_request`` returns the fundamentals payload as a JSON string, so
    parse, filter, and re-serialize. A non-JSON body or an unset ``curr_date`` is
    returned unchanged.
    """
    if not curr_date or not isinstance(result, str):
        return result
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return result
    if not isinstance(payload, dict):
        return result
    for key in ("annualReports", "quarterlyReports"):
        if isinstance(payload.get(key), list):
            payload[key] = [
                r for r in payload[key]
                if r.get("fiscalDateEnding", "") <= curr_date
            ]
    return json.dumps(payload)


def get_fundamentals(ticker: str, curr_date: str = None) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol using Alpha Vantage.

    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd (not used for Alpha Vantage)

    Returns:
        str: Company overview data including financial ratios and key metrics
    """
    params = {
        "symbol": ticker,
    }

    return _raise_if_empty(
        _make_api_request("OVERVIEW", params), ticker, has_reports=False
    )


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str = None):
    """Retrieve balance sheet data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("BALANCE_SHEET", {"symbol": ticker})
    result = _filter_reports_by_date(result, curr_date)
    return _raise_if_empty(result, ticker, has_reports=True)


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str = None):
    """Retrieve cash flow statement data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("CASH_FLOW", {"symbol": ticker})
    result = _filter_reports_by_date(result, curr_date)
    return _raise_if_empty(result, ticker, has_reports=True)


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str = None):
    """Retrieve income statement data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("INCOME_STATEMENT", {"symbol": ticker})
    result = _filter_reports_by_date(result, curr_date)
    return _raise_if_empty(result, ticker, has_reports=True)

