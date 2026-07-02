# TradingAgents Live v1 — Always-On Orchestrator Plan (Plan 3b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the always-on `ops run` service on top of Plan 3a's broker layer: APScheduler-driven orchestrator tick every 30 minutes during NYSE market hours, guardian poll every 60 seconds, per-position stops persisted on the fills table and rehydrated on restart, startup reconciliation that halts the orchestrator (but not the guardian) on state divergence, and equity-snapshot writers that make the drawdown rules functional. Live order placement stays gated behind `broker_mode="robinhood"`; paper remains default.

**Architecture:** Single Python process. `ops.main.run()` builds a `GuardedBroker`, runs `reconcile.reconcile(...)`, and starts an `APScheduler.BackgroundScheduler` with two jobs — `orchestrator.tick()` on `CronTrigger(minute='0,30', hour='9-15', day_of_week='mon-fri')` and `guardian.check_stops_once()` on `IntervalTrigger(seconds=60)`. Each tick body no-ops out of market hours via `MarketCalendar.is_open_now()`. Every job body wraps its work in a try/except that journals the exception rather than crashing the scheduler. SIGINT/SIGTERM triggers `sched.shutdown(wait=True)` and `journal.close()`.

**Tech Stack:** Python 3.12, `apscheduler>=3.10`, `pandas_market_calendars>=4.4`, existing `sqlite3`, existing `pytest` + `click`.

## Global Constraints

- Decimal end-to-end for any monetary value — no `float` anywhere in `ops/`.
- Every new public function/class type-hinted with Python 3.12 modern syntax (`list[X]`, `X | None`).
- Production code never instantiates `PaperBroker` or `RobinhoodBroker` directly — always the factory in `ops/__init__.py`.
- The upstream `tradingagents/` package is imported, never modified.
- Tests hitting external network or requiring credentials are marked `integration` (skipped by default). Live-network tests remain gated on `OPS_RH_LIVE_TESTS=1`.
- Branch: `feat/ops-orchestrator` (already created off `main`, design doc committed).
- `config.broker_mode` defaults to `paper`; `robinhood` is opt-in.
- Every scheduler job body must catch and journal exceptions rather than propagate — a bad tick must not crash the scheduler.
- Baseline entering this plan: **192 passing tests** on `main`. Target exit: ~230-250 passing.

## Design Doc

`docs/superpowers/specs/2026-07-02-tradingagents-live-v1-plan-3b-orchestrator-design.md`

## Parent Spec

`docs/superpowers/specs/2026-06-30-tradingagents-live-v1-design.md`

---

## File Structure

```
ops/
  main.py                          # NEW  entrypoint used by `ops run`
  cli.py                           # MODIFY  add `run` subcommand
  reconcile.py                     # NEW  startup state reconciliation
  scheduler/
    __init__.py                    # NEW  empty
    market_calendar.py             # NEW  NYSE calendar adapter
    orchestrator.py                # NEW  tick handler
  pipeline_adapter.py              # MODIFY  threading.Lock around _ensure_graph
  journal.py                       # MODIFY  fills.stop_loss_price + last_buy_fill_for + has_event_today + has_event_since_last_monday
  broker/
    paper.py                       # MODIFY  _fill_buy writes stop; from_journal rehydrates
    robinhood.py                   # MODIFY  get_positions() rehydrates stop from journal
  config.py                        # MODIFY  orchestrator tick config knobs
  position_guardian.py             # MODIFY  kill-switch action (paper close-all, live halt-only)
tests/ops/
  scheduler/
    __init__.py                    # NEW  empty
    test_market_calendar.py        # NEW
    test_orchestrator.py           # NEW
  test_reconcile.py                # NEW
  test_main.py                     # NEW
  test_cli_run.py                  # NEW
  test_integration_orchestrator.py # NEW  end-to-end
  test_pipeline_adapter.py         # MODIFY  add concurrency test
  test_journal.py                  # MODIFY  fills.stop_loss_price + helpers
  test_position_guardian.py        # MODIFY  kill-switch action tests
  broker/
    test_paper.py                  # MODIFY  from_journal rehydrates stop
    test_robinhood.py              # MODIFY  get_positions rehydrates stop
pyproject.toml                     # MODIFY  add apscheduler + pandas_market_calendars
ops/README.md                      # MODIFY  ops run docs
```

---

## Task 0: Scaffold + deps

**Files:**
- Modify: `pyproject.toml` — add `apscheduler>=3.10` and `pandas_market_calendars>=4.4` to dependencies
- Create: `ops/scheduler/__init__.py` (empty)
- Create: `ops/scheduler/market_calendar.py`, `ops/scheduler/orchestrator.py` (docstring stubs)
- Create: `ops/main.py`, `ops/reconcile.py` (docstring stubs)
- Create: `tests/ops/scheduler/__init__.py` (empty)
- Create: `tests/ops/scheduler/test_market_calendar.py`, `tests/ops/scheduler/test_orchestrator.py`, `tests/ops/test_reconcile.py`, `tests/ops/test_main.py`, `tests/ops/test_cli_run.py`, `tests/ops/test_integration_orchestrator.py` (docstring stubs)

**Interfaces:**
- Consumes: nothing
- Produces: importable empty modules; new deps installed.

- [ ] **Step 1: Inspect current deps**

Run: `grep -A 40 'dependencies = \[' pyproject.toml | head -45`

- [ ] **Step 2: Add deps in alphabetical position**

Insert into the `dependencies = [...]` list:
```toml
    "apscheduler>=3.10",
    "pandas_market_calendars>=4.4",
```

- [ ] **Step 3: Install**

Run: `.venv/bin/pip install -e .`
Expected: both packages install cleanly.

- [ ] **Step 4: Verify imports**

Run: `.venv/bin/python -c "import apscheduler; import pandas_market_calendars; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Create stub files**

Each new file gets a one-line docstring:
```python
"""Stub — implemented in later tasks of the orchestrator plan."""
```

- [ ] **Step 6: Baseline check**

Run: `.venv/bin/pytest tests/ops/ -q`
Expected: **192 passed, 4 skipped**.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml ops/main.py ops/reconcile.py ops/scheduler/ tests/ops/scheduler/ tests/ops/test_reconcile.py tests/ops/test_main.py tests/ops/test_cli_run.py tests/ops/test_integration_orchestrator.py
git commit -m "chore(ops): add apscheduler + pandas_market_calendars deps and empty scaffold for plan-3b"
```

---

## Task 1: Journal — fills.stop_loss_price + last_buy_fill_for + event scan helpers

**Files:**
- Modify: `ops/journal.py`
- Modify: `tests/ops/test_journal.py`

**Interfaces:**
- Consumes: existing `Journal` class
- Produces:
  - `fills.stop_loss_price` TEXT column, nullable, migrated on pre-existing DBs
  - `Journal.record_fill(..., stop_loss_price: Decimal | None = None)` — new kwarg (default preserves existing behavior)
  - `Journal.last_buy_fill_for(symbol: str) -> dict[str, Any] | None`
  - `Journal.has_event_today(kind: str, *, now: datetime | None = None) -> bool`
  - `Journal.has_event_since_last_monday(kind: str, *, now: datetime | None = None) -> bool`

The two `has_event_*` helpers use the `at` column already on `events`. `now` param exists for testability (freeze time in tests without freezegun).

- [ ] **Step 1: Write failing tests**

Add to `tests/ops/test_journal.py`:
```python
def test_fills_gain_stop_loss_price_column(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    ts = datetime(2026, 7, 2, 14, tzinfo=timezone.utc)
    j.record_fill(
        order_id="o-1", client_order_id="c-1", symbol="AAPL",
        side="BUY", quantity=Decimal("5"), price=Decimal("10"),
        filled_at=ts, stop_loss_price=Decimal("9.2"),
    )
    fills = j.read_fills()
    assert fills[0]["stop_loss_price"] == Decimal("9.2")


def test_record_fill_stop_loss_price_default_none(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    ts = datetime(2026, 7, 2, 14, tzinfo=timezone.utc)
    j.record_fill(
        order_id="o-1", client_order_id="c-1", symbol="AAPL",
        side="SELL", quantity=Decimal("5"), price=Decimal("10"),
        filled_at=ts,
    )
    assert j.read_fills()[0]["stop_loss_price"] is None


def test_last_buy_fill_for_returns_most_recent_buy(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    older = datetime(2026, 6, 30, tzinfo=timezone.utc)
    newer = datetime(2026, 7, 2, tzinfo=timezone.utc)
    j.record_fill(order_id="o-1", client_order_id="c-1", symbol="AAPL",
                  side="BUY", quantity=Decimal("5"), price=Decimal("10"),
                  filled_at=older, stop_loss_price=Decimal("9"))
    j.record_fill(order_id="o-2", client_order_id="c-2", symbol="AAPL",
                  side="BUY", quantity=Decimal("3"), price=Decimal("11"),
                  filled_at=newer, stop_loss_price=Decimal("10.1"))
    last = j.last_buy_fill_for("AAPL")
    assert last["stop_loss_price"] == Decimal("10.1")
    assert last["filled_at"] == newer


def test_last_buy_fill_for_none_when_missing(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    assert j.last_buy_fill_for("AAPL") is None


def test_last_buy_fill_for_ignores_sells(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    ts = datetime(2026, 7, 2, tzinfo=timezone.utc)
    j.record_fill(order_id="o-1", client_order_id="c-1", symbol="AAPL",
                  side="SELL", quantity=Decimal("5"), price=Decimal("10"),
                  filled_at=ts)
    assert j.last_buy_fill_for("AAPL") is None


def test_has_event_today_true_when_event_today(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    j.record_event("daily_halt", {"reason": "drawdown"})
    now = datetime.now(timezone.utc)
    assert j.has_event_today("daily_halt", now=now) is True


def test_has_event_today_false_when_no_event_today(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    now = datetime.now(timezone.utc)
    assert j.has_event_today("daily_halt", now=now) is False


def test_has_event_since_last_monday_true(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    j.record_event("kill_switch", {"reason": "weekly"})
    # 2026-07-02 is a Thursday; last Monday is 2026-06-29.
    now = datetime(2026, 7, 2, 15, tzinfo=timezone.utc)
    assert j.has_event_since_last_monday("kill_switch", now=now) is True


def test_migrates_pre_existing_fills_without_stop_column(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.sqlite")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL,
            order_id TEXT NOT NULL,
            client_order_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity TEXT NOT NULL,
            price TEXT NOT NULL,
            filled_at TEXT NOT NULL
        );
    """)
    conn.close()
    j = Journal(path)
    ts = datetime(2026, 7, 2, tzinfo=timezone.utc)
    j.record_fill(order_id="o-1", client_order_id="c-1", symbol="AAPL",
                  side="BUY", quantity=Decimal("1"), price=Decimal("10"),
                  filled_at=ts, stop_loss_price=Decimal("9"))
    assert j.read_fills()[0]["stop_loss_price"] == Decimal("9")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/ops/test_journal.py -v -k "stop_loss_price or last_buy_fill or has_event or migrates_pre_existing_fills"`
