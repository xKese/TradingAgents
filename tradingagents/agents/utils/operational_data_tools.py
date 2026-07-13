"""Operational evidence tool routed through the repository vendor abstraction."""

from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_operational_evidence(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "analysis date in yyyy-mm-dd format"],
) -> str:
    """Retrieve point-in-time operational evidence from the configured provider."""
    return route_to_vendor("get_operational_evidence", ticker, curr_date)
