from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.errors import VendorError
from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """
    Retrieve a single technical indicator for a given ticker symbol.
    Uses the configured technical_indicators vendor.
    Args:
        symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
        indicator (str): A single technical indicator name, e.g. 'rsi', 'macd'. Call this tool once per indicator.
        curr_date (str): The current trading date you are trading on, YYYY-mm-dd
        look_back_days (int): How many days to look back, default is 30
    Returns:
        str: A formatted dataframe containing the technical indicators for the specified ticker symbol and indicator.
    """
    # LLMs sometimes pass multiple indicators as a comma-separated string;
    # split and process each individually.
    indicators = [i.strip().lower() for i in indicator.split(",") if i.strip()]
    results = []
    for ind in indicators:
        try:
            results.append(route_to_vendor("get_indicators", symbol, ind, curr_date, look_back_days))
        except ValueError as e:
            # Includes VendorNotConfiguredError (a ValueError) — unchanged.
            results.append(str(e))
        except VendorError as e:
            # A rate-limited vendor chain must not abort the whole tool call:
            # keep the other indicators' results and report this one as
            # temporarily unavailable rather than raising out of the loop.
            results.append(f"TEMPORARILY_UNAVAILABLE: {ind}: {e}")
    return "\n\n".join(results)
