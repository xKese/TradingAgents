"""Credential-free operational evidence provider for synthetic fixtures."""

from __future__ import annotations

import json
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from tradingagents.evidence import consolidate_evidence, prepare_evidence

from .config import get_config


def _load_fixture_payload() -> dict[str, Any]:
    configured = get_config().get("operational_fixture_path")
    if configured:
        return json.loads(Path(configured).read_text(encoding="utf-8"))
    fixture = resources.files("evaluations.fixtures").joinpath("operational_cases.json")
    return json.loads(fixture.read_text(encoding="utf-8"))


def get_fixture_operational_evidence(ticker: str, curr_date: str) -> str:
    """Return the matching synthetic fixture case as provider-compatible JSON."""
    payload = _load_fixture_payload()
    ticker_upper = ticker.upper()
    selected = next(
        (
            case
            for case in payload.get("cases", [])
            if str(case.get("ticker", "")).upper() == ticker_upper
            and case.get("analysis_date") == curr_date
        ),
        None,
    )
    if selected is None:
        return json.dumps(
            {
                "status": "unavailable",
                "ticker": ticker_upper,
                "analysis_date": curr_date,
                "evidence_records": [],
                "retrieval_failures": ["No matching synthetic fixture case."],
                "limitations": "Synthetic fixture provider; no live source retrieval was attempted.",
            },
            sort_keys=True,
        )

    records = prepare_evidence(
        selected.get("evidence_records", []),
        date.fromisoformat(curr_date),
        strict_temporal=True,
    )
    records = consolidate_evidence(records)
    return json.dumps(
        {
            "status": "ok" if records else "unavailable",
            "case_id": selected.get("case_id"),
            "ticker": ticker_upper,
            "company_name": selected.get("company_name"),
            "analysis_date": curr_date,
            "evidence_records": [record.model_dump(mode="json") for record in records],
            "retrieval_failures": selected.get("retrieval_failures", []),
            "limitations": "All fixture content is synthetic and is for offline testing only.",
        },
        sort_keys=True,
    )
