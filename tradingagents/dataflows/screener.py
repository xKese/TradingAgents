"""Cheap, non-LLM candidate discovery ("screener") across asset classes.

This is the fallback path for the hermes-tradingagents-plugin screener
feature (``scripts/screen_candidates.py`` is the CLI wrapper around this
module) — used when the plugin's own in-process screener can't import
``yfinance`` in Hermes's Python environment. Since TradingAgents already
depends on ``yfinance>=1.4.1`` (see pyproject.toml), this path is always
available wherever TradingAgents itself runs (Docker image or local venv).

Nothing here calls the TradingAgents multi-agent pipeline — this is pure
"which tickers are worth a deep dive", a cheap quantitative pre-filter.
Deliberately kept independent of ``tradingagents/graph`` and ``cli`` so it
has no import-time cost or dependency on the rest of the pipeline.

Contract (shared with the plugin's native screener.py — keep the shapes in
sync if either side changes):

    discover(asset_classes, risk, horizon, limit) -> list[dict], each:
        {"ticker": str, "asset_type": "stock"|"crypto"|"commodity",
         "source": str, "metrics": {...}}
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

VALID_RISK_LEVELS = {"low", "medium", "high"}
VALID_HORIZONS = {"swing", "position"}
VALID_ASSET_CLASSES = {"stock", "crypto", "commodity"}

_COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"

# Fixed universe of liquid, Yahoo-tradable futures — small and stable enough
# that a real screener API isn't worth it. `risk_tier` is a static editorial
# call (precious metals lower vol than energy/softs historically); the
# per-run ranking within a tier still comes from realized momentum.
COMMODITY_UNIVERSE = [
    {"ticker": "GC=F", "name": "Gold", "risk_tier": "low"},
    {"ticker": "SI=F", "name": "Silver", "risk_tier": "medium"},
    {"ticker": "PL=F", "name": "Platinum", "risk_tier": "medium"},
    {"ticker": "PA=F", "name": "Palladium", "risk_tier": "high"},
    {"ticker": "HG=F", "name": "Copper", "risk_tier": "medium"},
    {"ticker": "CL=F", "name": "Crude Oil (WTI)", "risk_tier": "high"},
    {"ticker": "NG=F", "name": "Natural Gas", "risk_tier": "high"},
    {"ticker": "ZC=F", "name": "Corn", "risk_tier": "medium"},
    {"ticker": "ZS=F", "name": "Soybeans", "risk_tier": "medium"},
    {"ticker": "ZW=F", "name": "Wheat", "risk_tier": "medium"},
    {"ticker": "KC=F", "name": "Coffee", "risk_tier": "high"},
]


def _equity_beta_bounds(risk: str) -> tuple[float, float]:
    return {
        "low": (0.0, 1.0),
        "medium": (1.0, 1.8),
        "high": (1.8, 10.0),
    }[risk]


def screen_equities(risk: str, horizon: str, limit: int = 20) -> list[dict[str, Any]]:
    """US-listed equities via Yahoo Finance's own screener (``yf.screen``).

    Region is fixed to "us" for v1 — Yahoo's screener supports other regions
    but the fields available (beta, market cap bands) are most reliable
    there; broaden later if non-US coverage is requested.
    """
    import yfinance as yf
    from yfinance import EquityQuery

    beta_lo, beta_hi = _equity_beta_bounds(risk)
    sort_field = "percentchange" if horizon == "swing" else "fiftytwowkpercentchange"

    query = EquityQuery("and", [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("btwn", ["beta", beta_lo, beta_hi]),
        EquityQuery("gte", ["intradaymarketcap", 300_000_000]),
        EquityQuery("gt", ["dayvolume", 100_000]),
    ])
    result = yf.screen(query, sortField=sort_field, sortAsc=False, size=limit)
    quotes = result.get("quotes", []) if isinstance(result, dict) else []

    candidates = []
    for q in quotes[:limit]:
        candidates.append({
            "ticker": q.get("symbol"),
            "asset_type": "stock",
            "source": "yfinance_screen",
            "metrics": {
                "beta": q.get("beta"),
                "percent_change": q.get("regularMarketChangePercent"),
                "fifty_two_week_change": q.get("fiftyTwoWeekChangePercent"),
                "market_cap": q.get("marketCap"),
                "sector": q.get("sector"),
            },
        })
    return candidates


def _coingecko_risk_bounds(risk: str) -> tuple[int, int]:
    """(min_rank, max_rank) market-cap-rank band used as the risk proxy —
    top-cap coins are the "low risk" tier of crypto, long tail is "high"."""
    return {
        "low": (1, 10),
        "medium": (11, 100),
        "high": (101, 250),
    }[risk]


def screen_crypto(risk: str, horizon: str, limit: int = 20) -> list[dict[str, Any]]:
    """Top-N crypto by market cap (CoinGecko public API, no key required),
    filtered to a market-cap-rank band per risk and sorted by the momentum
    window matching the requested horizon."""
    min_rank, max_rank = _coingecko_risk_bounds(risk)
    change_field = "price_change_percentage_24h_in_currency" if horizon == "swing" \
        else "price_change_percentage_7d_in_currency"

    resp = requests.get(
        _COINGECKO_MARKETS_URL,
        params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": max_rank,
            "page": 1,
            "price_change_percentage": "24h,7d",
        },
        timeout=15,
    )
    resp.raise_for_status()
    coins = resp.json()

    banded = [c for c in coins[min_rank - 1:max_rank] if c.get(change_field) is not None]
    banded.sort(key=lambda c: c[change_field], reverse=True)

    candidates = []
    for c in banded[:limit]:
        candidates.append({
            "ticker": f"{c['symbol'].upper()}-USD",
            "asset_type": "crypto",
            "source": "coingecko",
            "metrics": {
                "market_cap_rank": c.get("market_cap_rank"),
                "price_change_24h_pct": c.get("price_change_percentage_24h_in_currency"),
                "price_change_7d_pct": c.get("price_change_percentage_7d_in_currency"),
                "market_cap": c.get("market_cap"),
            },
        })
    return candidates


def screen_commodities(risk: str, horizon: str, limit: int = 20) -> list[dict[str, Any]]:
    """Static futures universe, ranked by realized momentum over the window
    matching the requested horizon (5 trading days for swing, 6 months for
    position). Falls back to the unranked, risk-filtered static list (no
    momentum figures) if yfinance history can't be fetched for one/all of
    them, rather than failing the whole screen."""
    tier_universe = [c for c in COMMODITY_UNIVERSE if c["risk_tier"] == risk]
    period = "5d" if horizon == "swing" else "6mo"

    try:
        import yfinance as yf
    except ImportError:
        return [
            {"ticker": c["ticker"], "asset_type": "commodity", "source": "static_list",
             "metrics": {"name": c["name"]}}
            for c in tier_universe[:limit]
        ]

    scored = []
    for c in tier_universe:
        try:
            hist = yf.Ticker(c["ticker"]).history(period=period)
            if hist.empty:
                continue
            pct_change = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
        except Exception as exc:  # noqa: BLE001 — one bad future shouldn't kill the screen
            logger.warning("commodity screen: failed to fetch %s: %s", c["ticker"], exc)
            continue
        scored.append({
            "ticker": c["ticker"],
            "asset_type": "commodity",
            "source": "yfinance_history",
            "metrics": {"name": c["name"], "percent_change": round(float(pct_change), 2)},
        })

    scored.sort(key=lambda x: x["metrics"]["percent_change"], reverse=True)
    return scored[:limit] if scored else [
        {"ticker": c["ticker"], "asset_type": "commodity", "source": "static_list",
         "metrics": {"name": c["name"]}}
        for c in tier_universe[:limit]
    ]


def discover(
    asset_classes: list[str],
    risk: str,
    horizon: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Run the screener for each requested asset class and return the
    combined candidate list (each asset class contributes up to `limit`)."""
    if risk not in VALID_RISK_LEVELS:
        raise ValueError(f"risk must be one of {sorted(VALID_RISK_LEVELS)}, got {risk!r}")
    if horizon not in VALID_HORIZONS:
        raise ValueError(f"horizon must be one of {sorted(VALID_HORIZONS)}, got {horizon!r}")
    invalid_classes = set(asset_classes) - VALID_ASSET_CLASSES
    if invalid_classes:
        raise ValueError(f"invalid asset class(es): {sorted(invalid_classes)}")

    results: list[dict[str, Any]] = []
    for asset_class in asset_classes:
        if asset_class == "stock":
            results.extend(screen_equities(risk, horizon, limit))
        elif asset_class == "crypto":
            results.extend(screen_crypto(risk, horizon, limit))
        elif asset_class == "commodity":
            results.extend(screen_commodities(risk, horizon, limit))
    return results
