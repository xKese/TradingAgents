# Plan 3b — Always-on orchestration (design)

**Date:** 2026-07-02
**Status:** Design approved, ready for implementation plan
**Parent spec:** [`2026-06-30-tradingagents-live-v1-design.md`](./2026-06-30-tradingagents-live-v1-design.md)
**Predecessor plans:** Plan 1 (PR #1), Plan 2 (PR #3, followup #4), Plan 3a (PR #5).

## Scope

3b is the middle of the three-plan split of the original Plan 3. It ships the always-on `ops run` service that ties Plan 3a's broker layer into a live tick loop, closes the state-recovery gap that Plan 3a's review flagged as load-bearing, and makes the drawdown rules functional by wiring the equity-snapshot writers.

Plan 3c will layer Pushover/SMTP delivery on top of the events 3b emits, plus the LIVE_MAX_POSITION first-N-fills gate.

## Goals

- `ops run` command: APScheduler-driven service that ticks the strategy pipeline every 30 minutes during NYSE market hours and polls the guardian every 60 seconds.
- Per-position stops survive orchestrator restart (fills table gains `stop_loss_price`; PaperBroker and RobinhoodBroker both rehydrate positions from the journal).
- Startup reconciliation halts the orchestrator when journal-replayed state disagrees with the live broker; guardian keeps running so existing stops are enforced.
- Equity snapshots (`open_day`, `open_week`) written by the orchestrator; drawdown rules stop being no-ops.
- Kill-switch action: paper mode auto-closes all positions; live mode halts orchestrator (guardian still enforces individual stops).
- `TradingAgentsPipelineAdapter._ensure_graph` is thread-safe.
- Graceful SIGINT/SIGTERM shutdown drains in-flight jobs and closes the journal cleanly.

## Non-goals (deferred to 3c or later)

- Pushover / SMTP notification delivery. 3b emits events; 3c subscribes.
- `LIVE_MAX_POSITION` first-N-fills cap.
- Streamlit dashboard.
- `_fetch_from_wikipedia` stale-cache fallback.
- launchd integration (user runs `ops run` in a terminal/tmux).

## Architecture

Single Python process. `ops run` calls `ops.main.run()` which:

1. Loads config + opens `Journal`.
2. Builds `GuardedBroker` (paper or robinhood by `config.broker_mode`).
3. Runs startup reconciliation.
4. Starts `APScheduler.BackgroundScheduler(timezone='America/New_York')` with two jobs:
   - `orchestrator.tick()` — `CronTrigger(minute='0,30', hour='9-15', day_of_week='mon-fri')`.
   - `guardian.check_stops_once()` — `IntervalTrigger(seconds=60)`.
5. Waits on SIGINT/SIGTERM, then shuts down scheduler (wait=True) and closes journal.

APScheduler owns its own thread pool so orchestrator and guardian never block each other. `GuardedBroker._lock` (Plan 1) already serialises money-touching calls between them.

### File structure

```
ops/
  main.py                        # NEW  entrypoint
  cli.py                         # MODIFY  add `run` subcommand
  reconcile.py                   # NEW  startup state reconciliation
  scheduler/
    __init__.py                  # NEW
    market_calendar.py           # NEW
    orchestrator.py              # NEW
  pipeline_adapter.py            # MODIFY  threading.Lock around _ensure_graph
  journal.py                     # MODIFY  fills.stop_loss_price + last_buy_fill_for
  broker/
    paper.py                     # MODIFY  _fill_buy writes stop_loss_price; from_journal rehydrates
    robinhood.py                 # MODIFY  get_positions() rehydrates stop from journal
  config.py                      # MODIFY  orchestrator_tick_minutes + related knobs
tests/ops/
  scheduler/
    __init__.py
    test_market_calendar.py
    test_orchestrator.py
  test_reconcile.py
  test_main.py
  test_pipeline_adapter.py       # MODIFY  add thread-safety test
```

New runtime deps: `apscheduler>=3.10`, `pandas_market_calendars>=4.4`.

## Design

### 1. Persisted per-position stops

**Schema.** `fills` gains a nullable `stop_loss_price TEXT` column. `Journal.__init__` handles the migration with the same PRAGMA-guarded ALTER TABLE pattern used for `equity_snapshots` in Plan 3a.

**Write path.**
- `Journal.record_fill(..., stop_loss_price=None)` — new kwarg with a None default so existing callers keep working.
- `PaperBroker._fill_buy` passes `stop_loss_price=order.stop_loss_price`.
- `RobinhoodBroker.place_order` (BUY branch) same.
- Close/SELL fills leave the column NULL.

**Read path.**
- `Journal.last_buy_fill_for(symbol: str) -> dict | None` returns the most recent BUY fill row for `symbol`.
- `PaperBroker.from_journal` walks the reconstructed `_positions` dict; for each symbol, calls `last_buy_fill_for` and, if the row's `stop_loss_price` is non-NULL, attaches it to the Position.
- `RobinhoodBroker.get_positions()` maps each MCPPosition and, in the same pass, calls `last_buy_fill_for(pos.symbol)`; attaches stop when present. Positions with no journaled BUY come back with `stop_loss_price=None` AND the reconciler emits `positions_recovered_without_stops` naming those symbols.

### 2. Startup reconciliation

`ops/reconcile.py`:

```python
@dataclass(frozen=True)
class PositionDiff:
    symbol: str
    journal_qty: Decimal | None
    broker_qty: Decimal | None
    kind: str  # "extra_in_broker" | "extra_in_journal" | "qty_mismatch"

@dataclass(frozen=True)
class ReconcileResult:
    diffs: list[PositionDiff]
    cash_journal: Decimal
    cash_broker: Decimal
    cash_diff: Decimal  # broker - journal


def reconcile(*, journal: Journal, broker: GuardedBroker,
              broker_mode: str) -> ReconcileResult: ...
```

**Paper.** Journal is authoritative. Reconciler runs against the freshly-replayed `PaperBroker.from_journal(...)`; expected to match. A diff in paper mode is a bug we want to see.

**Live (robinhood).**
1. Replay journal via `PaperBroker.from_journal(journal, quote_source=broker.get_quote, starting_cash=Decimal("0"))` — starting_cash zero because we compare *deltas* not absolute cash.
2. Ask `broker.get_positions()` and `broker.get_cash()`.
3. Diff per-symbol quantity within `Decimal("1e-6")`, cash to the nearest cent.
4. Return `ReconcileResult`.

**`main.run()` behavior.**
- No diffs → normal startup, both scheduler jobs.
- Diffs → journal `inconsistency` (with the diff list) and `startup_halted` (`reason: reconciliation`); stderr message pointing at recent journal entries; guardian job starts, orchestrator job does not; process waits on signal.
- Exit code 0 on clean shutdown, 2 on reconciliation-halted shutdown (so a shell wrapper can distinguish).

### 3. Orchestrator tick

`ops/scheduler/orchestrator.py`:

```python
def tick(self) -> None:
    if not self._calendar.is_open_now():
        return
    self._maybe_snapshot_equity()
    if self._is_daily_halted() or self._is_weekly_halted():
        return
    universe = self._universe_builder.build(...)
    held = {p.symbol for p in self._broker.get_positions()}
    candidates = self._strategy.rank(universe - held)
    for candidate in candidates:
        decision = self._pipeline_adapter.propagate(candidate.symbol, self._today())
        if decision.action != "BUY":
            continue
        order = self._strategy.build_order(candidate, decision)
        try:
            self._broker.place_order(order)
        except OrderRejected:
            continue  # rule reject: try next candidate
        except BrokerError:
            break  # broker layer error: end tick, retry next cycle
```

Every exception inside `tick()` is journaled and swallowed at the APScheduler boundary so a bad tick can't crash the scheduler.

**`_maybe_snapshot_equity`** — at first tick on/after 09:30 ET on a trading day, writes `journal.record_equity_snapshot(kind='open_day', ...)` if no `open_day` snapshot exists for today. Same shape for `open_week` on Monday. Idempotent — safe to call every tick.

**`_is_daily_halted` / `_is_weekly_halted`** — new `Journal.has_event_today(kind)` and `Journal.has_event_since_last_monday(kind)` helpers; the daily-halt and kill-switch events are emitted by the guardian when drawdown thresholds cross. Orchestrator short-circuits before spending LLM tokens on a day it can't trade.

**Kill-switch action.**
- Paper mode: guardian, when it detects weekly drawdown crossed, records `kill_switch` and then calls `broker.close_position(symbol)` for every open position in a loop (still in guardian's thread; broker lock serialises).
- Live mode: guardian records `kill_switch` and returns. Orchestrator sees the halt on its next tick and no-ops. Guardian keeps enforcing individual stops.

### 4. Thread-safe pipeline adapter

`TradingAgentsPipelineAdapter._ensure_graph` gets a `threading.Lock`. First caller inside the lock builds and caches the graph; subsequent callers acquire the lock, see the cache, and return the shared instance. Contention is effectively nil (only orchestrator's tick thread touches it), but the lock prevents a torn build if two ticks ever overlap.

### 5. Market calendar

`ops/scheduler/market_calendar.py`:

```python
class MarketCalendar:
    def __init__(self) -> None:
        self._cal = mcal.get_calendar("NYSE")

    def is_open_now(self, at: datetime | None = None) -> bool: ...
    def is_trading_day(self, date: date) -> bool: ...
    def previous_close(self, at: datetime | None = None) -> datetime: ...
    def next_open(self, at: datetime | None = None) -> datetime: ...
```

Tests pass `at=` explicitly to avoid pulling in `freezegun`.

### 6. `ops/main.py` + `ops run` CLI

```python
def run() -> int:
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)
    config = load_config()
    journal = Journal(config.journal_path)
    broker = _build_broker(config, journal)
    orchestrator, guardian, calendar = _wire(broker, journal, config)
    result = reconcile.reconcile(journal=journal, broker=broker,
                                 broker_mode=config.broker_mode)
    if result.diffs:
        _emit_halt_events(journal, result)
        print("Reconciliation halted orchestrator — guardian continues.", file=sys.stderr)
        _start_guardian_only(guardian, calendar)
        _run_until_signal()
        return 2
    _start_full_scheduler(orchestrator, guardian, calendar)
    _run_until_signal()
    return 0
```

`ops/cli.py` gains a `run` subcommand that calls `ops.main.run()`.

## Task list (approximate, writing-plans finalises)

1. Scaffold + deps (apscheduler, pandas_market_calendars); empty stubs; branch already at `feat/ops-orchestrator`.
2. Journal fills.stop_loss_price column + migration + record_fill kwarg + last_buy_fill_for + tests.
3. PaperBroker._fill_buy writes stop; from_journal rehydrates from journal; tests.
4. RobinhoodBroker.get_positions() rehydrates stop from journal (with `positions_recovered_without_stops` for missing); tests.
5. MarketCalendar adapter + tests.
6. TradingAgentsPipelineAdapter._ensure_graph lock + concurrency test.
7. Orchestrator.tick — market-hour gate, universe → pipeline → guarded broker, equity snapshots, halt shorts; unit tests.
8. reconcile.py + tests (paper matches, live matches, live mismatch → diffs).
9. Guardian kill-switch action (paper close-all, live halt); tests.
10. ops/main.py::run() + SIGINT graceful shutdown; tests.
11. `ops run` CLI subcommand; CliRunner smoke test.
12. End-to-end integration test — paper, frozen time, one orchestrator tick + one guardian tick, clean shutdown.
13. ops/README.md update (running the service, log locations, halt semantics).
14. Push branch + open PR.

## Constraints

- Decimal end-to-end; no float.
- Every scheduler job body wraps work in try/except that journals the exception rather than propagating (must not crash the scheduler).
- `broker_mode` defaults to `paper`; live is opt-in.
- Baseline entering plan: 192 tests. Target exit: ~230–250.
- Upstream `tradingagents/` package imported, never modified (unchanged from prior plans).

## Open items deferred to the implementation plan

- APScheduler config knobs — max_instances, misfire_grace_time — pick sane defaults, document.
- Log location for stderr/stdout when `ops run` runs (Plan 3c likely wants structured logging).
- Whether `has_event_today` / `has_event_since_last_monday` should scan all events or use an indexed subset — indexed with a small helper is safer as the journal grows.
