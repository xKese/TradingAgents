import functools
import logging
from collections.abc import Mapping
from typing import Any

import yfinance as yf
from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import get_stock_data
from tradingagents.agents.utils.fundamental_data_tools import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
)
from tradingagents.agents.utils.macro_data_tools import get_macro_indicators
from tradingagents.agents.utils.market_data_validation_tools import get_verified_market_snapshot
from tradingagents.agents.utils.news_data_tools import (
    get_global_news,
    get_insider_transactions,
    get_news,
)
from tradingagents.agents.utils.prediction_markets_tools import get_prediction_markets
from tradingagents.agents.utils.technical_indicators_tools import get_indicators

# Public surface: the data tools are imported here so agents and the graph
# import them from one place, plus the instrument/language helpers defined below.
__all__ = [
    "get_stock_data",
    "get_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "get_global_news",
    "get_insider_transactions",
    "get_macro_indicators",
    "get_prediction_markets",
    "get_verified_market_snapshot",
    "build_instrument_context",
    "resolve_instrument_identity",
    "get_instrument_context_from_state",
    "get_language_instruction",
    "create_msg_delete",
]

logger = logging.getLogger(__name__)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to every agent whose output reaches the saved report —
    analysts, researchers, debaters, research manager, trader, and
    portfolio manager — so a non-English run produces a fully localized
    report rather than a mix of languages.
    """
    from tradingagents.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


def _clean_identity_value(value: Any) -> str | None:
    """Return a trimmed string, or None for empty / placeholder-ish values."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "n/a", "nan", "null"}:
        return None
    return cleaned


@functools.lru_cache(maxsize=256)
def resolve_instrument_identity(ticker: str) -> dict:
    """Resolve deterministic identity metadata (company name, sector, …) for a ticker.

    This exists to stop the pipeline from hallucinating a *different* company
    when a chart pattern suggests a different industry than the real one
    (#814): without a ground-truth name, the market analyst would pattern-match
    the price action to a narrative and invent an identity that then cascaded
    through every downstream agent.

    Best-effort by design: if yfinance is unavailable, rate-limited, or doesn't
    recognise the ticker, we return ``{}`` and the caller falls back to
    ticker-only context rather than failing before analysis starts. Cached so
    the lookup happens at most once per ticker per process.

    The symbol is normalized first (e.g. ``XAUUSD`` -> ``GC=F``) so identity
    resolves for the same instrument the price path actually fetches (#983).
    """
    from tradingagents.dataflows.symbol_utils import normalize_symbol

    try:
        info = yf.Ticker(normalize_symbol(ticker)).info or {}
    except Exception as exc:  # noqa: BLE001 — fail open, never block the run
        logger.debug("Could not resolve instrument identity for %s: %s", ticker, exc)
        return {}

    identity: dict[str, str] = {}
    company_name = _clean_identity_value(info.get("longName")) or _clean_identity_value(
        info.get("shortName")
    )
    if company_name:
        identity["company_name"] = company_name
    for source_key, target_key in (
        ("sector", "sector"),
        ("industry", "industry"),
        ("exchange", "exchange"),
        ("quoteType", "quote_type"),
    ):
        value = _clean_identity_value(info.get(source_key))
        if value:
            identity[target_key] = value
    return identity


def build_instrument_context(
    ticker: str,
    asset_type: str = "stock",
    identity: Mapping[str, str] | None = None,
) -> str:
    """Describe the exact instrument so agents preserve identity and ticker.

    When ``identity`` is provided (resolved deterministically via
    :func:`resolve_instrument_identity`), the company name and business
    classification are injected so agents anchor to the real company rather
    than pattern-matching the price chart to a wrong one (#814).
    """
    is_crypto = asset_type == "crypto"
    instrument_label = "asset" if is_crypto else "instrument"
    context = (
        f"The {instrument_label} to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`, `-USD`)."
    )

    details = []
    if identity:
        name = identity.get("company_name") or identity.get("name")
        if name:
            details.append(f"{'Name' if is_crypto else 'Company'}: {name}")
        sector, industry = identity.get("sector"), identity.get("industry")
        if sector and industry:
            details.append(f"Business classification: {sector} / {industry}")
        elif sector:
            details.append(f"Sector: {sector}")
        elif industry:
            details.append(f"Industry: {industry}")
        if identity.get("exchange"):
            details.append(f"Exchange: {identity['exchange']}")

    if details:
        context += (
            f" Resolved identity: {'; '.join(details)}. "
            "Do not substitute a different company or ticker unless a tool "
            "result explicitly disproves this resolved identity."
        )

    if is_crypto:
        context += (
            " Treat it as a crypto asset rather than a company, and do not "
            "assume company fundamentals are available."
        )
    return context


_FACTOR_LABELS = {
    "value": "Value",
    "quality": "Quality",
    "growth": "Growth",
    "momentum": "Momentum",
    "lowvol": "Low-Vol",
    "investment": "Investment",
}

_V2_ZONE_LABELS = {
    "KANDIDAT": "KANDIDAT (buy candidate: above the entry percentile)",
    "HALTEN": "HALTEN (hold: between exit and entry percentile)",
    "VERKAUFEN": "VERKAUFEN (sell: below the exit percentile)",
    "FILTER": "FILTER (fails the universe filters, not investable)",
}