Expected: FAIL — methods/column don't exist.

- [ ] **Step 3: Extend `_SCHEMA` for fills**

In `ops/journal.py`, update the `fills` table DDL inside `_SCHEMA` to include `stop_loss_price TEXT`:
```python
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    order_id TEXT NOT NULL,
    client_order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    filled_at TEXT NOT NULL,
    stop_loss_price TEXT
);
```

- [ ] **Step 4: Migration guard in `__init__`**

Right after `executescript(_SCHEMA)` (and any existing equity_snapshots migration), add:
```python
cur = self._conn.execute("PRAGMA table_info(fills)")
cols = {row[1] for row in cur.fetchall()}
if "stop_loss_price" not in cols:
    self._conn.execute("ALTER TABLE fills ADD COLUMN stop_loss_price TEXT")
```

- [ ] **Step 5: Extend `record_fill` signature**

```python
def record_fill(
    self, *, order_id: str, client_order_id: str, symbol: str, side: str,
    quantity: Decimal, price: Decimal, filled_at: datetime,
    stop_loss_price: Decimal | None = None,
) -> None:
    self._conn.execute(
        "INSERT INTO fills (at, order_id, client_order_id, symbol, side, quantity, price, filled_at, stop_loss_price)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _now_iso(), order_id, client_order_id, symbol, side,
            str(quantity), str(price), _to_iso(filled_at),
            str(stop_loss_price) if stop_loss_price is not None else None,
        ),
    )
```

- [ ] **Step 6: Extend `read_fills` to return the new column**

```python
def read_fills(self) -> list[dict[str, Any]]:
    cur = self._conn.execute(
        "SELECT at, order_id, client_order_id, symbol, side, quantity, price, filled_at, stop_loss_price"
        " FROM fills ORDER BY id"
    )
    return [
        {
            "at": _from_iso(row[0]), "order_id": row[1],
            "client_order_id": row[2], "symbol": row[3], "side": row[4],
            "quantity": Decimal(row[5]), "price": Decimal(row[6]),
            "filled_at": _from_iso(row[7]),
            "stop_loss_price": Decimal(row[8]) if row[8] is not None else None,
        }
        for row in cur
    ]
```

- [ ] **Step 7: Add `last_buy_fill_for`**

```python
def last_buy_fill_for(self, symbol: str) -> dict[str, Any] | None:
    row = self._conn.execute(
        "SELECT at, order_id, client_order_id, symbol, side, quantity, price, filled_at, stop_loss_price"
        " FROM fills WHERE symbol = ? AND side = 'BUY' ORDER BY filled_at DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row is None:
        return None
    return {
        "at": _from_iso(row[0]), "order_id": row[1],
        "client_order_id": row[2], "symbol": row[3], "side": row[4],
        "quantity": Decimal(row[5]), "price": Decimal(row[6]),
        "filled_at": _from_iso(row[7]),
        "stop_loss_price": Decimal(row[8]) if row[8] is not None else None,
    }
```

- [ ] **Step 8: Add `has_event_today` and `has_event_since_last_monday`**

```python
def has_event_today(self, kind: str, *, now: datetime | None = None) -> bool:
    when = now if now is not None else datetime.now(timezone.utc)
    start = when.replace(hour=0, minute=0, second=0, microsecond=0)
    row = self._conn.execute(
        "SELECT 1 FROM events WHERE kind = ? AND at >= ? LIMIT 1",
        (kind, _to_iso(start)),
    ).fetchone()
    return row is not None


def has_event_since_last_monday(self, kind: str, *, now: datetime | None = None) -> bool:
    when = now if now is not None else datetime.now(timezone.utc)
    monday = when - timedelta(days=when.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    row = self._conn.execute(
        "SELECT 1 FROM events WHERE kind = ? AND at >= ? LIMIT 1",
        (kind, _to_iso(monday)),
    ).fetchone()
    return row is not None
```

Add `from datetime import timedelta` to the imports.

- [ ] **Step 9: Run tests**

Run: `.venv/bin/pytest tests/ops/test_journal.py -v`
Expected: PASS.

- [ ] **Step 10: Run full suite**

Run: `.venv/bin/pytest tests/ops/ -q`
Expected: PASS (equity_snapshots + old fills tests unaffected).

- [ ] **Step 11: Commit**

```bash
git add ops/journal.py tests/ops/test_journal.py
git commit -m "feat(ops/journal): fills.stop_loss_price + last_buy_fill_for + event-scan helpers

Adds the schema + APIs Plan 3b consumes: fill journaling now records the
per-position stop; last_buy_fill_for reconstructs it on restart;
has_event_today / has_event_since_last_monday let the orchestrator
short-circuit before spending LLM tokens on a halted day."
```

---

## Task 2: PaperBroker._fill_buy writes stop; from_journal rehydrates

**Files:**
- Modify: `ops/broker/paper.py`
- Modify: `tests/ops/broker/test_paper.py`

**Interfaces:**
- Consumes: `Journal.record_fill(..., stop_loss_price=...)`, `Journal.last_buy_fill_for(symbol)` (Task 1)
- Produces:
  - `PaperBroker._fill_buy` passes `stop_loss_price=order.stop_loss_price` when journaling.
  - `PaperBroker.from_journal(...)` attaches recovered stop from `last_buy_fill_for` on each replayed symbol.

- [ ] **Step 1: Write failing tests**

Add to `tests/ops/broker/test_paper.py`:
```python
def test_fill_buy_journals_stop_loss_price(_broker, tmp_path):
    broker, journal = _broker(prices={"AAPL": Decimal("10")}, cash=Decimal("500"))
    broker.place_order(Order(
        client_order_id="b-1", symbol="AAPL", side=Side.BUY,
        notional_dollars=Decimal("50"), order_type=OrderType.MARKET,
        stop_loss_price=Decimal("9.2"),
    ))
    fills = journal.read_fills()
    assert fills[0]["side"] == "BUY"
    assert fills[0]["stop_loss_price"] == Decimal("9.2")


def test_from_journal_rehydrates_stop_loss_price(tmp_path, quote_source):
    from ops.broker.paper import PaperBroker
    journal = Journal(str(tmp_path / "j.sqlite"))
    quote_source.set("AAPL", Decimal("10"))
    seed = PaperBroker(journal=journal, quote_source=quote_source, starting_cash=Decimal("500"))
    seed.place_order(Order(
        client_order_id="b-1", symbol="AAPL", side=Side.BUY,
        notional_dollars=Decimal("100"), order_type=OrderType.MARKET,
        stop_loss_price=Decimal("9.2"),
    ))
    replayed = PaperBroker.from_journal(
        journal=journal, quote_source=quote_source, starting_cash=Decimal("500"),
    )
    positions = replayed.get_positions()
    assert positions[0].stop_loss_price == Decimal("9.2")


def test_from_journal_stop_none_when_no_journaled_stop(tmp_path, quote_source):
    """Positions opened via a BUY that has no stop (legacy) still get None."""
    from ops.broker.paper import PaperBroker
    from ops.journal import Journal
    journal = Journal(str(tmp_path / "j.sqlite"))
    quote_source.set("AAPL", Decimal("10"))
    # Directly journal a BUY fill with stop_loss_price=None to simulate legacy data.
    ts = datetime(2026, 7, 2, tzinfo=timezone.utc)
    journal.record_order(client_order_id="b-1", symbol="AAPL", side="BUY",
                         notional_dollars=Decimal("50"), stop_loss_price=None)
    journal.record_fill(order_id="o-1", client_order_id="b-1", symbol="AAPL",
                        side="BUY", quantity=Decimal("5"), price=Decimal("10"),
                        filled_at=ts, stop_loss_price=None)
    replayed = PaperBroker.from_journal(
        journal=journal, quote_source=quote_source, starting_cash=Decimal("500"),
    )
    assert replayed.get_positions()[0].stop_loss_price is None
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/pytest tests/ops/broker/test_paper.py -v -k "stop_loss_price or rehydrate"`
Expected: FAIL — stops not journaled / not rehydrated.

- [ ] **Step 3: Update `_fill_buy` to journal the stop**

