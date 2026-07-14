"""Red-flag trigger detection for the short sleeve — the reason to look NOW.

Mirror of ops/research/triggers.py. Kinds map to
short_screen.SHORT_TRIGGER_KINDS. Every source degrades independently: a
full-text-search failure must not suppress an 8-K red flag. Every callable
is injectable so tests run with zero network.
"""
from __future__ import annotations

from datetime import date, timedelta

from ops.research.triggers import TRIGGER_LOOKBACK_DAYS, Trigger
from tradingagents.dataflows import edgar

SHORT_8K_ITEMS = {"4.02", "1.03", "3.01"}
CFO_ITEM = "5.02"
INSIDER_SELL_CLUSTER_MIN = 3
GOING_CONCERN_QUERY = '"substantial doubt" "going concern"'


def find_short_triggers(
    ticker: str,
    *,
    asof: date,
    lookback_days: int = TRIGGER_LOOKBACK_DAYS,
    list_filings=None,
    transactions_fetcher=None,
    full_text_search=None,
    fetch_text=None,
    cik_resolver=None,
) -> list[Trigger]:
    list_filings = list_filings or edgar.list_filings
    full_text_search = full_text_search or edgar.full_text_search
    fetch_text = fetch_text or edgar.fetch_filing_text
    cik_resolver = cik_resolver or edgar.get_cik
    since = asof - timedelta(days=lookback_days)
    out: list[Trigger] = []

    out += _red_flag_8ks(ticker, since=since, asof=asof,
                         list_filings=list_filings, fetch_text=fetch_text)
    cluster = _insider_sell_cluster(ticker, since=since, asof=asof,
                                    transactions_fetcher=transactions_fetcher)
    if cluster is not None:
        out.append(cluster)
    gc = _going_concern(ticker, since=since, asof=asof,
                        full_text_search=full_text_search,
                        cik_resolver=cik_resolver)
    if gc is not None:
        out.append(gc)
    return out


def _red_flag_8ks(ticker, *, since, asof, list_filings, fetch_text) -> list[Trigger]:
    # Filing.items carries the raw 8-K item numbers (tuple[str, ...]).
    out = []
    for f in list_filings(ticker, forms={"8-K"}, since=since):
        if f.filing_date is None or f.filing_date > asof:
            continue
        hit_items = set(f.items) & SHORT_8K_ITEMS
        if hit_items:
            out.append(Trigger(
                kind="red_flag_8k", description=", ".join(sorted(hit_items)),
                date=f.filing_date, source=f.accession_number,
            ))
            continue
        if CFO_ITEM in f.items:
            # 5.02 alone is any officer/director churn — only a CFO exit is
            # a red flag, so the (rare) 5.02 filing text gets one fetch.
            try:
                text = fetch_text(f)
            except Exception:
                continue  # degrade: unreadable 8-K is not a trigger
            if ("Chief Financial Officer" in text
                    or "principal financial officer" in text.lower()):
                out.append(Trigger(
                    kind="red_flag_8k", description="CFO departure (5.02)",
                    date=f.filing_date, source=f.accession_number,
                ))
    return out


def _insider_sell_cluster(ticker, *, since, asof, transactions_fetcher) -> Trigger | None:
    """>= INSIDER_SELL_CLUSTER_MIN distinct insiders selling open-market,
    non-10b5-1 — a higher bar than the buy cluster (routine selling is
    common; three discretionary sellers in one window is not)."""
    from tradingagents.dataflows.form4 import get_insider_transactions

    fetch = transactions_fetcher or get_insider_transactions
    txns = fetch(ticker, since=since)
    sells = [
        t for t in txns
        if t.code == "S" and not t.ten_b5_1
        and t.transaction_date is not None and since <= t.transaction_date <= asof
    ]
    sellers = {t.insider_name for t in sells}
    if len(sellers) < INSIDER_SELL_CLUSTER_MIN:
        return None
    latest = max(sells, key=lambda t: t.transaction_date)
    return Trigger(
        kind="insider_sell_cluster",
        description=f"{len(sellers)} insiders sold (non-10b5-1) in window",
        date=latest.transaction_date, source=latest.accession,
    )


def _going_concern(ticker, *, since, asof, full_text_search, cik_resolver) -> Trigger | None:
    try:
        cik = cik_resolver(ticker)
        hits = full_text_search(GOING_CONCERN_QUERY, forms={"10-K", "10-Q"},
                                start=since, end=asof)
    except Exception:
        return None  # degrade: FTS/CIK failure must not suppress other flags
    for hit in hits:
        src = hit.get("_source", {})
        ciks = {int(c) for c in src.get("ciks", []) if str(c).isdigit()}
        if cik in ciks:
            return Trigger(
                kind="going_concern",
                description="going-concern language in 10-K/10-Q",
                date=asof, source=hit.get("_id", "fts"),
            )
    return None
