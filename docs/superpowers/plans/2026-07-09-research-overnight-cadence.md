# Research Overnight Cadence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the research sleeve from weekly/name-budgeted to an every-3-days screen + nightly 00:00–08:00 deadline-boxed drain, consolidated into the always-on `ops run` service, with a 7-day per-symbol "since last screened" TTL and a `research kick` one-shot.

**Architecture:** A new pure `drain_pending()` function (deadline- and shutdown-aware) is shared by the `research run` CLI and a new `_research_overnight_tick` job in the `ops run` scheduler. The nightly tick screens only if it's been ≥3 days, then drains the whole pending queue on ds4 until the queue empties, an 08:00 deadline passes, or shutdown is requested. `ScreenStore` gains a TTL skip so the deterministic 3-day screen never re-queues a name screened this week. The two Saturday launchd plists are retired.

**Tech Stack:** Python 3.11, stdlib `sqlite3`, APScheduler `CronTrigger`, Click CLI, pytest. Local ds4 model via the managed backend (`ops/llm_backend.py`).

## Global Constraints

- All money in `Decimal`; timestamps ISO-8601 UTC TEXT (repo store convention).
- Scheduler timezone is `America/New_York`; the drain deadline is local 08:00.
- ds4 is a single ~86 GB resource — no LLM job may overlap another. The overnight drain runs 00:00–08:00 (market closed); momentum ticks run 09:30–16:00. Never bleed one into the other.
- Every scheduler tick wrapper catches broadly and records an error event instead of raising (raising kills the APScheduler job). Mirror the existing `_research_monitor_tick` / `_research_trade_tick`.
- Heavy imports live inside function/command bodies (repo convention). Tests patch the source module, not `ops.cli`.
- `drain_pending()` is pure of backend lifecycle — the caller owns `ensure_up()`/`shutdown()`.
- TDD: failing test first, minimal implementation, frequent commits. Run tests with `python -m pytest`.

---

### Task 1: Config — cadence + TTL fields

**Files:**
- Modify: `ops/config.py` (dataclass fields ~line 91, `__post_init__` validation ~line 158, `load_config` env parsing ~line 219)
- Test: `tests/ops/test_config.py`

**Interfaces:**
- Produces: `OpsConfig.research_screen_interval_days: int` (default 3), `OpsConfig.research_drain_deadline_hour: int` (default 8), `OpsConfig.research_screen_ttl_days: int` (default 7). Env: `OPS_RESEARCH_SCREEN_INTERVAL_DAYS`, `OPS_RESEARCH_DRAIN_DEADLINE_HOUR`, `OPS_RESEARCH_SCREEN_TTL_DAYS`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/ops/test_config.py`:

```python
def test_research_cadence_defaults():
    from ops.config import OpsConfig
    cfg = OpsConfig()
    assert cfg.research_screen_interval_days == 3
    assert cfg.research_drain_deadline_hour == 8
    assert cfg.research_screen_ttl_days == 7


def test_research_cadence_env_overrides(monkeypatch):
    from ops.config import load_config
    monkeypatch.setenv("OPS_RESEARCH_SCREEN_INTERVAL_DAYS", "2")
    monkeypatch.setenv("OPS_RESEARCH_DRAIN_DEADLINE_HOUR", "7")
    monkeypatch.setenv("OPS_RESEARCH_SCREEN_TTL_DAYS", "5")
    cfg = load_config()
    assert cfg.research_screen_interval_days == 2
    assert cfg.research_drain_deadline_hour == 7
    assert cfg.research_screen_ttl_days == 5


def test_research_cadence_validation():
    import pytest
    from ops.config import OpsConfig
    with pytest.raises(ValueError):
        OpsConfig(research_screen_interval_days=0)
    with pytest.raises(ValueError):
        OpsConfig(research_screen_ttl_days=0)
    with pytest.raises(ValueError):
        OpsConfig(research_drain_deadline_hour=24)
    with pytest.raises(ValueError):
        OpsConfig(research_drain_deadline_hour=-1)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/ops/test_config.py -k research_cadence -v`
Expected: FAIL (`TypeError: unexpected keyword argument` / `AttributeError`).

- [ ] **Step 3: Add the fields**

In `ops/config.py`, after `research_thesis_model: str = _DEFAULT_RESEARCH_MODEL` (line 92):

```python
    research_screen_interval_days: int = 3
    research_drain_deadline_hour: int = 8   # local America/New_York
    research_screen_ttl_days: int = 7        # skip symbols screened within this window
```

- [ ] **Step 4: Add validation**

In `__post_init__`, after the `research_starting_cash` check (line ~160):

```python
        for fname in ("research_screen_interval_days", "research_screen_ttl_days"):
            val = getattr(self, fname)
            if val <= 0:
                raise ValueError(f"{fname} must be > 0, got {val}")
        if not (0 <= self.research_drain_deadline_hour <= 23):
            raise ValueError(
                "research_drain_deadline_hour must be in 0..23, got "
                f"{self.research_drain_deadline_hour}"
            )
```

- [ ] **Step 5: Add env parsing**

In `load_config`, alongside the other `_env_int` blocks (after `max_open_positions`, ~line 205):

```python
    screen_interval = _env_int("OPS_RESEARCH_SCREEN_INTERVAL_DAYS")
    if screen_interval is not None:
        kwargs["research_screen_interval_days"] = screen_interval

    drain_deadline_hour = _env_int("OPS_RESEARCH_DRAIN_DEADLINE_HOUR")
    if drain_deadline_hour is not None:
        kwargs["research_drain_deadline_hour"] = drain_deadline_hour

    screen_ttl = _env_int("OPS_RESEARCH_SCREEN_TTL_DAYS")
    if screen_ttl is not None:
        kwargs["research_screen_ttl_days"] = screen_ttl
```

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest tests/ops/test_config.py -k research_cadence -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add ops/config.py tests/ops/test_config.py
git commit -m "feat(config): research cadence + screen-TTL knobs"
```

---

### Task 2: Drain-run journal events

