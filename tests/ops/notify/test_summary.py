from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from ops.broker.types import Position
from ops.journal import Journal
from ops.notify.summary import emit_daily_summary


def _broker(equity, positions):
    b = MagicMock()
    b.get_equity.return_value = Decimal(equity)
    b.get_positions.return_value = positions
    return b


def test_emits_once_per_day(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    now = datetime(2026, 7, 2, 20, 5, tzinfo=timezone.utc)
    b = _broker("260", [Position("AAPL", Decimal("0.1"), Decimal("200"))])
    assert emit_daily_summary(j, b, now=now) is True
    assert emit_daily_summary(j, b, now=now) is False   # idempotent
    events = [e for e in j.read_events() if e["kind"] == "daily_summary"]
    assert len(events) == 1
    assert events[0]["payload"]["equity"] == "260"


def test_summary_excludes_spot(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    now = datetime(2026, 7, 2, 20, 5, tzinfo=timezone.utc)
    b = _broker("260", [
        Position("AAPL", Decimal("0.1"), Decimal("200")),
        Position("SPOT", Decimal("0.1"), Decimal("500")),
    ])
    emit_daily_summary(j, b, now=now)
    body = [e for e in j.read_events() if e["kind"] == "daily_summary"][0]["payload"]["body"]
    assert "SPOT" not in body


def test_summary_uses_filled_at_for_fill_bucketing(tmp_path, monkeypatch):
    """M3: fills are filtered by filled_at, not at (journal write time).
    A fill with filled_at today but journaled at a different time buckets
    correctly."""
    j = Journal(str(tmp_path / "j.sqlite"))
    # now=2026-07-02 20:05 UTC is 2026-07-02 16:05 ET (market close)
    # ET trading-day start is 2026-07-02 04:00 UTC
    now = datetime(2026, 7, 2, 20, 5, tzinfo=timezone.utc)

    # Record a fill with filled_at in the current trading day but at=previous day
    _record_fill_at(
        j, monkeypatch, datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc),
        order_id="o-1", client_order_id="c-1", symbol="AAPL", side="BUY",
        quantity=Decimal("1"), price=Decimal("10"),
        filled_at=datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc),  # today
    )

    b = _broker("260", [Position("AAPL", Decimal("1"), Decimal("10"))])
    emit_daily_summary(j, b, now=now)
    payload = [e for e in j.read_events() if e["kind"] == "daily_summary"][0]["payload"]
    # Fill should count because filled_at is in the current trading day
    assert payload["n_fills_today"] == 1


def test_summary_reports_n_a_pnl_without_same_day_baseline(tmp_path):
    """M3: stale (yesterday's) open_day snapshot → P&L 'n/a', not a number."""
    j = Journal(str(tmp_path / "j.sqlite"))
    now = datetime(2026, 7, 2, 20, 5, tzinfo=timezone.utc)

    # Record an open_day snapshot from YESTERDAY (before the current trading day)
    yesterday = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    j.record_equity_snapshot(kind="open_day", equity=Decimal("1000"), cash=Decimal("900"), at=yesterday)

    b = _broker("1100", [Position("AAPL", Decimal("1"), Decimal("100"))])
    emit_daily_summary(j, b, now=now)
    payload = [e for e in j.read_events() if e["kind"] == "daily_summary"][0]["payload"]
    # No same-day baseline → P&L should be "n/a"
    assert "n/a" in payload["headline"]


def _record_fill_at(j, monkeypatch, at, **kwargs):
    """Journal.record_fill always stamps its `at` column with the real
    wall-clock time (there is no `at` override parameter), so to test
    ET-boundary bucketing we pin the module's _now_iso() for the duration
    of a single record_fill call."""
    import ops.journal as journal_mod

    monkeypatch.setattr(journal_mod, "_now_iso", lambda: at.isoformat())
    j.record_fill(**kwargs)
    monkeypatch.undo()


def test_fills_today_counts_since_et_trading_day_start(tmp_path, monkeypatch):
    """fills_today must use the ET trading-day boundary (trading_day_start),
    matching the ET boundary the idempotency guard (has_event_today) already
    uses — not a UTC-calendar-date comparison, which mis-buckets fills in
    the UTC-evening/ET-morning gap. now=2026-07-02 20:05 UTC is 2026-07-02
    16:05 ET (market close); ET trading-day start is 2026-07-02 04:00 UTC
    (midnight ET, EDT = UTC-4). A fill at 2026-07-02 04:01 UTC is just after
    that ET day-start -> counts. A fill at 2026-07-02 03:30 UTC is
    2026-07-01 23:30 ET — still the PREVIOUS ET trading day — and must NOT
    count, even though a naive UTC .date() comparison against `now` (also
    2026-07-02) would wrongly count it."""
    j = Journal(str(tmp_path / "j.sqlite"))
    now = datetime(2026, 7, 2, 20, 5, tzinfo=timezone.utc)

    _record_fill_at(
        j, monkeypatch, datetime(2026, 7, 2, 4, 1, tzinfo=timezone.utc),
        order_id="o-1", client_order_id="c-1", symbol="AAPL", side="BUY",
        quantity=Decimal("1"), price=Decimal("10"),
        filled_at=datetime(2026, 7, 2, 4, 1, tzinfo=timezone.utc),
    )
    _record_fill_at(
        j, monkeypatch, datetime(2026, 7, 2, 3, 30, tzinfo=timezone.utc),
        order_id="o-2", client_order_id="c-2", symbol="MSFT", side="BUY",
        quantity=Decimal("1"), price=Decimal("10"),
        filled_at=datetime(2026, 7, 2, 3, 30, tzinfo=timezone.utc),
    )

    b = _broker("260", [Position("AAPL", Decimal("1"), Decimal("10"))])
    emit_daily_summary(j, b, now=now)
    payload = [e for e in j.read_events() if e["kind"] == "daily_summary"][0]["payload"]
    assert payload["n_fills_today"] == 1


def test_summary_emits_with_real_market_calendar_on_trading_day(tmp_path):
    """M3 regression guard: the 16:05 ET summary cron always runs AFTER the
    close, so the calendar gate must not depend on is_open_now — and must
    not touch private MarketCalendar attributes. With a REAL MarketCalendar
    on a normal trading day the summary must emit."""
    from ops.scheduler.market_calendar import MarketCalendar
    j = Journal(str(tmp_path / "j.sqlite"))
    # Monday 2026-07-06 16:05 ET == 20:05 UTC (EDT)
    now = datetime(2026, 7, 6, 20, 5, tzinfo=timezone.utc)
    b = _broker("260", [Position("AAPL", Decimal("0.1"), Decimal("200"))])
    assert emit_daily_summary(j, b, calendar=MarketCalendar(), now=now) is True
    assert len([e for e in j.read_events() if e["kind"] == "daily_summary"]) == 1


def test_summary_skipped_on_market_holiday(tmp_path):
    """The mon-fri cron fires on NYSE holidays too; the calendar gate must
    skip them. 2026-07-03 (Friday) is the observed Independence Day holiday."""
    from ops.scheduler.market_calendar import MarketCalendar
    j = Journal(str(tmp_path / "j.sqlite"))
    now = datetime(2026, 7, 3, 20, 5, tzinfo=timezone.utc)
    b = _broker("260", [])
    assert emit_daily_summary(j, b, calendar=MarketCalendar(), now=now) is False
    assert [e for e in j.read_events() if e["kind"] == "daily_summary"] == []