In `ops/broker/paper.py::_fill_buy`, find the `_make_fill` call at the end and pass the stop through. The cleanest path: modify `_make_fill` to accept `stop_loss_price` and journal it there. Update `_make_fill`:
```python
def _make_fill(
    self, order: Order, qty: Decimal, price: Decimal,
    *, stop_loss_price: Decimal | None = None,
) -> Fill:
    fill = Fill(
        order_id=str(uuid4()),
        client_order_id=order.client_order_id,
        symbol=order.symbol, side=order.side,
        quantity=qty, price=price,
        filled_at=datetime.now(timezone.utc),
    )
    self._journal.record_fill(
        order_id=fill.order_id, client_order_id=fill.client_order_id,
        symbol=fill.symbol, side=fill.side.value,
        quantity=fill.quantity, price=fill.price, filled_at=fill.filled_at,
        stop_loss_price=stop_loss_price,
    )
    return fill
```

Then in `_fill_buy`, pass `stop_loss_price=order.stop_loss_price` on the `_make_fill` call:
```python
return self._make_fill(order, qty, price, stop_loss_price=order.stop_loss_price)
```

Leave `_fill_sell` and `close_position` paths passing `stop_loss_price=None` (which is already the default), matching the design (sells don't carry stops).

- [ ] **Step 4: Update `from_journal` to attach stops after replay**

Read the current `from_journal` implementation and, after the replay loop finishes building `_positions`, walk that dict and re-attach stops:
```python
# After the replay loop, before returning `broker`:
new_positions = {}
for symbol, pos in broker._positions.items():
    last_buy = journal.last_buy_fill_for(symbol)
    if last_buy is not None and last_buy["stop_loss_price"] is not None:
        new_positions[symbol] = Position(
            symbol=pos.symbol,
            quantity=pos.quantity,
            avg_entry_price=pos.avg_entry_price,
            stop_loss_price=last_buy["stop_loss_price"],
        )
    else:
        new_positions[symbol] = pos
broker._positions = new_positions
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/ops/broker/test_paper.py -v`
Expected: PASS.

- [ ] **Step 6: Run full suite**

Run: `.venv/bin/pytest tests/ops/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ops/broker/paper.py tests/ops/broker/test_paper.py
git commit -m "feat(ops/broker): PaperBroker persists per-position stop on BUY; from_journal rehydrates

Closes the state-recovery gap flagged in the Plan 3a review: after a
restart, PaperBroker.from_journal reconstructs positions with their
strategy-set stop_loss_price attached, so guardian no longer silently
falls back to the config default."
```

---

## Task 3: RobinhoodBroker.get_positions() rehydrates stop from journal

**Files:**
- Modify: `ops/broker/robinhood.py`
- Modify: `tests/ops/broker/test_robinhood.py`

**Interfaces:**
- Consumes: `Journal.last_buy_fill_for` (Task 1)
- Produces: `RobinhoodBroker.get_positions()` maps each `MCPPosition` and attaches `stop_loss_price` from the most recent journaled BUY when available; None otherwise (guardian falls back to config).

- [ ] **Step 1: Write failing tests**

Add to `tests/ops/broker/test_robinhood.py`:
```python
def test_get_positions_attaches_stop_from_journal(fake_client, journal):
    """A journaled BUY with stop → RobinhoodBroker.get_positions() carries it."""
    fake_client.seed_position("AAPL", Decimal("5"), Decimal("10"))
    ts = datetime(2026, 7, 2, tzinfo=timezone.utc)
    journal.record_fill(order_id="o-1", client_order_id="b-1", symbol="AAPL",
                        side="BUY", quantity=Decimal("5"), price=Decimal("10"),
                        filled_at=ts, stop_loss_price=Decimal("9.2"))
    broker = RobinhoodBroker(client=fake_client, journal=journal)
    positions = broker.get_positions()
    assert positions[0].stop_loss_price == Decimal("9.2")


def test_get_positions_stop_none_when_no_journaled_buy(fake_client, journal):
    """Manual (non-journaled) position in RH → stop_loss_price=None."""
    fake_client.seed_position("MSFT", Decimal("2"), Decimal("300"))
    broker = RobinhoodBroker(client=fake_client, journal=journal)
    positions = broker.get_positions()
    assert positions[0].stop_loss_price is None


def test_get_positions_stop_none_when_journaled_buy_lacks_stop(fake_client, journal):
    fake_client.seed_position("NVDA", Decimal("1"), Decimal("500"))
    ts = datetime(2026, 7, 2, tzinfo=timezone.utc)
    journal.record_fill(order_id="o-1", client_order_id="b-1", symbol="NVDA",
                        side="BUY", quantity=Decimal("1"), price=Decimal("500"),
                        filled_at=ts, stop_loss_price=None)
    broker = RobinhoodBroker(client=fake_client, journal=journal)
    assert broker.get_positions()[0].stop_loss_price is None
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/pytest tests/ops/broker/test_robinhood.py -v -k "attaches_stop or stop_none"`
Expected: FAIL — current code sets `stop_loss_price=None` unconditionally.

- [ ] **Step 3: Update `RobinhoodBroker.get_positions()`**

Replace the current implementation:
```python
def get_positions(self) -> list[Position]:
    try:
        mcp_positions = self._client.get_positions()
    except MCPUnavailable as exc:
        raise BrokerError(f"mcp unavailable: {exc}") from exc
    result: list[Position] = []
    for p in mcp_positions:
        stop = None
        last_buy = self._journal.last_buy_fill_for(p.symbol)
        if last_buy is not None:
            stop = last_buy["stop_loss_price"]
        result.append(Position(
            symbol=p.symbol, quantity=p.quantity,
            avg_entry_price=p.avg_price, stop_loss_price=stop,
        ))
    return result
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/ops/broker/test_robinhood.py -v`
Expected: PASS. Prior tests that seeded positions but expected `stop_loss_price=None` still pass because those tests don't journal a BUY.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest tests/ops/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ops/broker/robinhood.py tests/ops/broker/test_robinhood.py
git commit -m "feat(ops/broker): RobinhoodBroker.get_positions rehydrates per-position stop from journal

Live restart no longer silently loosens strategy-set stops for
positions we opened. Positions RH reports that have no journaled BUY
still come back with stop_loss_price=None; the reconciler in a later
task flags those as positions_recovered_without_stops."
```

---

## Task 4: MarketCalendar adapter

**Files:**
- Modify: `ops/scheduler/market_calendar.py`
- Modify: `tests/ops/scheduler/test_market_calendar.py`

**Interfaces:**
- Consumes: `pandas_market_calendars` (Task 0 dep)
- Produces:
  - `MarketCalendar.is_open_now(at: datetime | None = None) -> bool`
  - `MarketCalendar.is_trading_day(d: date) -> bool`
  - `MarketCalendar.previous_close(at: datetime | None = None) -> datetime`
  - `MarketCalendar.next_open(at: datetime | None = None) -> datetime`

All datetimes are timezone-aware. Interpret `at=None` as "now, UTC".

- [ ] **Step 1: Write failing tests**

`tests/ops/scheduler/test_market_calendar.py`:
```python
"""Uses explicit `at=` params instead of freezegun — no new test dep."""
from datetime import date, datetime, timezone
import pytest
from ops.scheduler.market_calendar import MarketCalendar

# Fixed reference points chosen from a real NYSE calendar:
#   2026-07-02 (Thursday): trading day
#   2026-07-03 (Friday): market closed for July 4 observed
#   2026-07-04 (Saturday): weekend
#   2026-07-06 (Monday): trading day


def test_is_open_now_regular_hours_true():
    cal = MarketCalendar()
    at = datetime(2026, 7, 2, 14, 30, tzinfo=timezone.utc)   # 10:30 ET
    assert cal.is_open_now(at=at) is True


def test_is_open_now_pre_market_false():
    cal = MarketCalendar()
    at = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)   # 08:00 ET
    assert cal.is_open_now(at=at) is False


def test_is_open_now_weekend_false():
    cal = MarketCalendar()
    at = datetime(2026, 7, 4, 14, 30, tzinfo=timezone.utc)   # Saturday
    assert cal.is_open_now(at=at) is False


def test_is_trading_day_true():
    cal = MarketCalendar()
    assert cal.is_trading_day(date(2026, 7, 2)) is True
    assert cal.is_trading_day(date(2026, 7, 6)) is True


def test_is_trading_day_weekend_false():
    cal = MarketCalendar()
    assert cal.is_trading_day(date(2026, 7, 4)) is False


def test_next_open_from_saturday_is_next_monday_or_holiday_skipped():
    cal = MarketCalendar()
    at = datetime(2026, 7, 4, 12, tzinfo=timezone.utc)
    nxt = cal.next_open(at=at)
    assert nxt > at
    assert cal.is_trading_day(nxt.astimezone(timezone.utc).date())