**Files:**
- Modify: `ops/events.py` (kind constants after `KIND_RESEARCH_TRADE_ERROR` ~line 114; `AUDIT_ONLY` frozenset ~line 135, near the "Research monitoring events" comment; payload builders after `research_trade_error_payload` ~line 636; `BUILDERS` map ~line 693)
- Test: create `tests/ops/test_events_research_drain.py`

**Interfaces:**
- Produces: `KIND_RESEARCH_DRAIN_RUN = "research_drain_run"`, `KIND_RESEARCH_DRAIN_ERROR = "research_drain_error"`; `research_drain_run_payload(*, asof: str, screened_this_run: bool, researched: int, failed: int, still_pending: int, hit_deadline: bool) -> dict`; `research_drain_error_payload(*, error: str) -> dict`. Both kinds registered in `events.BUILDERS` **and** `events.AUDIT_ONLY` (audit breadcrumbs, not notified — like `KIND_RESEARCH_MONITOR_RUN`).

**Critical:** `tests/ops/notify/test_policy.py` asserts every kind in `BUILDERS` is also in `POLICY` or `AUDIT_ONLY`. Adding to `BUILDERS` without also adding to `AUDIT_ONLY` breaks that partition test. Add to both.

- [ ] **Step 1: Write the failing test**

Create `tests/ops/test_events_research_drain.py`:

```python
from ops import events


def test_research_drain_payloads_round_trip():
    run = events.research_drain_run_payload(
        asof="2026-07-09", screened_this_run=True, researched=4,
        failed=1, still_pending=2, hit_deadline=False,
    )
    assert run == {
        "asof": "2026-07-09", "screened_this_run": True, "researched": 4,
        "failed": 1, "still_pending": 2, "hit_deadline": False,
    }
    err = events.research_drain_error_payload(error="RuntimeError: boom")
    assert err == {"error": "RuntimeError: boom"}


def test_research_drain_kinds_registered_and_audit_only():
    for kind in (events.KIND_RESEARCH_DRAIN_RUN, events.KIND_RESEARCH_DRAIN_ERROR):
        assert kind in events.BUILDERS
        assert kind in events.AUDIT_ONLY
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ops/test_events_research_drain.py -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Add kinds, AUDIT_ONLY entries, payloads, BUILDERS entries**

In `ops/events.py`, after `KIND_RESEARCH_TRADE_ERROR` (line 114):

```python
KIND_RESEARCH_DRAIN_RUN = "research_drain_run"
KIND_RESEARCH_DRAIN_ERROR = "research_drain_error"
```

In the `AUDIT_ONLY` frozenset, alongside the existing research monitor/trade audit kinds (near the "Research monitoring events" comment, ~line 167):

```python
    KIND_RESEARCH_DRAIN_RUN,
    KIND_RESEARCH_DRAIN_ERROR,
```

After `research_trade_error_payload` (~line 636):

```python
def research_drain_run_payload(
    *, asof: str, screened_this_run: bool, researched: int, failed: int,
    still_pending: int, hit_deadline: bool,
) -> dict[str, Any]:
    """Overnight research drain summary: whether a screen refilled the queue
    this run, how the drain resolved, and whether it stopped on the deadline
    (rather than emptying the queue)."""
    return {
        "asof": asof, "screened_this_run": screened_this_run,
        "researched": researched, "failed": failed,
        "still_pending": still_pending, "hit_deadline": hit_deadline,
    }


def research_drain_error_payload(*, error: str) -> dict[str, Any]:
    """Overnight drain aborted (screen or backend failure)."""
    return {"error": error}
```

Register both in the `BUILDERS` map (~line 693):

```python
    KIND_RESEARCH_DRAIN_RUN: research_drain_run_payload,
    KIND_RESEARCH_DRAIN_ERROR: research_drain_error_payload,
```

- [ ] **Step 4: Run to verify pass (including the policy partition test)**

Run: `python -m pytest tests/ops/test_events_research_drain.py tests/ops/notify/test_policy.py -v`
Expected: PASS (drain tests green AND the BUILDERS/AUDIT_ONLY partition test still green).

- [ ] **Step 5: Commit**

```bash
git add ops/events.py tests/ops/test_events_research_drain.py
git commit -m "feat(events): research drain run/error events (audit-only)"
```

---

### Task 3: ScreenStore — "since last screened" TTL

**Files:**
- Modify: `ops/research/store.py` (`_SCHEMA` ~line 40, `record_run` ~line 69, `enqueue_hit` ~line 100)
- Modify: `ops/research/run.py` (the `store.record_run(...)` call ~line 165 — thread the config TTL through)
- Test: `tests/ops/research/test_store.py`, and re-run `tests/ops/research/test_run.py`

**Interfaces:**
- Consumes: `OpsConfig.research_screen_ttl_days` (Task 1).
- Produces: `ScreenStore.record_run(..., ttl_days: int = 0)` and `ScreenStore.enqueue_hit(..., ttl_days: int = 0)` — when `ttl_days > 0`, a symbol with any existing `screen_hit` whose `created_at` is within `ttl_days` is skipped (any status). `ttl_days=0` disables the TTL (existing behavior). Adds index `idx_hits_symbol_created` on `screen_hits(symbol, created_at)`. `run_screen` passes `config.research_screen_ttl_days` so the actual screen path enforces the TTL.

**Note:** the monitor's `enqueue_hit` call (`ops/research/monitor.py:134`) is deliberately left at the `ttl_days=0` default — a monitor escalation means "the thesis broke, re-research this now," which the TTL must not suppress.

- [ ] **Step 1: Write the failing test**

Add to `tests/ops/research/test_store.py`:

```python
def test_record_run_skips_recently_screened(tmp_path):
    import sqlite3
    from datetime import date, datetime, timedelta, timezone
    from ops.research.screener import Bar, ScreenResult
    from ops.research.store import ScreenStore

    def result(sym):
        return ScreenResult(
            symbol=sym, asof=date(2026, 7, 9), passed=True, cheap=True,
            quality=True, valuation_bars=(Bar("v", True, "ok"),),
            quality_bars=(Bar("q", True, "ok"),), triggers=(),
            market_cap=Decimal("4e8"), ev_ebit=Decimal("6"),
        )

    store = ScreenStore(tmp_path / "s.sqlite")
    store.record_run(asof=date(2026, 7, 2), universe_size=1,
                     results=[result("AAA")], ttl_days=7)
    # Backdate AAA's hit to 3 days ago (inside the 7-day window).
    with sqlite3.connect(tmp_path / "s.sqlite") as conn:
        three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        conn.execute("UPDATE screen_hits SET status='researched', created_at=? "
                     "WHERE symbol='AAA'", (three_days_ago,))
    # A fresh screen 7-day TTL must NOT re-queue AAA.
    store.record_run(asof=date(2026, 7, 9), universe_size=1,
                     results=[result("AAA")], ttl_days=7)
    assert [h["symbol"] for h in store.pending_hits()] == []


