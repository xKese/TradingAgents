"""ops run — always-on orchestrator + guardian service.

Runs in the foreground. SIGINT/SIGTERM triggers graceful shutdown:
scheduler drains in-flight jobs, journal closes cleanly. Exit codes:
- 0: clean shutdown
- 2: reconciliation-halted shutdown (journal has inconsistency events)
"""
from __future__ import annotations

import signal
import sys
import threading
from decimal import Decimal

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ops import (
    build_guarded_paper_broker_from_journal,
    build_guarded_robinhood_broker,
)
from ops.config import OpsConfig, load_config
from ops.journal import Journal
from ops.position_guardian import PositionGuardian
from ops.scheduler.market_calendar import MarketCalendar
from ops.scheduler.orchestrator import Orchestrator
from ops.reconcile import ReconcileResult, reconcile, emit_reconcile_events


_shutdown_event = threading.Event()


def _shutdown_handler(signum, frame) -> None:
    _shutdown_event.set()


def _build_broker(config: OpsConfig, journal: Journal):
    """Construct the guarded broker for the configured mode.

    Paper mode rebuilds the inner PaperBroker from the journal (via
    build_guarded_paper_broker_from_journal) so a restarted ops run
    picks up prior positions and cash. Robinhood mode reads live state
    from the MCP.
    """
    from ops.quotes import make_yfinance_quote_source
    quote_source = make_yfinance_quote_source()

    def _sod():
        snap = journal.get_latest_equity_snapshot(kind="open_day")
        return snap.equity if snap is not None else Decimal("250")

    def _sow():
        snap = journal.get_latest_equity_snapshot(kind="open_week")
        return snap.equity if snap is not None else Decimal("250")

    if config.broker_mode == "robinhood":
        return build_guarded_robinhood_broker(
            config=config, journal=journal,
            start_of_day_equity=_sod, start_of_week_equity=_sow,
        )
    return build_guarded_paper_broker_from_journal(
        config=config, journal=journal,
        quote_source=quote_source,
        starting_cash=Decimal("250"),
        start_of_day_equity=_sod, start_of_week_equity=_sow,
    )


def _wire(broker, journal: Journal, config: OpsConfig):
    """Wire the orchestrator + guardian + calendar for the given broker+config."""
    from ops.strategy.post_earnings_momentum import PostEarningsMomentumStrategy
    from ops.pipeline_adapter import TradingAgentsPipelineAdapter
    from ops.universe import build_universe

    calendar = MarketCalendar()
    orchestrator = Orchestrator(
        broker=broker,
        universe_builder=build_universe,
        strategy=PostEarningsMomentumStrategy(config=config),
        pipeline_adapter=TradingAgentsPipelineAdapter(),
        calendar=calendar, journal=journal, config=config,
    )
    guardian = PositionGuardian(
        broker=broker, quote_source=broker.get_quote, config=config,
        journal=journal, broker_mode=config.broker_mode,
    )
    return orchestrator, guardian, calendar


def _emit_halt_events(journal: Journal, result: ReconcileResult) -> None:
    emit_reconcile_events(journal, result)
    journal.record_event("startup_halted", {"reason": "reconciliation"})


def _start_full_scheduler(orchestrator: Orchestrator, guardian: PositionGuardian) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="America/New_York")
    sched.add_job(
        orchestrator.tick,
        CronTrigger(minute="0,30", hour="9-15", day_of_week="mon-fri"),
        id="orchestrator_tick", max_instances=1, misfire_grace_time=60,
    )
    sched.add_job(
        guardian.check_stops_once,
        IntervalTrigger(seconds=60),
        id="guardian_poll", max_instances=1, misfire_grace_time=15,
    )
    sched.start()
    return sched


def _start_guardian_only(guardian: PositionGuardian) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="America/New_York")
    sched.add_job(
        guardian.check_stops_once,
        IntervalTrigger(seconds=60),
        id="guardian_poll", max_instances=1, misfire_grace_time=15,
    )
    sched.start()
    return sched


def _run_until_signal() -> None:
    _shutdown_event.wait()


def run() -> int:
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)
    config = load_config()
    journal = Journal(config.journal_path)
    try:
        broker = _build_broker(config, journal)
        orchestrator, guardian, calendar = _wire(broker, journal, config)
        result = reconcile(journal=journal, broker=broker, broker_mode=config.broker_mode)
        if result.positions_recovered_without_stops:
            print(
                "WARNING: "
                f"{len(result.positions_recovered_without_stops)} position(s) "
                "opened without recorded stops — guardian will use config "
                f"fallback: {result.positions_recovered_without_stops}",
                file=sys.stderr,
            )
        if result.diffs:
            _emit_halt_events(journal, result)
            print(
                f"Reconciliation halted orchestrator — {len(result.diffs)} diff(s). "
                "Guardian continues. Investigate journal 'inconsistency' events.",
                file=sys.stderr,
            )
            sched = _start_guardian_only(guardian)
            _run_until_signal()
            sched.shutdown(wait=True)
            return 2
        sched = _start_full_scheduler(orchestrator, guardian)
        _run_until_signal()
        sched.shutdown(wait=True)
        return 0
    finally:
        journal.close()