def test_previous_close_is_before_at():
    cal = MarketCalendar()
    at = datetime(2026, 7, 2, 14, tzinfo=timezone.utc)
    prev = cal.previous_close(at=at)
    assert prev < at
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/pytest tests/ops/scheduler/test_market_calendar.py -v`
Expected: FAIL — module has only a docstring.

- [ ] **Step 3: Implement `MarketCalendar`**

`ops/scheduler/market_calendar.py`:
```python
"""NYSE calendar adapter over pandas_market_calendars.

Every method accepts an optional `at`/`d` for testability; production
callers pass `None` to mean "now, UTC".
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import pandas_market_calendars as mcal


class MarketCalendar:
    def __init__(self) -> None:
        self._cal = mcal.get_calendar("NYSE")

    def is_open_now(self, at: datetime | None = None) -> bool:
        when = at if at is not None else datetime.now(timezone.utc)
        d = when.astimezone(timezone.utc).date()
        if not self.is_trading_day(d):
            return False
        sched = self._cal.schedule(start_date=d, end_date=d)
        if sched.empty:
            return False
        open_ = sched.iloc[0]["market_open"].to_pydatetime()
        close_ = sched.iloc[0]["market_close"].to_pydatetime()
        return open_ <= when <= close_

    def is_trading_day(self, d: date) -> bool:
        return self._trading_day_cached(d.isoformat())

    @lru_cache(maxsize=1024)
    def _trading_day_cached(self, iso: str) -> bool:
        d = date.fromisoformat(iso)
        sched = self._cal.schedule(start_date=d, end_date=d)
        return not sched.empty

    def previous_close(self, at: datetime | None = None) -> datetime:
        when = at if at is not None else datetime.now(timezone.utc)
        start = (when - timedelta(days=7)).date()
        end = when.date()
        sched = self._cal.schedule(start_date=start, end_date=end)
        for row in reversed(list(sched.iterrows())):
            close_ = row[1]["market_close"].to_pydatetime()
            if close_ < when:
                return close_
        raise RuntimeError(f"no previous close within 7 days of {when}")

    def next_open(self, at: datetime | None = None) -> datetime:
        when = at if at is not None else datetime.now(timezone.utc)
        start = when.date()
        end = (when + timedelta(days=7)).date()
        sched = self._cal.schedule(start_date=start, end_date=end)
        for _, row in sched.iterrows():
            open_ = row["market_open"].to_pydatetime()
            if open_ > when:
                return open_
        raise RuntimeError(f"no next open within 7 days of {when}")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/ops/scheduler/test_market_calendar.py -v`
Expected: PASS. If `previous_close`/`next_open` timezone details differ from expected (pandas_market_calendars returns tz-aware pd.Timestamp; the `.to_pydatetime()` call preserves tz), the tests still pass because they only assert ordering, not exact values.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest tests/ops/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ops/scheduler/market_calendar.py tests/ops/scheduler/test_market_calendar.py
git commit -m "feat(ops/scheduler): MarketCalendar adapter over pandas_market_calendars

is_open_now / is_trading_day / next_open / previous_close, all
timezone-aware, with an lru_cache on the date-only trading-day check
so guardian's 60s poll stays cheap during market hours."
```

---

## Task 5: TradingAgentsPipelineAdapter — threading.Lock around _ensure_graph

**Files:**
- Modify: `ops/pipeline_adapter.py`
- Modify: `tests/ops/test_pipeline_adapter.py`

**Interfaces:**
- Consumes: existing `TradingAgentsPipelineAdapter`
- Produces: `_ensure_graph` is safe to call from multiple threads; returns a single cached instance.

- [ ] **Step 1: Read current implementation**

Run: `sed -n '1,80p' ops/pipeline_adapter.py`
Note where `_ensure_graph` builds the graph and caches it on `self._graph` (or similar).

- [ ] **Step 2: Write failing concurrency test**

Add to `tests/ops/test_pipeline_adapter.py`:
```python
def test_ensure_graph_is_thread_safe(monkeypatch):
    """Two concurrent callers get the same graph instance; build runs once."""
    import threading
    from ops.pipeline_adapter import TradingAgentsPipelineAdapter

    build_count = 0
    build_lock = threading.Lock()
    barrier = threading.Barrier(2)

    class FakeGraph:
        pass

    def slow_build(self):
        nonlocal build_count
        barrier.wait()
        # Simulate a slow build so two threads overlap.
        import time
        time.sleep(0.05)
        with build_lock:
            build_count += 1
        return FakeGraph()

    adapter = TradingAgentsPipelineAdapter.__new__(TradingAgentsPipelineAdapter)
    adapter._graph = None
    adapter._lock = threading.Lock()

    # Monkeypatch the actual builder used inside _ensure_graph:
    monkeypatch.setattr(TradingAgentsPipelineAdapter, "_build_graph", slow_build)

    results = {}
    def call(idx):
        results[idx] = adapter._ensure_graph()

    t1 = threading.Thread(target=call, args=(0,))
    t2 = threading.Thread(target=call, args=(1,))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert results[0] is results[1]
    assert build_count == 1
```

Adjust the test if the current `_ensure_graph` builds inline rather than via a `_build_graph` method — refactor the build into a `_build_graph` method in the next step so the monkeypatch has a stable target.

- [ ] **Step 3: Verify failure**

Run: `.venv/bin/pytest tests/ops/test_pipeline_adapter.py::test_ensure_graph_is_thread_safe -v`
Expected: FAIL — no `_lock`, or `build_count == 2` from the race.

- [ ] **Step 4: Refactor `_ensure_graph` to lock + `_build_graph`**

In `ops/pipeline_adapter.py`, add `import threading` at the top. In `__init__`, add `self._lock = threading.Lock()`. Rewrite `_ensure_graph` to acquire the lock, double-check the cache, then call an extracted `_build_graph` method:
```python
def _ensure_graph(self):
    if self._graph is not None:
        return self._graph
    with self._lock:
        if self._graph is None:
            self._graph = self._build_graph()
    return self._graph


def _build_graph(self):
    # existing build logic moved here (verbatim)
    ...
```

- [ ] **Step 5: Run test**

Run: `.venv/bin/pytest tests/ops/test_pipeline_adapter.py::test_ensure_graph_is_thread_safe -v`
Expected: PASS.

- [ ] **Step 6: Run full suite**

Run: `.venv/bin/pytest tests/ops/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ops/pipeline_adapter.py tests/ops/test_pipeline_adapter.py
git commit -m "fix(ops/pipeline_adapter): make _ensure_graph thread-safe

Under APScheduler two overlapping orchestrator ticks could double-build
the upstream graph. Double-checked lock keeps the cache path
lock-free once populated and prevents torn state on first build."
```

---

## Task 6: Orchestrator.tick — market gate + equity snapshots + halt shorts + candidate loop

**Files:**
- Modify: `ops/scheduler/orchestrator.py`
- Modify: `tests/ops/scheduler/test_orchestrator.py`

**Interfaces:**
- Consumes: `MarketCalendar` (Task 4), `Journal.record_equity_snapshot`/`get_latest_equity_snapshot` (Plan 3a), `Journal.has_event_today` / `has_event_since_last_monday` (Task 1), the `GuardedBroker`, an injected `UniverseBuilder`, `Strategy`, `PipelineAdapter`.
- Produces:
  - `class Orchestrator` with `tick() -> None`.
  - Injectable dependencies via constructor kwargs so tests never build a real universe/pipeline.

- [ ] **Step 1: Write failing tests**

`tests/ops/scheduler/test_orchestrator.py`:
```python
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from ops.scheduler.orchestrator import Orchestrator
from ops.broker.types import Order, OrderType, Side
from ops.broker.base import OrderRejected, BrokerError


def _fake_calendar(is_open: bool):
    cal = MagicMock()
    cal.is_open_now.return_value = is_open
    return cal


def _fake_pipeline(decision_by_symbol: dict[str, str]):
    pa = MagicMock()
    def _propagate(symbol, d):
        result = MagicMock()
        result.action = decision_by_symbol.get(symbol, "HOLD")
        return result
    pa.propagate.side_effect = _propagate
    return pa


def _fake_strategy(candidates, buy_order_by_symbol):
    strat = MagicMock()
    strat.rank.return_value = candidates
    def _build(candidate, decision):
        return buy_order_by_symbol[candidate.symbol]
    strat.build_order.side_effect = _build
    return strat


def _fake_universe(symbols):
    ub = MagicMock()
    ub.build.return_value = set(symbols)
    return ub


def _fake_broker(positions=None, equity=Decimal("1000"), cash=Decimal("500")):
    b = MagicMock()
    b.get_positions.return_value = positions or []
    b.get_equity.return_value = equity
    b.get_cash.return_value = cash
    return b


def _order(symbol):
    return Order(
        client_order_id=f"b-{symbol}", symbol=symbol, side=Side.BUY,
        notional_dollars=Decimal("50"), order_type=OrderType.MARKET,
        stop_loss_price=Decimal("9.2"),
    )


def test_tick_market_closed_noop(tmp_path):
    from ops.journal import Journal
    j = Journal(str(tmp_path / "j.sqlite"))
    orch = Orchestrator(
        broker=_fake_broker(), universe_builder=_fake_universe([]),
        strategy=_fake_strategy([], {}), pipeline_adapter=_fake_pipeline({}),
        calendar=_fake_calendar(is_open=False), journal=j,
        config=MagicMock(),
    )
    orch.tick()
    assert j.read_events() == []


def test_tick_places_buy_when_pipeline_says_buy(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker()
    orch = Orchestrator(
        broker=broker,
        universe_builder=_fake_universe(["AAPL"]),
        strategy=_fake_strategy([MagicMock(symbol="AAPL")], {"AAPL": _order("AAPL")}),
        pipeline_adapter=_fake_pipeline({"AAPL": "BUY"}),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    broker.place_order.assert_called_once()
    placed = broker.place_order.call_args.args[0]
    assert placed.symbol == "AAPL"


def test_tick_skips_hold(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker()
    orch = Orchestrator(
        broker=broker,
        universe_builder=_fake_universe(["AAPL"]),
        strategy=_fake_strategy([MagicMock(symbol="AAPL")], {"AAPL": _order("AAPL")}),
        pipeline_adapter=_fake_pipeline({"AAPL": "HOLD"}),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    broker.place_order.assert_not_called()


def test_tick_continues_after_rule_reject(tmp_path):
    """OrderRejected on one candidate → next candidate still tried."""
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker()
    broker.place_order.side_effect = [OrderRejected("Some", "reason"), MagicMock()]
    orch = Orchestrator(
        broker=broker,
        universe_builder=_fake_universe(["AAPL", "MSFT"]),
        strategy=_fake_strategy(
            [MagicMock(symbol="AAPL"), MagicMock(symbol="MSFT")],
            {"AAPL": _order("AAPL"), "MSFT": _order("MSFT")},
        ),
        pipeline_adapter=_fake_pipeline({"AAPL": "BUY", "MSFT": "BUY"}),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    assert broker.place_order.call_count == 2


def test_tick_breaks_on_broker_error(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker()
    broker.place_order.side_effect = BrokerError("mcp died")
    orch = Orchestrator(
        broker=broker,
        universe_builder=_fake_universe(["AAPL", "MSFT"]),
        strategy=_fake_strategy(
            [MagicMock(symbol="AAPL"), MagicMock(symbol="MSFT")],
            {"AAPL": _order("AAPL"), "MSFT": _order("MSFT")},
        ),
        pipeline_adapter=_fake_pipeline({"AAPL": "BUY", "MSFT": "BUY"}),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    assert broker.place_order.call_count == 1


def test_maybe_snapshot_equity_writes_open_day_once(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker(equity=Decimal("1000"), cash=Decimal("500"))
    orch = Orchestrator(
        broker=broker, universe_builder=_fake_universe([]),
        strategy=_fake_strategy([], {}), pipeline_adapter=_fake_pipeline({}),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    orch.tick()
    open_day_snaps = [
        s for s in j._conn.execute(
            "SELECT kind FROM equity_snapshots"
        ).fetchall() if s[0] == "open_day"
    ]
    assert len(open_day_snaps) == 1


def test_tick_shortcircuits_on_daily_halt(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    j.record_event("daily_halt", {"reason": "drawdown"})
    broker = _fake_broker()
    universe = _fake_universe(["AAPL"])
    orch = Orchestrator(
        broker=broker, universe_builder=universe,
        strategy=_fake_strategy([MagicMock(symbol="AAPL")], {"AAPL": _order("AAPL")}),
        pipeline_adapter=_fake_pipeline({"AAPL": "BUY"}),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    universe.build.assert_not_called()
    broker.place_order.assert_not_called()


def test_tick_shortcircuits_on_weekly_kill_switch(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    j.record_event("kill_switch", {"reason": "weekly"})
    broker = _fake_broker()
    universe = _fake_universe(["AAPL"])
    orch = Orchestrator(
        broker=broker, universe_builder=universe,
        strategy=_fake_strategy([MagicMock(symbol="AAPL")], {"AAPL": _order("AAPL")}),
        pipeline_adapter=_fake_pipeline({"AAPL": "BUY"}),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    universe.build.assert_not_called()
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/pytest tests/ops/scheduler/test_orchestrator.py -v`
Expected: FAIL — Orchestrator does not exist.

- [ ] **Step 3: Implement Orchestrator**

`ops/scheduler/orchestrator.py`:
```python
"""Orchestrator tick handler — called by APScheduler at :00/:30 during trading hours."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from ops.broker.base import BrokerError, OrderRejected


