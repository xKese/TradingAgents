"""Crash-point recovery property tests (ops architecture plan, section A6).

The whole design rests on "journal replay + reconciliation recovers any
crash". This module proves it systematically: one deterministic scenario is
driven through a `CrashingJournal` that raises `SimulatedCrash` on the Nth
journal write, for every N in 1..W (W = total writes of the uncrashed run).
After each crashed run, the journal file is reopened cold and the full
production recovery path must succeed.

Crash semantics ("the process died at write N"):

- The Nth write attempt raises BEFORE committing, so the on-disk journal
  contains exactly writes 1..N-1 (plus, see below, at most the handful of
  error-report writes production itself would emit before dying).
- `SimulatedCrash` derives from `Exception` directly — deliberately NOT from
  `BrokerError`/`QuoteUnavailable` — so the narrow production handlers
  (guardian per-position quote/close handling, GuardedBroker's
  `except BrokerError`) do NOT swallow it. Only the broad catch-alls that
  swallow *any* journal failure in production swallow it here:
  `PositionGuardian.check_stops_once` (journals guardian_check_error) and
  the kill-switch sweep's per-symbol `except Exception` (journals
  kill_switch_close_failed).
- The crash is ONE-SHOT: only the Nth write raises; later writes succeed.
  This is required for realism inside guardian passes — journaling the
  guardian_check_error / kill_switch_close_failed report itself goes through
  the same journal, and production would only be able to write that report
  if the underlying store recovered. The scenario driver then STOPS at the
  first crash: direct journal writes and broker-path writes propagate
  `SimulatedCrash` to the driver (caught there), and for the
  guardian-swallowed cases the driver checks `CrashingJournal.crashed`
  after every step and aborts. The point is state-at-crash recovery, not
  multi-crash behavior.

Recovery invariant asserted for every N:

1. `PaperBroker.from_journal` replays the reopened journal without raising,
   and without ever needing the `journal_replay_fallback` /
   `journal_replay_orphan_sell` escape hatches (i.e. no fill was ever
   journaled before its order row — the journal-before-side-effect ordering
   property).
2. `reconcile(broker_mode="paper")` against a freshly rebuilt guarded paper
   broker yields zero diffs (paper mode's own invariant).
3. A follow-up guardian pass on the rebuilt broker completes any interrupted
   work without raising: no new guardian_check_error is journaled, any
   kill-switch sweep resumes to an empty book, and no surviving position is
   left past its stop.

Scenario (paper mode, injected quotes, config defaults — $500 seed):
seed cash adjustment -> open_week/open_day baselines -> guarded broker via
the ops factory -> BUY AAPL $50 @ $10 (stop $9.20) and MSFT $50 @ $100
(stop $10) -> AAPL gaps to $2 (past its stop) -> guardian pass #1
(stop-sell AAPL at $2, realizing -$40; daily halt trips at -8%) -> MSFT
collapses to $20 (equity $420, -16% weekly) -> guardian pass #2 (kill
switch + paper sweep closes MSFT).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ops import build_guarded_paper_broker_from_journal
from ops.broker.paper import PaperBroker
from ops.broker.types import Order, OrderType, Side
from ops.config import OpsConfig
from ops.journal import Journal
from ops.position_guardian import PositionGuardian
from ops.reconcile import reconcile
from ops.trading_time import trading_day_start, trading_week_start


class SimulatedCrash(Exception):
    """Injected 'process died here' marker.

    Derives from Exception directly (never BrokerError/QuoteUnavailable) so
    production's narrow handlers let it escape; only the broad catch-alls
    that swallow any journal error in production swallow it.
    """


_WRITE_METHODS = frozenset({
    "record_event",
    "record_order",
    "record_fill",
    "record_equity_snapshot",
    "record_cash_adjustment",
    "set_cursor",
})


class CrashingJournal:
    """Wraps a real Journal; delegates everything; counts write calls and
    raises SimulatedCrash on the crash_at-th write (one-shot, pre-commit)."""

    def __init__(self, inner: Journal, *, crash_at: int | None = None):
        self._inner = inner
        self._crash_at = crash_at
        self.write_count = 0
        self.crashed = False

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name not in _WRITE_METHODS:
            return attr

        def _write(*args, **kwargs):
            self.write_count += 1
            if (
                self._crash_at is not None
                and not self.crashed
                and self.write_count == self._crash_at
            ):
                self.crashed = True
                raise SimulatedCrash(
                    f"simulated crash at write #{self.write_count} ({name})"
                )
            return attr(*args, **kwargs)

        return _write


class _MutableQuotes:
    """A settable quote source: quotes.set(sym, price) / quotes.get(sym)."""

    def __init__(self):
        self._prices: dict[str, Decimal] = {}

    def set(self, symbol: str, price: Decimal) -> None:
        self._prices[symbol] = price

    def get(self, symbol: str) -> Decimal:
        return self._prices[symbol]


def _scenario_quotes() -> _MutableQuotes:
    quotes = _MutableQuotes()
    quotes.set("AAPL", Decimal("10"))
    quotes.set("MSFT", Decimal("100"))
    return quotes


def _snapshot_equity(journal, kind: str, since) -> Decimal:
    """Mirror ops.main._start_of_day_equity/_start_of_week_equity."""
    snap = journal.get_latest_equity_snapshot(kind=kind, since=since)
    return snap.equity if snap is not None else Decimal("0")


def _build_broker(journal, quotes: _MutableQuotes, cfg: OpsConfig):
    """Production restart path: rebuild the guarded paper broker from the
    journal (ops.main._build_broker's paper branch, minus the yfinance
    quote source)."""
    now = datetime.now(timezone.utc)
    return build_guarded_paper_broker_from_journal(
        config=cfg,
        journal=journal,
        quote_source=quotes.get,
        starting_cash=Decimal("0"),
        start_of_day_equity=lambda: _snapshot_equity(
            journal, "open_day", trading_day_start(now)),
        start_of_week_equity=lambda: _snapshot_equity(
            journal, "open_week", trading_week_start(now)),
    )


def _run_scenario(journal: CrashingJournal, quotes: _MutableQuotes) -> None:
    """Drive the scenario to completion, or abort at the first simulated
    crash. Crashes on direct journal writes and broker-path writes propagate
    here as SimulatedCrash; crashes inside guardian passes are swallowed by
    production's catch-alls, so the `crashed` flag is checked after each
    guardian step."""
    cfg = OpsConfig()
    now = datetime.now(timezone.utc)
    try:
        # Seed + baselines (what _ensure_paper_seed and the orchestrator's
        # first tick of the day/week record).
        journal.record_cash_adjustment(
            kind="seed", amount=Decimal("500"), note="paper starting cash")
        journal.record_equity_snapshot(
            kind="open_week", equity=Decimal("500"), cash=Decimal("500"), at=now)
        journal.record_equity_snapshot(
            kind="open_day", equity=Decimal("500"), cash=Decimal("500"), at=now)

        broker = _build_broker(journal, quotes, cfg)
        guardian = PositionGuardian(
            broker=broker, quote_source=quotes.get, config=cfg,
            journal=journal, broker_mode="paper",
        )

        # Two BUY fills. Stops: AAPL 10 * (1 - 0.08) = $9.20 (tight),
        # MSFT 100 * (1 - 0.90) = $10 (loose — survives until the kill switch).
        broker.place_order(Order(
            client_order_id="buy-AAPL", symbol="AAPL", side=Side.BUY,
            notional_dollars=Decimal("50"), order_type=OrderType.MARKET,
            stop_pct=Decimal("-0.08"),
        ))
        broker.place_order(Order(
            client_order_id="buy-MSFT", symbol="MSFT", side=Side.BUY,
            notional_dollars=Decimal("50"), order_type=OrderType.MARKET,
            stop_pct=Decimal("-0.90"),
        ))

        # AAPL gaps down hard past its $9.20 stop; guardian pass #1
        # stop-sells it at $2 (equity 460, daily halt trips at -8%).
        quotes.set("AAPL", Decimal("2"))
        guardian.check_stops_once()
        if journal.crashed:
            return

        # MSFT collapses; equity 420 = -16% weekly, past the -15% default.
        # Guardian pass #2 trips the kill switch and sweeps MSFT.
        quotes.set("MSFT", Decimal("20"))
        guardian.check_stops_once()
        if journal.crashed:
            return
    except SimulatedCrash:
        return


def _learn_write_count() -> int:
    with tempfile.TemporaryDirectory() as td:
        inner = Journal(os.path.join(td, "j.sqlite"))
        try:
            journal = CrashingJournal(inner, crash_at=None)
            _run_scenario(journal, _scenario_quotes())
            return journal.write_count
        finally:
            inner.close()


# Learned once at collection time so N can be parametrized over 1..W.
_W = _learn_write_count()


def _assert_recovers(journal_path: str, quotes: _MutableQuotes) -> None:
    """Reopen the journal cold and assert the full recovery invariant."""
    fresh = Journal(journal_path)
    try:
        cfg = OpsConfig()

        # 1. Replay must not raise...
        PaperBroker.from_journal(
            journal=fresh, quote_source=quotes.get, starting_cash=Decimal("0"))
        # ...and must never have needed the escape hatches: a fill journaled
        # before its order row (or an orphan SELL) is exactly the
        # write-ordering bug class this test exists to catch.
        kinds = [e["kind"] for e in fresh.read_events()]
        assert "journal_replay_fallback" not in kinds
        assert "journal_replay_orphan_sell" not in kinds

        # 2. Rebuild the guarded broker the way a restarted `ops run` does
        # and reconcile: zero diffs (paper mode's own invariant).
        rebuilt = _build_broker(fresh, quotes, cfg)
        result = reconcile(journal=fresh, broker=rebuilt, broker_mode="paper")
        assert result.diffs == [], f"reconcile diffs after crash: {result.diffs}"

        # 3. A follow-up guardian pass completes any interrupted work.
        pre_kinds = [e["kind"] for e in fresh.read_events()]
        errors_before = pre_kinds.count("guardian_check_error")
        guardian = PositionGuardian(
            broker=rebuilt, quote_source=quotes.get, config=cfg,
            journal=fresh, broker_mode="paper",
        )
        guardian.check_stops_once()

        post_kinds = [e["kind"] for e in fresh.read_events()]
        # "Without raising": check_stops_once never propagates, so the
        # meaningful assertion is that nothing was swallowed either.
        assert post_kinds.count("guardian_check_error") == errors_before, (
            "recovery guardian pass swallowed an exception")
        # An interrupted (or freshly recomputed) kill-switch sweep must
        # resume to an empty book.
        if "kill_switch" in post_kinds:
            assert rebuilt.get_positions() == [], (
                "kill-switch sweep did not resume after restart")
        # Stop enforcement is complete: no surviving position past its stop.
        for pos in rebuilt.get_positions():
            assert pos.stop_loss_price is not None, (
                f"{pos.symbol} recovered without a stop")
            assert quotes.get(pos.symbol) > pos.stop_loss_price, (
                f"{pos.symbol} left open past its stop after recovery pass")
    finally:
        fresh.close()


def test_scenario_write_count_is_deterministic_and_in_expected_range():
    assert 15 <= _W <= 30, f"scenario write count drifted: W={_W}"
    assert _learn_write_count() == _W, "scenario write count is nondeterministic"


def test_uncrashed_scenario_end_state_and_recovery(tmp_path):
    """Baseline: the uncrashed scenario reaches the designed end state
    (stop-sell, daily halt, kill switch, swept book) and itself satisfies
    the recovery invariant."""
    path = str(tmp_path / "j.sqlite")
    inner = Journal(path)
    journal = CrashingJournal(inner, crash_at=None)
    quotes = _scenario_quotes()
    _run_scenario(journal, quotes)
    assert not journal.crashed
    assert journal.write_count == _W

    kinds = [e["kind"] for e in inner.read_events()]
    assert "stop_hit" in kinds
    assert "daily_halt" in kinds
    assert "kill_switch" in kinds
    assert "guardian_check_error" not in kinds

    replay = PaperBroker.from_journal(
        journal=inner, quote_source=quotes.get, starting_cash=Decimal("0"))
    assert replay.get_positions() == []
    inner.close()

    _assert_recovers(path, quotes)


@pytest.mark.parametrize("n", range(1, _W + 1))
def test_recovery_after_crash_at_write_n(tmp_path, n):
    path = str(tmp_path / "j.sqlite")
    inner = Journal(path)
    journal = CrashingJournal(inner, crash_at=n)
    quotes = _scenario_quotes()
    _run_scenario(journal, quotes)
    assert journal.crashed, (
        f"crash point {n} was never reached (scenario prefix diverged)")
    inner.close()

    # The process died at write n: reopen cold and recover.
    _assert_recovers(path, quotes)