def test_record_run_requeues_after_ttl_window(tmp_path):
    import sqlite3
    from datetime import date, datetime, timedelta, timezone
    from ops.research.screener import Bar, ScreenResult
    from ops.research.store import ScreenStore

    def result(sym):
        return ScreenResult(
            symbol=sym, asof=date(2026, 7, 9), passed=True, cheap=True,
            quality=True, valuation_bars=(Bar("v", True, "ok"),),
            quality_bars=(Bar("q", True, "ok"),), triggers=(),
            market_cap=Decimal("4e8"), ev_ebit=Decimal("6"),
        )

    store = ScreenStore(tmp_path / "s.sqlite")
    store.record_run(asof=date(2026, 6, 1), universe_size=1,
                     results=[result("BBB")], ttl_days=7)
    with sqlite3.connect(tmp_path / "s.sqlite") as conn:
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        conn.execute("UPDATE screen_hits SET status='researched', created_at=? "
                     "WHERE symbol='BBB'", (old,))
    store.record_run(asof=date(2026, 7, 9), universe_size=1,
                     results=[result("BBB")], ttl_days=7)
    assert [h["symbol"] for h in store.pending_hits()] == ["BBB"]


def test_ttl_zero_disables_skip(tmp_path):
    from datetime import date
    from ops.research.screener import Bar, ScreenResult
    from ops.research.store import ScreenStore

    def result(sym):
        return ScreenResult(
            symbol=sym, asof=date(2026, 7, 9), passed=True, cheap=True,
            quality=True, valuation_bars=(Bar("v", True, "ok"),),
            quality_bars=(Bar("q", True, "ok"),), triggers=(),
            market_cap=Decimal("4e8"), ev_ebit=Decimal("6"),
        )

    store = ScreenStore(tmp_path / "s.sqlite")
    store.record_run(asof=date(2026, 7, 9), universe_size=1,
                     results=[result("CCC")], ttl_days=0)
    # Mark researched so the pending-dedup doesn't mask the TTL-disabled path.
    hid = store.pending_hits()[0]["id"]
    store.mark_researched(hid)
    store.record_run(asof=date(2026, 7, 9), universe_size=1,
                     results=[result("CCC")], ttl_days=0)
    assert [h["symbol"] for h in store.pending_hits()] == ["CCC"]
```

Add `from decimal import Decimal` at the top of the test file if not already present.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ops/research/test_store.py -k "ttl or recently_screened or requeues" -v`
Expected: FAIL (`record_run() got an unexpected keyword argument 'ttl_days'`).

- [ ] **Step 3: Add the schema index**

In `_SCHEMA` (after the `idx_hits_status` line):

```python
CREATE INDEX IF NOT EXISTS idx_hits_symbol_created ON screen_hits(symbol, created_at);
```

- [ ] **Step 4: Add the TTL helper + wire into record_run/enqueue_hit**

Add to `ScreenStore` (after `_connect`), plus `from datetime import timedelta` to the module imports (it already imports `date, datetime, timezone`):

```python
    def _screened_within(self, conn, symbol: str, ttl_days: int) -> bool:
        """True if any screen_hit for this symbol (any status) is newer than
        ttl_days. ttl_days <= 0 disables the check."""
        if ttl_days <= 0:
            return False
        cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
        row = conn.execute(
            "SELECT 1 FROM screen_hits WHERE symbol = ? AND created_at >= ? LIMIT 1",
            (symbol, cutoff),
        ).fetchone()
        return row is not None
```

In `record_run`, change the signature to accept `ttl_days: int = 0` and, inside the `for result in passed:` loop, replace the `already_pending` guard with a combined guard:

```python
            for result in passed:
                already_pending = conn.execute(
                    "SELECT 1 FROM screen_hits WHERE symbol = ? AND status = 'pending' LIMIT 1",
                    (result.symbol,),
                ).fetchone()
                if already_pending or self._screened_within(conn, result.symbol, ttl_days):
                    continue
```

In `enqueue_hit`, add `ttl_days: int = 0` to the signature and extend its pending guard:

```python
            if pending or self._screened_within(conn, symbol, ttl_days):
                return None
```

- [ ] **Step 5: Thread the TTL through `run_screen`**

In `ops/research/run.py`, at the `store.record_run(...)` call (~line 165), add the TTL argument:

```python
        run_id = store.record_run(
            asof=asof, universe_size=len(universe), results=results,
            coverage=coverage, ttl_days=config.research_screen_ttl_days,
        )
```

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest tests/ops/research/test_store.py tests/ops/research/test_run.py -v`
Expected: PASS (new store tests + existing store/run tests green — `ttl_days=0` default preserves prior behavior; `run_screen` tests screen each symbol once so the 7-day TTL doesn't change their outcomes).

- [ ] **Step 7: Commit**

```bash
git add ops/research/store.py ops/research/run.py tests/ops/research/test_store.py
git commit -m "feat(research): since-last-screened TTL in ScreenStore, wired through run_screen"
```

---

### Task 4: `drain_pending` — deadline/shutdown-boxed drain

**Files:**
- Create: `ops/research/drain.py`
- Test: `tests/ops/research/test_drain.py`

**Interfaces:**
- Consumes: `ScreenStore.pending_hits()`, `ScreenStore.mark_researched(id)`, `ScreenStore.mark_failed(id)`; `ops.research.brain.research_hit(hit, *, evidence_llm, thesis_llm, memo_store, thesis_model_spec) -> ResearchOutcome`; `ops.research.brain.ResearchError`.
- Produces: `DrainSummary(researched: int, failed: int, still_pending: int, hit_deadline: bool)`; `drain_pending(*, store, memo_store, evidence_llm, thesis_llm, thesis_model_spec, max_names=None, deadline=None, should_stop=None, now=<utcnow>, echo=<noop>) -> DrainSummary`.

- [ ] **Step 1: Write the failing test**

Create `tests/ops/research/test_drain.py`:

```python
"""Unit tests for the deadline/shutdown-boxed research drain."""
from datetime import datetime, timezone