_V2_SEGMENT_LABELS = {
    "financial": "financial (bank/insurer indicator set: P/B, P/E, ROE, accruals)",
    "real_estate": "real estate (non-financial indicator set without accruals)",
    "nonfinancial": "non-financial",
}

_PRIOR_FOOTER = (
    "\nTreat this pre-rating as a prior, dry quantitative evaluation to "
    "validate or challenge with your own in-depth analysis. It is NOT "
    "ground truth and must not replace your independent judgment; "
    "explicitly note where your findings confirm or contradict it."
)


def _fmt_num(value: Any, digits: int = 2, signed: bool = False) -> str | None:
    """Compact number for prompt text; ``None`` for non-numeric input."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"


def _format_composite_v2(comp: Mapping[str, Any]) -> list[str]:
    """Lines for the multi_factor Composite v2 block (score, zone, factor and
    indicator z-scores, universe filter). Every field is optional."""
    lines: list[str] = []

    overall = []
    if (score := _fmt_num(comp.get("score"), 1)) is not None:
        overall.append(f"composite score {score}/100 (percentile rank x 100)")
    else:
        overall.append(
            "composite score not computable for this stock (insufficient "
            "factor coverage); factor and indicator data below still apply"
        )
    if (z := _fmt_num(comp.get("z"), 2, signed=True)) is not None:
        overall.append(f"composite z {z}")
    if comp.get("classification"):
        overall.append(f"classification {comp['classification']}")
    zone = str(comp.get("zone") or "").upper()
    if zone:
        overall.append("zone " + _V2_ZONE_LABELS.get(zone, zone))
    if overall:
        lines.append("- Overall (Composite v2): " + ", ".join(overall) + ".")

    thresholds = comp.get("zone_thresholds") or {}
    if isinstance(thresholds, Mapping):
        entry = _fmt_num(thresholds.get("entry_pct"), 3)
        exit_ = _fmt_num(thresholds.get("exit_pct"), 3)
        if entry is not None or exit_ is not None:
            bits = []
            if entry is not None:
                bits.append(f"entry >= {entry}")
            if exit_ is not None:
                bits.append(f"exit < {exit_}")
            lines.append(
                "- Zone thresholds (composite percentile): " + ", ".join(bits) + "."
            )

    meta = []
    segment = str(comp.get("segment") or "")
    if segment:
        meta.append("segment " + _V2_SEGMENT_LABELS.get(segment, segment))
    if (cov := _fmt_num(comp.get("data_coverage"), 2)) is not None:
        meta.append(f"data coverage {cov}")
    if meta:
        lines.append("- Scoring basis: " + ", ".join(meta) + ".")

    factors = comp.get("factors") or {}
    if isinstance(factors, Mapping):
        rendered = []
        for name, entry in factors.items():
            if not isinstance(entry, Mapping):
                continue
            z = _fmt_num(entry.get("z"), 2, signed=True)
            label = _FACTOR_LABELS.get(name, str(name))
            if z is None:
                rendered.append(f"{label} n/a (insufficient indicator coverage)")
                continue
            extras = []
            if (w := _fmt_num(entry.get("weight_strategic"), 2)) is not None:
                extras.append(f"weight {w}")
            if (c := _fmt_num(entry.get("coverage"), 2)) is not None:
                extras.append(f"coverage {c}")
            rendered.append(f"{label} {z}" + (f" ({', '.join(extras)})" if extras else ""))
        if rendered:
            lines.append(
                "- Factor z-scores (peer-neutralised, positive = better than "
                "peers): " + ", ".join(rendered) + "."
            )

    indicators = comp.get("indicators") or {}
    if isinstance(indicators, Mapping):
        by_factor: dict[str, list[str]] = {}
        for name, entry in indicators.items():
            if not isinstance(entry, Mapping):
                continue
            z = _fmt_num(entry.get("z"), 2, signed=True)
            raw = _fmt_num(entry.get("value"), 4)
            if z is None and raw is None:
                continue
            parts = []
            if z is not None:
                parts.append(f"z {z}")
            if raw is not None:
                parts.append(f"raw {raw}")
            text = f"{name} ({', '.join(parts)}"
            if entry.get("direction") == -1:
                text += ", lower is better"
            if entry.get("neut_level"):
                text += f", vs {entry['neut_level']}"
            text += ")"
            by_factor.setdefault(str(entry.get("factor") or "other"), []).append(text)
        if by_factor:
            chunks = [
                f"{_FACTOR_LABELS.get(f, f)}: " + "; ".join(items)
                for f, items in by_factor.items()
            ]
            lines.append("- Indicators behind the factors: " + " | ".join(chunks) + ".")

    filt = comp.get("filter") or {}
    if isinstance(filt, Mapping) and "pass" in filt:
        if filt.get("pass"):
            lines.append("- Universe filter: passed.")
        else:
            reasons = [str(r) for r in (filt.get("reasons") or []) if r]
            lines.append(
                "- Universe filter: FAILED"
                + (f" ({', '.join(reasons)})" if reasons else "")
                + "."
            )

    if comp.get("trend_warning"):
        lines.append(
            "- Trend warning: death cross (price below SMA-50 below SMA-200); "
            "informational only, does not trigger a sell in the model."
        )

    methodology = comp.get("methodology")
    if isinstance(methodology, str):
        methodology = [methodology]
    if isinstance(methodology, (list, tuple)):
        notes = [str(m).strip() for m in methodology if str(m).strip()]
        if notes:
            lines.append(
                "- Methodology of the factor model (use it to interpret the "
                "numbers above):"
            )
            lines.extend(f"  * {note}" for note in notes)
    return lines


def format_factor_context(factor_context: Mapping[str, Any] | None) -> str:
    """Render an external quantitative pre-rating as an instrument-context block.

    The block is framed explicitly as a prior to validate or challenge — not
    ground truth — so agents build on the dry factor evaluation without
    over-anchoring on it. Returns "" when no factor context was supplied.

    Two payload shapes are supported: the legacy flat v1 scoring
    (``total_score``, ``factor_scores`` 0-100, ``recommendation``) and the
    multi_factor Composite v2 block (``composite_v2``: composite score/
    percentile/z, class, zone, factor and indicator z-scores, universe
    filter). Neutral fields (Piotroski, Altman-Z, technical signals) are
    rendered for both.
    """
    if not factor_context:
        return ""

    lines = []
    as_of = factor_context.get("as_of")
    source = factor_context.get("source") or "an external deterministic factor model"
    composite = factor_context.get("composite_v2")
    version = factor_context.get("scoring_version")
    if isinstance(composite, Mapping) and composite:
        version = version or "v2"
    lines.append(
        f"A quantitative multi-factor pre-rating from `{source}`"
        + (f" (scoring {version})" if version else "")
        + (f" (as of {as_of})" if as_of else "")
        + " is available for this instrument:"
    )

    if isinstance(composite, Mapping) and composite:
        lines.extend(_format_composite_v2(composite))
    else:
        total = factor_context.get("total_score")
        classification = factor_context.get("classification")
        if total is not None or classification:
            parts = []
            if total is not None:
                parts.append(f"total score {total:g}/100")
            if classification:
                parts.append(f"classification {classification}")
            lines.append("- Overall: " + ", ".join(parts) + ".")

        scores = factor_context.get("factor_scores") or {}
        if scores:
            rendered = ", ".join(
                f"{_FACTOR_LABELS.get(k, k)} {v:g}"
                for k, v in scores.items()
                if isinstance(v, (int, float))
            )
            if rendered:
                lines.append(f"- Factor scores (0-100): {rendered}.")

    quality_bits = []
    if factor_context.get("filter_ok"):
        quality_bits.append(f"quality filters passed: {factor_context['filter_ok']}")
    if factor_context.get("piotroski") is not None:
        quality_bits.append(f"Piotroski F-Score {factor_context['piotroski']:g}/9")
    if factor_context.get("altman_z") is not None:
        quality_bits.append(f"Altman-Z {factor_context['altman_z']:g}")
    if quality_bits:
        lines.append("- Quality screen: " + ", ".join(quality_bits) + ".")

    if factor_context.get("recommendation"):
        lines.append(f"- Model recommendation: {factor_context['recommendation']}.")

    signals = factor_context.get("signals") or {}
    signal_bits = [f"{k}: {v}" for k, v in signals.items() if v not in (None, "")]
    if signal_bits:
        lines.append("- Technical signals: " + ", ".join(signal_bits) + ".")

    return (
        "\n\n--- Quantitative multi-factor pre-rating ---\n"
        + "\n".join(lines)
        + _PRIOR_FOOTER
    )


def get_instrument_context_from_state(state: Mapping[str, Any]) -> str:
    """Return the instrument context for the current run.

    Prefers the identity-resolved context computed once at run start and
    stored on the state (see ``TradingAgentsGraph.resolve_instrument_context``).
    Falls back to a ticker-only context — with no network lookup — when the
    state was constructed without it (bare programmatic states, tests), so a
    consumer is never forced to make a yfinance call mid-graph.
    """
    context = state.get("instrument_context")
    if isinstance(context, str) and context.strip():
        return context
    return build_instrument_context(
        str(state["company_of_interest"]),
        state.get("asset_type", "stock"),
    )


def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add a context-anchored placeholder.

        The placeholder must not be a bare ``"Continue"``: some
        OpenAI-compatible providers interpret that literally as the user task
        and produce output about the word "continue" instead of analysing the
        instrument (#888). Anchoring it to the resolved instrument context and
        date keeps the next analyst on-task even if the provider treats the
        placeholder as a standalone request.
        """
        messages = state["messages"]
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        instrument_context = get_instrument_context_from_state(state)
        trade_date = state.get("trade_date", "the requested date")
        placeholder = HumanMessage(
            content=(
                f"Proceed with your assigned analysis for this workflow. "
                f"{instrument_context} The analysis date is {trade_date}."
            )
        )
        return {"messages": removal_operations + [placeholder]}

    return delete_messages



