"""Read-only snapshot builder for the local ops dashboard.

Same contract as ops/status.py::build_status, wider scope: reads ONLY the
sqlite stores (mode=ro URIs — a hard guarantee, not a convention) plus two
flag files. No broker, no MCP, no OAuth, no quotes, no LLM, no network.

Every top-level section is exception-isolated: a missing or mid-migration
store turns into {"error": ...} for that section while the rest of the
snapshot still builds — a partial dashboard beats a blank page.
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ops import events
from ops.config import OpsConfig
from ops.journal import Journal
from ops.live_gate import count_live_buy_fills, flip_epoch

# A guardian pass starts every 60s; 3 missed passes = stale. Matches the
# heartbeat's staleness window in ops/main.py.
GUARDIAN_STALE_S = 180.0


def jsonable(value: Any) -> Any:
    """Deep-convert to JSON-safe types. Decimal -> str (never float: this
    is money), aware datetime -> UTC ISO-8601."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def section(builder: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return jsonable(builder())
    except Exception as exc:  # noqa: BLE001 — isolation is the point
        return {"error": f"{type(exc).__name__}: {exc}"}


def ro_conn(path: str) -> sqlite3.Connection:
    """mode=ro sqlite connection (raises OperationalError if missing)."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _event_view(ev: dict[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    if ev is None:
        return None
    return {
        "at": ev["at"],
        "age_seconds": (now - ev["at"]).total_seconds(),
        "payload": ev["payload"],
    }


def _health_section(config: OpsConfig, now: datetime) -> dict[str, Any]:
    with Journal(config.journal_path, readonly=True) as j:
        started = j.last_event(events.KIND_SERVICE_STARTED)
        stopping = j.last_event(events.KIND_SERVICE_STOPPING)
        halts = {
            "daily_halt_today": j.has_event_today(events.KIND_DAILY_HALT, now=now),
            "kill_switch_this_week": j.has_event_since_last_monday(
                events.KIND_KILL_SWITCH, now=now),
        }
        cycle_run = j.last_event(events.KIND_DAILY_CYCLE_RUN)
        cycle_done = j.last_event(events.KIND_DAILY_CYCLE_COMPLETED)
        cursor = j.get_cursor("notify")
        max_event_id = j.last_event_id_before(now) or 0
        epoch = flip_epoch(j)
        live_fills = count_live_buy_fills(j)
        heartbeat_errors = j.count_events(
            events.KIND_HEARTBEAT_ERROR, since=now - timedelta(hours=24))

    guardian_alive_at: datetime | None = None
    try:
        mtime = os.stat(config.guardian_liveness_path).st_mtime
        guardian_alive_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
    except OSError:
        pass
    guardian_age = (
        (now - guardian_alive_at).total_seconds()
        if guardian_alive_at is not None else None
    )

    # Verdict: journal start/stop ordering first (a fresh liveness file
    # from a guardian that outlived a clean shutdown must not say
    # RUNNING), then liveness recency.
    if started is None:
        verdict = "UNKNOWN"
    elif stopping is not None and stopping["at"] > started["at"]:
        verdict = "STOPPED"
    elif guardian_age is None:
        verdict = "UNKNOWN"
    elif guardian_age <= GUARDIAN_STALE_S:
        verdict = "RUNNING"
    else:
        verdict = "STALE"

    last_stopping = _event_view(stopping, now)
    if last_stopping is not None:
        last_stopping["exit_code"] = last_stopping.pop("payload").get("exit_code")
    last_started = _event_view(started, now)
    if last_started is not None:
        last_started.pop("payload")

    return {
        "verdict": verdict,
        "broker_mode": config.broker_mode,
        "last_started": last_started,
        "last_stopping": last_stopping,
        "guardian": {"alive_at": guardian_alive_at, "age_seconds": guardian_age},
        "daily_cycle": {
            "last_run_at": cycle_run["at"] if cycle_run else None,
            "last_completed_at": cycle_done["at"] if cycle_done else None,
        },
        "halts": halts,
        "research_paused": os.path.exists(config.research_pause_flag_path),
        "live_gate": {
            "flip_marker_present": epoch is not None,
            "flip_at": epoch,
            "live_buy_fills": live_fills,
            "cap": config.live_max_position,
            "gate_count": config.live_fill_gate_count,
            "remaining": max(0, config.live_fill_gate_count - live_fills),
        },
        "notify": {
            "cursor": cursor,
            "max_event_id": max_event_id,
            "lag": max(0, max_event_id - cursor),
        },
        "heartbeat_errors_24h": heartbeat_errors,
    }


def _market_section(config: OpsConfig, now: datetime) -> dict[str, Any]:
    from ops.scheduler.market_calendar import MarketCalendar

    cal = MarketCalendar()
    return {
        "is_open": cal.is_open_now(now),
        "next_open": cal.next_open(now),
        "previous_close": cal.previous_close(now),
        "is_trading_day": cal.is_trading_day(now.date()),
        "research_deadline_hour_et": config.research_drain_deadline_hour,
    }


def _refuse_quotes(symbol: str) -> Decimal:
    """Quote source handed to PaperBroker.from_journal: the dashboard is
    journal-only, so any quote request during position replay is a bug that
    would silently make the snapshot network-dependent. Mirrors
    ops.status._refuse_quotes without importing the private name."""
    raise RuntimeError(
        f"dashboard snapshot is journal-only, but a quote was requested "
        f"for {symbol!r} — position replay must not touch quote sources"
    )


def _one_sleeve(path: str, now: datetime) -> dict[str, Any]:
    """One ledger's P&L / positions / fills, from journal replay alone.

    Opened readonly (missing file → sqlite3.OperationalError, which the
    caller turns into a per-sleeve {"error": ...}). Positions/cash come
    from PaperBroker.from_journal with a refuse-quotes guard, exactly like
    ops.status; equity/day P&L come from the equity-snapshot table.
    """
    from ops.broker.paper import PaperBroker
    from ops.trading_time import trading_day_start

    day_start = trading_day_start(now)
    with Journal(path, readonly=True) as j:
        snaps = j.read_equity_snapshots()
        fills = j.read_fills()
        replay = PaperBroker.from_journal(
            journal=j, quote_source=_refuse_quotes, starting_cash=Decimal("0"))
        positions = [
            {"symbol": p.symbol, "quantity": p.quantity,
             "entry": p.avg_entry_price, "stop": p.stop_loss_price}
            for p in replay.get_positions()
        ]
        cash = replay.get_cash()

    latest = snaps[-1] if snaps else None
    before_today = [s for s in snaps if s["at"] < day_start]
    day_pnl: Decimal | None = None
    if latest is not None and before_today and before_today[-1]["equity"] != 0:
        prev = before_today[-1]["equity"]
        day_pnl = (latest["equity"] - prev) / prev
    return {
        "equity": latest["equity"] if latest else None,
        "cash": cash,
        "equity_at": latest["at"] if latest else None,
        "equity_kind": latest["kind"] if latest else None,
        "day_pnl_pct": day_pnl,
        "series": [{"at": s["at"], "equity": s["equity"]} for s in snaps[-60:]],
        "positions": positions,
        "fills_today": [
            {"symbol": f["symbol"], "side": f["side"], "quantity": f["quantity"],
             "price": f["price"], "filled_at": f["filled_at"]}
            for f in fills if f["filled_at"] >= day_start
        ],
    }


def _sleeves_section(config: OpsConfig, now: datetime) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, path in (
        ("momentum", config.journal_path),
        ("research", config.research_journal_path),
        ("baseline", config.baseline_journal_path),
    ):
        out[name] = section(lambda p=path: _one_sleeve(p, now))
    return out


def _funnel_section(config: OpsConfig, now: datetime) -> dict[str, Any]:
    raise NotImplementedError("Task 5")


# Recent-anomaly kinds over the momentum journal (mirrors ops/status.py:31-37).
_MOMENTUM_ANOMALY_KINDS = (
    events.KIND_GUARDIAN_CHECK_ERROR,
    events.KIND_ORCHESTRATOR_TICK_ERROR,
    events.KIND_STOP_FAILED,
    events.KIND_GUARDIAN_BLIND,
    events.KIND_INCONSISTENCY,
)

# Anomaly kinds over the research journal (own isolation: a missing research
# journal simply omits these keys rather than zeroing them).
_RESEARCH_ANOMALY_KINDS = (
    events.KIND_RESEARCH_MONITOR_ERROR,
    events.KIND_RESEARCH_TRADE_ERROR,
    events.KIND_RESEARCH_VETTING_ERROR,
    events.KIND_RESEARCH_DRAIN_ERROR,
)


def _kind_anomaly(j: Journal, kind: str, since: datetime) -> dict[str, Any]:
    last = j.last_event(kind)
    return {
        "count": j.count_events(kind, since=since),
        "last_at": last["at"] if last is not None else None,
    }


def _anomalies_section(config: OpsConfig, now: datetime) -> dict[str, Any]:
    since = now - timedelta(days=7)
    out: dict[str, Any] = {}
    with Journal(config.journal_path, readonly=True) as j:
        for kind in _MOMENTUM_ANOMALY_KINDS:
            out[kind] = _kind_anomaly(j, kind, since)
    # Research journal under its own isolation: absence of the store is
    # information — omit its kinds rather than reporting a false zero.
    try:
        with Journal(config.research_journal_path, readonly=True) as rj:
            for kind in _RESEARCH_ANOMALY_KINDS:
                out[kind] = _kind_anomaly(rj, kind, since)
    except sqlite3.OperationalError:
        pass
    return out


def build_snapshot(
    config: OpsConfig, *, now: datetime | None = None,
) -> dict[str, Any]:
    when = now if now is not None else datetime.now(timezone.utc)
    return {
        "generated_at": when.isoformat(),
        "health": section(lambda: _health_section(config, when)),
        "sleeves": section(lambda: _sleeves_section(config, when)),
        "funnel": section(lambda: _funnel_section(config, when)),
        "anomalies_7d": section(lambda: _anomalies_section(config, when)),
        "market": section(lambda: _market_section(config, when)),
    }