import pytest

from ops.research.brain import ResearchError, ResearchOutcome
from ops.research.drain import DrainSummary, drain_pending

pytestmark = pytest.mark.unit


class FakeStore:
    def __init__(self, symbols):
        self._hits = [{"id": i, "symbol": s} for i, s in enumerate(symbols, 1)]
        self.researched, self.failed = [], []

    def pending_hits(self):
        done = set(self.researched) | set(self.failed)
        return [h for h in self._hits if h["id"] not in done]

    def mark_researched(self, hid):
        self.researched.append(hid)

    def mark_failed(self, hid):
        self.failed.append(hid)


def _outcome(hit, status):
    return ResearchOutcome(symbol=hit["symbol"], hit_id=hit["id"], status=status)


def test_drains_whole_queue(monkeypatch):
    store = FakeStore(["AAA", "BBB", "CCC"])
    monkeypatch.setattr(
        "ops.research.drain.research_hit",
        lambda hit, **kw: _outcome(hit, "researched"),
    )
    summary = drain_pending(
        store=store, memo_store=object(), evidence_llm=None, thesis_llm=None,
        thesis_model_spec="spec",
    )
    assert summary == DrainSummary(researched=3, failed=0, still_pending=0,
                                   hit_deadline=False)
    assert store.researched == [1, 2, 3]


def test_deadline_stops_between_names(monkeypatch):
    store = FakeStore(["AAA", "BBB", "CCC"])
    calls = {"n": 0}

    def fake_hit(hit, **kw):
        calls["n"] += 1
        return _outcome(hit, "researched")

    monkeypatch.setattr("ops.research.drain.research_hit", fake_hit)
    base = datetime(2026, 7, 9, 6, tzinfo=timezone.utc)
    deadline = datetime(2026, 7, 9, 8, tzinfo=timezone.utc)
    # now() returns 06:00 for the first check, 09:00 (past deadline) after.
    times = iter([base, base, datetime(2026, 7, 9, 9, tzinfo=timezone.utc)])
    summary = drain_pending(
        store=store, memo_store=object(), evidence_llm=None, thesis_llm=None,
        thesis_model_spec="spec", deadline=deadline, now=lambda: next(times),
    )
    assert calls["n"] == 1
    assert summary.researched == 1
    assert summary.still_pending == 2
    assert summary.hit_deadline is True


def test_should_stop_halts(monkeypatch):
    store = FakeStore(["AAA", "BBB"])
    monkeypatch.setattr(
        "ops.research.drain.research_hit",
        lambda hit, **kw: _outcome(hit, "researched"),
    )
    summary = drain_pending(
        store=store, memo_store=object(), evidence_llm=None, thesis_llm=None,
        thesis_model_spec="spec", should_stop=lambda: True,
    )
    assert summary.researched == 0
    assert summary.still_pending == 2


def test_failed_outcome_marks_failed(monkeypatch):
    store = FakeStore(["AAA"])
    monkeypatch.setattr(
        "ops.research.drain.research_hit",
        lambda hit, **kw: _outcome(hit, "failed"),
    )
    summary = drain_pending(
        store=store, memo_store=object(), evidence_llm=None, thesis_llm=None,
        thesis_model_spec="spec",
    )
    assert summary.failed == 1
    assert store.failed == [1]


def test_exception_marks_failed_and_continues(monkeypatch):
    store = FakeStore(["AAA", "BBB"])

    def fake_hit(hit, **kw):
        if hit["symbol"] == "AAA":
            raise RuntimeError("boom")
        return _outcome(hit, "researched")

    monkeypatch.setattr("ops.research.drain.research_hit", fake_hit)
    summary = drain_pending(
        store=store, memo_store=object(), evidence_llm=None, thesis_llm=None,
        thesis_model_spec="spec",
    )
    assert store.failed == [1]
    assert store.researched == [2]
    assert summary == DrainSummary(researched=1, failed=1, still_pending=0,
                                   hit_deadline=False)


def test_research_error_propagates(monkeypatch):
    store = FakeStore(["AAA"])

    def fake_hit(hit, **kw):
        raise ResearchError("config problem")

    monkeypatch.setattr("ops.research.drain.research_hit", fake_hit)
    with pytest.raises(ResearchError):
        drain_pending(
            store=store, memo_store=object(), evidence_llm=None,
            thesis_llm=None, thesis_model_spec="spec",
        )


