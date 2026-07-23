"""Turn a browser form payload into a validated TradingAgents run config.

Mirrors ``cli.main._build_run_config`` (env-precedence included) but takes a
plain dict instead of interactive selections and raises ``ValueError`` on bad
input instead of calling ``exit()``, so the web layer can return HTTP 400.
"""

from __future__ import annotations

import json
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

_MAX_ENSEMBLE_RUNS = 5

# Whitelisted keys of the external quantitative pre-rating payload; anything
# else is dropped so callers can evolve their schema without breaking us.
_FACTOR_CONTEXT_KEYS = {
    "source",
    "as_of",
    "total_score",
    "classification",
    "factor_scores",
    "filter_ok",
    "recommendation",
    "piotroski",
    "altman_z",
    "signals",
    "identity",
    "source_ticker",
}

_MAX_FACTOR_CONTEXT_BYTES = 8192


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


def _is_blank(raw) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


def _validate_temperature(raw) -> float | None:
    if _is_blank(raw):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RunRequestError(
            "Ungültige Temperatur. Bitte eine Zahl zwischen 0 und 2 angeben."
        ) from exc
    if not 0.0 <= value <= 2.0:
        raise RunRequestError("Die Temperatur muss zwischen 0 und 2 liegen.")
    return value


def _validate_seed(raw) -> int | None:
    if _is_blank(raw):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RunRequestError(
            "Ungültiger Seed. Bitte eine ganze Zahl angeben."
        ) from exc


def _validate_flag(raw, label: str) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in ("true", "1", "yes", "on"):
            return True
        if value in ("false", "0", "no", "off", ""):
            return False
    raise RunRequestError(f"Ungültiger Wert für {label}.")


def _validate_ensemble_runs(raw) -> int:
    if _is_blank(raw):
        return 1
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RunRequestError(
            "Ungültige Anzahl Läufe. Bitte eine ganze Zahl angeben."
        ) from exc
    if not 1 <= value <= _MAX_ENSEMBLE_RUNS:
        raise RunRequestError(
            f"Die Anzahl Läufe muss zwischen 1 und {_MAX_ENSEMBLE_RUNS} liegen."
        )
    return value


def _coerce_number(value):
    """Best-effort numeric coercion; returns None for non-numeric input."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return None


def _validate_factor_context(raw) -> dict | None:
    """Validate the optional external factor pre-rating attached to a run.

    Field-level coercion is fail-open (a bad number is dropped, not fatal),
    but a payload that is not a dict or exceeds the size cap is rejected —
    that indicates a broken client rather than a missing metric.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RunRequestError(
            "factor_context muss ein Objekt sein (Schlüssel/Wert-Paare)."
        )

    if len(json.dumps(raw, ensure_ascii=False)) > _MAX_FACTOR_CONTEXT_BYTES:
        raise RunRequestError("factor_context ist zu groß (max. 8 KB).")

    cleaned: dict = {}
    for key in _FACTOR_CONTEXT_KEYS & set(raw):
        value = raw[key]
        if value is None:
            continue
        if key in ("total_score", "piotroski", "altman_z"):
            value = _coerce_number(value)
            if value is None:
                continue
        elif key == "factor_scores":
            if not isinstance(value, dict):
                continue
            value = {
                k: n
                for k, v in value.items()
                if (n := _coerce_number(v)) is not None
            }
        elif key in ("signals", "identity"):
            if not isinstance(value, dict):
                continue
        else:
            value = str(value)
        cleaned[key] = value
    return cleaned or None


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

    # Reproducibility / stability options. The form is prefilled from the
    # env-aware defaults (catalog.form_defaults), so a submitted value is
    # authoritative; keys absent from the payload keep the config default so
    # older clients stay unaffected.
    if "temperature" in payload:
        config["temperature"] = _validate_temperature(payload.get("temperature"))
    if "seed" in payload:
        config["seed"] = _validate_seed(payload.get("seed"))
    if "memory_enabled" in payload:
        config["memory_enabled"] = _validate_flag(
            payload.get("memory_enabled"), "Lernfunktion (Memory)"
        )
    if "data_cache_daily" in payload:
        config["data_cache_daily"] = _validate_flag(
            payload.get("data_cache_daily"), "Tages-Cache"
        )
    if "ensemble_runs" in payload:
        config["ensemble_runs"] = _validate_ensemble_runs(payload.get("ensemble_runs"))

    return {
        "config": config,
        "ticker": ticker,
        "analysis_date": analysis_date,
        "asset_type": asset_type.value,
        "analysts": [a.value for a in analysts],
        "factor_context": _validate_factor_context(payload.get("factor_context")),
    }
