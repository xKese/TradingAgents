"""Deterministic market-data verification snapshot.

The market analyst is an LLM that can confabulate exact numbers — citing a
Bollinger band or a "historically validated bounce" that the underlying data
doesn't support (#830). This module computes a ground-truth snapshot (latest
OHLCV row on or before the analysis date, common indicators, recent closes)
the analyst is told to treat as the source of truth for any exact numeric
claim. Deterministic, no LLM involved.
"""

from __future__ import annotations

from collections.abc import Iterable
from io import StringIO

import pandas as pd
from stockstats import wrap

from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.stockstats_utils import load_ohlcv

# A fixed, common indicator set so the snapshot is the same shape every run.
DEFAULT_SNAPSHOT_INDICATORS: tuple[str, ...] = (
    "close_10_ema", "close_50_sma", "close_200_sma",
    "rsi", "boll", "boll_ub", "boll_lb",
    "macd", "macds", "macdh", "atr",
)

# Calendar-day window requested from the vendor: enough trading rows to warm up
# the slowest snapshot indicator (200 SMA) with margin.
_OHLCV_WINDOW_DAYS = 450

# get_stock_data returns a CSV string whose dialect depends on the vendor —
# Alpha Vantage: lowercase ``timestamp,open,...,adjusted_close,volume``;
# yfinance: capitalized columns behind a ``#`` comment preamble. Map both onto
# the capitalized OHLCV frame the validator (and stockstats) works with.
_CSV_COLUMN_ALIASES = {
    "timestamp": "Date", "date": "Date",
    "open": "Open", "high": "High", "low": "Low", "close": "Close",
    "adjusted_close": "Adj Close", "adj close": "Adj Close",
    "volume": "Volume",
}


def _parse_vendor_csv(raw) -> pd.DataFrame | None:
    """Parse a get_stock_data vendor CSV into an OHLCV frame; None if unusable."""
    if not isinstance(raw, str) or raw.lstrip().startswith("NO_DATA_AVAILABLE"):
        return None
    try:
        df = pd.read_csv(StringIO(raw), comment="#", skip_blank_lines=True)
    except Exception:
        return None
    if df.empty:
        return None
    df = df.rename(
        columns={c: _CSV_COLUMN_ALIASES.get(str(c).strip().lower(), c) for c in df.columns}
    )
    if "Date" not in df.columns or "Close" not in df.columns:
        return None
    return df


def _routed_rows(symbol: str, curr_date: str) -> pd.DataFrame | None:
    """OHLCV via the configured data_vendors chain; None when unavailable.

    The snapshot must verify against the same source the analyst's data tools
    use, so route through ``get_stock_data`` (honoring an all-Alpha-Vantage
    setup and its native symbols) instead of assuming the yfinance loader.
    """
    start = (
        pd.to_datetime(curr_date) - pd.Timedelta(days=_OHLCV_WINDOW_DAYS)
    ).strftime("%Y-%m-%d")
    try:
        raw = route_to_vendor("get_stock_data", symbol, start, curr_date)
    except Exception:
        return None
    return _parse_vendor_csv(raw)


def _verified_rows(symbol: str, curr_date: str) -> pd.DataFrame:
    """OHLCV on or before curr_date, date-sorted. Raises if nothing usable.

    Prefers the configured vendor chain and falls back to the legacy yfinance
    loader. The Date cutoff is re-applied defensively — this is a verification
    path, so it must not trust its input to be pre-filtered.
    """
    data = _routed_rows(symbol, curr_date)
    if data is None or data.empty:
        data = load_ohlcv(symbol, curr_date)
    if data is None or data.empty:
        raise ValueError(f"No OHLCV data available for {symbol}.")

    df = data.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    df = df[df["Date"] <= pd.to_datetime(curr_date)].sort_values("Date")
    if df.empty:
        raise ValueError(f"No OHLCV rows on or before {curr_date} for {symbol}.")
    return df


def _fmt(value) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def build_verified_market_snapshot(
    symbol: str,
    curr_date: str,
    look_back_days: int = 30,
    indicators: Iterable[str] | None = None,
) -> str:
    """Render a ground-truth snapshot: latest OHLCV row, indicators, recent closes."""
    # `df` keeps the original capitalized OHLCV columns (Open/High/Low/Close/
    # Volume); stockstats `wrap()` lowercases columns and adds indicator
    # columns, so read raw prices from `df` and indicators from `stock_df`.
    df = _verified_rows(symbol, curr_date)
    stock_df = wrap(df.copy())

    selected = tuple(indicators or DEFAULT_SNAPSHOT_INDICATORS)
    indicator_values: dict[str, str] = {}
    for name in selected:
        try:
            stock_df[name]  # triggers stockstats calculation
            indicator_values[name] = _fmt(stock_df.iloc[-1][name])
        except Exception as exc:  # noqa: BLE001 — one bad indicator shouldn't sink the snapshot
            indicator_values[name] = f"N/A ({type(exc).__name__})"

    latest = df.iloc[-1]
    latest_date = _fmt(latest["Date"])
    window = max(1, min(int(look_back_days), 30))
    recent = df.tail(window)

    lines = [
        f"## Verified market data snapshot for {symbol.upper()}",
        "",
        f"- Requested analysis date: {curr_date}",
        f"- Latest trading row used: {latest_date}",
        "- Rows after the requested analysis date are excluded before verification.",
        "",
        "### Latest verified OHLCV row",
        "",
        "| Field | Value |",
        "|---|---:|",
    ]
    for field in ("Open", "High", "Low", "Close", "Volume"):
        lines.append(f"| {field} | {_fmt(latest.get(field))} |")

    lines += ["", "### Verified technical indicators (latest row)", "",
              "| Indicator | Value |", "|---|---:|"]
    for name, value in indicator_values.items():
        lines.append(f"| {name} | {value} |")

    lines += ["", f"### Recent verified closes (last {len(recent)} rows)", "",
              "| Date | Close |", "|---|---:|"]
    for _, row in recent.iterrows():
        lines.append(f"| {_fmt(row['Date'])} | {_fmt(row.get('Close'))} |")

    lines += [
        "",
        "Use this snapshot as the source of truth for exact OHLCV, price-level, "
        "and indicator-value claims. If another tool output conflicts with it, "
        "flag the discrepancy rather than inventing a reconciled number. Do not "
        "claim historical validation, support/resistance bounces, or exact "
        "percentage moves unless directly supported by tool output with concrete "
        "dates and prices.",
    ]
    return "\n".join(lines)