class Orchestrator:
    def __init__(
        self, *, broker, universe_builder, strategy, pipeline_adapter,
        calendar, journal, config,
    ) -> None:
        self._broker = broker
        self._universe_builder = universe_builder
        self._strategy = strategy
        self._pipeline_adapter = pipeline_adapter
        self._calendar = calendar
        self._journal = journal
        self._config = config

    def tick(self) -> None:
        try:
            self._tick_impl()
        except Exception as exc:
            self._journal.record_event(
                "orchestrator_tick_error",
                {"error": f"{type(exc).__name__}: {exc}"},
            )

    def _tick_impl(self) -> None:
        if not self._calendar.is_open_now():
            return
        self._maybe_snapshot_equity()
        if self._is_daily_halted() or self._is_weekly_halted():
            return
        universe = self._universe_builder.build()
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
                continue
            except BrokerError:
                break

    def _maybe_snapshot_equity(self) -> None:
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        existing_day = self._journal.get_latest_equity_snapshot(
            kind="open_day", since=start_of_day,
        )
        if existing_day is None:
            self._journal.record_equity_snapshot(
                kind="open_day",
                equity=self._broker.get_equity(),
                cash=self._broker.get_cash(),
                at=now,
            )
        # Weekly snapshot at first tick of the week.
        weekday = now.weekday()
        monday = now - _days(weekday)
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        existing_week = self._journal.get_latest_equity_snapshot(
            kind="open_week", since=monday,
        )
        if existing_week is None:
            self._journal.record_equity_snapshot(
                kind="open_week",
                equity=self._broker.get_equity(),
                cash=self._broker.get_cash(),
                at=now,
            )

    def _is_daily_halted(self) -> bool:
        return self._journal.has_event_today("daily_halt")

    def _is_weekly_halted(self) -> bool:
        return self._journal.has_event_since_last_monday("kill_switch")

    def _today(self) -> str:
        return datetime.now(timezone.utc).date().isoformat()


def _days(n: int):
    from datetime import timedelta
    return timedelta(days=n)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/ops/scheduler/test_orchestrator.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest tests/ops/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ops/scheduler/orchestrator.py tests/ops/scheduler/test_orchestrator.py
git commit -m "feat(ops/scheduler): Orchestrator.tick — market gate + equity snapshots + halt shorts

Every tick: gate on market hours, write open_day/open_week snapshots
idempotently, short-circuit on daily_halt or kill_switch events, then
run universe → strategy rank → pipeline decision → guarded broker per
candidate. Rule rejects continue the loop; BrokerErrors abort the
tick. All uncaught exceptions are journaled so the scheduler cannot
crash."
```

---

## Task 7: reconcile — startup state comparison

**Files:**
- Modify: `ops/reconcile.py`
- Modify: `tests/ops/test_reconcile.py`

**Interfaces:**
- Consumes: `Journal`, `GuardedBroker`, `PaperBroker.from_journal` (Plan 3a)
- Produces:
  - `PositionDiff` and `ReconcileResult` frozen dataclasses.
  - `reconcile(*, journal, broker, broker_mode) -> ReconcileResult`.
  - `emit_reconcile_events(journal, result)` writes `inconsistency` (if diffs) and `positions_recovered_without_stops` (if any recovered position lacks a journaled stop) events.

- [ ] **Step 1: Write failing tests**

`tests/ops/test_reconcile.py`:
```python
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ops.reconcile import PositionDiff, ReconcileResult, reconcile, emit_reconcile_events
from ops.journal import Journal
from ops.broker.types import Order, OrderType, Side
from ops import build_guarded_paper_broker
from ops.config import OpsConfig


def _quote_source(prices):
    def q(sym):
        return prices[sym]
    return q


