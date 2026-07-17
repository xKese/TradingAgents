"""Turn a browser form payload into a validated TradingAgents run config.

Mirrors ``cli.main._build_run_config`` (env-precedence included) but takes a
plain dict instead of interactive selections and raises ``ValueError`` on bad
input instead of calling ``exit()``, so the web layer can return HTTP 400.
"""

from __future__ import annotations

import os
from datetime import datetime

from cli.models import AnalystType, AssetType
from cli.utils import (
    detect_asset_type,
    filter_analysts_for_asset_type,
    is_valid_ticker_input,
    normalize_ticker_symbol,
    provider_default_url,
)
from tradingagents.default_config import DEFAULT_CONFIG

_VALID_DEPTHS = {1, 3, 5}


class RunRequestError(ValueError):
    """Raised when the submitted run form is invalid."""


def _validate_ticker(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return "SPY"  # matches CLI default
    if not is_valid_ticker_input(value):
        raise RunRequestError(
            "Ungültiges Ticker-Symbol. Erlaubt sind z. B. AAPL, 0700.HK, "
            "600519.SS, BTC-USD."
        )
    return normalize_ticker_symbol(value)


def _validate_date(raw: str) -> str:
    value = (raw or "").strip()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise RunRequestError(
            "Ungültiges Datum. Bitte im Format YYYY-MM-DD angeben."
        ) from exc
    if parsed.date() > datetime.now().date():
        raise RunRequestError("Das Analyse-Datum darf nicht in der Zukunft liegen.")
    return value


def _validate_analysts(values, asset_type: AssetType) -> list[AnalystType]:
    if not values:
        selected = [a for _, a in _ALL_ANALYSTS]
    else:
        selected = []
        for v in values:
            try:
                selected.append(AnalystType(v))
            except ValueError as exc:
                raise RunRequestError(f"Unbekannter Analyst: {v!r}") from exc
    filtered = filter_analysts_for_asset_type(selected, asset_type)
    if not filtered:
        raise RunRequestError("Mindestens ein Analyst muss ausgewählt sein.")
    return filtered


# (label, AnalystType) — local copy of the full set for the default case.
_ALL_ANALYSTS = [
    ("Market Analyst", AnalystType.MARKET),
    ("Sentiment Analyst", AnalystType.SOCIAL),
    ("News Analyst", AnalystType.NEWS),
    ("Fundamentals Analyst", AnalystType.FUNDAMENTALS),
]


def build_run(payload: dict) -> dict:
    """Validate ``payload`` and return everything needed to launch a run.

    Returns a dict with ``config`` (the DEFAULT_CONFIG overlay), ``ticker``,
    ``analysis_date``, ``asset_type`` (str), and ``analysts`` (list of wire
    values for the graph constructor).
    """
    ticker = _validate_ticker(payload.get("ticker", ""))
    analysis_date = _validate_date(payload.get("analysis_date", ""))
    asset_type = detect_asset_type(ticker)

    provider = str(payload.get("llm_provider", "")).strip().lower()
    if not provider:
        raise RunRequestError("Es wurde kein LLM-Provider gewählt.")

    quick = str(payload.get("shallow_thinker", "")).strip()
    deep = str(payload.get("deep_thinker", "")).strip()
    if not quick or not deep:
        raise RunRequestError("Quick- und Deep-Modell müssen angegeben sein.")

    try:
        depth = int(payload.get("research_depth", 1))
    except (TypeError, ValueError) as exc:
        raise RunRequestError("Ungültige Research-Tiefe.") from exc
    if depth not in _VALID_DEPTHS:
        raise RunRequestError("Research-Tiefe muss 1, 3 oder 5 sein.")

    analysts = _validate_analysts(payload.get("analysts"), asset_type)

    # Backend URL: explicit custom value wins, else the provider default.
    backend_url = (payload.get("backend_url") or "").strip() or provider_default_url(
        provider
    )

    # --- Build config, mirroring cli.main._build_run_config env-precedence. ---
    config = DEFAULT_CONFIG.copy()
    if not os.environ.get("TRADINGAGENTS_MAX_DEBATE_ROUNDS"):
        config["max_debate_rounds"] = depth
    if not os.environ.get("TRADINGAGENTS_MAX_RISK_ROUNDS"):
        config["max_risk_discuss_rounds"] = depth
    config["quick_think_llm"] = quick
    config["deep_think_llm"] = deep
    config["backend_url"] = backend_url
    config["llm_provider"] = provider
    config["google_thinking_level"] = payload.get("google_thinking_level") or None
    config["openai_reasoning_effort"] = payload.get("openai_reasoning_effort") or None
    config["anthropic_effort"] = payload.get("anthropic_effort") or None
    config["output_language"] = payload.get("output_language") or "English"

    return {
        "config": config,
        "ticker": ticker,
        "analysis_date": analysis_date,
        "asset_type": asset_type.value,
        "analysts": [a.value for a in analysts],
    }
