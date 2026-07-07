"""Unit tests for the null-baseline equal-weight paper portfolio."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ops import events
from ops.broker.base import QuoteUnavailable
from ops.broker.paper import PaperBroker
from ops.journal import Journal
from ops.research.baseline import auto_write_off_delisted, update_baseline_portfolio

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


def test_baseline_survives_unquotable_held_position(journal):
    """A delisted holding must not wedge the control (final-review Fix 1).

    AAA is bought with a working quote, then goes unquotable (delisted).
    A later run for a new passer BBB must not raise, must still buy BBB,
    and must mark AAA at its last journaled fill price when computing
    equity for sizing/reporting instead of calling the (raising) live quote.
    """
    from ops.broker.base import QuoteUnavailable

    aaa_quotable = {"ok": True}

    def quotes(symbol):
        if symbol == "AAA" and not aaa_quotable["ok"]:
            raise QuoteUnavailable("delisted")
        return Decimal("20")

    broker = PaperBroker(
        journal=journal, quote_source=quotes, starting_cash=Decimal("100000"),
    )
    update_baseline_portfolio(
        broker=broker, journal=journal, passers=["AAA"], asof=ASOF, now=NOW,
    )
    aaa_fill = journal.last_buy_fill_for("AAA")
    aaa_quotable["ok"] = False  # AAA is now delisted / unquotable

    summary = update_baseline_portfolio(
        broker=broker, journal=journal, passers=["BBB"], asof=ASOF, now=NOW,
    )

    assert summary["buys"] == ["BBB"]

    positions = {p.symbol: p for p in broker.get_positions()}
    expected_equity = (
        broker.get_cash()
        + positions["AAA"].quantity * aaa_fill["price"]
        + positions["BBB"].quantity * Decimal("20")
    )
    snapshot = journal.get_latest_equity_snapshot(kind="baseline_run")
    assert snapshot is not None
    assert snapshot.equity == expected_equity


def test_same_day_retry_after_quote_failure_does_not_collide(journal):
    from ops.broker.base import QuoteUnavailable

    quotes_ok = {"flag": False}

    def quotes(symbol):
        if symbol == "BAD" and not quotes_ok["flag"]:
            raise QuoteUnavailable("no quote")
        return Decimal("20")

    broker = PaperBroker(
        journal=journal, quote_source=quotes, starting_cash=Decimal("100000"),
    )
    first = update_baseline_portfolio(
        broker=broker, journal=journal, passers=["BAD", "GOOD"], asof=ASOF, now=NOW,
    )
    assert first["skipped"] == ["BAD"]

    quotes_ok["flag"] = True
    second = update_baseline_portfolio(
        broker=broker, journal=journal, passers=["BAD", "GOOD"], asof=ASOF, now=NOW,
    )
    assert second["buys"] == ["BAD"]          # GOOD already held, BAD now fills
    assert second["skipped"] == []


def test_write_off_closes_position_at_given_price(journal):
    from ops.research.baseline import update_baseline_portfolio, write_off_position

    broker = _broker(journal)
    update_baseline_portfolio(
        broker=broker, journal=journal, passers=["DEAD"], asof=ASOF, now=NOW,
    )
    result = write_off_position(
        journal=journal, symbol="DEAD", price=Decimal("2.50"),
        starting_cash=Decimal("100000"), note="acquired at $2.50",
    )
    assert result["symbol"] == "DEAD"
    # Replay must show the position gone and cash credited at the write-off price.
    rebuilt = PaperBroker.from_journal(
        journal=journal, quote_source=lambda s: Decimal("20"),
        starting_cash=Decimal("100000"),
    )
    assert all(p.symbol != "DEAD" for p in rebuilt.get_positions())
    kinds = [e["kind"] for e in journal.read_events()]
    assert "baseline_writeoff" in kinds


def test_write_off_unknown_symbol_raises(journal):
    from ops.broker.base import NoSuchPosition
    from ops.research.baseline import write_off_position

    with pytest.raises(NoSuchPosition):
        write_off_position(
            journal=journal, symbol="GHOST", price=Decimal("1"),
            starting_cash=Decimal("100000"),
        )


def test_quote_failure_journaled_and_writeoff_after_three_runs(tmp_path):
    """A position that stops quoting is written off on the 3rd consecutive
    failing run, at the last buy-fill price, with the auto event journaled."""
    journal = Journal(str(tmp_path / "b.sqlite"))
    quotes = {"AAA": Decimal("10"), "DEAD": Decimal("4")}

    def quote_source(symbol):
        if symbol not in quotes:
            raise QuoteUnavailable(symbol)
        return quotes[symbol]

    broker = PaperBroker.from_journal(
        journal=journal, quote_source=quote_source, starting_cash=Decimal("100000"),
    )
    update_baseline_portfolio(broker=broker, journal=journal,
                              passers=["AAA", "DEAD"], asof=date(2026, 6, 20))
    del quotes["DEAD"]  # delisted between runs

    for i, asof in enumerate([date(2026, 6, 27), date(2026, 7, 4), date(2026, 7, 11)]):
        writeoffs = auto_write_off_delisted(
            journal=journal, quote_source=quote_source,
            starting_cash=Decimal("100000"), asof=asof,
        )
        if i < 2:
            assert writeoffs == []
            # keep the baseline-run cadence: each cycle records a screen-run event
            broker = PaperBroker.from_journal(
                journal=journal, quote_source=quote_source,
                starting_cash=Decimal("100000"),
            )
            update_baseline_portfolio(broker=broker, journal=journal,
                                      passers=["AAA"], asof=asof)
    assert [w["symbol"] for w in writeoffs] == ["DEAD"]
    # Fallback price = last buy fill price for DEAD.
    last_buy = journal.last_buy_fill_for("DEAD")
    assert Decimal(writeoffs[0]["price"]) == last_buy["price"]
    kinds = [e["kind"] for e in journal.read_events()]
    assert kinds.count("baseline_quote_failure") == 3
    assert kinds.count("baseline_auto_writeoff") == 1
    # The position is gone from a fresh replay.
    broker = PaperBroker.from_journal(
        journal=journal, quote_source=quote_source, starting_cash=Decimal("100000"),
    )
    assert "DEAD" not in {p.symbol for p in broker.get_positions()}


def test_transient_failure_does_not_write_off(tmp_path):
    """One failing run followed by a healthy run resets nothing permanent —
    the streak rule needs failures on EVERY one of the last 3 run asofs."""
    journal = Journal(str(tmp_path / "b.sqlite"))
    quotes = {"AAA": Decimal("10"), "FLKY": Decimal("4")}

    def quote_source(symbol):
        if symbol not in quotes:
            raise QuoteUnavailable(symbol)
        return quotes[symbol]

    broker = PaperBroker.from_journal(
        journal=journal, quote_source=quote_source, starting_cash=Decimal("100000"),
    )
    update_baseline_portfolio(broker=broker, journal=journal,
                              passers=["AAA", "FLKY"], asof=date(2026, 6, 20))

    del quotes["FLKY"]  # fails on run 2
    assert auto_write_off_delisted(journal=journal, quote_source=quote_source,
                                   starting_cash=Decimal("100000"),
                                   asof=date(2026, 6, 27)) == []
    broker = PaperBroker.from_journal(journal=journal, quote_source=quote_source,
                                      starting_cash=Decimal("100000"))
    update_baseline_portfolio(broker=broker, journal=journal, passers=["AAA"],
                              asof=date(2026, 6, 27))

    quotes["FLKY"] = Decimal("3.5")  # back on run 3: healthy, no failure event
    assert auto_write_off_delisted(journal=journal, quote_source=quote_source,
                                   starting_cash=Decimal("100000"),
                                   asof=date(2026, 7, 4)) == []
    broker = PaperBroker.from_journal(journal=journal, quote_source=quote_source,
                                      starting_cash=Decimal("100000"))
    update_baseline_portfolio(broker=broker, journal=journal, passers=["AAA"],
                              asof=date(2026, 7, 4))

    del quotes["FLKY"]  # fails again on run 4 — but run 3 was healthy
    assert auto_write_off_delisted(journal=journal, quote_source=quote_source,
                                   starting_cash=Decimal("100000"),
                                   asof=date(2026, 7, 11)) == []


def test_reinvoke_same_asof_does_not_collapse_streak(tmp_path):
    """A crash-retry (or accidental second run_screen invocation) of
    auto_write_off_delisted at the SAME asof must not let today's own
    baseline_screen_run/failure event double as one of the 2 prior-run
    streak slots — that would collapse the 3-distinct-date requirement to
    2 and write off a position one run early. Also verifies the re-run
    does not stack a duplicate baseline_quote_failure event for the same
    (symbol, asof)."""
    journal = Journal(str(tmp_path / "b.sqlite"))
    quotes = {"AAA": Decimal("10"), "DEAD": Decimal("4")}

    def quote_source(symbol):
        if symbol not in quotes:
            raise QuoteUnavailable(symbol)
        return quotes[symbol]

    broker = PaperBroker.from_journal(
        journal=journal, quote_source=quote_source, starting_cash=Decimal("100000"),
    )
    update_baseline_portfolio(broker=broker, journal=journal,
                              passers=["AAA", "DEAD"], asof=date(2026, 6, 13))
    del quotes["DEAD"]  # delisted between runs

    # Run 1: failing run at 6/20 — only one prior baseline-run asof (6/13)
    # exists, so there isn't enough history for a streak yet.
    writeoffs = auto_write_off_delisted(
        journal=journal, quote_source=quote_source,
        starting_cash=Decimal("100000"), asof=date(2026, 6, 20),
    )
    assert writeoffs == []
    broker = PaperBroker.from_journal(
        journal=journal, quote_source=quote_source, starting_cash=Decimal("100000"),
    )
    update_baseline_portfolio(broker=broker, journal=journal,
                              passers=["AAA"], asof=date(2026, 6, 20))

    # Run 2: failing run at 6/27 — DEAD has no failure event journaled for
    # 6/13 (the run before it ever failed), so the streak still isn't met.
    writeoffs = auto_write_off_delisted(
        journal=journal, quote_source=quote_source,
        starting_cash=Decimal("100000"), asof=date(2026, 6, 27),
    )
    assert writeoffs == []
    broker = PaperBroker.from_journal(
        journal=journal, quote_source=quote_source, starting_cash=Decimal("100000"),
    )
    update_baseline_portfolio(broker=broker, journal=journal,
                              passers=["AAA"], asof=date(2026, 6, 27))

    # Crash-retry: re-invoke auto_write_off_delisted at the SAME asof
    # (6/27). Without the fix, the baseline_screen_run event just recorded
    # for 6/27 (above) makes today's own date eligible to fill a prior
    # slot, and the failure event journaled moments earlier satisfies it —
    # collapsing distinct failing dates {6/20, 6/27} to a false streak.
    writeoffs = auto_write_off_delisted(
        journal=journal, quote_source=quote_source,
        starting_cash=Decimal("100000"), asof=date(2026, 6, 27),
    )
    assert writeoffs == []

    failure_events = [
        e for e in journal.read_events()
        if e["kind"] == events.KIND_BASELINE_QUOTE_FAILURE
        and e["payload"]["symbol"] == "DEAD"
        and e["payload"]["asof"] == date(2026, 6, 27).isoformat()
    ]
    assert len(failure_events) == 1
