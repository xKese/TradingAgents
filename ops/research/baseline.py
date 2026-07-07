"""The null-baseline portfolio: equal-weight everything that passes the screen.

This is the control the whole research system is measured against (design
doc, "the mandatory null baseline"): if the LLM deep-research stages cannot
beat this dumb portfolio by more than the token bill, they are not adding
value. It must therefore stay dumb on purpose — no guardrails, no stops, no
conviction sizing, no discretion. Separate journal DB from the trading
journal so the control can never contaminate real state.

Policy:
- exits first: close any position held >= BASELINE_MAX_HOLD_DAYS (matched to
  the value sleeve's 12-month floor horizon);
- then buy every passer not currently held at BASELINE_SLICE_PCT of current
  equity (~equal weight at the target ~25 names), clamped to available cash;
- re-running on the same day is idempotent because held names are skipped.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from ops import events
from ops.broker.base import Broker, InsufficientFunds, NoSuchPosition, QuoteUnavailable
from ops.broker.types import Order, OrderType, Side
from ops.journal import Journal

BASELINE_SLICE_PCT = Decimal("0.04")
BASELINE_MAX_HOLD_DAYS = 365
_MIN_ORDER_DOLLARS = Decimal("100")
DELIST_WRITEOFF_RUNS = 3

# Outage guard (final-review Important-1, incident 2026-07-06): a feed-wide
# quote outage makes make_yfinance_quote_source raise for every symbol, and
# that is journaled as QuoteUnavailable identically to a genuine delisted
# quote -- auto_write_off_delisted cannot tell the two apart from the
# journal alone. This repo already lived the every-fetch-fails mode once; if
# it recurs on 3 consecutive Saturday runs, treating it as delisting would
# write off the ENTIRE control portfolio at cost -- irreversible, and it
# permanently corrupts the null baseline. A simultaneous failure across a
# STRICT MAJORITY of the held book (or the whole book) in a single run is
# far more likely to be an outage than that many names delisting on the
# same day, so the write-off pass is skipped entirely for that run when
# this fires. Quote-failure events are still journaled beforehand, so
# streak history for genuinely delisted names is preserved and the
# write-off resumes once quotes recover (or once other positions are
# added/removed and the book is no longer majority-failing).
QUOTE_OUTAGE_MAJORITY_MULTIPLE = 2


def _equity_with_fallback(broker: Broker, journal: Journal) -> Decimal:
    """Equity for sizing/reporting, resilient to unquotable positions.

    A delisted name (tender, acquisition) has no live quote; PaperBroker's
    get_equity would raise and wedge the control forever. Mark such a
    position at its last journaled BUY fill price instead — stale, but the
    baseline keeps accruing, which is the whole point of a control.
    """
    total = broker.get_cash()
    for pos in broker.get_positions():
        try:
            price = broker.get_quote(pos.symbol)
        except QuoteUnavailable:
            last_buy = journal.last_buy_fill_for(pos.symbol)
            price = last_buy["price"] if last_buy is not None else pos.avg_entry_price
        total += pos.market_value(price)
    return total


def update_baseline_portfolio(
    *,
    broker: Broker,
    journal: Journal,
    passers: list[str],
    asof: date,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    exits: list[str] = []
    for pos in list(broker.get_positions()):
        last_buy = journal.last_buy_fill_for(pos.symbol)
        if last_buy is None:
            # No journaled BUY fill for a held position has no hold clock.
            # Replay can't produce this from normal operation — if it shows
            # up, don't guess at a hold age; leave it for manual resolution
            # (the monitoring loop, build-order step 6).
            continue
        held_days = (now - last_buy["filled_at"]).days
        if held_days < BASELINE_MAX_HOLD_DAYS:
            continue
        try:
            broker.close_position(pos.symbol)
        except QuoteUnavailable as exc:
            print(f"[baseline] exit skipped {pos.symbol}: {exc}", file=sys.stderr)
            continue
        journal.record_event(
            events.KIND_BASELINE_EXIT,
            events.baseline_exit_payload(symbol=pos.symbol, held_days=held_days),
        )
        exits.append(pos.symbol)

    held = {p.symbol for p in broker.get_positions()}
    slice_dollars = _equity_with_fallback(broker, journal) * BASELINE_SLICE_PCT
    buys: list[str] = []
    skipped: list[str] = []
    for symbol in sorted(dict.fromkeys(passers)):
        if symbol in held:
            continue
        notional = min(slice_dollars, broker.get_cash())
        if notional < _MIN_ORDER_DOLLARS:
            break
        # uuid4-suffixed: client_order_id must be unique per ORDER, not per
        # (day, symbol) — PaperBroker journals the order row BEFORE quoting,
        # so a QuoteUnavailable skip leaves an orphan order row and a
        # same-day retry would collide with the journal's UNIQUE index
        # (same rationale as ops/strategy/post_earnings_momentum.py).
        order = Order(
            client_order_id=f"baseline-{asof.isoformat()}-{symbol}-{uuid4().hex[:8]}",
            symbol=symbol,
            side=Side.BUY,
            notional_dollars=notional,
            order_type=OrderType.MARKET,
        )
        try:
            broker.place_order(order)
        except QuoteUnavailable as exc:
            print(f"[baseline] buy skipped {symbol}: {exc}", file=sys.stderr)
            skipped.append(symbol)
            continue
        except InsufficientFunds:
            break
        buys.append(symbol)

    equity = _equity_with_fallback(broker, journal)
    journal.record_event(
        events.KIND_BASELINE_SCREEN_RUN,
        events.baseline_screen_run_payload(
            asof=asof.isoformat(), passers=len(passers),
            buys=buys, exits=exits, skipped=skipped, equity=equity,
        ),
    )
    journal.record_equity_snapshot(
        kind="baseline_run", equity=equity, cash=broker.get_cash(), at=now,
    )
    return {"buys": buys, "exits": exits, "skipped": skipped}


def _journal_synthetic_sell(
    journal: Journal,
    position,
    price: Decimal,
    *,
    now: datetime,
    coid_prefix: str,
) -> None:
    """Journal a SELL order+fill for `position` directly (no broker quote
    involved) — the shared core behind manual write-off and the automated
    delisted write-off. Replay reconstructs the cash from these two rows."""
    proceeds = position.quantity * price
    coid = f"{coid_prefix}-{now.date().isoformat()}-{position.symbol}-{uuid4().hex[:8]}"
    journal.record_order(
        client_order_id=coid, symbol=position.symbol, side=Side.SELL.value,
        notional_dollars=proceeds, stop_loss_price=None,
    )
    journal.record_fill(
        order_id=str(uuid4()), client_order_id=coid, symbol=position.symbol,
        side=Side.SELL.value, quantity=position.quantity, price=price, filled_at=now,
    )


def write_off_position(
    *,
    journal: Journal,
    symbol: str,
    price: Decimal,
    starting_cash: Decimal,
    note: str | None = None,
) -> dict:
    """Manually resolve a position the broker can no longer quote (delisted:
    tender, acquisition, bankruptcy) by journaling a synthetic SELL at the
    known settlement price. PaperBroker.close_position would quote and fail,
    so the order+fill are written directly — replay reconstructs the cash.
    """
    from ops.broker.paper import PaperBroker

    broker = PaperBroker.from_journal(
        journal=journal,
        quote_source=_no_quotes,
        starting_cash=starting_cash,
    )
    position = next((p for p in broker.get_positions() if p.symbol == symbol.upper()), None)
    if position is None:
        raise NoSuchPosition(f"no baseline position in {symbol!r}")
    now = datetime.now(timezone.utc)
    _journal_synthetic_sell(journal, position, price, now=now, coid_prefix="baseline-writeoff")
    journal.record_event(
        events.KIND_BASELINE_WRITEOFF,
        events.baseline_writeoff_payload(
            symbol=symbol.upper(), quantity=position.quantity, price=price, note=note,
        ),
    )
    return {
        "symbol": symbol.upper(), "quantity": str(position.quantity),
        "price": str(price), "proceeds": str(position.quantity * price),
    }


def _no_quotes(symbol: str) -> Decimal:
    raise AssertionError("write-off must never quote")


def _is_quote_outage(*, failing_count: int, held_count: int) -> bool:
    """True when this run's failing-quote count looks like a feed outage
    rather than delisting: a strict majority of a multi-position book, or
    (n=1 special case, since one name can't be told apart from an outage
    by count alone) the entire book. See QUOTE_OUTAGE_MAJORITY_MULTIPLE."""
    if held_count >= 1 and failing_count == held_count:
        return True
    return held_count >= 2 and failing_count * QUOTE_OUTAGE_MAJORITY_MULTIPLE > held_count


def auto_write_off_delisted(
    *,
    journal: Journal,
    quote_source,
    starting_cash: Decimal,
    asof: date,
    now: datetime | None = None,
) -> list[dict]:
    """Write off positions that failed to quote on DELIST_WRITEOFF_RUNS
    consecutive baseline runs (spec Phase C). Consecutiveness is derived
    from the journal — a failure event per failing run asof, checked against
    the last N-1 baseline_screen_run asofs — so there is no counter state to
    lose. Runs BEFORE the main baseline broker is built: the replay after
    this call no longer contains the dead position.

    If a majority (or all) of the held book fails to quote in this run, the
    write-off pass is skipped entirely — see _is_quote_outage /
    QUOTE_OUTAGE_MAJORITY_MULTIPLE. Failure events are journaled regardless.
    """
    from ops.broker.paper import PaperBroker

    now = now or datetime.now(timezone.utc)
    broker = PaperBroker.from_journal(
        journal=journal, quote_source=quote_source, starting_cash=starting_cash,
    )
    positions = broker.get_positions()
    held_count = len(positions)
    failing: list = []
    for pos in positions:
        try:
            broker.get_quote(pos.symbol)
        except QuoteUnavailable as exc:
            already_journaled = journal.count_events(
                events.KIND_BASELINE_QUOTE_FAILURE,
                payload_equals={"symbol": pos.symbol, "asof": asof.isoformat()},
            ) > 0
            if not already_journaled:
                journal.record_event(
                    events.KIND_BASELINE_QUOTE_FAILURE,
                    events.baseline_quote_failure_payload(
                        symbol=pos.symbol, asof=asof.isoformat(), error=str(exc),
                    ),
                )
            failing.append(pos)

    if not failing:
        return []
    if _is_quote_outage(failing_count=len(failing), held_count=held_count):
        # Outage, not delisting (see QUOTE_OUTAGE_MAJORITY_MULTIPLE above).
        # Failures are already journaled; skip the write-off pass entirely.
        return []
    run_asofs = [
        e["payload"]["asof"]
        for e in journal.read_events()
        if e["kind"] == events.KIND_BASELINE_SCREEN_RUN
    ]
    # Exclude today's own asof: on a same-asof re-run (crash-retry, or
    # running the screen twice in one day), today's date can already be
    # among the distinct run asofs, and the failure event journaled moments
    # ago by this very call would satisfy that slot — collapsing the
    # required 3 DISTINCT failing run dates to 2.
    prior = [
        a for a in dict.fromkeys(run_asofs) if a != asof.isoformat()
    ][-(DELIST_WRITEOFF_RUNS - 1):]
    if len(prior) < DELIST_WRITEOFF_RUNS - 1:
        return []  # not enough baseline-run history for a streak

    written_off: list[dict] = []
    for pos in failing:
        streak = all(
            journal.count_events(
                events.KIND_BASELINE_QUOTE_FAILURE,
                payload_equals={"symbol": pos.symbol, "asof": prior_asof},
            ) > 0
            for prior_asof in prior
        )
        if not streak:
            continue
        last_buy = journal.last_buy_fill_for(pos.symbol)
        price = last_buy["price"] if last_buy is not None else pos.avg_entry_price
        _journal_synthetic_sell(journal, pos, price, now=now,
                                coid_prefix="baseline-auto-writeoff")
        journal.record_event(
            events.KIND_BASELINE_AUTO_WRITEOFF,
            events.baseline_auto_writeoff_payload(
                symbol=pos.symbol, quantity=str(pos.quantity), price=str(price),
                failing_runs=DELIST_WRITEOFF_RUNS,
                note=f"quote failures on {DELIST_WRITEOFF_RUNS} consecutive baseline runs",
            ),
        )
        written_off.append({
            "symbol": pos.symbol, "quantity": str(pos.quantity),
            "price": str(price), "failing_runs": DELIST_WRITEOFF_RUNS,
        })
    return written_off