def test_max_names_caps_batch(monkeypatch):
    store = FakeStore(["AAA", "BBB", "CCC"])
    monkeypatch.setattr(
        "ops.research.drain.research_hit",
        lambda hit, **kw: _outcome(hit, "researched"),
    )
    summary = drain_pending(
        store=store, memo_store=object(), evidence_llm=None, thesis_llm=None,
        thesis_model_spec="spec", max_names=2,
    )
    assert summary.researched == 2
    assert summary.still_pending == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/ops/research/test_drain.py -v`
Expected: FAIL (`ModuleNotFoundError: ops.research.drain`).

- [ ] **Step 3: Implement `ops/research/drain.py`**

```python
"""Deadline- and shutdown-boxed drain of the pending research queue.

Shared by `ops research run` (name-capped, manual) and the overnight
scheduler tick (deadline-boxed, unattended). Pure of backend lifecycle:
the caller brings ds4 up and tears it down around this call.

Stop conditions, checked BEFORE each name so a name already in flight
always finishes:
  1. should_stop() is true  (graceful shutdown requested)
  2. now() >= deadline       (08:00 wall-clock reached)
  3. the pending queue is empty
A ResearchError is a configuration problem and aborts the whole batch
(re-raised); any other per-name exception marks that hit failed and
continues — one bad name must not strand the queue.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from ops.research.brain import ResearchError, research_hit


@dataclass(frozen=True)
class DrainSummary:
    researched: int
    failed: int
    still_pending: int
    hit_deadline: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def drain_pending(
    *,
    store,
    memo_store,
    evidence_llm,
    thesis_llm,
    thesis_model_spec: str,
    max_names: int | None = None,
    deadline: datetime | None = None,
    should_stop: Callable[[], bool] | None = None,
    now: Callable[[], datetime] = _utcnow,
    echo: Callable[[str], None] = lambda msg: None,
) -> DrainSummary:
    hits = store.pending_hits()
    if max_names is not None:
        hits = hits[:max_names]

    researched = failed = 0
    hit_deadline = False
    for hit in hits:
        if should_stop is not None and should_stop():
            break
        if deadline is not None and now() >= deadline:
            hit_deadline = True
            break
        try:
            outcome = research_hit(
                hit, evidence_llm=evidence_llm, thesis_llm=thesis_llm,
                memo_store=memo_store, thesis_model_spec=thesis_model_spec,
            )
        except ResearchError:
            raise  # configuration problem: abort the whole batch
        except Exception as exc:  # noqa: BLE001 - one bad name must not strand the queue
            store.mark_failed(hit["id"])
            failed += 1
            echo(f"{hit['symbol']}: FAILED ({type(exc).__name__}: {exc})")
            continue
        if outcome.status == "researched":
            store.mark_researched(hit["id"])
            researched += 1
            echo(
                f"{outcome.symbol}: memo {outcome.memo_id} "
                f"({outcome.recommendation}; evidence {outcome.evidence_kept} kept"
                f"/{outcome.evidence_dropped} dropped)"
            )
        else:
            store.mark_failed(hit["id"])
            failed += 1
            echo(f"{outcome.symbol}: FAILED — " + "; ".join(outcome.errors))

    return DrainSummary(
        researched=researched, failed=failed,
        still_pending=len(store.pending_hits()), hit_deadline=hit_deadline,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/ops/research/test_drain.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add ops/research/drain.py tests/ops/research/test_drain.py
git commit -m "feat(research): deadline/shutdown-boxed drain_pending"
```

---

### Task 5: CLI — refactor `research run` onto `drain_pending`, add `research kick`

**Files:**
- Modify: `ops/cli.py` (`research_run` ~line 399; add `research_kick` under the `research` group)
- Test: `tests/ops/test_cli_research_run.py` (existing must stay green), `tests/ops/test_cli_research_kick.py` (new)

**Interfaces:**
- Consumes: `ops.research.drain.drain_pending`, `DrainSummary`; `ops.research.run.run_screen`; `ops.research.trading.trade_research_sleeve`.
- Produces: `ops research run` behavior preserved (name-capped, no deadline); new `ops research kick` command running screen → drain-all → trade synchronously.

- [ ] **Step 1: Refactor `research_run` to call `drain_pending`**

Replace the per-hit loop inside `research_run` (the `for hit in hits:` block, ~lines 429–460) so the batch body becomes:

```python
        memo_store = MemoStore(config.memo_store_path)
        evidence_llm = build_stage_llm(config.research_evidence_model)
        thesis_llm = build_stage_llm(config.research_thesis_model)
        backend = build_managed_backend(load_managed_backend_config())
        try:
            backend.ensure_up()
            summary = drain_pending(
                store=store, memo_store=memo_store,
                evidence_llm=evidence_llm, thesis_llm=thesis_llm,
                thesis_model_spec=config.research_thesis_model,
                max_names=max_names, echo=click.echo,
            )
            researched, failed = summary.researched, summary.failed
        finally:
            backend.shutdown()
```

Add `from ops.research.drain import drain_pending` to the command's imports. Keep the `hits = store.pending_hits()[:max_names]` / "no pending hits" early return, the `edgar.get_user_agent()` fail-fast, the notify blocks, and the final summary line unchanged (they already reference `researched`/`failed`).

- [ ] **Step 2: Run existing CLI tests to verify still green**

Run: `python -m pytest tests/ops/test_cli_research_run.py -v`
Expected: PASS (unchanged behavior — the extracted loop is equivalent).

- [ ] **Step 3: Write the failing `research kick` test**

Create `tests/ops/test_cli_research_kick.py`:

```python
"""`ops research kick`: screen -> drain-all -> trade, one shot."""
import pytest
from click.testing import CliRunner

import ops.cli as cli_mod

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_SCREEN_STORE_PATH", str(tmp_path / "screen.sqlite"))
    monkeypatch.setenv("OPS_MEMO_STORE_PATH", str(tmp_path / "memos.sqlite"))
    monkeypatch.setenv("OPS_RESEARCH_JOURNAL_PATH", str(tmp_path / "rj.sqlite"))
    monkeypatch.delenv("OPS_LLM_MANAGED_BACKEND", raising=False)
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Test Suite test@example.com")
    monkeypatch.setattr("ops.research.models.build_stage_llm", lambda spec: f"llm:{spec}")
    return tmp_path


def test_kick_runs_screen_drain_trade_in_order(env, monkeypatch):
    calls = []
    monkeypatch.setattr("ops.research.run.run_screen",
                        lambda **kw: calls.append("screen"))

    from ops.research.drain import DrainSummary
    monkeypatch.setattr(
        "ops.research.drain.drain_pending",
        lambda **kw: (calls.append("drain"),
                      DrainSummary(2, 0, 0, False))[1],
    )
    monkeypatch.setattr("ops.research.trading.trade_research_sleeve",
                        lambda **kw: calls.append("trade"))
    # Neutralize the managed backend.
    class _NoBackend:
        def ensure_up(self): pass
        def shutdown(self): pass
    monkeypatch.setattr("ops.cli.build_managed_backend", lambda cfg: _NoBackend(),
                        raising=False)

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["research", "kick"])
    assert result.exit_code == 0, result.output
    assert calls == ["screen", "drain", "trade"]
```

If `build_managed_backend` is imported lazily inside the command body, patch its source (`ops.llm_backend.build_managed_backend`) instead — match whichever the implementation uses.

- [ ] **Step 4: Run to verify it fails**

Run: `python -m pytest tests/ops/test_cli_research_kick.py -v`
Expected: FAIL (`No such command 'kick'`).

- [ ] **Step 5: Implement `research kick`**

Add under the `research` group in `ops/cli.py`:

```python
@research.command("kick")
def research_kick() -> None:
    """One-shot demo: screen now (ignore the 3-day gate), drain the whole
    pending queue, then run the research trade step — so paper positions
    appear in a single manual run. Independent of the nightly schedule."""
    from datetime import date

    from ops.llm_backend import build_managed_backend, load_managed_backend_config
    from ops.quotes import make_yfinance_quote_source
    from ops.research.drain import drain_pending
    from ops.research.models import build_stage_llm
    from ops.research.run import run_screen
    from ops.research.store import ScreenStore
    from ops.research.trading import trade_research_sleeve
    from tradingagents.dataflows import edgar
    from tradingagents.memos.store import MemoStore

    config = load_config()
    edgar.get_user_agent()  # fail fast on missing SEC user agent

    click.echo("kick: screening...")
    run_screen(config=config, asof=date.today())

    store = ScreenStore(config.screen_store_path)
    memo_store = MemoStore(config.memo_store_path)
    evidence_llm = build_stage_llm(config.research_evidence_model)
    thesis_llm = build_stage_llm(config.research_thesis_model)
    backend = build_managed_backend(load_managed_backend_config())
    try:
        backend.ensure_up()
        summary = drain_pending(
            store=store, memo_store=memo_store,
            evidence_llm=evidence_llm, thesis_llm=thesis_llm,
            thesis_model_spec=config.research_thesis_model, echo=click.echo,
        )
    finally:
        backend.shutdown()
    click.echo(f"kick: drained {summary.researched} researched, "
               f"{summary.failed} failed")

    with Journal(config.research_journal_path) as research_journal, \
            Journal(config.journal_path) as main_journal:
        trade_research_sleeve(
            memo_store=memo_store, research_journal=research_journal,
            main_journal=main_journal,
            quote_source=make_yfinance_quote_source(),
            starting_cash=config.research_starting_cash, asof=date.today(),
        )
    click.echo("kick: done")
```

Confirm `Journal` is already imported at the top of `ops/cli.py`; if not, add `from ops.journal import Journal`. Cross-check the `trade_research_sleeve(...)` keyword arguments against `ops/main.py::_research_trade_tick` (lines 466–474) and match them exactly.

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest tests/ops/test_cli_research_kick.py tests/ops/test_cli_research_run.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ops/cli.py tests/ops/test_cli_research_kick.py
git commit -m "feat(cli): research run uses drain_pending; add research kick one-shot"
```

---

### Task 6: Overnight tick + scheduler registration

**Files:**
- Modify: `ops/main.py` (add `_days_since_iso`, `_drain_deadline`, `_research_overnight_tick`; register in `_start_full_scheduler` ~line 574)
- Test: `tests/ops/test_main.py`

**Interfaces:**
- Consumes: `ScreenStore.last_run()`, `run_screen`, `drain_pending`, `build_managed_backend`, `build_stage_llm`, config fields from Task 1, events from Task 2, `_shutdown_event`.
- Produces: `_research_overnight_tick(journal, config, *, now=None, should_stop=None)`; a scheduler job `id="research_overnight"` on `CronTrigger(hour=0, minute=0)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/ops/test_main.py`:

```python
def test_overnight_tick_screens_when_due_then_drains(monkeypatch, tmp_path):
    import ops.main as main_mod
    from ops.config import load_config
    from ops.journal import Journal
    from ops.research.drain import DrainSummary
    from ops.research.store import ScreenStore

    monkeypatch.setenv("OPS_SCREEN_STORE_PATH", str(tmp_path / "screen.sqlite"))
    monkeypatch.setenv("OPS_MEMO_STORE_PATH", str(tmp_path / "memos.sqlite"))
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "T t@e.com")
    monkeypatch.setattr("ops.research.models.build_stage_llm", lambda s: f"llm:{s}")

    class _NoBackend:
        def ensure_up(self): pass
        def shutdown(self): pass
    monkeypatch.setattr(main_mod, "build_managed_backend", lambda c: _NoBackend())

    events_seen = []
    monkeypatch.setattr("ops.research.run.run_screen",
                        lambda **kw: events_seen.append("screen"))
    monkeypatch.setattr("ops.research.drain.drain_pending",
                        lambda **kw: (events_seen.append("drain"),
                                      DrainSummary(3, 0, 0, False))[1])

    config = load_config()
    with Journal(str(tmp_path / "j.sqlite")) as journal:
        # No prior screen run -> screen is due.
        main_mod._research_overnight_tick(journal, config)
        assert events_seen == ["screen", "drain"]
        assert journal.has_event_today(main_mod.events.KIND_RESEARCH_DRAIN_RUN)


def test_overnight_tick_skips_screen_when_recent(monkeypatch, tmp_path):
    import ops.main as main_mod
    from datetime import date
    from ops.config import load_config
    from ops.journal import Journal
    from ops.research.drain import DrainSummary
    from ops.research.screener import Bar, ScreenResult
    from ops.research.store import ScreenStore

    monkeypatch.setenv("OPS_SCREEN_STORE_PATH", str(tmp_path / "screen.sqlite"))
    monkeypatch.setenv("OPS_MEMO_STORE_PATH", str(tmp_path / "memos.sqlite"))
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "T t@e.com")
    monkeypatch.setattr("ops.research.models.build_stage_llm", lambda s: f"llm:{s}")

    class _NoBackend:
        def ensure_up(self): pass
        def shutdown(self): pass
    monkeypatch.setattr(main_mod, "build_managed_backend", lambda c: _NoBackend())

    # A screen run recorded just now -> interval (3 days) not elapsed.
    store = ScreenStore(str(tmp_path / "screen.sqlite"))
    store.record_run(asof=date.today(), universe_size=0, results=[])

    seen = []
    monkeypatch.setattr("ops.research.run.run_screen",
                        lambda **kw: seen.append("screen"))
    monkeypatch.setattr("ops.research.drain.drain_pending",
                        lambda **kw: (seen.append("drain"),
                                      DrainSummary(0, 0, 0, False))[1])

    config = load_config()
    with Journal(str(tmp_path / "j.sqlite")) as journal:
        main_mod._research_overnight_tick(journal, config)
    assert seen == ["drain"]  # screened recently -> only drain


def test_overnight_tick_records_error_event_not_raises(monkeypatch, tmp_path):
    import ops.main as main_mod
    from ops.config import load_config
    from ops.journal import Journal

    monkeypatch.setenv("OPS_SCREEN_STORE_PATH", str(tmp_path / "screen.sqlite"))
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "T t@e.com")
    monkeypatch.setattr("ops.research.run.run_screen",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    config = load_config()
    with Journal(str(tmp_path / "j.sqlite")) as journal:
        main_mod._research_overnight_tick(journal, config)  # must not raise
        assert journal.has_event_today(main_mod.events.KIND_RESEARCH_DRAIN_ERROR)


def test_full_scheduler_registers_overnight_job(monkeypatch, tmp_path):
    import ops.main as main_mod
    from ops.config import load_config
    from ops.journal import Journal

    monkeypatch.delenv("OPS_BROKER_MODE", raising=False)
    config = load_config()
    with Journal(str(tmp_path / "j.sqlite")) as journal:
        broker, orch, guardian, calendar, result, backend = main_mod._startup(config, journal)
        dispatcher = main_mod._build_dispatcher(journal)
        sched = main_mod._start_full_scheduler(
            orch, guardian, dispatcher, journal, broker,
            calendar=calendar, config=config,
        )
        try:
            job = sched.get_job("research_overnight")
            assert job is not None
        finally:
            sched.shutdown(wait=False)
        if backend is not None:
            backend.shutdown()
```

If `_startup` in `test_full_scheduler_registers_overnight_job` is awkward in the paper path, model it on the nearest existing scheduler test in `tests/ops/test_main.py` (grep `_start_full_scheduler`) and only assert `get_job("research_overnight")`.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/ops/test_main.py -k overnight -v`
Expected: FAIL (`AttributeError: _research_overnight_tick`).

- [ ] **Step 3: Implement the tick + helpers**

In `ops/main.py`, add imports at the top: `from datetime import date, datetime, timezone` already present; add `from zoneinfo import ZoneInfo`. Then after `_research_trade_tick` (~line 481):

```python
def _days_since_iso(iso: str) -> float:
    """Whole-plus-fractional days between an ISO-8601 UTC timestamp and now."""
    then = datetime.fromisoformat(iso)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400.0


def _drain_deadline(hour: int) -> datetime:
    """Today's local (America/New_York) HH:00 as a tz-aware datetime — the
    wall-clock the overnight drain must stop before, well ahead of the
    09:30 first momentum tick."""
    ny = ZoneInfo("America/New_York")
    return datetime.now(ny).replace(hour=hour, minute=0, second=0, microsecond=0)


def _research_overnight_tick(journal: Journal, config, *, now=None, should_stop=None) -> None:
    """Nightly 00:00 job: screen if it's been >= research_screen_interval_days,
    then drain the whole pending queue on ds4 until the queue empties, the
    local deadline hour is reached, or shutdown is requested. Scheduler-safe:
    any failure records research_drain_error rather than raising."""
    screened_this_run = False
    try:
        from ops.research.store import ScreenStore

        store = ScreenStore(config.screen_store_path)
        last = store.last_run()
        due = last is None or _days_since_iso(last["created_at"]) >= config.research_screen_interval_days
        if due:
            from ops.research.run import run_screen

            run_screen(config=config, asof=date.today())
            screened_this_run = True

        from tradingagents.dataflows import edgar
        edgar.get_user_agent()  # fail fast before spinning ds4

        from ops.research.drain import drain_pending
        from ops.research.models import build_stage_llm
        from tradingagents.memos.store import MemoStore

        evidence_llm = build_stage_llm(config.research_evidence_model)
        thesis_llm = build_stage_llm(config.research_thesis_model)
        deadline = _drain_deadline(config.research_drain_deadline_hour)
        stop = should_stop or _shutdown_event.is_set
        backend = build_managed_backend(load_managed_backend_config())
        try:
            backend.ensure_up()
            summary = drain_pending(
                store=store, memo_store=MemoStore(config.memo_store_path),
                evidence_llm=evidence_llm, thesis_llm=thesis_llm,
                thesis_model_spec=config.research_thesis_model,
                deadline=deadline, should_stop=stop,
                now=(now or (lambda: datetime.now(deadline.tzinfo))),
            )
        finally:
            backend.shutdown()

        journal.record_event(
            events.KIND_RESEARCH_DRAIN_RUN,
            events.research_drain_run_payload(
                asof=date.today().isoformat(), screened_this_run=screened_this_run,
                researched=summary.researched, failed=summary.failed,
                still_pending=summary.still_pending, hit_deadline=summary.hit_deadline,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        journal.record_event(
            events.KIND_RESEARCH_DRAIN_ERROR,
            events.research_drain_error_payload(
                error=f"{type(exc).__name__}: {exc}",
            ),
        )
```

`build_managed_backend` and `load_managed_backend_config` are already imported at module top (line 41). Note the `now` default uses `deadline.tzinfo` (America/New_York) so the deadline comparison in `drain_pending` compares like tz.

- [ ] **Step 4: Register the job**

In `_start_full_scheduler`, inside the `if config is not None:` block (after the `research_trade` job, ~line 584):

```python
        sched.add_job(
            lambda: _research_overnight_tick(journal, config),
            CronTrigger(hour=0, minute=0),
            id="research_overnight", max_instances=1, misfire_grace_time=600,
        )
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/ops/test_main.py -k overnight -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add ops/main.py tests/ops/test_main.py
git commit -m "feat(ops): nightly research overnight tick (3-day screen + drain) in ops run"
```

---

### Task 7: Retire the two Saturday launchd plists

**Files:**
- Delete: `ops/deploy/com.tradingagents.screen.plist.template`, `ops/deploy/com.tradingagents.research.plist.template`
- Modify: `ops/deploy/__init__.py` (remove `render_screen_plist`, `render_research_plist`, their template paths, `SCREEN_LABEL`, `RESEARCH_LABEL`, `DEFAULT_SCREEN_PLIST_PATH`, `DEFAULT_RESEARCH_PLIST_PATH`), `ops/cli.py` (remove `install-screen-service` and `install-research-service` commands, lines ~95–190)
- Modify: `tests/ops/test_deploy.py` (remove the screen/research render tests)
- Test: `tests/ops/test_deploy.py`

**Interfaces:**
- Consumes: nothing.
- Produces: deploy surface reduced to the always-on ops plist only. The screen + research work now lives in `_research_overnight_tick` (Task 6).

- [ ] **Step 1: Remove the render functions + constants**

In `ops/deploy/__init__.py` delete `_SCREEN_TEMPLATE_PATH`, `_RESEARCH_TEMPLATE_PATH`, `SCREEN_LABEL`, `RESEARCH_LABEL`, `DEFAULT_SCREEN_PLIST_PATH`, `DEFAULT_RESEARCH_PLIST_PATH`, and the `render_screen_plist` + `render_research_plist` functions. Leave `render_launchd_plist`, `_render`, `SERVICE_LABEL`, `DEFAULT_PLIST_PATH`, `DEFAULT_LOG_DIR` intact.

- [ ] **Step 2: Remove the CLI install commands**

In `ops/cli.py` delete the `@cli.command("install-screen-service")` function (`install_screen_service`, ~lines 95–131) and the `@cli.command("install-research-service")` function (`install_research_service`, ~lines 133–190).

- [ ] **Step 3: Delete the templates**

```bash
git rm ops/deploy/com.tradingagents.screen.plist.template ops/deploy/com.tradingagents.research.plist.template
```

- [ ] **Step 4: Prune the deploy tests**

In `tests/ops/test_deploy.py` remove every test that references `render_screen_plist`, `render_research_plist`, `install-screen-service`, `install-research-service`, `SCREEN_LABEL`, or `RESEARCH_LABEL`. Keep the always-on ops plist render tests.

- [ ] **Step 5: Run the deploy + CLI tests**

Run: `python -m pytest tests/ops/test_deploy.py -v`
Expected: PASS (only ops-plist tests remain).

- [ ] **Step 6: Full suite sanity**

Run: `python -m pytest tests/ops -q`
Expected: PASS (no import references the removed symbols).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore(deploy): retire Saturday screen+research launchd plists (folded into ops run)"
```

---

### Task 8: Docs — pipelines + a short cadence runbook

**Files:**
- Modify: `docs/research_pipelines.md` (the "weekly (Saturday)" description of the brain, ~lines 63–66)
- Modify: `docs/research_screener.md` (screen cadence, if it states "weekly/Saturday")
- Create: `docs/research_cadence.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: Update `research_pipelines.md`**

Change the brain's cadence line (currently "a deep **structured memo** for one screener passer, **weekly** (Saturday, `ops research run --max-names 10`)") to describe the every-3-days screen + nightly 00:00–08:00 deadline-boxed drain inside `ops run`, and point to `docs/research_cadence.md`.

- [ ] **Step 2: Grep and fix stale "Saturday" cadence claims**

Run: `grep -rn "Saturday\|weekly screen\|--max-names 10\|install-screen-service\|install-research-service" docs/`
For each hit describing the retired schedule, update to the new cadence or remove.

- [ ] **Step 3: Write `docs/research_cadence.md`**

Cover: the nightly `research_overnight` job (00:00), the 3-day screen gate (`ScreenStore.last_run` age vs `research_screen_interval_days`), the deadline-boxed drain (`research_drain_deadline_hour`, default 08:00 NY), the `research_screen_ttl_days` skip, the three env knobs, the ds4 non-overlap invariant (drain 00:00–08:00 vs momentum ticks 09:30–16:00), and `ops research kick` for a manual end-to-end run. Note the two Saturday launchd plists are retired and must be `launchctl unload`ed + deleted on the deployed machine.

- [ ] **Step 4: Commit**

```bash
git add docs/research_pipelines.md docs/research_screener.md docs/research_cadence.md
git commit -m "docs(research): every-3-days screen + overnight drain cadence"
```

---

## Rollout (post-merge, manual)

1. On the deploy machine, unload + remove the retired agents:
   `launchctl unload ~/Library/LaunchAgents/com.tradingagents.screen.plist ~/Library/LaunchAgents/com.tradingagents.research.plist` then delete the two plist files.
2. Reload the always-on ops agent so the new `research_overnight` job registers.
3. Run `ops research kick` once by hand to seed the first memos + paper positions and confirm the full chain end-to-end (the operator's "today" ask).
4. Let the nightly 00:00 job take over.

## Self-Review Notes

- **Spec coverage:** consolidation into `ops run` (Task 6/7), 3-day screen gate (Task 6), deadline-boxed drain (Task 4/6), 7-day since-last-screened TTL (Task 3), config knobs (Task 1), kick one-shot (Task 5), events/observability (Task 2), docs (Task 8) — all mapped.
- **ds4 non-overlap:** drain deadline (08:00 NY) < first momentum tick (09:30); single-process scheduler serializes; backend torn down in `finally`.
- **Backwards-compat:** `record_run`/`enqueue_hit` gain `ttl_days=0` default (no behavior change for existing callers); `research run --max-names` preserved.
