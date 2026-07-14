"""Daemon wiring for the short sleeve: trade tick gate/error discipline and
the overnight short pass (zero-work bookkeeping, drain path)."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ops import events
from ops.config import OpsConfig
from ops.journal import Journal
from ops.main import (
    _short_overnight_pass,
    _short_overnight_work_pending,
    _short_trade_tick,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    # Isolate every sleeve path under tmp so no test touches real state.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return OpsConfig()


@pytest.fixture
def journal(tmp_path):
    with Journal(str(tmp_path / "main.sqlite")) as j:
        yield j


# --- _short_trade_tick -------------------------------------------------------

def test_trade_tick_gates_on_todays_run_event(journal, cfg, monkeypatch):
    journal.record_event(
        events.KIND_SHORT_TRADE_RUN,
        events.short_trade_run_payload(
            asof="2026-07-13", entered=[], exited=[], skipped=[],
            equity="10000", cash="10000",
        ),
    )

    def boom(**kwargs):
        raise AssertionError("trade step must not run twice in one day")

    monkeypatch.setattr("ops.research.short_trading.trade_short_sleeve", boom)
    _short_trade_tick(journal, cfg)  # returns before touching the step
    assert journal.count_events(events.KIND_SHORT_TRADE_ERROR) == 0


def test_trade_tick_journals_errors_instead_of_raising(journal, cfg, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("quote feed down")

    monkeypatch.setattr("ops.research.short_trading.trade_short_sleeve", boom)
    _short_trade_tick(journal, cfg)  # must not raise
    assert journal.count_events(events.KIND_SHORT_TRADE_ERROR) == 1


# --- _short_overnight_pass ---------------------------------------------------

def _window(hours=2):
    now = datetime.now(timezone.utc)
    return {
        "deadline": now + timedelta(hours=hours),
        "should_stop": lambda: False,
        "tick_now": lambda: datetime.now(timezone.utc),
    }


class _Backend:
    def __init__(self):
        self.ensured = 0

    def ensure_up(self):
        self.ensured += 1

    def shutdown(self):
        pass


def test_zero_work_records_drain_run_once_per_day(journal, cfg, monkeypatch):
    # Queues empty; screening is the combined run_screens stage's job, so
    # empty stores mean NO short work at all.
    backend = _Backend()
    _short_overnight_pass(journal, cfg, backend=backend, **_window())
    _short_overnight_pass(journal, cfg, backend=backend, **_window())
    assert journal.count_events(events.KIND_SHORT_DRAIN_RUN) == 1
    assert backend.ensured == 0  # empty queues must never spin ds4
    assert not _short_overnight_work_pending(cfg)


def test_pass_never_screens_and_passes_bookkeeping_through(journal, cfg, monkeypatch):
    # The pass must NOT call any screen (the combined sweep already ran in
    # the tick); it only reports the tick's screened_this_run in its event.
    monkeypatch.setattr(
        "ops.research.run.run_short_screen",
        lambda **kw: (_ for _ in ()).throw(AssertionError("pass must not screen")),
    )
    monkeypatch.setattr(
        "ops.research.run.run_screens",
        lambda **kw: (_ for _ in ()).throw(AssertionError("pass must not screen")),
    )
    _short_overnight_pass(journal, cfg, backend=_Backend(),
                          screened_this_run=True, **_window())
    (event,) = journal.read_events()
    assert event["kind"] == events.KIND_SHORT_DRAIN_RUN
    assert event["payload"]["screened_this_run"] is True


def test_pass_failure_records_short_drain_error(journal, cfg, monkeypatch):
    import dataclasses

    from ops.research.store import ScreenStore

    # Seed one pending hit so the pass proceeds past the zero-work return,
    # then blow up the drain path (LLM build) to hit the error handler.
    @dataclasses.dataclass
    class FakeResult:
        symbol: str
        passed: bool

    store = ScreenStore(cfg.short_screen_store_path)
    store.record_run(asof=date.today(), universe_size=1,
                     results=[FakeResult("GHST", True)])
    assert store.pending_hits()
    monkeypatch.setattr(
        "ops.research.models.build_stage_llm",
        lambda spec: (_ for _ in ()).throw(RuntimeError("model config broken")),
    )
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "T t@e.com")
    _short_overnight_pass(journal, cfg, backend=_Backend(), **_window())
    assert journal.count_events(events.KIND_SHORT_DRAIN_ERROR) == 1
