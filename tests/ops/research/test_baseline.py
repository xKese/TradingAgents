"""Unit tests for the null-baseline equal-weight paper portfolio."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ops import events
from ops.broker.paper import PaperBroker
from ops.journal import Journal
from ops.research.baseline import update_baseline_portfolio

pytestmark = pytest.mark.unit

ASOF = date(2026, 7, 1)
NOW = datetime(2026, 7, 1, 21, 0, tzinfo=timezone.utc)


@pytest.fixture
def journal(tmp_path):
    j = Journal(str(tmp_path / "baseline.sqlite"))
    yield j
    j.close()


def _broker(journal, cash="100000"):
    return PaperBroker(
        journal=journal, quote_source=lambda s: Decimal("20"),
        starting_cash=Decimal(cash),
    )


def test_buys_passers_equal_weight(journal):
    broker = _broker(journal)
    summary = update_baseline_portfolio(
        broker=broker, journal=journal, passers=["AAA", "BBB"], asof=ASOF, now=NOW,
    )
    assert summary["buys"] == ["AAA", "BBB"]
    positions = {p.symbol: p for p in broker.get_positions()}
    # 4% of 100k = 4000 for AAA; BBB gets 4% of remaining equity (still 100k mark).
    assert positions["AAA"].quantity == Decimal("4000") / Decimal("20")
    # Events and snapshot recorded.
    kinds = [e["kind"] for e in journal.read_events()]
    assert events.KIND_BASELINE_SCREEN_RUN in kinds
    assert journal.get_latest_equity_snapshot(kind="baseline_run") is not None


def test_held_names_are_not_rebought(journal):
    broker = _broker(journal)
    update_baseline_portfolio(
        broker=broker, journal=journal, passers=["AAA"], asof=ASOF, now=NOW,
    )
    summary = update_baseline_portfolio(
        broker=broker, journal=journal, passers=["AAA"], asof=ASOF, now=NOW,
    )
    assert summary["buys"] == []
    assert len(broker.get_positions()) == 1


def test_positions_exit_after_max_hold(journal):
    # PaperBroker stamps fills with the REAL clock, so "366 days later" must
    # be computed from the real clock too, not from the fake NOW.
    broker = _broker(journal)
    update_baseline_portfolio(
        broker=broker, journal=journal, passers=["AAA"], asof=ASOF, now=NOW,
    )
    later = datetime.now(timezone.utc) + timedelta(days=366)
    summary = update_baseline_portfolio(
        broker=broker, journal=journal, passers=[], asof=date(2027, 7, 2), now=later,
    )
    assert summary["exits"] == ["AAA"]
    assert broker.get_positions() == []
    kinds = [e["kind"] for e in journal.read_events()]
    assert events.KIND_BASELINE_EXIT in kinds


def test_exited_name_can_reenter_on_same_run(journal):
    broker = _broker(journal)
    update_baseline_portfolio(
        broker=broker, journal=journal, passers=["AAA"], asof=ASOF, now=NOW,
    )
    later = datetime.now(timezone.utc) + timedelta(days=366)
    summary = update_baseline_portfolio(
        broker=broker, journal=journal, passers=["AAA"], asof=date(2027, 7, 2), now=later,
    )
    assert summary["exits"] == ["AAA"]
    assert summary["buys"] == ["AAA"]


def test_stops_buying_when_cash_exhausted(journal):
    broker = _broker(journal, cash="5000")
    # Slice = 4% of 5000 = 200; cash runs out after ~25 buys.
    passers = [f"SYM{i:02d}" for i in range(40)]
    summary = update_baseline_portfolio(
        broker=broker, journal=journal, passers=passers, asof=ASOF, now=NOW,
    )
    assert len(summary["buys"]) < 40
    assert broker.get_cash() < Decimal("200")


def test_quote_failure_skips_name_and_continues(journal):
    from ops.broker.base import QuoteUnavailable

    def quotes(symbol):
        if symbol == "BAD":
            raise QuoteUnavailable("no quote")
        return Decimal("20")

    broker = PaperBroker(
        journal=journal, quote_source=quotes, starting_cash=Decimal("100000"),
    )
    summary = update_baseline_portfolio(
        broker=broker, journal=journal, passers=["BAD", "GOOD"], asof=ASOF, now=NOW,
    )
    assert summary["buys"] == ["GOOD"]
    assert summary["skipped"] == ["BAD"]
