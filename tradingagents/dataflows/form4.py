"""Form 4 (insider ownership) XML parsing.

Raw Form 4 filing counts are useless as a signal — dominated by routine
10b5-1 sales and equity grants. The parser separates what matters: an
open-market purchase (code P) outside a 10b5-1 plan is an insider spending
their own cash at market. Clusters of those are the strongest single trigger
in the screener taxonomy (and were deferred from build-order step 3 until
this parser existed).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true"}
_XSL_PREFIX = re.compile(r"^xslF345X\d+/")


@dataclass(frozen=True)
class InsiderTransaction:
    insider_name: str
    insider_title: str
    is_director: bool
    is_officer: bool
    is_ten_pct_owner: bool
    transaction_date: date | None
    code: str
    shares: Decimal | None
    price: Decimal | None
    acquired: bool
    ten_b5_1: bool
    accession: str
    filed_date: date

    @property
    def kind(self) -> str:
        if self.code == "P":
            return "open_market_buy"
        if self.code == "S":
            return "open_market_sale"
        if self.code == "A":
            return "grant"
        return "other"


def _text(node: ET.Element | None, path: str) -> str:
    if node is None:
        return ""
    found = node.find(path)
    return (found.text or "").strip() if found is not None else ""


def _flag(node: ET.Element | None, path: str) -> bool:
    return _text(node, path).lower() in _TRUE_VALUES


def _decimal(raw: str) -> Decimal | None:
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def parse_form4_xml(
    xml_text: str, *, accession: str, filed_date: date,
) -> list[InsiderTransaction]:
    """Parse one ownership document. Malformed XML yields [] (skip, don't die)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("form4 %s: unparseable XML (%s)", accession, exc)
        return []
    ten_b5_1 = (root.findtext("aff10b5One") or "").strip().lower() in _TRUE_VALUES
    owner = root.find("reportingOwner")
    name = _text(owner, "reportingOwnerId/rptOwnerName")
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    out: list[InsiderTransaction] = []
    for txn in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        out.append(InsiderTransaction(
            insider_name=name,
            insider_title=_text(rel, "officerTitle"),
            is_director=_flag(rel, "isDirector"),
            is_officer=_flag(rel, "isOfficer"),
            is_ten_pct_owner=_flag(rel, "isTenPercentOwner"),
            transaction_date=_date(_text(txn, "transactionDate/value")),
            code=_text(txn, "transactionCoding/transactionCode"),
            shares=_decimal(_text(txn, "transactionAmounts/transactionShares/value")),
            price=_decimal(
                _text(txn, "transactionAmounts/transactionPricePerShare/value")
            ),
            acquired=_text(
                txn, "transactionAmounts/transactionAcquiredDisposedCode/value"
            ).upper() == "A",
            ten_b5_1=ten_b5_1,
            accession=accession,
            filed_date=filed_date,
        ))
    return out


def raw_xml_url(filing) -> str:
    """URL of the raw ownership XML (primary_document minus the XSL view prefix)."""
    from tradingagents.dataflows.edgar import ARCHIVES_URL

    document = _XSL_PREFIX.sub("", filing.primary_document)
    return ARCHIVES_URL.format(
        cik=filing.cik,
        accession_nodash=filing.accession_number.replace("-", ""),
        document=document,
    )


def _default_fetch_raw(url: str) -> str:
    from tradingagents.dataflows.edgar import _throttled_get

    return _throttled_get(url).text


def get_insider_transactions(
    ticker: str,
    *,
    since: date,
    max_filings: int = 10,
    list_filings=None,
    fetch_raw=None,
) -> list[InsiderTransaction]:
    """All non-derivative insider transactions from Form 4s filed since ``since``.

    ``max_filings`` caps XML fetches — this runs inside the weekly sweep over
    ~1500 names, so per-name I/O must be bounded. Filings are newest-first.
    """
    from tradingagents.dataflows import edgar

    list_filings = list_filings or edgar.list_filings
    fetch_raw = fetch_raw or _default_fetch_raw
    filings = list_filings(ticker, forms={"4"}, since=since, limit=max_filings)
    out: list[InsiderTransaction] = []
    for filing in filings[:max_filings]:
        try:
            xml_text = fetch_raw(raw_xml_url(filing))
        except Exception as exc:  # one bad document must not kill the sweep
            logger.warning("form4 %s: fetch failed (%s)", filing.accession_number, exc)
            continue
        out.extend(parse_form4_xml(
            xml_text, accession=filing.accession_number,
            filed_date=filing.filing_date,
        ))
    return out
