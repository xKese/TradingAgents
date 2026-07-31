"""Read access to archived run.json sidecars for cross-run context.

The webapp writes a ``run.json`` sidecar per completed run under
``<results_dir>/reports/<SAFE_TICKER>_<YYYYMMDD_HHMMSS>/`` (see
``webapp.server._write_run_archive``). This module locates the newest
sidecar for a ticker and condenses it into a short context string
(date, rating, executive summary) for the decision-layer agents.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Pulls the summary out of the rendered PM decision markdown (see
# ``render_pm_decision`` in agents/schemas.py); stops at the next bold
# section header so the investment thesis is not dragged along.
_EXEC_SUMMARY_RE = re.compile(
    r"\*\*Executive Summary\*\*:?\s*(.+?)(?=\n\s*\*\*|\Z)", re.DOTALL
)
_MAX_SUMMARY_CHARS = 700


def find_latest_run_sidecar(ticker: str, results_dir) -> dict | None:
    """Return the newest parseable run.json payload for ``ticker``, or None.

    Matches on the sidecar's ``ticker`` field (the normalized ticker as the
    run received it), not on the sanitized directory prefix, so tickers whose
    ``safe_ticker_component`` differs from their wire form still match.
    Corrupted or foreign files are skipped — this must never fail a run.
    """
    if not ticker or not results_dir:
        return None
    root = Path(results_dir) / "reports"
    if not root.is_dir():
        return None
    # Directory names end in YYYYMMDD_HHMMSS, so a reverse lexicographic sort
    # yields newest-first (same ordering trick as /api/reports).
    for sidecar_path in sorted(
        root.glob("*/run.json"), key=lambda p: p.parent.name, reverse=True
    ):
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("ticker") == ticker:
            return data
    return None


def build_previous_analysis_context(ticker: str, results_dir) -> str:
    """Compact context string for the newest archived analysis of ``ticker``.

    Returns "" when no archive exists, so callers can inject it verbatim and
    prompts stay unchanged for first-time tickers.
    """
    data = find_latest_run_sidecar(ticker, results_dir)
    if data is None:
        return ""

    final_decision = (data.get("reports") or {}).get("final_trade_decision") or ""
    match = _EXEC_SUMMARY_RE.search(final_decision)
    summary = (match.group(1) if match else final_decision).strip()
    if len(summary) > _MAX_SUMMARY_CHARS:
        summary = summary[:_MAX_SUMMARY_CHARS].rstrip() + "…"

    lines = [
        f"Most recent previous analysis of {ticker}:",
        f"- Date: {data.get('analysis_date') or 'unknown'}",
        f"- Final rating: {data.get('decision') or 'n/a'}",
    ]
    if summary:
        lines.append(f"- Executive summary: {summary}")
    return "\n".join(lines)
