"""The insider-sleeve post-close trade step — mechanical, no LLM, ever.

The sleeve IS the signal: cluster buys in, fixed exits out. The memo-lite
pass (ops/insider/memo_lite.py, overnight) is a passenger — it annotates
entries for the corpus and never gates or sizes anything here.

Exits run before entries, first match wins: "stop" (quote <= entry x 0.80),
"target" (quote >= entry x 1.40), "time" (held > MAX_HOLD_CALENDAR_DAYS).
Entries: today's clusters (cooldown-filtered by find_clusters), sized by
strength, fenced by deny-list / name cap / max positions / ADV / cash.

Sleeve isolation: opens/replays only the INSIDER journal via
PaperBroker.from_journal (this sleeve buys — the long broker is correct).
main_journal gets the single insider_trade_run summary event (daemon gate +
push). The memo store is touched only through the injected resolver, so
resolution failures never block an exit.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from ops import events
from ops.broker.base import InsufficientFunds, QuoteUnavailable
from ops.broker.paper import PaperBroker
from ops.broker.types import Order, OrderType, Side
from ops.insider.clusters import find_clusters
from ops.research.trading import TradeOutcome, _default_adv_fetcher

BASIC_SLICE_PCT = Decimal("0.03")
STRONG_SLICE_PCT = Decimal("0.05")
NAME_CAP_PCT = Decimal("0.05")
MAX_POSITIONS = 25
ADV_CAP_PCT = Decimal("0.05")
MIN_ORDER_DOLLARS = Decimal("100")
STOP_PCT = Decimal("-0.20")
TARGET_PCT = Decimal("0.40")
MAX_HOLD_CALENDAR_DAYS = 126  # ~90 trading days


def _equity_with_fallback(broker, journal, outcome: TradeOutcome) -> Decimal:
    total = broker.get_cash()
    for pos in broker.get_positions():
        try:
            price = broker.get_quote(pos.symbol)
        except QuoteUnavailable as exc:
            last_buy = journal.last_buy_fill_for(pos.symbol)
            price = last_buy["price"] if last_buy is not None else pos.avg_entry_price
            outcome.errors.append(
                f"{pos.symbol}: equity fallback to last fill price ({exc})"
            )
        total += pos.market_value(price)
    return total


def _exit_reason(pos, quote: Decimal, entry_date: str | None, today: date) -> str | None:
    if quote <= pos.avg_entry_price * (Decimal("1") + STOP_PCT):
        return "stop"
    if quote >= pos.avg_entry_price * (Decimal("1") + TARGET_PCT):
        return "target"
    if entry_date and (today - date.fromisoformat(entry_date)).days > MAX_HOLD_CALENDAR_DAYS:
        return "time"
    return None


def _exit_pass(*, broker, insider_journal, prov, now, outcome, resolver) -> set[str]:
    exited: set[str] = set()
    for pos in list(broker.get_positions()):
        symbol = pos.symbol
        pos_prov = prov.get(symbol, {})
        try:
            quote = broker.get_quote(symbol)
            reason = _exit_reason(pos, quote, pos_prov.get("entry_date"), now.date())
            if reason is None:
                continue
            fill = broker.close_position(symbol)
        except QuoteUnavailable as exc:
            outcome.errors.append(f"{symbol}: quote unavailable on exit check ({exc})")
            continue
        except Exception as exc:  # noqa: BLE001 - one bad position must not wedge the run
            outcome.errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
            continue
        memo_id = pos_prov.get("memo_id", "")
        insider_journal.record_event(
            events.KIND_INSIDER_POSITION_CLOSED,
            events.insider_position_closed_payload(
                symbol=symbol, memo_id=memo_id, reason=reason,
                exit_date=now.date().isoformat(), price=str(fill.price),
            ),
            at=now,
        )
        if memo_id and resolver is not None:
            try:
                resolver(
                    memo_id=memo_id, entry_price=pos.avg_entry_price,
                    exit_price=fill.price,
                    entry_date=date.fromisoformat(pos_prov["entry_date"]),
                    exit_date=now.date(), reason=reason,
                )
            except Exception as exc:  # noqa: BLE001 - resolution never blocks an exit
                outcome.errors.append(
                    f"{symbol}: memo resolution failed ({type(exc).__name__}: {exc})"
                )
        outcome.exited.append(symbol)
        exited.add(symbol)
    return exited


def _entry_pass(
    *, broker, signal_store, insider_journal, deny_list, asof, now, exited,
    adv_fetcher, outcome,
) -> None:
    held = {p.symbol for p in broker.get_positions()}
    for cluster in find_clusters(signal_store, asof=asof):
        symbol = cluster.symbol
        if symbol in held or symbol in exited:
            outcome.skipped.append(f"{symbol}: already held")
            continue
        if symbol in deny_list:
            outcome.skipped.append(f"{symbol}: deny-listed")
            continue
        if len(held) >= MAX_POSITIONS:
            outcome.skipped.append(f"{symbol}: max positions ({MAX_POSITIONS})")
            continue

        equity = broker.get_equity()
        slice_pct = STRONG_SLICE_PCT if cluster.strength == "STRONG" else BASIC_SLICE_PCT
        notional = (equity * slice_pct).quantize(Decimal("0.01"))

        cost = {p.symbol: p.quantity * p.avg_entry_price for p in broker.get_positions()}
        name_room = NAME_CAP_PCT * equity - cost.get(symbol, Decimal("0"))
        notional = min(notional, name_room.quantize(Decimal("0.01")))

        adv = adv_fetcher(symbol)
        if adv is None:
            outcome.skipped.append(f"{symbol}: adv unavailable")
            continue
        notional = min(notional, (ADV_CAP_PCT * adv).quantize(Decimal("0.01")))
        notional = min(notional, broker.get_cash().quantize(Decimal("0.01")))

        if notional < MIN_ORDER_DOLLARS:
            outcome.skipped.append(f"{symbol}: below min order ({notional})")
            continue

        order = Order(
            client_order_id=f"insider-{asof.isoformat()}-{symbol}-{uuid4().hex[:8]}",
            symbol=symbol, side=Side.BUY, notional_dollars=notional,
            order_type=OrderType.MARKET,
        )
        try:
            broker.place_order(order)
        except QuoteUnavailable as exc:
            outcome.skipped.append(f"{symbol}: quote unavailable ({exc})")
            continue
        except InsufficientFunds:
            break

        signal_store.record_entry(symbol, asof=asof)
        insider_journal.record_event(
            events.KIND_INSIDER_POSITION_OPENED,
            events.insider_position_opened_payload(
                symbol=symbol, strength=cluster.strength,
                entry_date=asof.isoformat(), client_order_id=order.client_order_id,
                notional=str(notional), buyers=list(cluster.buyers),
                accessions=list(cluster.accessions),
            ),
            at=now,
        )
        outcome.entered.append(symbol)
        held.add(symbol)


def trade_insider_sleeve(
    *,
    signal_store,
    insider_journal,
    main_journal,
    quote_source,
    starting_cash: Decimal,
    deny_list: frozenset[str],
    asof: date,
    now: datetime | None = None,
    adv_fetcher=None,
    resolver=None,
) -> TradeOutcome:
    now = now or datetime.now(timezone.utc)
    outcome = TradeOutcome(asof=asof.isoformat())

    broker = PaperBroker.from_journal(
        journal=insider_journal, quote_source=quote_source, starting_cash=starting_cash,
    )
    prov = insider_journal.latest_event_payload_by_symbol(
        events.KIND_INSIDER_POSITION_OPENED)

    exited = _exit_pass(
        broker=broker, insider_journal=insider_journal, prov=prov, now=now,
        outcome=outcome, resolver=resolver,
    )

    if adv_fetcher is None:
        adv_fetcher = _default_adv_fetcher

    _entry_pass(
        broker=broker, signal_store=signal_store, insider_journal=insider_journal,
        deny_list=deny_list, asof=asof, now=now, exited=exited,
        adv_fetcher=adv_fetcher, outcome=outcome,
    )

    equity = _equity_with_fallback(broker, insider_journal, outcome)
    cash = broker.get_cash()
    outcome.equity = equity
    outcome.cash = cash

    insider_journal.record_equity_snapshot(
        kind="insider_run", equity=equity, cash=cash, at=now,
    )
    main_journal.record_event(
        events.KIND_INSIDER_TRADE_RUN,
        events.insider_trade_run_payload(
            asof=outcome.asof, entered=outcome.entered, exited=outcome.exited,
            skipped=outcome.skipped, equity=str(equity), cash=str(cash),
        ),
        at=now,
    )
    return outcome
