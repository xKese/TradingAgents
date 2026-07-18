from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.market_data_validator import build_verified_market_snapshot


@tool
def get_verified_market_snapshot(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "the current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[
        int, "number of recent trading rows to include for sanity-checking"
    ] = 30,
) -> str:
    """Deterministic verification snapshot for exact market-data claims.

    Returns the latest OHLCV row on or before curr_date, common technical
    indicators, and recent closes. Call this before making exact claims about
    price levels, Bollinger bands, RSI, MACD, moving averages, support /
    resistance, or historical comparisons, and treat it as the source of truth.
    """
    # Verification is an enrichment, not a core data feed: if no vendor can
    # serve OHLCV for this symbol, degrade to an explicit sentinel instead of
    # letting the exception abort the whole run (#830 hardening vs. robustness).
    try:
        return build_verified_market_snapshot(symbol, curr_date, look_back_days)
    except Exception as exc:
        return (
            f"VERIFIED_SNAPSHOT_UNAVAILABLE: could not build a verified market "
            f"snapshot for {symbol} ({type(exc).__name__}: {exc}). Proceed "
            "without it, make exact numeric claims only when directly supported "
            "by other tool outputs, and do not fabricate values."
        )
