"""Point-in-time operational evidence retrieval from SEC EDGAR filings."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timezone
from typing import Any

import requests
from parsel import Selector

from tradingagents.evidence import EvidenceRecord, consolidate_evidence, prepare_evidence

from .config import get_config
from .errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError

SEC_DATA_BASE = "https://data.sec.gov"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
REQUEST_TIMEOUT = 30

_CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "backlog_and_demand": (
        "backlog",
        "book-to-bill",
        "book to bill",
        "bookings",
        "remaining performance obligation",
        "order cancellation",
        "demand visibility",
        "lead time",
    ),
    "capacity_and_capex": (
        "capacity utilization",
        "capacity expansion",
        "manufacturing capacity",
        "production capacity",
        "capital expenditure",
        "capital expenditures",
        "new facility",
        "factory expansion",
    ),
    "concentration_risk": (
        "customer concentration",
        "supplier concentration",
        "geographic concentration",
        "segment concentration",
        "significant customer",
        "major customer",
        "single supplier",
    ),
    "supply_chain": (
        "supply chain",
        "supply constraint",
        "production constraint",
        "component shortage",
        "bottleneck",
        "inventory levels",
        "inventory management",
    ),
    "contracts_and_guidance": (
        "contract duration",
        "long-term contract",
        "customer win",
        "customer loss",
        "demand guidance",
        "capacity guidance",
    ),
}


def _sec_user_agent() -> str:
    user_agent = get_config().get("sec_user_agent") or os.getenv("SEC_USER_AGENT")
    if not user_agent:
        raise VendorNotConfiguredError(
            "SEC retrieval requires TRADINGAGENTS_SEC_USER_AGENT (for example, "
            "'Your Name your-email@example.com')."
        )
    return str(user_agent)


def _get(url: str) -> requests.Response:
    response = requests.get(
        url,
        headers={"User-Agent": _sec_user_agent(), "Accept-Encoding": "gzip, deflate"},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code in {403, 429}:
        raise VendorRateLimitError(
            f"SEC EDGAR rejected the request with HTTP {response.status_code}; "
            "verify the User-Agent and retry rate."
        )
    response.raise_for_status()
    return response


def _request_json(url: str) -> dict[str, Any]:
    payload = _get(url).json()
    if not isinstance(payload, dict):
        raise ValueError(f"SEC endpoint returned a non-object payload: {url}")
    return payload


def _resolve_company(ticker: str) -> tuple[int, str]:
    payload = _request_json("https://www.sec.gov/files/company_tickers.json")
    ticker_upper = ticker.upper()
    for item in payload.values():
        if str(item.get("ticker", "")).upper() == ticker_upper:
            return int(item["cik_str"]), str(item.get("title") or ticker_upper)
    raise NoMarketDataError(ticker, detail="ticker not found in SEC company_tickers.json")


def _recent_filings(
    submissions: dict[str, Any],
    analysis_date: date,
    *,
    limit: int,
) -> list[dict[str, str]]:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    keys = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")
    rows = []
    for values in zip(*(recent.get(key, []) for key in keys), strict=False):
        row = dict(zip(keys, values, strict=True))
        try:
            filing_date = date.fromisoformat(row["filingDate"])
        except (TypeError, ValueError):
            continue
        if row["form"] not in {"10-K", "10-Q", "8-K"}:
            continue
        if filing_date > analysis_date or not row["primaryDocument"]:
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _filing_text(url: str) -> str:
    response = _get(url)
    selector = Selector(text=response.text)
    chunks = selector.xpath("//body//text()").getall()
    if not chunks:
        chunks = selector.xpath("//text()").getall()
    text = " ".join(chunks) if chunks else response.text
    return " ".join(text.split())


def _short_excerpt(text: str, terms: tuple[str, ...]) -> str | None:
    """Return one short sentence containing a category keyword."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        lowered = sentence.casefold()
        if not any(term in lowered for term in terms):
            continue
        compact = " ".join(sentence.split())
        if len(compact) > 360:
            compact = compact[:357].rstrip() + "..."
        return compact
    return None


def get_sec_operational_evidence(ticker: str, curr_date: str) -> str:
    """Return short, date-valid SEC filing excerpts as evidence JSON."""
    analysis_date = date.fromisoformat(curr_date)
    cik, company_name = _resolve_company(ticker)
    submissions = _request_json(f"{SEC_DATA_BASE}/submissions/CIK{cik:010d}.json")
    max_filings = int(get_config().get("operational_max_filings", 4))
    filings = _recent_filings(submissions, analysis_date, limit=max_filings)
    records: list[EvidenceRecord] = []
    failures: list[str] = []

    for filing in filings:
        accession = filing["accessionNumber"]
        accession_path = accession.replace("-", "")
        document = filing["primaryDocument"]
        source_url = f"{SEC_ARCHIVES_BASE}/{cik}/{accession_path}/{document}"
        try:
            text = _filing_text(source_url)
        except (requests.RequestException, VendorRateLimitError) as exc:
            failures.append(f"{accession}: {type(exc).__name__}")
            continue

        for category, terms in _CATEGORY_TERMS.items():
            excerpt = _short_excerpt(text, terms)
            if not excerpt:
                continue
            filing_date = date.fromisoformat(filing["filingDate"])
            records.append(
                EvidenceRecord(
                    claim_category=category,
                    source_type="sec_filing",
                    source_title=(
                        f"{company_name} {filing['form']} filed {filing['filingDate']}"
                    ),
                    source_url=source_url,
                    publisher="U.S. Securities and Exchange Commission",
                    publication_date=filing_date,
                    filing_date=filing_date,
                    reporting_period=filing.get("reportDate") or None,
                    retrieved_at=datetime.now(timezone.utc),
                    ticker=ticker,
                    short_excerpt=excerpt,
                    confidence="high",
                    is_primary_source=True,
                    metadata={
                        "accession_number": accession,
                        "cik": cik,
                        "company_name": company_name,
                        "form": filing["form"],
                    },
                )
            )

    prepared = consolidate_evidence(prepare_evidence(records, analysis_date))
    status = "ok" if prepared else "unavailable"
    return json.dumps(
        {
            "status": status,
            "ticker": ticker.upper(),
            "company_name": company_name,
            "analysis_date": curr_date,
            "evidence_records": [record.model_dump(mode="json") for record in prepared],
            "retrieval_failures": failures,
            "limitations": (
                "Keyword extraction finds short filing passages; it does not infer facts that "
                "the filing does not explicitly state."
            ),
        },
        sort_keys=True,
    )