def test_reconcile_paper_empty_journal_no_diffs(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = build_guarded_paper_broker(
        config=OpsConfig(), journal=j,
        quote_source=_quote_source({"AAPL": Decimal("10")}),
        starting_cash=Decimal("500"),
        start_of_day_equity=lambda: Decimal("500"),
        start_of_week_equity=lambda: Decimal("500"),
    )
    result = reconcile(journal=j, broker=broker, broker_mode="paper")
    assert result.diffs == []


def test_reconcile_paper_after_buy_no_diffs(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = build_guarded_paper_broker(
        config=OpsConfig(), journal=j,
        quote_source=_quote_source({"AAPL": Decimal("10")}),
        starting_cash=Decimal("500"),
        start_of_day_equity=lambda: Decimal("500"),
        start_of_week_equity=lambda: Decimal("500"),
    )
    broker.place_order(Order(
        client_order_id="b-1", symbol="AAPL", side=Side.BUY,
        notional_dollars=Decimal("100"), order_type=OrderType.MARKET,
        stop_loss_price=Decimal("9"),
    ))
    result = reconcile(journal=j, broker=broker, broker_mode="paper")
    assert result.diffs == []
    assert result.cash_diff == Decimal("0")


def test_reconcile_live_diff_when_rh_has_extra_symbol(tmp_path):
    """Live broker reports an unjournaled position → PositionDiff kind extra_in_broker."""
    from tests.ops.broker.fakes import FakeMCPClient
    from ops import build_guarded_robinhood_broker
    j = Journal(str(tmp_path / "j.sqlite"))
    client = FakeMCPClient(cash=Decimal("500"))
    client.seed_position("NVDA", Decimal("1"), Decimal("500"))
    broker = build_guarded_robinhood_broker(
        config=OpsConfig(broker_mode="robinhood"), journal=j,
        mcp_client=client,
        start_of_day_equity=lambda: Decimal("500"),
        start_of_week_equity=lambda: Decimal("500"),
    )
    result = reconcile(journal=j, broker=broker, broker_mode="robinhood")
    assert len(result.diffs) == 1
    assert result.diffs[0].symbol == "NVDA"
    assert result.diffs[0].kind == "extra_in_broker"


def test_reconcile_live_diff_when_journal_has_extra_symbol(tmp_path):
    """Journal says a position exists that RH doesn't → extra_in_journal."""
    from tests.ops.broker.fakes import FakeMCPClient
    from ops import build_guarded_robinhood_broker
    j = Journal(str(tmp_path / "j.sqlite"))
    ts = datetime(2026, 7, 2, tzinfo=timezone.utc)
    j.record_order(client_order_id="b-1", symbol="AAPL", side="BUY",
                   notional_dollars=Decimal("50"), stop_loss_price=Decimal("9"))
    j.record_fill(order_id="o-1", client_order_id="b-1", symbol="AAPL",
                  side="BUY", quantity=Decimal("5"), price=Decimal("10"),
                  filled_at=ts, stop_loss_price=Decimal("9"))
    client = FakeMCPClient(cash=Decimal("500"))
    broker = build_guarded_robinhood_broker(
        config=OpsConfig(broker_mode="robinhood"), journal=j,
        mcp_client=client,
        start_of_day_equity=lambda: Decimal("500"),
        start_of_week_equity=lambda: Decimal("500"),
    )
    result = reconcile(journal=j, broker=broker, broker_mode="robinhood")
    assert any(d.symbol == "AAPL" and d.kind == "extra_in_journal" for d in result.diffs)


def test_emit_reconcile_events_writes_inconsistency_when_diffs(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    result = ReconcileResult(
        diffs=[PositionDiff(symbol="AAPL", journal_qty=Decimal("5"),
                            broker_qty=Decimal("3"), kind="qty_mismatch")],
        cash_journal=Decimal("100"), cash_broker=Decimal("100"),
        cash_diff=Decimal("0"),
    )
    emit_reconcile_events(j, result)
    events = j.read_events()
    kinds = [e["kind"] for e in events]
    assert "inconsistency" in kinds
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/pytest tests/ops/test_reconcile.py -v`
Expected: FAIL — reconcile module has only a docstring.

- [ ] **Step 3: Implement `ops/reconcile.py`**

```python
"""Startup state reconciliation between journal replay and live broker."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ops.journal import Journal


_EPSILON_QTY = Decimal("1e-6")
_EPSILON_CASH = Decimal("0.01")


@dataclass(frozen=True)
class PositionDiff:
    symbol: str
    journal_qty: Decimal | None
    broker_qty: Decimal | None
    kind: str   # "extra_in_broker" | "extra_in_journal" | "qty_mismatch"


@dataclass(frozen=True)
class ReconcileResult:
    diffs: list[PositionDiff]
    cash_journal: Decimal
    cash_broker: Decimal
    cash_diff: Decimal


def reconcile(*, journal: Journal, broker: Any, broker_mode: str) -> ReconcileResult:
    """Compare journal-replayed state to live broker state.

    Paper: journal is authoritative; the guarded PaperBroker was built
    from the same journal, so the two should agree exactly. Any diff
    indicates a bug we want to catch.

    Live (robinhood): compare per-symbol qty and cash; diffs are
    surfaced as PositionDiff objects for the caller to journal + halt on.
    """
    from ops.broker.paper import PaperBroker

    replay = PaperBroker.from_journal(
        journal=journal,
        quote_source=broker.get_quote,
        starting_cash=Decimal("0"),
    )
    replay_positions = {p.symbol: p.quantity for p in replay.get_positions()}
    broker_positions = {p.symbol: p.quantity for p in broker.get_positions()}

    diffs: list[PositionDiff] = []
    all_symbols = set(replay_positions) | set(broker_positions)
    for symbol in sorted(all_symbols):
        jq = replay_positions.get(symbol)
        bq = broker_positions.get(symbol)
        if jq is None:
            diffs.append(PositionDiff(symbol=symbol, journal_qty=None, broker_qty=bq, kind="extra_in_broker"))
        elif bq is None:
            diffs.append(PositionDiff(symbol=symbol, journal_qty=jq, broker_qty=None, kind="extra_in_journal"))
        elif abs(jq - bq) > _EPSILON_QTY:
            diffs.append(PositionDiff(symbol=symbol, journal_qty=jq, broker_qty=bq, kind="qty_mismatch"))

    cash_journal = replay.get_cash()
    cash_broker = broker.get_cash()
    cash_diff = cash_broker - cash_journal
    return ReconcileResult(
        diffs=diffs,
        cash_journal=cash_journal, cash_broker=cash_broker,
        cash_diff=cash_diff,
    )


def emit_reconcile_events(journal: Journal, result: ReconcileResult) -> None:
    if result.diffs:
        journal.record_event(
            "inconsistency",
            {
                "diffs": [
                    {
                        "symbol": d.symbol,
                        "journal_qty": str(d.journal_qty) if d.journal_qty is not None else None,
                        "broker_qty": str(d.broker_qty) if d.broker_qty is not None else None,
                        "kind": d.kind,
                    }
                    for d in result.diffs
                ],
                "cash_journal": str(result.cash_journal),
                "cash_broker": str(result.cash_broker),
                "cash_diff": str(result.cash_diff),
            },
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/ops/test_reconcile.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest tests/ops/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ops/reconcile.py tests/ops/test_reconcile.py
git commit -m "feat(ops): startup state reconciliation

Compares journal-replayed state against live broker state; emits
PositionDiff objects for the caller to journal + halt on. Paper mode
expected to always match; live mode surfaces any drift so the operator
must reconcile manually before the orchestrator starts trading."
```

---

## Task 8: Guardian kill-switch action — paper close-all, live halt-only

**Files:**
- Modify: `ops/position_guardian.py`
- Modify: `tests/ops/test_position_guardian.py`

**Interfaces:**
- Consumes: `broker.close_position(symbol)` (Plan 3a), `journal.get_latest_equity_snapshot(kind='open_week')`
- Produces: `PositionGuardian.check_stops_once()` gains post-stop kill-switch logic. When weekly drawdown crosses `cfg.weekly_drawdown_pct`, in paper mode: journal `kill_switch`, close every remaining open position; in live mode: journal `kill_switch`, return (orchestrator sees the halt via `has_event_since_last_monday` on next tick).

The `broker_mode` decision requires the guardian to know which mode it's in. Add a `broker_mode: str` param to the guardian constructor (default `"paper"` for backward compatibility with existing tests).

- [ ] **Step 1: Write failing tests**

Add to `tests/ops/test_position_guardian.py`:
```python
def test_kill_switch_paper_mode_closes_all_positions(_broker_with_positions):
    """Paper mode: guardian trips kill_switch and closes every open position."""
    broker, quotes, cfg, journal = _broker_with_positions(
        [("AAPL", Decimal("10"), Decimal("9"), Decimal("50")),
         ("MSFT", Decimal("300"), Decimal("270"), Decimal("100"))],
        weekly_open_equity=Decimal("500"),
    )
    # Force a drop below weekly threshold (-15%): AAPL to $8 makes equity ≈ 425 (~ -15%).
    quotes.set("AAPL", Decimal("7"))
    quotes.set("MSFT", Decimal("250"))
    guardian = PositionGuardian(
        broker=broker, quote_source=quotes.get, config=cfg,
        journal=journal, broker_mode="paper",
    )
    guardian.check_stops_once()
    assert broker.get_positions() == []
    events = journal.read_events()
    assert any(e["kind"] == "kill_switch" for e in events)


def test_kill_switch_live_mode_halts_only(_broker_with_positions):
    """Live mode: guardian trips kill_switch but does NOT close positions."""
    broker, quotes, cfg, journal = _broker_with_positions(
        [("AAPL", Decimal("10"), Decimal("9"), Decimal("50"))],
        weekly_open_equity=Decimal("500"),
    )
    quotes.set("AAPL", Decimal("7"))
    guardian = PositionGuardian(
        broker=broker, quote_source=quotes.get, config=cfg,
        journal=journal, broker_mode="robinhood",
    )
    guardian.check_stops_once()
    # Position remains; kill_switch event was still journaled.
    assert len(broker.get_positions()) == 1
    events = journal.read_events()
    assert any(e["kind"] == "kill_switch" for e in events)


def test_kill_switch_not_tripped_when_within_threshold(_broker_with_positions):
    broker, quotes, cfg, journal = _broker_with_positions(
        [("AAPL", Decimal("10"), Decimal("9"), Decimal("50"))],
        weekly_open_equity=Decimal("500"),
    )
    # Small drop only (-5%): does NOT trip.
    quotes.set("AAPL", Decimal("9.5"))
    guardian = PositionGuardian(
        broker=broker, quote_source=quotes.get, config=cfg,
        journal=journal, broker_mode="paper",
    )
    guardian.check_stops_once()
    events = journal.read_events()
    assert not any(e["kind"] == "kill_switch" for e in events)
```

The `_broker_with_positions` fixture: constructs a guarded paper broker, seeds positions per the argument list `(symbol, entry_price, stop_price, notional)`, and journals an `open_week` snapshot at the specified equity. Add it as a pytest fixture at the top of the test file.

- [ ] **Step 2: Add fixture**

At the top of `tests/ops/test_position_guardian.py`, add:
```python
@pytest.fixture
def _broker_with_positions(tmp_path):
    from ops.journal import Journal
    from ops import build_guarded_paper_broker
    from ops.config import OpsConfig

    class _Q:
        def __init__(self):
            self._m = {}
        def set(self, s, p): self._m[s] = p
        def get(self, s): return self._m[s]

    def _make(positions, weekly_open_equity):
        j = Journal(str(tmp_path / "j.sqlite"))
        quotes = _Q()
        for symbol, entry, _stop, _notional in positions:
            quotes.set(symbol, entry)
        broker = build_guarded_paper_broker(
            config=OpsConfig(), journal=j, quote_source=quotes.get,
            starting_cash=Decimal("500"),
            start_of_day_equity=lambda: weekly_open_equity,
            start_of_week_equity=lambda: weekly_open_equity,
        )
        for symbol, entry, stop, notional in positions:
            broker.place_order(Order(
                client_order_id=f"b-{symbol}", symbol=symbol, side=Side.BUY,
                notional_dollars=notional, order_type=OrderType.MARKET,
                stop_loss_price=stop,
            ))
        from datetime import datetime, timezone
        j.record_equity_snapshot(
            kind="open_week", equity=weekly_open_equity, cash=Decimal("500"),
            at=datetime(2026, 6, 29, 13, 30, tzinfo=timezone.utc),
        )
        return broker, quotes, OpsConfig(), j
    return _make
```

- [ ] **Step 3: Verify failure**

Run: `.venv/bin/pytest tests/ops/test_position_guardian.py -v -k kill_switch`
Expected: FAIL — guardian has no kill-switch logic and no `broker_mode` param.

- [ ] **Step 4: Extend PositionGuardian**

In `ops/position_guardian.py`:
- Add `journal` and `broker_mode` to `__init__` (default `broker_mode="paper"` for existing callers):
```python
def __init__(
    self, *, broker, quote_source, config,
    journal=None, broker_mode: str = "paper",
) -> None:
    self._broker = broker
    self._quote = quote_source
    self._cfg = config
    self._journal = journal if journal is not None else broker.journal
    self._broker_mode = broker_mode
```

The `journal` default falls back to `broker.journal` (available on GuardedBroker) so existing tests that construct the guardian without a journal still work.

- At the end of `check_stops_once`, after the per-position loop, add:
```python
self._maybe_trip_kill_switch()
```

- Implement `_maybe_trip_kill_switch`:
```python
def _maybe_trip_kill_switch(self) -> None:
    snap = self._journal.get_latest_equity_snapshot(kind="open_week")
    if snap is None:
        return
    equity_now = self._broker.get_equity()
    weekly_pct = (equity_now - snap.equity) / snap.equity
    if weekly_pct > self._cfg.weekly_drawdown_pct:
        return
    # Idempotency: don't fire twice.
    if self._journal.has_event_since_last_monday("kill_switch"):
        return
    self._journal.record_event(
        "kill_switch",
        {
            "mode": self._broker_mode,
            "equity_now": str(equity_now),
            "equity_open_week": str(snap.equity),
            "pct": str(weekly_pct),
            "threshold": str(self._cfg.weekly_drawdown_pct),
        },
    )
    if self._broker_mode == "paper":
        for pos in list(self._broker.get_positions()):
            try:
                self._broker.close_position(pos.symbol)
            except Exception as exc:
                self._journal.record_event(
                    "kill_switch_close_failed",
                    {"symbol": pos.symbol, "error": f"{type(exc).__name__}: {exc}"},
                )
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/ops/test_position_guardian.py -v`
Expected: PASS.

- [ ] **Step 6: Run full suite**

Run: `.venv/bin/pytest tests/ops/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ops/position_guardian.py tests/ops/test_position_guardian.py
git commit -m "feat(ops/position_guardian): weekly kill-switch action per broker mode

After each stop-check pass, if weekly drawdown crossed the config
threshold, journal kill_switch and — in paper mode — close every open
position. In live mode: halt only, human handles positions. Idempotent
via has_event_since_last_monday check."
```

---

## Task 9: ops/main.py — service entrypoint + SIGINT/SIGTERM handling

**Files:**
- Modify: `ops/main.py`
- Modify: `tests/ops/test_main.py`

**Interfaces:**
- Consumes: `Orchestrator`, `PositionGuardian`, `MarketCalendar`, `reconcile.reconcile`, `emit_reconcile_events`, `Journal`, factories.
- Produces:
  - `run() -> int` — build service, run reconciler, start scheduler, wait on shutdown event, return exit code (0 clean, 2 reconciliation-halt).
  - Helpers used by tests: `_build_broker`, `_wire`, `_shutdown_handler`, `_start_full_scheduler`, `_start_guardian_only`.

Because APScheduler runs jobs on real threads that we do NOT want to fire in unit tests, the `run()` helpers must be testable individually. The full end-to-end lives in Task 11.

- [ ] **Step 1: Write failing tests**

`tests/ops/test_main.py`:
```python
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from ops.main import _build_broker, _wire, _emit_halt_events
from ops.reconcile import ReconcileResult, PositionDiff
from ops.config import OpsConfig
from ops.journal import Journal


def test_build_broker_paper(tmp_path):
    cfg = OpsConfig()  # broker_mode default "paper"
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _build_broker(cfg, j)
    # We don't assert internal type — just that place_order is callable
    assert callable(broker.place_order)


def test_build_broker_robinhood(monkeypatch, tmp_path):
    cfg = OpsConfig(broker_mode="robinhood")
    j = Journal(str(tmp_path / "j.sqlite"))
    # Stub RealRobinhoodMCPClient so no OAuth flow triggers.
    from tests.ops.broker.fakes import FakeMCPClient
    monkeypatch.setattr(
        "ops.broker.mcp_client.RealRobinhoodMCPClient",
        lambda: FakeMCPClient(),
    )
    broker = _build_broker(cfg, j)
    assert callable(broker.place_order)


def test_wire_returns_orchestrator_guardian_calendar(tmp_path):
    cfg = OpsConfig()
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _build_broker(cfg, j)
    orch, guardian, cal = _wire(broker, j, cfg)
    assert callable(orch.tick)
    assert callable(guardian.check_stops_once)
    assert callable(cal.is_open_now)


def test_emit_halt_events_writes_inconsistency_and_startup_halted(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    result = ReconcileResult(
        diffs=[PositionDiff(symbol="AAPL", journal_qty=Decimal("5"),
                            broker_qty=Decimal("3"), kind="qty_mismatch")],
        cash_journal=Decimal("100"), cash_broker=Decimal("100"),
        cash_diff=Decimal("0"),
    )
    _emit_halt_events(j, result)
    kinds = [e["kind"] for e in j.read_events()]
    assert "inconsistency" in kinds
    assert "startup_halted" in kinds
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/pytest tests/ops/test_main.py -v`
Expected: FAIL — helpers don't exist.

- [ ] **Step 3: Implement `ops/main.py`**

```python
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
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ops import build_guarded_paper_broker, build_guarded_robinhood_broker
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

    Paper starts with a fixed $250 (matches the user's account posture);
    the orchestrator does not need starting_cash for RH because live cash
    comes from the MCP.
    """
    # A quote source is required for the paper factory; the orchestrator
    # gets the same source via the guarded broker.
    from ops.quotes import build_yfinance_quote_source
    quote_source = build_yfinance_quote_source()

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
    return build_guarded_paper_broker(
        config=config, journal=journal,
        quote_source=quote_source,
        starting_cash=Decimal("250"),
        start_of_day_equity=_sod, start_of_week_equity=_sow,
    )


def _wire(broker, journal: Journal, config: OpsConfig):
    """Wire the orchestrator + guardian + calendar for the given broker+config."""
    from ops.universe import build_universe
    from ops.strategy.post_earnings_momentum import PostEarningsMomentumStrategy
    from ops.pipeline_adapter import TradingAgentsPipelineAdapter

    calendar = MarketCalendar()
    orchestrator = Orchestrator(
        broker=broker,
        universe_builder=_UniverseBuilder(),
        strategy=PostEarningsMomentumStrategy(),
        pipeline_adapter=TradingAgentsPipelineAdapter(),
        calendar=calendar, journal=journal, config=config,
    )
    guardian = PositionGuardian(
        broker=broker, quote_source=broker.get_quote, config=config,
        journal=journal, broker_mode=config.broker_mode,
    )
    return orchestrator, guardian, calendar


class _UniverseBuilder:
    """Adapter — wraps ops.universe.build_universe() into the .build() interface Orchestrator expects."""
    def build(self):
        from ops.universe import build_universe
        return build_universe()


def _emit_halt_events(journal: Journal, result: ReconcileResult) -> None:
    emit_reconcile_events(journal, result)
    journal.record_event("startup_halted", {"reason": "reconciliation"})


def _start_full_scheduler(orchestrator: Orchestrator, guardian: PositionGuardian,
                          calendar: MarketCalendar) -> BackgroundScheduler:
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


def _start_guardian_only(guardian: PositionGuardian, calendar: MarketCalendar) -> BackgroundScheduler:
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
        if result.diffs:
            _emit_halt_events(journal, result)
            print(
                f"Reconciliation halted orchestrator — {len(result.diffs)} diff(s). "
                "Guardian continues. Investigate journal 'inconsistency' events.",
                file=sys.stderr,
            )
            sched = _start_guardian_only(guardian, calendar)
            _run_until_signal()
            sched.shutdown(wait=True)
            return 2
        sched = _start_full_scheduler(orchestrator, guardian, calendar)
        _run_until_signal()
        sched.shutdown(wait=True)
        return 0
    finally:
        journal.close()
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/ops/test_main.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest tests/ops/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ops/main.py tests/ops/test_main.py
git commit -m "feat(ops/main): service entrypoint + graceful shutdown

_build_broker branches on config.broker_mode; _wire assembles the
Orchestrator + PositionGuardian + MarketCalendar; run() invokes the
reconciler and either starts both scheduler jobs (clean startup) or
guardian-only (halted) and waits on SIGINT/SIGTERM. Exit codes 0 vs 2
distinguish clean vs halted shutdowns."
```

---

## Task 10: `ops run` CLI subcommand

**Files:**
- Modify: `ops/cli.py`
- Modify: `tests/ops/test_cli_run.py`

**Interfaces:**
- Consumes: `ops.main.run` (Task 9)
- Produces: `ops run` click command that calls `sys.exit(ops.main.run())`.

- [ ] **Step 1: Write failing test**

`tests/ops/test_cli_run.py`:
```python
from unittest.mock import patch
from click.testing import CliRunner
from ops.cli import cli


def test_ops_run_invokes_main_run():
    runner = CliRunner()
    with patch("ops.main.run", return_value=0) as m:
        result = runner.invoke(cli, ["run"])
    assert result.exit_code == 0
    m.assert_called_once()


def test_ops_run_propagates_exit_code_2():
    runner = CliRunner()
    with patch("ops.main.run", return_value=2):
        result = runner.invoke(cli, ["run"])
    assert result.exit_code == 2
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/pytest tests/ops/test_cli_run.py -v`
Expected: FAIL — `ops run` subcommand missing.

- [ ] **Step 3: Add the subcommand**

In `ops/cli.py`, add:
```python
@cli.command()
def run():
    """Start the always-on orchestrator + guardian service."""
    import sys
    from ops.main import run as _run
    sys.exit(_run())
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/ops/test_cli_run.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest tests/ops/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ops/cli.py tests/ops/test_cli_run.py
git commit -m "feat(ops/cli): ops run subcommand starts the orchestrator service"
```

---

## Task 11: End-to-end integration test

**Files:**
- Modify: `tests/ops/test_integration_orchestrator.py`

**Interfaces:**
- Consumes: everything from Tasks 1-10.
- Produces: an integration test that runs one full orchestrator tick + one guardian pass in paper mode, using stubbed universe / strategy / pipeline so no LLM cost and no network hit; asserts events land in the journal and a BUY-then-stop lifecycle survives a restart via `PaperBroker.from_journal`.

- [ ] **Step 1: Write the integration test**

`tests/ops/test_integration_orchestrator.py`:
```python
"""End-to-end: BUY through orchestrator → simulated price drop → stop fires via guardian → close_position.
Verifies stop_loss_price persists across a simulated restart (from_journal rehydrate)."""
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from ops import build_guarded_paper_broker
from ops.broker.types import Order, OrderType, Side
from ops.config import OpsConfig
from ops.journal import Journal
from ops.position_guardian import PositionGuardian
from ops.scheduler.orchestrator import Orchestrator


class _Q:
    def __init__(self): self._m = {}
    def set(self, s, p): self._m[s] = p
    def get(self, s): return self._m[s]


def test_end_to_end_orchestrator_buy_then_guardian_stop_survives_restart(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    quotes = _Q()
    quotes.set("AAPL", Decimal("10"))
    broker = build_guarded_paper_broker(
        config=OpsConfig(), journal=j,
        quote_source=quotes.get, starting_cash=Decimal("250"),
        start_of_day_equity=lambda: Decimal("250"),
        start_of_week_equity=lambda: Decimal("250"),
    )
    calendar = MagicMock()
    calendar.is_open_now.return_value = True
    strategy = MagicMock()
    strategy.rank.return_value = [MagicMock(symbol="AAPL")]
    strategy.build_order.return_value = Order(
        client_order_id="b-AAPL", symbol="AAPL", side=Side.BUY,
        notional_dollars=Decimal("20"), order_type=OrderType.MARKET,
        stop_loss_price=Decimal("9.5"),
    )
    universe = MagicMock()
    universe.build.return_value = {"AAPL"}
    pipeline = MagicMock()
    decision = MagicMock(); decision.action = "BUY"
    pipeline.propagate.return_value = decision
    orch = Orchestrator(
        broker=broker, universe_builder=universe, strategy=strategy,
        pipeline_adapter=pipeline, calendar=calendar, journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].stop_loss_price == Decimal("9.5")

    # Simulate restart — rebuild broker from journal, guardian sees rehydrated stop.
    from ops.broker.paper import PaperBroker
    replayed_inner = PaperBroker.from_journal(
        journal=j, quote_source=quotes.get, starting_cash=Decimal("250"),
    )
    assert replayed_inner.get_positions()[0].stop_loss_price == Decimal("9.5")

    # Drop the quote below the persisted absolute stop and run the guardian.
    quotes.set("AAPL", Decimal("9.4"))
    guardian = PositionGuardian(
        broker=broker, quote_source=quotes.get, config=OpsConfig(),
        journal=j, broker_mode="paper",
    )
    guardian.check_stops_once()

    events = j.read_events()
    kinds = [e["kind"] for e in events]
    assert "stop_hit" in kinds
    stop_hit = [e for e in events if e["kind"] == "stop_hit"][0]
    assert stop_hit["payload"]["mode"] == "absolute"
    assert broker.get_positions() == []
```

- [ ] **Step 2: Run the test**

Run: `.venv/bin/pytest tests/ops/test_integration_orchestrator.py -v`
Expected: PASS.

- [ ] **Step 3: Run full suite**

Run: `.venv/bin/pytest tests/ops/ -q`
Expected: passes; total ~225-245.

- [ ] **Step 4: Commit**

```bash
git add tests/ops/test_integration_orchestrator.py
git commit -m "test(ops): end-to-end integration — orchestrator BUY, restart, guardian stop"
```

---

## Task 12: `ops/README.md` — running the service

**Files:**
- Modify: `ops/README.md`

- [ ] **Step 1: Append new section**

At the end of `ops/README.md`, add:
```markdown
## Running the orchestrator service

The `ops run` command starts the always-on orchestrator + guardian in the
foreground. Keep it in a terminal, tmux, or a persistent shell of your
choice — SIGINT (Ctrl-C) triggers a graceful shutdown that drains
in-flight jobs and closes the journal cleanly.

```bash
# Paper mode (default): safe to run anytime.
.venv/bin/python -m ops.cli run

# Live mode: opt-in via env var.
OPS_BROKER_MODE=robinhood .venv/bin/python -m ops.cli run
```

### Startup behavior

1. Load config from `OPS_*` env vars and the built-in defaults.
2. Open the journal at `$OPS_JOURNAL_PATH` (default `ops_journal.sqlite`).
3. Build the guarded broker for the configured mode.
4. **Reconciliation gate:** compare journal-replayed state against the live
   broker's positions and cash.
   - No diffs → normal startup. Orchestrator ticks every 30 minutes during
     NYSE market hours; guardian polls every 60 seconds.
   - Diffs found → journal `inconsistency` + `startup_halted` events;
     orchestrator job does NOT start; guardian keeps running so existing
     stops are enforced. Exit code 2 on clean shutdown so a shell wrapper
     can distinguish this from a normal exit.

### Halt semantics

- **Daily drawdown** ≤ config threshold (default −7%): guardian records
  `daily_halt`; orchestrator no-ops for the rest of the day.
- **Weekly drawdown** ≤ config threshold (default −15%): guardian records
  `kill_switch`. Paper mode: guardian auto-closes all positions. Live mode:
  guardian halts only; user handles positions manually.

### Where things get logged

Journal (`sqlite3 ops_journal.sqlite`) — the events, orders, fills, and
equity snapshots tables carry the full audit trail. Notifications (push +
email) arrive in Plan 3c; until then, `sqlite3` queries against the journal
are the way to inspect state.
```

- [ ] **Step 2: Commit**

```bash
git add ops/README.md
git commit -m "docs(ops): document ops run service, startup gate, halt semantics"
```

---

## Task 13: Push branch + open PR

- [ ] **Step 1: Verify clean state**

Run:
```bash
git status
.venv/bin/pytest tests/ops/ -q
```
Expected: clean working tree; ~225-245 passing.

- [ ] **Step 2: Push**

```bash
git push -u origin feat/ops-orchestrator
```

- [ ] **Step 3: Open the PR**

```bash
gh pr create --repo CWFred/TradingAgents --title "feat(ops): plan 3b — always-on orchestrator" --body "$(cat <<'EOF'
Plan 3b of the live-v1 spec. Design doc:
`docs/superpowers/specs/2026-07-02-tradingagents-live-v1-plan-3b-orchestrator-design.md`.

## Highlights

- `ops run` command: APScheduler-driven service with an orchestrator tick every 30 minutes during NYSE market hours and a guardian poll every 60 seconds. Foreground process; SIGINT/SIGTERM triggers graceful shutdown.
- **Per-position stops persist across restart.** `fills.stop_loss_price` column added with migration; `PaperBroker._fill_buy` writes it and `from_journal` rehydrates; `RobinhoodBroker.get_positions()` looks up the most recent journaled BUY per symbol. Closes the state-recovery gap flagged in the Plan 3a review.
- **Startup reconciliation.** Replays journal, compares against live broker positions + cash. Diffs → `inconsistency` + `startup_halted` events, orchestrator does NOT start, guardian still runs so existing stops are enforced. Exit code 2 distinguishes halted-startup from clean shutdown.
- **Equity snapshots wired.** Orchestrator writes `open_day` at first tick per trading day, `open_week` at first tick per trading week. Drawdown rules — previously effective no-ops — are now functional.
- **Kill-switch action.** Paper mode: guardian trips and auto-closes all positions. Live mode: guardian trips and halts only; user handles positions manually.
- **`TradingAgentsPipelineAdapter._ensure_graph` is now thread-safe** via a double-checked lock.
- `MarketCalendar` adapter over `pandas_market_calendars.get_calendar('NYSE')` for open/close/holidays.

## Not in this PR

- Notifications (Pushover / SMTP) — Plan 3c consumes the events this plan emits.
- `LIVE_MAX_POSITION` first-N-fills cap — Plan 3c.
- Streamlit dashboard — v2.
- launchd integration — user runs `ops run` in a terminal/tmux.

## Test plan
- [x] `.venv/bin/pytest tests/ops/` — target ~225-245 passing (was 192 on main).

## Environment
- Model: Claude Opus 4.7
- Harness: Claude Code + superpowers plugin

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Note the PR URL** — capture for the ledger.
