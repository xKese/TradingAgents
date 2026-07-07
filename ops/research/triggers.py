"""Change-trigger detection — the reason to look at a name NOW.

A name enters deep research only when it is cheap+quality AND has a change
trigger (design doc: "looking at everything all the time drowns in noise").
Two sources:

- EDGAR filings (via the existing edgar vendor's trigger taxonomy): 13D
  activists, tenders, spinoff registrations, going-private, and 8-Ks whose
  item numbers are in edgar.NOTABLE_8K_ITEMS. Form 4 is excluded from this
  list — see the insider-cluster trigger below.
- Insider clusters: >= INSIDER_CLUSTER_MIN_BUYERS distinct insiders each
  making at least one open-market buy (code P, not a 10b5-1 plan) within
  the lookback window. Routine 10b5-1 sales and equity grants never count —
  raw Form 4 counts are dominated by those and would be noise.
- Price: a guidance-cut-style selloff, defined as the latest close sitting
  >= 25% below the 60-trading-day high.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from tradingagents.dataflows import edgar

TRIGGER_LOOKBACK_DAYS = 90
SELLOFF_LOOKBACK_DAYS = 60
SELLOFF_DRAWDOWN = Decimal("0.25")
# Fewer closes than this and the "60-day high" is meaningless (fresh IPO).
_MIN_SELLOFF_HISTORY = 20


@dataclass(frozen=True)
class Trigger:
    kind: str          # e.g. "activist_stake", "material_event", "selloff"
    description: str
    date: date
    source: str        # accession number, or "price" for the selloff trigger


def find_edgar_triggers(
    ticker: str,
    *,
    asof: date,
    lookback_days: int = TRIGGER_LOOKBACK_DAYS,
    list_filings: Callable[..., list[edgar.Filing]] | None = None,
) -> list[Trigger]:
    list_filings = list_filings or edgar.list_filings
    forms = set(edgar.CHANGE_TRIGGER_FORMS) - {"4"}
    filings = list_filings(ticker, forms=forms, since=asof - timedelta(days=lookback_days))
    out: list[Trigger] = []
    for f in filings:
        if f.filing_date is None or f.filing_date > asof:
            continue
        if f.form == "8-K":
            labels = f.notable_8k_items()
            if not labels:
                continue
            out.append(Trigger(
                kind="material_event", description=", ".join(labels),
                date=f.filing_date, source=f.accession_number,
            ))
            continue
        kind = f.trigger_kind()
        if kind is None:
            continue
        out.append(Trigger(
            kind=kind, description=f.form,
            date=f.filing_date, source=f.accession_number,
        ))
    return out


def find_selloff_trigger(
    symbol: str, closes: list[Decimal], *, asof: date,
) -> Trigger | None:
    """``closes``: up to the last 60 daily closes ending at ``asof``, oldest-first."""
    if len(closes) < _MIN_SELLOFF_HISTORY:
        return None
    peak = max(closes)
    last = closes[-1]
    if peak <= 0:
        return None
    drawdown = (peak - last) / peak
    if drawdown < SELLOFF_DRAWDOWN:
        return None
    return Trigger(
        kind="selloff",
        description=(
            f"{symbol} close {last} is {(drawdown * 100).quantize(Decimal('1'))}% "
            f"below its {SELLOFF_LOOKBACK_DAYS}-day high {peak}"
        ),
        date=asof,
        source="price",
    )


INSIDER_CLUSTER_MIN_BUYERS = 2


def find_insider_cluster_trigger(
    ticker: str,
    *,
    asof: date,
    lookback_days: int = TRIGGER_LOOKBACK_DAYS,
    transactions_fetcher: Callable[..., list] | None = None,
) -> Trigger | None:
    """A cluster of distinct insiders buying on the open market, own cash,
    outside 10b5-1 plans — the strongest single trigger in the taxonomy."""
    from tradingagents.dataflows.form4 import get_insider_transactions

    fetch = transactions_fetcher or get_insider_transactions
    since = asof - timedelta(days=lookback_days)
    txns = fetch(ticker, since=since)
    buys = [
        t for t in txns
        if t.kind == "open_market_buy" and not t.ten_b5_1
        and t.transaction_date is not None and since <= t.transaction_date <= asof
    ]
    buyers = {t.insider_name for t in buys}
    if len(buyers) < INSIDER_CLUSTER_MIN_BUYERS:
        return None
    latest = max(buys, key=lambda t: t.transaction_date)
    return Trigger(
        kind="insider_cluster",
        description=(
            f"{len(buyers)} insiders made open-market buys (non-10b5-1) "
            f"in the last {lookback_days} days"
        ),
        date=latest.transaction_date,
        source=latest.accession,
    )


def find_triggers(
    ticker: str,
    *,
    asof: date,
    lookback_days: int = TRIGGER_LOOKBACK_DAYS,
    list_filings: Callable[..., list[edgar.Filing]] | None = None,
    transactions_fetcher: Callable[..., list] | None = None,
) -> list[Trigger]:
    """All change triggers for a name: EDGAR filings + insider cluster.

    (The price-selloff trigger stays separate in run.py — it needs the price
    context, which this module deliberately does not fetch.)
    """
    out = find_edgar_triggers(
        ticker, asof=asof, lookback_days=lookback_days, list_filings=list_filings,
    )
    cluster = find_insider_cluster_trigger(
        ticker, asof=asof, lookback_days=lookback_days,
        transactions_fetcher=transactions_fetcher,
    )
    if cluster is not None:
        out.append(cluster)
    return out
